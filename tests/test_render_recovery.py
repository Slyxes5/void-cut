"""Isolated HTTP contract tests: no application lifespan, config or real workers."""
import importlib
import json
import logging
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

SCRATCH = Path(tempfile.gettempdir())


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='render-recovery-', dir=SCRATCH)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.exports = self.root / 'exports'
        self.exports.mkdir()
        config = types.ModuleType('backend.config')
        config.EXPORTS_DIR = self.exports
        config.ACTIVE_ENCODER_NAME = 'stub'
        config.detect_hardware_support = lambda: {}
        config.logger = logging.getLogger('recovery-tests')
        service = types.ModuleType('backend.services.render_service')
        service.RENDER_BATCHES = {}
        service.BATCH_REQUESTS = {}
        self.tasks = []
        async def render(*args):
            self.tasks.append(('render', args))
        async def retry(*args):
            self.tasks.append(('retry', args))
        service.process_batch_rendering = render
        service.process_batch_retry = retry
        routers = types.ModuleType('backend.routers')
        routers.__path__ = [str(Path(__file__).resolve().parents[1] / 'backend' / 'routers')]
        services = types.ModuleType('backend.services')
        services.__path__ = [str(Path(__file__).resolve().parents[1] / 'backend' / 'services')]
        self.modules = patch.dict(sys.modules, {'backend.config': config, 'backend.services.render_service': service,
                                                'backend.routers': routers, 'backend.services': services})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        for name in ('backend.routers.render', 'backend.services.render_status'):
            sys.modules.pop(name, None)
        self.router = importlib.import_module('backend.routers.render')
        self.batches = service.RENDER_BATCHES
        self.requests = service.BATCH_REQUESTS
        app = FastAPI()
        app.include_router(self.router.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.bid = 'batch_1792280000_abcdef'

    def file(self, name, data=b'fixture'):
        path = self.exports / name
        path.write_bytes(data)
        return path

    def batch(self, status='completed', clip_status='completed'):
        name = f'clip_1_{self.bid}.mp4'
        path = self.file(name)
        self.batches[self.bid] = {'batch_id': self.bid, 'overall_status': status, 'total_clips': 1,
            'current_clip_index': 1, 'clips': [{'clip_index': 0, 'title': 'Test', 'status': clip_status,
            'progress_percent': 100, 'output_path': str(path), 'download_url': f'/api/download-rendered/{name}'}],
            'zip_url': f'/api/download-batch-zip/{self.bid}', 'cookies': 'SECRET', 'api_key': 'SECRET'}
        return path

    def get(self):
        response = self.client.get(f'/api/render-status/{self.bid}')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers.get('cache-control'), 'no-store')
        return response.json()

    def test_status_file_verified_sanitized_and_sse_parity(self):
        path = self.batch()
        self.requests[self.bid] = object()
        snapshot = self.get()
        self.assertTrue(snapshot['clips'][0]['download_available'])
        self.assertTrue(snapshot['can_retry'])
        self.assertFalse(snapshot['zip_available'])
        self.assertNotIn('zip_url', snapshot)
        self.assertNotIn('output_path', snapshot['clips'][0])
        self.assertNotIn('SECRET', json.dumps(snapshot))
        stream = self.client.get(f'/api/render-progress/{self.bid}')
        events = [json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith('data: ')]
        self.assertEqual(events[0], snapshot)
        path.unlink()
        missing = self.get()['clips'][0]
        self.assertFalse(missing['download_available'])
        self.assertNotIn('download_url', missing)

    def test_stages_running_partial_error_and_zip_finalization(self):
        import zipfile
        self.batch(status='running', clip_status='pending')
        self.requests[self.bid] = object()
        archive = self.exports / f'cheat_clip_pro_{self.bid}.zip'
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr('clip.mp4', b'fixture')
        for stage in ('pending', 'downloading', 'transcribing', 'rendering', 'error'):
            with self.subTest(stage=stage):
                self.batches[self.bid]['clips'][0]['status'] = stage
                self.batches[self.bid]['clips'][0]['stage'] = stage
                snapshot = self.get()
                self.assertEqual(snapshot['clips'][0]['stage'], stage)
                self.assertFalse(snapshot['clips'][0]['download_available'])
                self.assertFalse(snapshot['zip_available'])
                self.assertFalse(snapshot['can_retry'])
        self.batches[self.bid]['overall_status'] = 'error'
        self.assertTrue(self.get()['can_retry'])
        self.batches[self.bid]['overall_status'] = 'completed'
        self.batches[self.bid]['clips'].append({'clip_index': 1, 'status': 'completed',
            'output_path': str(self.file(f'clip_2_{self.bid}.mp4'))})
        snapshot = self.get()
        self.assertTrue(snapshot['zip_available'])
        self.assertFalse(snapshot['clips'][0]['download_available'])
        self.assertTrue(snapshot['clips'][1]['download_available'])
        archive.write_bytes(b'partial zip')
        self.assertFalse(self.get()['zip_available'])
        archive.write_bytes(b'')
        self.assertFalse(self.get()['zip_available'])

    def test_unknown_batch_and_request_are_404(self):
        for route in ('render-status', 'render-progress', 'render-request'):
            self.assertEqual(self.client.get(f'/api/{route}/unknown').status_code, 404)

    def test_disk_recovery_and_recent_list_are_read_only(self):
        import zipfile
        self.file(f'clip_1_{self.bid}.mp4')
        self.file(f'clip_3_{self.bid}.mp4')
        self.file(f'compilation_My_Title_{self.bid}.mp4')
        self.file(f'clip_2_{self.bid}.mp4', b'')
        for name in (f'{self.bid}_clip_4_raw.mp4', f'other_{self.bid}.mp4',
                     'clip_1_batch_123_abcdefg.mp4', 'clip_0_batch_123_abcdef.mp4'):
            self.file(name)
        with zipfile.ZipFile(self.exports / ('cheat_clip_pro_' + self.bid + '.zip'), 'w') as zipped:
            zipped.writestr('clip.mp4', b'fixture')
        before = {p.name: p.read_bytes() for p in self.exports.iterdir()}
        snapshot = self.get()
        self.assertTrue(snapshot['recovered_from_files'])
        self.assertEqual(snapshot['overall_status'], 'completed')
        self.assertFalse(snapshot['can_retry'])
        self.assertEqual(len(snapshot['clips']), 3)
        self.assertTrue(all(c['download_available'] for c in snapshot['clips']))
        self.assertTrue(snapshot['zip_available'])
        self.assertIn('original', snapshot['warning_message'].lower())
        self.assertIn('unknown', snapshot['warning_message'].lower())
        recent = self.client.get('/api/render-batches')
        self.assertEqual(recent.status_code, 200)
        self.assertEqual(recent.headers['cache-control'], 'no-store')
        self.assertEqual([b['batch_id'] for b in recent.json()['batches']], [self.bid])
        self.assertEqual(self.batches, {})
        self.assertEqual(self.requests, {})
        self.assertEqual(self.tasks, [])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.exports.iterdir()})

    def test_recent_limit_order_and_memory_precedence(self):
        for i in range(12):
            self.file(f'clip_1_batch_{1792280000+i}_abcdef.mp4')
        self.batch(status='running', clip_status='pending')
        self.assertFalse(self.get().get('recovered_from_files', False))
        response = self.client.get('/api/render-batches')
        self.assertEqual(response.status_code, 200, response.text)
        batches = response.json()['batches']
        self.assertEqual(len(batches), 10)
        self.assertEqual([b['batch_id'] for b in batches],
                         [f'batch_{1792280000+i}_abcdef' for i in range(11, 1, -1)])

    def test_unsafe_paths_empty_files_and_downloads(self):
        path = self.batch()
        outside = self.root / 'outside.mp4'
        outside.write_bytes(b'secret')
        clip = self.batches[self.bid]['clips'][0]
        clip['output_path'] = str(outside)
        self.assertFalse(self.get()['clips'][0]['download_available'])
        self.assertEqual(self.client.get('/api/download-rendered/outside.mp4').status_code, 404)
        clip.pop('output_path')
        for url in ('https://attacker/clip.mp4', '/api/download-rendered/../outside.mp4',
                    '/api/download-rendered/%2e%2e%5coutside.mp4'):
            clip['download_url'] = url
            self.assertFalse(self.get()['clips'][0]['download_available'])
        clip['output_path'] = str(path)
        path.write_bytes(b'')
        self.assertFalse(self.get()['clips'][0]['download_available'])
        self.assertEqual(self.client.get(f'/api/download-rendered/{path.name}').status_code, 404)

    def test_symlink_outputs_are_not_advertised_or_served(self):
        target = self.root / 'target.mp4'
        target.write_bytes(b'outside export root')
        link = self.exports / f'clip_1_{self.bid}.mp4'
        try:
            link.symlink_to(target)
        except OSError as exc:
            self.skipTest(f'OS does not permit symlink fixtures: {exc}')
        self.assertEqual(self.client.get(f'/api/render-status/{self.bid}').status_code, 404)
        self.batches[self.bid] = {'batch_id': self.bid, 'overall_status': 'completed',
            'clips': [{'status': 'completed', 'output_path': str(link)}]}
        self.assertFalse(self.get()['clips'][0]['download_available'])
        self.assertEqual(self.client.get(f'/api/download-rendered/{link.name}').status_code, 404)

    def payload(self):
        return {'video_url': 'https://example.test/video', 'video_id': 'fixture',
                'clips': [{'title': 'One', 'start_time': 0, 'end_time': 1}], 'settings': {},
                'cookies': 'PRIVATE_COOKIE'}

    def test_idempotent_post_lookup_normalization_and_terminal_reuse(self):
        key = '39b8165c-6ac8-4c51-8677-66850b05cc2a'
        headers = {'Idempotency-Key': key}
        first = self.client.post('/api/render-batch', json=self.payload(), headers=headers)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertIn('total_clips', first.json())
        self.assertIn('is_merged', first.json())
        self.bid = first.json()['batch_id']
        snapshot = self.get()
        self.assertEqual(snapshot.get('request_id'), key)
        looked_up = self.client.get(f'/api/render-request/{key}')
        self.assertEqual(looked_up.status_code, 200, looked_up.text)
        self.assertEqual(looked_up.headers['cache-control'], 'no-store')
        self.assertEqual(looked_up.json(), snapshot)
        body = self.payload()
        body['settings'] = self.requests[self.bid].settings.model_dump(mode='json')
        duplicate = self.client.post('/api/render-batch', json=body, headers=headers)
        self.assertEqual(duplicate.json()['batch_id'], self.bid)
        self.batches[self.bid]['overall_status'] = 'completed'
        terminal = self.client.post('/api/render-batch', json=self.payload(), headers=headers)
        self.assertEqual(terminal.json()['batch_id'], self.bid)
        self.assertEqual(len(self.tasks), 1)
        self.assertNotIn('PRIVATE_COOKIE', json.dumps(self.get()))
        # Reload semantics: metadata is process-local, never reconstruct request IDs from files.
        self.router.REQUEST_IDS.clear()
        self.assertEqual(self.client.get(f'/api/render-request/{key}').status_code, 404)

    def test_identical_active_submission_from_another_tab_reuses_running_batch(self):
        first_key = '39b8165c-6ac8-4c51-8677-66850b05cc2a'
        next_key = 'f1a11be3-4e14-4dc6-a634-6839f3df4f1d'
        first = self.client.post('/api/render-batch', json=self.payload(), headers={'Idempotency-Key': first_key})
        second = self.client.post('/api/render-batch', json=self.payload(), headers={'Idempotency-Key': next_key})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()['batch_id'], second.json()['batch_id'])
        self.assertEqual(len(self.tasks), 1)
        self.assertEqual(self.client.get('/api/render-request/' + next_key).json()['batch_id'], first.json()['batch_id'])

    def test_idempotency_conflicts_and_invalid_keys(self):
        key = '39b8165c-6ac8-4c51-8677-66850b05cc2a'
        first = self.client.post('/api/render-batch', json=self.payload(), headers={'Idempotency-Key': key})
        self.assertEqual(first.status_code, 200)
        different = self.payload()
        different['cookies'] = 'CHANGED_SECRET'
        conflict = self.client.post('/api/render-batch', json=different, headers={'Idempotency-Key': key})
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(len(self.tasks), 1)
        for value in ('', 'bad key', '../unsafe', 'a' * 200):
            response = self.client.post('/api/render-batch', json=self.payload(), headers={'Idempotency-Key': value})
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(len(self.tasks), 1)
        self.assertEqual(len(self.batches), 1)

    def test_no_key_remains_supported_and_merged_response_preserved(self):
        body = self.payload()
        body['settings'] = {'render_mode': 'merged', 'compilation_title': 'Compilation'}
        for _ in range(2):
            response = self.client.post('/api/render-batch', json=body)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['total_clips'], 1)
            self.assertTrue(response.json()['is_merged'])
        self.assertEqual(len(self.tasks), 2)
        self.assertEqual(len(self.batches), 2)

    def retry_fixture(self, status='completed'):
        self.batch(status=status)
        self.batches[self.bid]['clips'].append({'clip_index': 1, 'status': 'error', 'error': 'failed'})
        self.requests[self.bid] = self.router.RenderBatchRequest(**self.payload())

    def test_retry_queued_running_is_409(self):
        self.retry_fixture(status='running')
        self.batches[self.bid]['clips'][0]['status'] = 'pending'
        response = self.client.post(f'/api/render-batch/{self.bid}/retry')
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.tasks, [])

    def test_retry_only_errors_deduplicates_and_schedules_once(self):
        self.retry_fixture()
        for indices in ([0], [0, 1], [99], [-1]):
            response = self.client.post(f'/api/render-batch/{self.bid}/retry',
                json={'clip_indices': indices, 'cookies': 'DO_NOT_UPDATE'})
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(self.requests[self.bid].cookies, 'PRIVATE_COOKIE')
            self.assertEqual(self.tasks, [])
        response = self.client.post(f'/api/render-batch/{self.bid}/retry', json={'clip_indices': [1, 1]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['retrying_clips'], [1])
        self.assertEqual(self.batches[self.bid]['clips'][0]['status'], 'completed')
        self.assertEqual(self.batches[self.bid]['clips'][1]['status'], 'pending')
        self.assertEqual(len(self.tasks), 1)
        again = self.client.post(f'/api/render-batch/{self.bid}/retry', json={'clip_indices': [1]})
        self.assertEqual(again.status_code, 409, again.text)
        self.assertEqual(len(self.tasks), 1)

    def test_retry_defaults_to_errors_only(self):
        self.retry_fixture()
        response = self.client.post(f'/api/render-batch/{self.bid}/retry')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['retrying_clips'], [1])
        self.assertEqual(len(self.tasks), 1)

    def test_zip_download_never_creates_or_serves_running_archive(self):
        import zipfile
        self.batch()
        route = f'/api/download-batch-zip/{self.bid}'
        self.assertEqual(self.client.get(route).status_code, 404)
        archive = self.exports / f'cheat_clip_pro_{self.bid}.zip'
        self.assertFalse(archive.exists())
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr('clip.mp4', b'fixture')
        before = archive.read_bytes()
        self.assertEqual(self.client.get(route).status_code, 200)
        self.batches[self.bid]['overall_status'] = 'running'
        self.assertEqual(self.client.get(route).status_code, 409)
        self.assertEqual(archive.read_bytes(), before)

    def test_symlink_guard_without_os_privilege(self):
        # Exercise real HTTP/snapshot paths with only the OS symlink probe substituted.
        path = self.batch()
        original = Path.is_symlink
        with patch.object(Path, 'is_symlink', lambda candidate: candidate == path or original(candidate)):
            self.assertFalse(self.get()['clips'][0]['download_available'])
            self.assertEqual(self.client.get(f'/api/download-rendered/{path.name}').status_code, 404)
            self.batches.clear()
            self.assertEqual(self.client.get(f'/api/render-status/{self.bid}').status_code, 404)

    def test_sse_running_snapshot_heartbeat_and_terminal_transition(self):
        import asyncio
        self.batch(status='running', clip_status='pending')
        response = self.client.get(f'/api/render-status/{self.bid}')
        expected = response.json()
        async def exercise():
            stream = await self.router.get_render_progress(self.bid)
            self.assertEqual(stream.headers['cache-control'], 'no-store')
            iterator = stream.body_iterator
            first = await anext(iterator)
            self.assertEqual(json.loads(first[6:]), expected)
            async def no_sleep(_):
                return None
            with patch.object(self.router.asyncio, 'sleep', no_sleep):
                self.assertEqual(await anext(iterator), ': keep-alive\n\n')
                self.batches[self.bid]['overall_status'] = 'error'
                final = json.loads((await anext(iterator))[6:])
                self.assertEqual(final['overall_status'], 'error')
            await iterator.aclose()
        asyncio.run(exercise())

    def test_sanitized_snapshots_do_not_echo_request_credentials(self):
        self.batch(clip_status='error')
        self.requests[self.bid] = self.router.RenderBatchRequest(**self.payload())
        self.batches[self.bid]['clips'][0]['error_message'] = 'download failed: PRIVATE_COOKIE'
        self.batches[self.bid]['error_message'] = 'Cookie PRIVATE_COOKIE failed'
        self.assertNotIn('PRIVATE_COOKIE', json.dumps(self.get()))

    def test_recovered_snapshot_has_no_request_and_forgets_deleted_exports(self):
        path = self.file(f'clip_1_{self.bid}.mp4')
        self.assertIn('request_id', self.get())
        self.assertIsNone(self.get()['request_id'])
        path.unlink()
        self.assertEqual(self.client.get(f'/api/render-status/{self.bid}').status_code, 404)
        self.assertEqual(self.client.get('/api/render-batches').json(), {'batches': []})

    def test_short_credentials_cannot_change_protocol_identity_or_status(self):
        self.batch(status='running', clip_status='pending')
        body = self.payload()
        body['cookies'] = 'running'
        self.requests[self.bid] = self.router.RenderBatchRequest(**body)
        snapshot = self.get()
        self.assertEqual(snapshot['overall_status'], 'running')
        self.assertEqual(snapshot['batch_id'], self.bid)

    def test_unknown_recovery_responses_are_not_cacheable(self):
        for route in ('render-status', 'render-request', 'render-progress'):
            response = self.client.get(f'/api/{route}/missing')
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.headers.get('cache-control'), 'no-store')

    def test_existing_raw_downloader_mp4_actions_remain_supported(self):
        for name in ('youtube_video_raw_1792280000.mp4', 'video_id_clip_0_30_1792280000_raw.mp4'):
            self.file(name)
            response = self.client.get(f'/api/download-rendered/{name}', headers={'Range': 'bytes=0-1'})
            self.assertEqual(response.status_code, 206, response.text)
            self.assertEqual(response.content, b'fi')
        self.assertEqual(self.client.get('/api/render-batches').json(), {'batches': []})

    def test_raw_and_unrelated_outputs_are_not_exposed(self):
        self.batch()
        for name in (f'{self.bid}_clip_1_raw.mp4', 'unrelated.mp4', 'clip_1_batch_123_abcdefg.mp4'):
            path = self.file(name)
            self.batches[self.bid]['clips'][0]['output_path'] = str(path)
            self.assertFalse(self.get()['clips'][0]['download_available'])
            self.assertEqual(self.client.get(f'/api/download-rendered/{name}').status_code, 404)


if __name__ == '__main__':
    unittest.main()
