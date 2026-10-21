"""Authentication contracts; isolated app, no Gemini, media workers or real credentials."""
import importlib.util
import unittest


class PasswordTests(unittest.TestCase):
    def test_password_is_salted_and_verified_without_plaintext_storage(self):
        self.assertIsNotNone(importlib.util.find_spec('backend.security'), 'Security module must exist')
        from backend.security import hash_password, verify_password
        password = 'test-only-password-not-a-real-credential'
        first = hash_password(password)
        second = hash_password(password)
        self.assertNotEqual(first, second)
        self.assertNotIn(password, first)
        self.assertTrue(verify_password(password, first))
        self.assertFalse(verify_password('wrong password', first))
        self.assertFalse(verify_password(password, 'invalid-hash'))


class SessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from backend.security import hash_password
        cls.encoded = hash_password('test-only-password-not-a-real-credential')

    def make_app(self, configured=True, origin='http://localhost:5173', clock=None):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import backend.security as security
        self.assertTrue(hasattr(security, 'SecurityManager'), 'Cookie security manager must exist')
        self.manager = security.SecurityManager(password_hash=self.encoded if configured else '',
            origins={origin}, clock=clock)
        app = FastAPI()
        app.state.security = self.manager
        app.include_router(security.auth_router)
        @app.api_route('/api/protected', methods=['GET', 'POST'])
        async def protected():
            return {'ok': True}
        app.add_middleware(security.AuthenticationMiddleware, manager=self.manager)
        client = TestClient(app, base_url=origin)
        self.addCleanup(client.close)
        return client

    def test_rejects_credential_query_without_reflection_even_before_login(self):
        client = self.make_app()
        for path in ('/api/models?api_key=test-query-secret', '/api/protected?token=test-query-secret',
                     '/api/auth/status?password=test-query-secret'):
            response = client.get(path)
            self.assertEqual(response.status_code, 400)
            self.assertNotIn('test-query-secret', response.text)

    def test_https_cookie_expiry_isolation_and_missing_config_fail_closed(self):
        now = [1000]
        client = self.make_app(origin='https://private-test.ts.net', clock=lambda: now[0])
        login = client.post('/api/auth/login', json={'password': 'test-only-password-not-a-real-credential'},
                            headers={'Origin': 'https://private-test.ts.net'})
        self.assertEqual(login.status_code, 200)
        cookie = login.headers['set-cookie']
        for flag in ('__Host-cheat_clip_session=', 'HttpOnly', 'Secure', 'SameSite=strict', 'Path=/'):
            self.assertIn(flag, cookie)
        self.assertNotIn('Domain=', cookie)
        self.assertEqual(client.get('/api/protected').status_code, 200)
        now[0] += 12 * 60 * 60 + 1
        self.assertEqual(client.get('/api/protected').status_code, 401)
        self.assertFalse(client.get('/api/auth/status').json()['authenticated'])
        client = self.make_app(configured=False)
        self.assertEqual(client.get('/api/protected').status_code, 401)
        self.assertEqual(client.post('/api/auth/login', json={'password': 'unused'},
                                    headers={'Origin': 'http://localhost:5173'}).status_code, 503)

    def test_authenticated_responses_disable_shared_caching(self):
        client = self.make_app()
        client.post('/api/auth/login', json={'password': 'test-only-password-not-a-real-credential'},
                    headers={'Origin': 'http://localhost:5173'})
        response = client.get('/api/protected')
        self.assertEqual(response.headers.get('cache-control'), 'no-store')
        self.assertEqual(response.headers.get('referrer-policy'), 'no-referrer')

    def test_login_is_bounded_and_rate_limited_without_echoing_password(self):
        client = self.make_app()
        headers = {'Origin': 'http://localhost:5173'}
        self.assertEqual(client.post('/api/auth/login', content='x' * 4097,
                         headers={**headers, 'Content-Type': 'application/json'}).status_code, 413)
        self.assertEqual(client.post('/api/auth/login', content='{}', headers=headers).status_code, 415)
        for _ in range(10):
            response = client.post('/api/auth/login', json={'password': 'bad'}, headers=headers)
            self.assertEqual(response.status_code, 401)
            self.assertNotIn('bad', response.text)
        response = client.post('/api/auth/login', json={'password': 'bad'}, headers=headers)
        self.assertEqual(response.status_code, 429)
        self.assertIn('retry-after', response.headers)

    def test_cookie_mutations_require_csrf_and_same_allowed_origin(self):
        client = self.make_app()
        login = client.post('/api/auth/login', json={'password': 'test-only-password-not-a-real-credential'},
                            headers={'Origin': 'http://localhost:5173'})
        csrf = login.json()['csrf_token']
        for headers in ({}, {'Origin': 'http://localhost:5173'},
                        {'Origin': 'https://evil.example', 'X-CSRF-Token': csrf},
                        {'Origin': 'null', 'X-CSRF-Token': csrf},
                        {'Origin': 'http://localhost:5173', 'X-CSRF-Token': 'wrong'}):
            with self.subTest(headers=list(headers)):
                self.assertEqual(client.post('/api/protected', headers=headers).status_code, 403)
        for origin in (None, 'null', 'https://evil.example'):
            with self.subTest(origin=origin):
                self.assertEqual(client.post('/api/auth/login', json={'password': 'wrong'},
                    headers={'Origin': origin} if origin else {}).status_code, 403)

    def test_login_cookie_protects_api_and_logout_revokes_session(self):
        client = self.make_app()
        self.assertEqual(client.get('/api/protected').status_code, 401)
        login = client.post('/api/auth/login', json={'password': 'test-only-password-not-a-real-credential'},
                            headers={'Origin': 'http://localhost:5173'})
        self.assertEqual(login.status_code, 200, login.text)
        cookie = login.headers['set-cookie']
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=strict', cookie)
        self.assertNotIn('test-only-password', cookie)
        self.assertTrue(client.get('/api/protected').json()['ok'])
        csrf = login.json()['csrf_token']
        headers = {'Origin': 'http://localhost:5173', 'X-CSRF-Token': csrf}
        self.assertEqual(client.post('/api/protected', headers=headers).status_code, 200)
        token = next(iter(client.cookies.values()))
        self.assertEqual(client.post('/api/auth/logout', headers=headers).status_code, 200)
        client.cookies.set('cheat_clip_local_session', token)
        self.assertEqual(client.get('/api/protected').status_code, 401)


