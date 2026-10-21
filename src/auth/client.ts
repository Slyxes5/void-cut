// Session credentials remain in the browser's HttpOnly cookie; CSRF stays in memory.
let csrfToken: string | undefined;
export const getCsrfToken = () => csrfToken;

type AuthState = {
  phase: 'checking' | 'ready';
  authenticated: boolean;
  everAuthenticated: boolean;
  configured: boolean | null;
  message: string;
  busy: boolean;
};

export class AuthSession {
  private fetcher: typeof fetch;
  private listeners = new Set<() => void>();
  private generation = 0;
  private state: AuthState = { phase: 'checking', authenticated: false, everAuthenticated: false, configured: null, message: '', busy: false };
  constructor(fetcher: typeof fetch) { this.fetcher = fetcher; }
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private update(change: Partial<AuthState>) {
    this.state = { ...this.state, ...change };
    this.listeners.forEach(listener => listener());
  }
  async login(password: string) {
    if (this.state.busy) return;
    this.generation++;
    this.update({ busy: true, message: '' });
    try {
      const response = await this.fetcher('/api/auth/login', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      if (!response.ok) {
        csrfToken = undefined;
        this.update({ phase: 'ready', authenticated: false,
          ...(response.status === 503 ? { configured: false } : {}),
          message: response.status === 401 ? 'Invalid password. Try again.'
            : response.status === 429 ? 'Too many sign-in attempts. Wait before trying again.'
            : response.status === 503 ? 'Authentication setup is required.' : 'Could not sign in. Try again when the laptop is available.' });
        return;
      }
      this.accept(await response.json());
    } catch {
      csrfToken = undefined;
      this.update({ phase: 'ready', authenticated: false, message: 'Could not sign in. Check the laptop connection and try again.' });
    } finally { this.update({ busy: false }); }
  }
  async logout() {
    if (this.state.busy) return;
    this.generation++;
    const token = csrfToken;
    this.update({ busy: true });
    let confirmed = false;
    try {
      const response = await this.fetcher('/api/auth/logout', { method: 'POST', credentials: 'same-origin',
        headers: token ? { 'X-CSRF-Token': token } : {} });
      confirmed = response.ok || response.status === 401;
    } catch { /* The cookie may still be live; do not claim it was revoked. */ }
    csrfToken = undefined;
    this.update({ authenticated: false, phase: 'ready', busy: false,
      message: confirmed ? 'Signed out.' : 'Sign out could not be confirmed. Check the laptop connection and try again.' });
  }
  private accept(data: { configured?: boolean; authenticated?: boolean; csrf_token?: string }) {
    const authenticated = data.configured === true && data.authenticated === true && typeof data.csrf_token === 'string' && !!data.csrf_token;
    csrfToken = authenticated ? data.csrf_token : undefined;
    this.update({ phase: 'ready', configured: data.configured === true, authenticated,
      everAuthenticated: this.state.everAuthenticated || authenticated, message: '' });
  }
  expire = () => {
    this.generation++;
    csrfToken = undefined;
    this.update({ phase: 'ready', authenticated: false, message: 'Your session expired. Sign in again; your editor is still open.' });
  };
  async refresh() {
    if (this.state.busy) return;
    const generation = ++this.generation;
    try {
      const response = await this.fetcher('/api/auth/status', { credentials: 'same-origin', cache: 'no-store' });
      if (!response.ok) throw new Error('Status unavailable');
      const data = await response.json();
      if (generation === this.generation) this.accept(data);
    } catch {
      if (generation !== this.generation) return;
      csrfToken = undefined;
      this.update({ phase: 'ready', authenticated: false, message: 'Could not check your session. Check the laptop connection, then try again.' });
    }
  }
}
const installedTargets = new WeakSet<object>();
export function installAuthFetch(target: Pick<Window, 'fetch' | 'location' | 'dispatchEvent'> = window) {
  if (installedTargets.has(target)) return;
  target.fetch = createAuthFetch(target.fetch.bind(target), target.location.origin, getCsrfToken,
    () => target.dispatchEvent(new Event('session-expired')));
  installedTargets.add(target);
}

export function attachAuthSession(session: AuthSession, target: EventTarget = window,
  visibility: EventTarget & { visibilityState: string } = document) {
  const refresh = () => { void session.refresh(); };
  const onVisible = () => { if (visibility.visibilityState === 'visible') refresh(); };
  target.addEventListener('focus', refresh);
  target.addEventListener('session-expired', session.expire);
  visibility.addEventListener('visibilitychange', onVisible);
  refresh();
  return () => {
    target.removeEventListener('focus', refresh);
    target.removeEventListener('session-expired', session.expire);
    visibility.removeEventListener('visibilitychange', onVisible);
  };
}

export function createAuthFetch(
  fetcher: typeof fetch,
  origin: string,
  getToken: () => string | undefined,
  onExpired: () => void,
): typeof fetch {
  return async (input, init) => {
    const url = new URL(input instanceof Request ? input.url : String(input), origin);
    if (url.origin !== origin || !url.pathname.startsWith('/api/')) return fetcher(input, init);
    const request = input instanceof Request ? input : undefined;
    const headers = new Headers(init?.headers ?? request?.headers);
    const method = (init?.method ?? request?.method ?? 'GET').toUpperCase();
    const token = getToken();
    const authRoute = url.pathname.startsWith('/api/auth/');
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && token && (!authRoute || url.pathname === '/api/auth/logout')) {
      headers.set('X-CSRF-Token', token);
    }
    const response = await fetcher(input, { ...init, headers, credentials: 'same-origin' });
    if (response.status === 401 && !authRoute) onExpired();
    return response; // Never retry: an unsafe operation may already have reached the server.
  };
}
