"""Clip automation contracts. No provider calls, bot API, real credentials or media workers."""
import importlib.util
import unittest


class UrlTests(unittest.TestCase):
    def validator(self):
        self.assertIsNotNone(importlib.util.find_spec('backend.services.clip_automation'), 'Clip automation module exists')
        from backend.services.clip_automation import canonical_youtube_url
        return canonical_youtube_url

    def test_legitimate_video_hosts_and_forms_are_canonicalized(self):
        validate = self.validator()
        expected = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
        for value in ('https://youtu.be/dQw4w9WgXcQ?si=tracking',
                      'http://youtube.com/watch?v=dQw4w9WgXcQ&t=10',
                      'https://www.youtube.com/shorts/dQw4w9WgXcQ',
                      'https://m.youtube.com/live/dQw4w9WgXcQ',
                      'https://music.youtube.com/embed/dQw4w9WgXcQ'):
            self.assertEqual(validate(value), expected)

    def test_unsupported_malformed_or_ambiguous_urls_are_rejected(self):
        validate = self.validator()
        for value in ('https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ',
                      'https://evil.test/youtube.com/watch?v=dQw4w9WgXcQ',
                      'https://user:password@youtube.com/watch?v=dQw4w9WgXcQ',
                      'https://youtube.com:443/watch?v=dQw4w9WgXcQ',
                      'https://youtube.com:/watch?v=dQw4w9WgXcQ',
                      'https://youtu.be/dQw4w9WgXcQ?v=dQw4w9WgXcQ&v=abcdefghijk',
                      'https://youtube.com/watch?v=dQw4w9WgXcQ&v=abcdefghijk',
                      'https://youtube.com/playlist?list=dQw4w9WgXcQ',
                      'https://youtu.be/dQw4w9WgXcQ/extra', 'file:///C:/video.mp4',
                      'https://youtu.be/short', 'https://youtu.be/dQw4w9WgXcQ\n',
                      'https://youtube.com\\@evil.test/watch?v=dQw4w9WgXcQ',
                      '$(whoami)', '/clip auto', 'https://youtu.be/dQw4w9WgXcQ;calc'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate(value)


class StoreTests(unittest.TestCase):
    def setUp(self):
        import os, tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = [1000.0]
        self.route = {'chat_id': '100', 'user_id': '200', 'thread_id': None, 'message_id': '300'}

    def store(self, **kwargs):
        import backend.services.clip_automation as module
        self.assertTrue(hasattr(module, 'ClipJobStore'), 'Persistent clip store exists')
        return module.ClipJobStore(self.root, clock=lambda: self.now[0], **kwargs)

    def analysis(self):
        return {'video_id': 'dQw4w9WgXcQ', 'title': 'Fixture', 'duration': 120,
                'heatmap': [], 'summary': 'Fixture summary', 'clips': [], 'source_type': 'youtube'}

    def test_delivery_deduplication_survives_store_recreation(self):
        store = self.store()
        first, reused = store.reserve('https://youtu.be/dQw4w9WgXcQ', 'a' * 64, self.route)
        self.assertFalse(reused)
        self.assertRegex(first['job_id'], r'^[0-9a-f]{32}$')
        second, reused = self.store().reserve(first['source_url'], 'a' * 64, self.route)
        self.assertTrue(reused)
        self.assertEqual(first['job_id'], second['job_id'])
        self.assertEqual(len(list(self.root.glob('job-*.json'))), 1)

    def test_completed_handoff_is_sanitized_expiring_and_does_not_replace_history(self):
        import json
        from unittest.mock import patch
        store = self.store()
        job, _ = store.reserve('https://youtu.be/dQw4w9WgXcQ', 'b' * 64, self.route)
        data = self.analysis()
        data.update({'api_key': 'test-secret-value', 'cookies': 'test-cookie-value'})
        data['summary'] = 'Never expose test-secret-value'
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'test-secret-value'}):
            store.complete(job['job_id'], data)
        saved = store.get(job['job_id'])
        self.assertEqual(saved['state'], 'completed')
        self.assertNotIn('api_key', saved['analysis'])
        self.assertNotIn('cookies', saved['analysis'])
        self.assertNotIn('test-secret-value', json.dumps(saved))
        self.assertNotIn('test-cookie-value', json.dumps(saved))
        self.now[0] = saved['expires_at'] + 1
        with self.assertRaises(Exception) as caught:
            store.get(job['job_id'])
        self.assertEqual(caught.exception.status_code, 410)

    def test_expired_interrupted_job_cannot_block_the_queue_after_startup(self):
        store = self.store(ttl_seconds=1, max_active=1)
        store.reserve('https://youtu.be/dQw4w9WgXcQ', 'e' * 64, self.route)
        self.now[0] += 2
        store.recover_interrupted()
        next_job, reused = store.reserve('https://youtu.be/dQw4w9WgXcQ', 'f' * 64, self.route)
        self.assertFalse(reused)
        self.assertEqual(next_job['state'], 'accepted')

    def test_retention_keeps_delivery_tombstone_and_notification_is_claimed_once(self):
        store = self.store(max_jobs=1)
        first, _ = store.reserve('https://youtu.be/dQw4w9WgXcQ', 'c' * 64, self.route)
        store.complete(first['job_id'], self.analysis())
        self.assertTrue(store.notification(first['job_id'], 'claim')['claimed'])
        self.assertFalse(store.notification(first['job_id'], 'claim')['claimed'])
        store.notification(first['job_id'], 'sent', '400')
        self.assertFalse(store.pending_notifications())
        store.reserve('https://youtu.be/dQw4w9WgXcQ', 'd' * 64, self.route)
        with self.assertRaises(Exception) as caught:
            store.reserve('https://youtu.be/dQw4w9WgXcQ', 'c' * 64, self.route)
        self.assertEqual(caught.exception.status_code, 410)


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import os, tempfile
        from pathlib import Path
        from unittest.mock import patch
        from backend.services.clip_automation import ClipJobStore
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict('os.environ', {'GEMINI_API_KEY': 'test-fixture-env-secret'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = ClipJobStore(Path(self.tmp.name))
        self.route = {'chat_id': '100', 'user_id': '200', 'thread_id': None, 'message_id': '300'}

    def manager(self, analyze):
        import backend.services.clip_automation as module
        self.assertTrue(hasattr(module, 'ClipJobManager'), 'Asynchronous existing-stream runner exists')
        return module.ClipJobManager(self.store, analyze)

    async def test_existing_sse_analysis_runs_once_and_handoff_get_never_reruns_it(self):
        import json
        from starlette.responses import StreamingResponse
        calls = []
        async def analyze(request):
            calls.append(request)
            async def events():
                yield 'data: {"stage":"Fetching transcript"}\n\n'
                payload = json.dumps({'done': True, 'result': {'video_id': 'dQw4w9WgXcQ',
                    'title': 'Fixture', 'duration': 120, 'heatmap': [], 'summary': 'Fixture summary',
                    'clips': [], 'source_type': 'youtube'}})
                yield 'data: ' + payload[:15]
                yield payload[15:] + '\n\n'
            return StreamingResponse(events(), media_type='text/event-stream')
        manager = self.manager(analyze)
        first = manager.submit('https://youtu.be/dQw4w9WgXcQ', 'e' * 64, self.route)
        second = manager.submit('https://youtu.be/dQw4w9WgXcQ', 'e' * 64, self.route)
        self.assertEqual(first['job_id'], second['job_id'])
        self.assertTrue(second['reused'])
        self.assertEqual(first['state'], 'accepted')
        await manager.wait_idle()
        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0].api_key)
        self.assertEqual(calls[0].model, 'gemini-3.8-flash')
        self.assertEqual(manager.status(first['job_id'])['state'], 'completed')
        handoff = manager.handoff(first['job_id'])
        self.assertEqual(handoff['analysis']['title'], 'Fixture')
        self.assertNotIn('analysis', manager.status(first['job_id']))
        manager.handoff(first['job_id'])
        self.assertEqual(len(calls), 1)

    async def test_failed_or_interrupted_analysis_is_not_automatically_retried(self):
        from starlette.responses import StreamingResponse
        calls = []
        async def analyze(request):
            calls.append(request)
            async def events():
                yield 'data: {"error":"sensitive provider failure", "status":403}\n\n'
            return StreamingResponse(events())
        manager = self.manager(analyze)
        job = manager.submit('https://youtu.be/dQw4w9WgXcQ', 'f' * 64, self.route)
        await manager.wait_idle()
        self.assertEqual(manager.status(job['job_id'])['state'], 'failed')
        manager.submit('https://youtu.be/dQw4w9WgXcQ', 'f' * 64, self.route)
        await manager.wait_idle()
        self.assertEqual(len(calls), 1)
        orphan, _ = self.store.reserve('https://youtu.be/dQw4w9WgXcQ', '1' * 64, self.route)
        self.store.recover_interrupted()
        self.assertEqual(self.store.get(orphan['job_id'])['state'], 'failed')
        self.assertIn('No automatic', self.store.get(orphan['job_id'])['error'])


if __name__ == '__main__':
    unittest.main()
