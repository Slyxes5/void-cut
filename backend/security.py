"""Single-user security primitives. No database, plaintext password or persistent sessions."""
import hashlib
import hmac
import secrets
import asyncio
import json
import logging
import re
import traceback
import os
import time
from collections import OrderedDict, deque
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException, Request
from starlette.responses import JSONResponse

SESSION_SECONDS = 12 * 60 * 60
LOCAL_COOKIE = 'cheat_clip_local_session'
SECURE_COOKIE = '__Host-cheat_clip_session'
SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}


_request_secrets = deque(maxlen=64)


def remember_secret(value):
    if isinstance(value, str) and len(value.strip()) >= 8 and value.strip() not in _request_secrets:
        _request_secrets.append(value.strip())


def redact_secrets(text):
    text = str(text)
    values = list(_request_secrets) + [os.environ.get(k, '') for k in
        ('GEMINI_API_KEY', 'ADMIN_API_KEY', 'CHEAT_CLIP_API_KEY', 'CHEAT_CLIP_PASSWORD_HASH',
         'YOUTUBE_CLIENT_SECRET', 'TIKTOK_CLIENT_SECRET', 'SOCIAL_TOKEN_ENCRYPTION_KEY')]
    for value in values:
        if len(value) >= 8:
            text = text.replace(value, '[REDACTED]')
    return re.sub(r'(?i)([?&](?:api_?key|key|token|access_token|refresh_token|client_secret|upload_token|code|state|code_verifier|password|cookies?)=)[^&\s\"\']+',
                  r'\1[REDACTED]', text)


class SecretLogFilter(logging.Filter):
    def filter(self, record):
        if record.name == 'uvicorn.access' and isinstance(record.args, tuple) and len(record.args) == 5:
            # Preserve the tuple contract expected by Uvicorn's AccessFormatter.
            address, method, target, version, status = record.args
            record.args = (address, method, str(target).split('?', 1)[0], version, status)
        else:
            text = record.getMessage()
            if record.exc_info:
                text += '\n' + ''.join(traceback.format_exception(*record.exc_info))
                record.exc_info = None
                record.exc_text = None
            record.msg, record.args = redact_secrets(text), ()
        if record.stack_info:
            record.stack_info = redact_secrets(record.stack_info)
        return True


def install_log_redaction():
    previous = logging.getLogRecordFactory()
    if getattr(previous, '_cheat_clip_redaction', False):
        return
    log_filter = SecretLogFilter()
    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        log_filter.filter(record)
        return record
    setattr(factory, '_cheat_clip_redaction', True)
    logging.setLogRecordFactory(factory)


class SecurityManager:
    def __init__(self, password_hash=None, origins=None, clock=None):
        self.password_hash = os.environ.get('CHEAT_CLIP_PASSWORD_HASH', '') if password_hash is None else password_hash
        self.origins = set(origins or default_origins())
        self.clock = clock or time.time
        self.sessions = OrderedDict()
        self.login_attempts = deque()

    @property
    def configured(self):
        return valid_password_hash(self.password_hash)

    def cookie_name(self, request):
        return LOCAL_COOKIE if request.url.hostname in {'localhost', '127.0.0.1', '::1'} else SECURE_COOKIE

    def session(self, request):
        now = self.clock()
        for key in list(self.sessions):
            if self.sessions[key]['expires_at'] <= now:
                del self.sessions[key]
        token = request.cookies.get(self.cookie_name(request), '')
        key = hashlib.sha256(token.encode()).hexdigest()
        return self.sessions.get(key)

    def valid_origin(self, request):
        origin = request.headers.get('origin', '')
        # Vite preserves Host; TLS terminates only at the configured private Tailscale Serve.
        parsed = urlsplit(origin)
        return (origin in self.origins and parsed.netloc.lower() == request.headers.get('host', '').lower()
                and (parsed.scheme == 'https' or parsed.hostname in {'localhost', '127.0.0.1', '::1'}))

    def new_session(self):
        token = secrets.token_urlsafe(32)
        session = {'csrf_token': secrets.token_urlsafe(32), 'expires_at': self.clock() + SESSION_SECONDS}
        self.sessions[hashlib.sha256(token.encode()).hexdigest()] = session
        while len(self.sessions) > 32:
            self.sessions.popitem(last=False)
        return token, session

    def revoke(self, request):
        token = request.cookies.get(self.cookie_name(request), '')
        self.sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)

    def status(self, request):
        session = self.session(request)
        return {'configured': self.configured, 'authenticated': bool(session), **(session or {})}


