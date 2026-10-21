"""Real ASGI authentication/routes; isolated stores and fake media bytes only."""
from tests.test_clip_selection import SCRATCH, ROUTE, URL, analysis
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient


class SelectionApiTests(unittest.TestCase):
    def setUp(self):
        from backend.main import app
        from backend.services.clip_automation import ClipJobStore
        from backend.services.clip_selection import SelectionStore, SelectionManager
        from backend.security import SecurityManager
        self.tmp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.assertTrue(hasattr(app.state, 'clip_selections'), 'Main installs selection manager')
        self.app = app
        self.manual = ClipJobStore(self.root / 'handoffs')
        self.manager = SelectionManager(SelectionStore(self.root / 'selections', self.manual), self.root / 'results')
        self.old = (app.state.clip_jobs.store, app.state.clip_selections, app.state.security)
        app.state.clip_jobs.store = self.manual
        app.state.clip_selections = self.manager
        # Middleware holds the original security object: restore it after each test.
        self.security = app.state.security
        self.saved_sessions = self.security.sessions.copy()
        self.addCleanup(self.restore)
        self.token_patch = patch('backend.clip_credentials.load_automation_token', return_value='offline-machine-token')
        self.token_patch.start()
        self.addCleanup(self.token_patch.stop)
        self.env = patch.dict(os.environ, {'ADMIN_API_KEY': '', 'CHEAT_CLIP_API_KEY': '', 'GEMINI_API_KEY': ''})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = TestClient(app, base_url='http://127.0.0.1:8000', client=('127.0.0.1', 52000))
        self.addCleanup(self.client.close)
        self.machine = {'x-clip-automation-key': 'offline-machine-token'}

    def restore(self):
        self.app.state.clip_jobs.store, self.app.state.clip_selections, self.app.state.security = self.old
        self.security.sessions = self.saved_sessions

    def cache(self):
        job, _ = self.manual.reserve(URL, 'a' * 64, ROUTE)
        self.manual.complete(job['job_id'], analysis())

    def submit(self):
        return self.client.post('/api/clip/selections', headers=self.machine,
            json={'url': URL, 'delivery_id': 'b' * 64, 'telegram': ROUTE})

    def test_loopback_machine_exact_transport_only_and_structured_cache_miss(self):
        response = self.submit()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail']['code'], 'analysis_required')
        self.cache()
        response = self.submit()
        self.assertEqual(response.status_code, 200, response.text)
        job = response.json()
        self.assertEqual(self.submit().json()['job_id'], job['job_id'])
        path = '/api/clip/selections/' + job['job_id']
        self.assertEqual(self.client.get(path, headers=self.machine).status_code, 200)
        self.assertEqual(len(self.client.get('/api/clip/selections?pending_notifications=true', headers=self.machine).json()['jobs']), 1)
        bound = self.client.post(path + '/actions', headers=self.machine,
            json={'action': 'bind', 'revision': 0, 'telegram': ROUTE, 'menu_message_id': '400'})
        self.assertEqual(bound.status_code, 200, bound.text)
        invalid = self.client.post(path + '/actions', headers=self.machine,
            json={'action': 'confirm', 'revision': 0, 'telegram': ROUTE, 'menu_message_id': '400'})
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()['detail']['code'], 'invalid_selection')
        self.assertEqual(invalid.json()['detail']['invalid_indices'], [])
        cancelled = self.client.post(path + '/actions', headers=self.machine,
            json={'action': 'cancel', 'revision': 0, 'telegram': ROUTE, 'menu_message_id': '400'})
        self.assertEqual(cancelled.json()['state'], 'cancelled')
        notice = path + '/notification'
        self.assertTrue(self.client.post(notice, headers=self.machine, json={'action': 'claim'}).json()['claimed'])
        self.assertFalse(self.client.post(notice, headers=self.machine, json={'action': 'claim'}).json()['claimed'])
        self.assertEqual(self.client.post(notice, headers=self.machine, json={'action': 'sent', 'message_id': '500'}).status_code, 200)
        self.assertEqual(self.client.get('/api/clip/selections?pending_notifications=true', headers=self.machine).json()['jobs'], [])
        self.assertEqual(self.client.get(path, headers=self.machine).json()['menu_message_id'], '400')
        self.assertEqual(self.client.get(path, headers=self.machine).json()['telegram']['message_id'], '300')
        for method, target in (('get', path + '/files/clip_1.mp4'), ('get', '/api/render-batches'),
                ('post', '/api/render-batch'), ('get', '/api/clip/handoffs/' + job['editor_job_id']),
                ('get', path + '/actions'), ('post', path), ('get', path + '/notification')):
            self.assertEqual(getattr(self.client, method)(target, headers=self.machine).status_code, 401, target)
        self.assertEqual(self.client.get(path, headers={**self.machine, 'origin': 'http://127.0.0.1:8000'}).status_code, 401)
        remote = TestClient(self.app, base_url='http://127.0.0.1:8000', client=('198.51.100.1', 52000))
        self.addCleanup(remote.close)
        self.assertEqual(remote.get(path, headers=self.machine).status_code, 401)

    def test_subtitle_override_route_is_strict_and_public_status_retains_choice(self):
        self.cache()
        job = self.submit().json()
        path = '/api/clip/selections/' + job['job_id']
        body = {'revision': 0, 'telegram': ROUTE, 'menu_message_id': '400'}
        self.client.post(path + '/actions', headers=self.machine, json={**body, 'action': 'bind'})
        response = self.client.post(path + '/actions', headers=self.machine,
            json={**body, 'action': 'subtitles', 'index': 0})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIs(response.json()['subtitles_enabled'], False)
        self.assertIs(self.client.get(path, headers=self.machine).json()['subtitles_enabled'], False)
        self.assertEqual(response.json()['state'], 'selecting')
        response = self.client.post(path + '/actions', headers=self.machine,
            json={**body, 'revision': 1, 'action': 'subtitles', 'index': True})
        self.assertEqual(response.status_code, 400)

    def test_clear_and_select_all_route_contract(self):
        self.cache()
        job = self.submit().json()
        path = '/api/clip/selections/' + job['job_id'] + '/actions'
        body = {'revision': 0, 'telegram': ROUTE, 'menu_message_id': '400'}
        self.assertEqual(self.client.post(path, headers=self.machine, json={**body, 'action': 'bind'}).status_code, 200)
        response = self.client.post(path, headers=self.machine, json={**body, 'action': 'select_all'})
        # Four eligible entries are a policy rejection, not an unknown-schema action.
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()['detail']['code'], 'invalid_selection')
        response = self.client.post(path, headers=self.machine, json={**body, 'action': 'clear'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['selected_indices'], [])
        self.assertEqual(response.json()['state'], 'selecting')

    def test_session_csrf_protected_download_range_and_expiry(self):
        from backend.security import LOCAL_COOKIE
        self.cache()
        job = self.submit().json()
        path = '/api/clip/selections/' + job['job_id']
        directory = self.manager.results_root / job['job_id']
        directory.mkdir(parents=True)
        (directory / 'clip_1.mp4').write_bytes(b'offline-fake-mp4-bytes')
        self.manager.store._change(job['job_id'], {'files': [{'name': 'clip_1.mp4', 'source_index': 0,
            'download_url': path + '/files/clip_1.mp4'}], 'state': 'completed'})
        token, session = self.security.new_session()
        self.client.cookies.set(LOCAL_COOKIE, token)
        response = self.client.get(path + '/files/clip_1.mp4', headers={'Range': 'bytes=0-6'})
        self.assertEqual(response.status_code, 206, response.text)
        self.assertEqual(response.content, b'offline')
        self.assertEqual(self.client.get(path + '/files/not-allowed.mp4').status_code, 404)
        self.assertEqual(self.client.get(path + '/files/%2e%2e%5csecret').status_code, 404)
        body = {'action': 'bind', 'revision': 0, 'telegram': ROUTE, 'menu_message_id': '400'}
        self.assertEqual(self.client.post(path + '/actions', json=body).status_code, 403)
        headers = {'origin': 'http://127.0.0.1:8000', 'x-csrf-token': session['csrf_token']}
        # Completed jobs reject edits after valid session/CSRF (not authentication failure).
        self.assertEqual(self.client.post(path + '/actions', json=body, headers=headers).status_code, 409)
        self.manager.store._change(job['job_id'], {'expires_at': self.manager.store.clock() - 1})
        self.assertEqual(self.client.get(path + '/files/clip_1.mp4').status_code, 410)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_recovers_and_waits_selection_tasks_and_cleanup_does_not_delete_active_outputs(self):
        import asyncio
        from unittest.mock import AsyncMock, Mock
        import backend.main as main
        selections = Mock()
        selections.tasks = {object()}
        selections.wait_idle = AsyncMock()
        manual = Mock()
        manual.wait_idle = AsyncMock()
        with patch.object(main.app.state, 'clip_selections', selections), \
             patch.object(main.app.state, 'clip_jobs', manual):
            async with main.lifespan(main.app):
                selections.start.assert_called_once()
            selections.wait_idle.assert_awaited_once()
        with patch.object(main.app.state, 'clip_selections', selections), \
             patch.object(main, 'auto_cleanup_expired_files') as cleanup, \
             patch.object(main.asyncio, 'sleep', new=AsyncMock(side_effect=[None, asyncio.CancelledError()])):
            with self.assertRaises(asyncio.CancelledError):
                await main.background_storage_cleanup_worker()
            cleanup.assert_not_called()


if __name__ == '__main__':
    unittest.main()
