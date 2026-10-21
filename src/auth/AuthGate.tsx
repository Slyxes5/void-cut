import { useEffect, useState, useSyncExternalStore, type ReactNode, type FormEvent } from 'react';
import { AuthSession, attachAuthSession } from './client';

const defaultSession = new AuthSession((...args) => window.fetch(...args));

export function AuthGate({ children, session = defaultSession, initialGuestPreview = false }: {
  children: ReactNode;
  session?: AuthSession;
  initialGuestPreview?: boolean;
}) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot, session.getSnapshot);
  const [password, setPassword] = useState('');
  const [guestPreview, setGuestPreview] = useState(initialGuestPreview);
  useEffect(() => attachAuthSession(session), [session]);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const value = password;
    setPassword('');
    await session.login(value);
  };
  return <>
    {guestPreview && !state.authenticated && <div className="auth-demo-toolbar" role="status">
      <span><strong>Mode demo</strong> · Fitur terkunci sampai Anda masuk.</span>
      <button type="button" onClick={() => setGuestPreview(false)}>Masuk untuk membuka fitur</button>
    </div>}
    {(state.everAuthenticated || guestPreview) && <div className="auth-editor" hidden={!state.authenticated && !guestPreview} inert={!state.authenticated}>
      {state.authenticated ? <div className="auth-toolbar"><span>Private session</span><button type="button" disabled={state.busy}
        onClick={() => void session.logout()}>Sign out</button></div> : null}
      {children}
    </div>}
    {!state.authenticated && !guestPreview && <main className="auth-screen">
      <section className="auth-card" aria-labelledby="auth-title">
        <div className="auth-brand">VOID CUT</div>
        <h1 id="auth-title">Private editor</h1>
        {state.phase === 'checking' ? <p role="status">Checking your session…</p> : <>
          <p>Sign in with your laptop’s app password. Your Gemini API key is not your login password.</p>
          {state.configured === false ? <div className="auth-notice" role="status">
            Login setup is required on the laptop. Run <code>venv\Scripts\python.exe -m backend.setup_login{typeof window !== 'undefined' && window.location.protocol === 'https:' ? ` --origin ${window.location.origin}` : ''}</code> from the project folder,
            then safely restart the backend when no jobs are active.
          </div> : <form onSubmit={submit}>
            <label htmlFor="app-password">App password</label>
            <input id="app-password" name="password" type="password" autoComplete="current-password" required maxLength={256}
              value={password} onChange={event => setPassword(event.target.value)} disabled={state.busy} autoFocus />
            <button type="submit" disabled={state.busy || !password}>{state.busy ? 'Signing in…' : 'Sign in'}</button>
          </form>}
          {state.message && <p className="auth-notice" role="alert">{state.message}</p>}
          <button className="auth-preview-button" type="button" onClick={() => setGuestPreview(true)}>
            View demo without signing in
          </button>
          <button className="auth-recheck" type="button" disabled={state.busy} onClick={() => void session.refresh()}>Check connection again</button>
          {state.everAuthenticated && <p>Your editor remains mounted. Sign in again to continue; do not refresh if you have unsaved edits.</p>}
        </>}
      </section>
    </main>}
  </>;
}