def default_origins():
    defaults = {f'http://{host}:{port}' for host in ('localhost', '127.0.0.1') for port in (5173, 8000, 3000)}
    return defaults | {s.strip() for s in os.environ.get('CHEAT_CLIP_ALLOWED_ORIGINS', '').split(',') if s.strip()}


def valid_admin_token(request):
    expected = (os.environ.get('ADMIN_API_KEY') or os.environ.get('CHEAT_CLIP_API_KEY') or '').strip()
    provided = request.headers.get('x-api-key', '').strip()
    authorization = request.headers.get('authorization', '')
    if not provided and authorization.startswith('Bearer '):
        provided = authorization[7:].strip()
    # Existing explicit API-token integrations remain supported; absent keys never grant access.
    return bool(expected and provided and hmac.compare_digest(provided.encode(), expected.encode()))


def valid_clip_automation_token(request):
    """Machine access is restricted to job transport, never editor data/admin APIs."""
    path, method = request.url.path, request.method
    permitted = ((path == '/api/clip/jobs' and method in {'GET', 'POST'})
                 or (re.fullmatch(r'/api/clip/jobs/[0-9a-f]{32}', path) and method == 'GET')
                 or (re.fullmatch(r'/api/clip/jobs/[0-9a-f]{32}/notification', path) and method == 'POST')
                 or (path == '/api/clip/selections' and method in {'GET', 'POST'})
                 or (re.fullmatch(r'/api/clip/selections/[0-9a-f]{32}', path) and method == 'GET')
                 or (re.fullmatch(r'/api/clip/selections/[0-9a-f]{32}/(?:actions|notification)', path) and method == 'POST'))
    if (not permitted or request.headers.get('origin') is not None or not request.client
            or request.client.host not in {'127.0.0.1', '::1'}):
        return False
    from backend.clip_credentials import load_automation_token
    expected = load_automation_token()
    provided = request.headers.get('x-clip-automation-key', '')
    if expected:
        remember_secret(expected)
    return bool(expected and provided and hmac.compare_digest(provided.encode(), expected.encode()))


class AuthenticationMiddleware:
    """Pure ASGI: do not buffer SSE, uploads, range downloads or background work."""
    def __init__(self, app, manager):
        self.app = app
        self.manager = manager

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        from starlette.datastructures import MutableHeaders
        original_send = send
        async def private_send(message):
            if message['type'] == 'http.response.start':
                headers = MutableHeaders(scope=message)
                headers['Cache-Control'] = 'no-store'
                headers['Referrer-Policy'] = 'no-referrer'
                headers['X-Content-Type-Options'] = 'nosniff'
                headers['X-Frame-Options'] = 'SAMEORIGIN'
            await original_send(message)
        send = private_send
        request = Request(scope)
        if any(key.lower().replace('-', '_') in {'api_key', 'apikey', 'key', 'token', 'access_token',
                'password', 'cookies', 'cookie', 'authorization'} for key in request.query_params):
            # Uvicorn shares this scope: remove rejected query credentials before its access log.
            scope['query_string'] = b''
            await JSONResponse({'detail': 'Credentials must not be sent in URLs.'}, status_code=400)(scope, receive, send)
            return
        protected = request.url.path.startswith('/api/') or request.url.path in {'/docs', '/redoc', '/openapi.json'}
        public = request.url.path in {'/api/auth/status', '/api/auth/login'}
        if protected and request.method not in SAFE_METHODS and public and not self.manager.valid_origin(request):
            await JSONResponse({'detail': 'Same-origin request required.'}, status_code=403)(scope, receive, send)
            return
        if protected and not public:
            session = self.manager.session(request)
            admin_auth = valid_admin_token(request)
            automation_auth = valid_clip_automation_token(request)
            token_auth = admin_auth or automation_auth
            if token_auth and request.headers.get('origin') and not self.manager.valid_origin(request):
                token_auth = False
            if not session and not token_auth:
                await JSONResponse({'detail': 'Sign in required.'}, status_code=401)(scope, receive, send)
                return
            if not token_auth and request.method not in SAFE_METHODS and (not self.manager.valid_origin(request)
                    or not hmac.compare_digest(request.headers.get('x-csrf-token', '').encode(), session['csrf_token'].encode())):
                await JSONResponse({'detail': 'Session verification failed. Refresh and sign in again.'}, status_code=403)(scope, receive, send)
                return
            scope['cheat_clip_authenticated'] = bool(session or (admin_auth and token_auth))
            scope['cheat_clip_automation'] = automation_auth and bool(token_auth)
        await self.app(scope, receive, send)