class LoggingTests(unittest.TestCase):
    def test_repeated_machine_token_checks_do_not_evict_other_request_secrets(self):
        from collections import deque
        from unittest.mock import patch
        import backend.security as security
        with patch.object(security, '_request_secrets', deque(maxlen=2)):
            security.remember_secret('test-browser-provider-secret')
            for _ in range(10):
                security.remember_secret('test-machine-transport-secret')
            self.assertNotIn('test-browser-provider-secret', security.redact_secrets('test-browser-provider-secret'))

    def test_redacts_access_queries_environment_and_request_secrets(self):
        import logging
        from unittest.mock import patch
        import backend.security as security
        self.assertTrue(hasattr(security, 'SecretLogFilter'), 'Credential log filter must exist')
        security.remember_secret('test-body-secret')
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'test-environment-secret'}):
            for msg, args in [('%s - "%s %s HTTP/%s" %s', ('client', 'GET', '/api/models?api_key=test-url-secret', '1.1', 400)),
                              ('API key test-environment-secret, supplied test-body-secret https://x.test/?key=test-url-secret', ())]:
                record = logging.LogRecord('uvicorn.access', logging.INFO, '', 0, msg, args, None)
                security.SecretLogFilter().filter(record)
                text = record.getMessage()
                for secret in ('test-url-secret', 'test-environment-secret', 'test-body-secret'):
                    self.assertNotIn(secret, text)


