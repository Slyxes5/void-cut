"""Scoped machine-auth and handoff API contracts; no provider or bot calls."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class ClipApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from backend.security import hash_password
        cls.password_hash = hash_password('fixture-only-login-password')

    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('backend.routers.clip_automation'), 'Clip API router exists')
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from starlette.responses import StreamingResponse
        from backend.security import AuthenticationMiddleware, SecurityManager, auth_router
        from backend.services.clip_automation import ClipJobManager, ClipJobStore
        from backend.routers.clip_automation import router
        self.temp = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        self.addCleanup(self.temp.cleanup)
        self.patch_env = patch.dict('os.environ', {'GEMINI_API_KEY': 'fixture-only-backend-key'})
        self.patch_env.start()
        self.addCleanup(self.patch_env.stop)
        self.token = 'b' * 64
        token_patch = patch('backend.clip_credentials.load_automation_token', return_value=self.token)
        token_patch.start()
        self.addCleanup(token_patch.stop)
        self.calls = []
        async def analyze(request):
            import json
            self.calls.append(request)
            async def stream():
                data = {'done': True, 'result': {'video_id': 'dQw4w9WgXcQ', 'title': 'Fixture',
                        'duration': 120, 'heatmap': [], 'summary': 'Fixture', 'clips': [], 'source_type': 'youtube'}}
                yield 'data: ' + json.dumps(data) + '\n\n'
            return StreamingResponse(stream())
        self.manager = ClipJobManager(ClipJobStore(Path(self.temp.name)), analyze)
        app = FastAPI()
        app.state.security = SecurityManager(password_hash=self.password_hash, origins={'http://localhost:5173'})
        app.state.clip_jobs = self.manager
        app.include_router(router)
        app.include_router(auth_router)
        @app.get('/api/health')
        async def health():
            return {'ok': True}
        app.add_middleware(AuthenticationMiddleware, manager=app.state.security)
        self.client = TestClient(app, base_url='http://localhost:5173', client=('127.0.0.1', 50000))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {'X-Clip-Automation-Key': self.token}
        self.payload = {'url': 'https://youtu.be/dQw4w9WgXcQ', 'delivery_id': 'a' * 64,
                        'telegram': {'chat_id': '100', 'user_id': '200', 'thread_id': None, 'message_id': '300'}}

    def test_machine_token_is_scoped_and_handoff_still_requires_user_login(self):
        self.assertEqual(self.client.post('/api/clip/jobs', json=self.payload).status_code, 401)
        response = self.client.post('/api/clip/jobs', json=self.payload, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        job_id = response.json()['job_id']
        self.assertEqual(self.client.get('/api/health', headers=self.headers).status_code, 401)
        self.assertEqual(self.client.post('/api/system/restart', headers=self.headers).status_code, 401)
        self.assertEqual(self.client.get('/api/clip/handoffs/' + job_id, headers=self.headers).status_code, 401)
        self.assertEqual(self.client.get('/api/clip/jobs', headers={**self.headers, 'Origin': 'https://evil.example'}).status_code, 401)
        login = self.client.post('/api/auth/login', json={'password': 'fixture-only-login-password'}, headers={'Origin': 'http://localhost:5173'})
        self.assertEqual(login.status_code, 200)
        handoff = self.client.get('/api/clip/handoffs/' + job_id)
        self.assertEqual(handoff.status_code, 200)
        self.assertEqual(handoff.json()['analysis']['title'], 'Fixture')
        self.client.get('/api/clip/handoffs/' + job_id)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.client.post('/api/clip/jobs/' + job_id + '/notification', json={'action': 'claim'}).status_code, 403)

    def test_retries_do_not_reanalyze_and_invalid_urls_do_not_reach_engine(self):
        response = self.client.post('/api/clip/jobs', json=self.payload, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        second = self.client.post('/api/clip/jobs', json=self.payload, headers=self.headers)
        self.assertTrue(second.json()['reused'])
        self.assertEqual(response.json()['job_id'], second.json()['job_id'])
        self.assertEqual(len(self.calls), 1)
        invalid = {**self.payload, 'url': 'https://evil.example/watch?v=dQw4w9WgXcQ'}
        result = self.client.post('/api/clip/jobs', json=invalid, headers=self.headers)
        self.assertEqual(result.status_code, 400)
        self.assertNotIn('evil.example', result.text)
        self.assertEqual(len(self.calls), 1)


class WiringTests(unittest.TestCase):
    def test_existing_app_registers_clip_router_and_bounded_manager(self):
        from backend.main import app
        paths = set(app.openapi()['paths'])
        self.assertIn('/api/clip/jobs', paths)
        self.assertIn('/api/clip/handoffs/{job_id}', paths)
        self.assertTrue(hasattr(app.state, 'clip_jobs'))
        self.assertNotIn('temp_clips', str(app.state.clip_jobs.store.root))
        self.assertNotIn('exports', str(app.state.clip_jobs.store.root))


if __name__ == '__main__':
    unittest.main()