auth_router = APIRouter(tags=['Authentication'])


@auth_router.get('/api/auth/status')
async def auth_status(request: Request):
    return JSONResponse(request.app.state.security.status(request), headers={'Cache-Control': 'no-store'})


@auth_router.post('/api/auth/login')
async def auth_login(request: Request):
    manager = request.app.state.security
    if not manager.configured:
        raise HTTPException(503, 'Login setup required on the laptop.')
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
        raise HTTPException(415, 'JSON request required.')
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 4096:
            raise HTTPException(413, 'Login request too large.')
    now = manager.clock()
    while manager.login_attempts and manager.login_attempts[0] <= now - 300:
        manager.login_attempts.popleft()
    if len(manager.login_attempts) >= 10:
        raise HTTPException(429, 'Too many login attempts. Wait five minutes.', headers={'Retry-After': '300'})
    manager.login_attempts.append(now)
    try:
        data = json.loads(body)
        password = data.get('password') if isinstance(data, dict) else None
    except (ValueError, UnicodeError):
        password = None
    if not isinstance(password, str) or not await asyncio.to_thread(verify_password, password, manager.password_hash):
        raise HTTPException(401, 'Invalid password.')
    manager.revoke(request)
    token, session = manager.new_session()
    response = JSONResponse({'configured': True, 'authenticated': True, **session}, headers={'Cache-Control': 'no-store'})
    name = manager.cookie_name(request)
    response.set_cookie(name, token, max_age=SESSION_SECONDS, httponly=True,
                        secure=name == SECURE_COOKIE, samesite='strict', path='/')
    return response


@auth_router.post('/api/auth/logout')
async def auth_logout(request: Request):
    manager = request.app.state.security
    manager.revoke(request)
    response = JSONResponse({'authenticated': False}, headers={'Cache-Control': 'no-store'})
    response.delete_cookie(manager.cookie_name(request), path='/', httponly=True,
                           secure=manager.cookie_name(request) == SECURE_COOKIE, samesite='strict')
    return response


def hash_password(password: str) -> str:
    if not 14 <= len(password) <= 256:
        raise ValueError('Use a password between 14 and 256 characters.')
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=32768, r=8, p=3,
                            maxmem=64 * 1024 * 1024, dklen=32)
    return f'scrypt$32768$8$3${salt.hex()}${digest.hex()}'


def valid_password_hash(encoded: str) -> bool:
    try:
        kind, n, r, p, salt, digest = encoded.split('$')
        return ((kind, n, r, p) == ('scrypt', '32768', '8', '3')
                and len(salt) == 32 and len(digest) == 64
                and len(bytes.fromhex(salt)) == 16 and len(bytes.fromhex(digest)) == 32)
    except (ValueError, AttributeError):
        return False


def verify_password(password: str, encoded: str) -> bool:
    if not isinstance(password, str) or len(password) > 256 or not valid_password_hash(encoded):
        return False
    _, _, _, _, salt, expected = encoded.split('$')
    actual = hashlib.scrypt(password.encode('utf-8'), salt=bytes.fromhex(salt),
                            n=32768, r=8, p=3, maxmem=64 * 1024 * 1024, dklen=32)
    return hmac.compare_digest(actual.hex(), expected)