class ApplicationGateTests(unittest.TestCase):
    def test_admin_dependency_never_allows_missing_configuration(self):
        from fastapi import FastAPI, Depends
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        from backend.routers.system import verify_admin_access
        app = FastAPI()
        @app.post('/admin')
        async def admin(authorized=Depends(verify_admin_access)):
            return {'ok': True}
        with patch.dict('os.environ', {'ADMIN_API_KEY': '', 'CHEAT_CLIP_API_KEY': ''}):
            client = TestClient(app, base_url='http://localhost:5173')
            self.addCleanup(client.close)
            self.assertEqual(client.post('/admin').status_code, 401)

    def test_model_discovery_uses_body_not_query_credentials(self):
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        from backend.main import app
        from backend.security import SecurityManager
        import backend.routers.analyze as analyze
        old = app.state.security
        # The middleware keeps the original manager reference; configure only for this isolated test.
        saved_hash = old.password_hash
        from backend.security import hash_password
        old.password_hash = hash_password('test-only-password-not-a-real-credential')
        self.addCleanup(setattr, old, 'password_hash', saved_hash)
        self.addCleanup(old.sessions.clear)
        client = TestClient(app, base_url='http://localhost:5173')
        self.addCleanup(client.close)
        login = client.post('/api/auth/login', json={'password': 'test-only-password-not-a-real-credential'},
                            headers={'Origin': 'http://localhost:5173'})
        headers = {'Origin': 'http://localhost:5173', 'X-CSRF-Token': login.json()['csrf_token']}
        with patch.object(analyze, 'list_available_gemini_models', return_value=['test-model']) as models:
            response = client.post('/api/models', json={'api_key': 'test-only-model-key'}, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            models.assert_called_once_with('test-only-model-key')
            self.assertEqual(response.json(), {'models': ['test-model']})
            models.reset_mock()
            response = client.get('/api/models?api_key=legacy-test-only-key')
            self.assertEqual(response.status_code, 400)
            models.assert_not_called()
            self.assertNotIn('legacy-test-only-key', response.text)

    def test_face_detection_cookies_are_body_only(self):
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        from backend.main import app
        import backend.routers.media as media
        manager = app.state.security
        saved_hash = manager.password_hash
        from backend.security import hash_password
        manager.password_hash = hash_password('test-only-password-not-a-real-credential')
        self.addCleanup(setattr, manager, 'password_hash', saved_hash)
        self.addCleanup(manager.sessions.clear)
        client = TestClient(app, base_url='http://localhost:5173')
        self.addCleanup(client.close)
        login = client.post('/api/auth/login', json={'password': 'test-only-password-not-a-real-credential'},
                            headers={'Origin': 'http://localhost:5173'})
        headers = {'Origin': 'http://localhost:5173', 'X-CSRF-Token': login.json()['csrf_token']}
        with patch.object(media, 'extract_clip_frame', return_value=None) as extract:
            response = client.post('/api/detect-face', json={'video_id': 'test-body-only',
                'timestamp': 1.25, 'cookies': 'test-only-cookie-value'}, headers=headers)
            self.assertEqual(response.status_code, 200)
            extract.assert_called_once_with('', 'test-body-only', 1.25, 'test-only-cookie-value')
            self.assertNotIn('test-only-cookie-value', response.text)

    def test_media_discovery_never_serves_cookie_or_configuration_files(self):
        import os
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import backend.routers.media as media
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as root:
            directory = Path(root)
            (directory / 'yt_cookie_fixture.txt').write_text('test-cookie-only')
            (directory / 'safe.mp4').write_bytes(b'test-media-fixture')
            with patch.multiple(media, UPLOADS_DIR=directory, TEMP_DIR=directory, EXPORTS_DIR=directory), patch.object(media, '_is_safe_path', return_value=True):
                self.assertIsNone(media._find_video_file_on_disk('yt_cookie_fixture.txt'))
                self.assertEqual(media._find_video_file_on_disk('safe.mp4'), directory / 'safe.mp4')

    def test_update_check_is_not_a_mutating_get_and_unknown_api_is_json_404(self):
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        from backend.main import app
        import backend.routers.system as system
        client = TestClient(app)
        self.addCleanup(client.close)
        with patch.dict('os.environ', {'ADMIN_API_KEY': 'test-only-admin-token'}), patch.object(system, 'get_current_git_info', return_value={}), patch.object(system, 'run_git_command', return_value=(0, '0', '')) as git:
            for path in ('/api/system/check-update', '/api/route-that-does-not-exist'):
                response = client.get(path, headers={'X-API-Key': 'test-only-admin-token'})
                self.assertEqual(response.status_code, 404)
                self.assertIn('application/json', response.headers['content-type'])
            git.assert_not_called()

    def test_cookie_status_is_read_only(self):
        import tempfile
        import os
        from pathlib import Path
        from unittest.mock import patch
        import backend.routers.cookies as cookies
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as root:
            path = Path(root) / 'test_cookie.txt'
            path.write_text('test-only-credential')
            with patch.multiple(cookies, COOKIES_PATH=path, ROOT_COOKIES_PATH=path):
                cookies.get_youtube_cookies_status()
                self.assertTrue(path.exists(), 'Status GET must not delete credentials')

    def test_spa_never_serves_files_outside_dist(self):
        from fastapi.testclient import TestClient
        from backend.main import app
        client = TestClient(app)
        self.addCleanup(client.close)
        for path in ('/..%2fpackage.json', '/..%5cpackage.json', '/C:%5cWindows%5cwin.ini'):
            response = client.get(path)
            self.assertEqual(response.status_code, 404, path)
            self.assertNotIn('"dependencies"', response.text)

    def test_real_application_gates_api_docs_media_and_administration(self):
        from fastapi.testclient import TestClient
        from backend.main import app
        with TestClient(app, base_url='http://localhost:5173') as client:
            self.assertEqual(client.get('/api/health').status_code, 401)
            for path in ('/api/models', '/api/cookies', '/api/render-batches',
                         '/api/video/not-real.mp4', '/api/download-rendered/not-real.mp4',
                         '/api/system/check-update', '/docs', '/openapi.json'):
                with self.subTest(path=path):
                    self.assertEqual(client.get(path).status_code, 401)
            self.assertEqual(client.post('/api/system/restart').status_code, 401)


if __name__ == '__main__':
    unittest.main()
