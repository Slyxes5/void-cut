"""Selection contracts. Offline fixtures only; never providers or real media."""
import os
import tempfile
import unittest
from pathlib import Path

SCRATCH = Path('C:/Users/vrandly/AppData/Local/hermes/cache/scratch')
SCRATCH.mkdir(parents=True, exist_ok=True)
# Must precede main/config imports; empty key prevents dotenv from filling it.
os.environ['GEMINI_API_KEY'] = ''
os.environ['LOCALAPPDATA'] = str(SCRATCH / 'clip-selection-test-local')
os.environ['TMPDIR'] = str(SCRATCH)

# Existing package initializers probe installed encoders on import. Suppress those
# subprocesses too; these tests must never execute real FFmpeg or a provider.
import subprocess
from unittest.mock import patch
with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', 'offline import fixture')):
    import backend.services.clip_automation
    import backend.routers.render

URL = 'https://youtu.be/dQw4w9WgXcQ'
ROUTE = {'chat_id': '-100', 'user_id': '200', 'thread_id': None, 'message_id': '300'}


def analysis():
    return {'video_id': 'dQw4w9WgXcQ', 'title': 'Offline fixture', 'duration': 200,
            'heatmap': [], 'summary': 'Fixture', 'source_type': 'youtube',
            'clips': [{'title': 'Dialogue ' + str(i), 'start_time': i * 40, 'end_time': i * 40 + 20,
                       'hook_time': i * 40 + 1, 'virality_score': 90 - i,
                       'key_quotes': ['Asli'], 'transcript': 'Asli'} for i in range(4)]}


class SelectionTests(unittest.TestCase):
    def setUp(self):
        from backend.services.clip_automation import ClipJobStore
        self.tmp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = [1000.0]
        self.manual = ClipJobStore(self.root / 'handoffs', clock=lambda: self.now[0])

    def cached(self):
        job, _ = self.manual.reserve(URL, 'a' * 64, ROUTE)
        return self.manual.complete(job['job_id'], analysis())

    def store(self, **kwargs):
        from backend.services.clip_selection import SelectionStore
        return SelectionStore(self.root / 'selections', self.manual, clock=lambda: self.now[0], **kwargs)

    def test_cache_only_reservation_is_persistent_and_never_changes_manual_ledger(self):
        from backend.services.clip_automation import ClipJobError
        store = self.store()
        self.assertFalse(store.root.exists(), 'Constructor must not write')
        with self.assertRaises(ClipJobError) as caught:
            store.reserve(URL, 'b' * 64, ROUTE)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn('/clip', str(caught.exception))
        cached = self.cached()
        before = {p.name: p.read_bytes() for p in self.manual.root.iterdir()}
        first, reused = store.reserve(URL, 'b' * 64, ROUTE)
        self.assertFalse(reused)
        self.assertNotEqual(first['job_id'], cached['job_id'])
        self.assertEqual(first['editor_job_id'], cached['job_id'])
        self.assertEqual(first['state'], 'selecting')
        self.assertEqual(first['selected_indices'], [])
        self.assertIsNone(first['menu_message_id'])
        self.assertEqual(first['revision'], 0)
        self.assertEqual(len(first['clips']), 4)
        self.assertTrue(all(c['eligible'] for c in first['clips']))
        second, reused = self.store().reserve(URL, 'b' * 64, ROUTE)
        self.assertTrue(reused)
        self.assertEqual(first['job_id'], second['job_id'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.manual.root.iterdir()})


    def test_parallel_toggle_cas_allows_exactly_one_writer(self):
        from concurrent.futures import ThreadPoolExecutor
        from backend.services.clip_automation import ClipJobError
        self.cached()
        store = self.store()
        job, _ = store.reserve(URL, 'b' * 64, ROUTE)
        store.action(job['job_id'], 'bind', 0, ROUTE, '400')
        def toggle(index):
            try:
                return store.action(job['job_id'], 'toggle', 0, ROUTE, '400', index)['revision']
            except ClipJobError as exc:
                return exc.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(toggle, [0, 1]))
        self.assertEqual(sorted(outcomes), [1, 409])
        saved = self.store().get(job['job_id'])
        self.assertEqual(saved['revision'], 1)
        self.assertEqual(len(saved['selected_indices']), 1)

    def test_cache_requires_nonexpired_completed_exact_source_and_preset(self):
        from backend.services.clip_automation import ClipJobError
        cached = self.cached()
        store = self.store(max_jobs=1)
        for changes in ({'state': 'analyzing'}, {'preset': {}}, {'expires_at': 0},
                        {'source_url': 'https://www.youtube.com/watch?v=abcdefghijk'}):
            with patch.object(self.manual, '_jobs', return_value=[{**cached, **changes}]):
                with self.assertRaises(ClipJobError) as caught:
                    store.reserve(URL, 'b' * 64, ROUTE)
                self.assertEqual(caught.exception.status_code, 409)
        first, _ = store.reserve(URL, 'b' * 64, ROUTE)
        with self.assertRaises(ClipJobError) as caught:
            store.reserve(URL, 'c' * 64, ROUTE)
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(store.get(first['job_id'])['state'], 'selecting')

    def test_subtitle_override_is_per_job_persisted_and_frozen_on_confirm(self):
        from backend.services.clip_automation import ClipJobError
        from backend.voidcut import load_preset
        self.cached()
        store = self.store()
        preset_before = load_preset()
        job, _ = store.reserve(URL, 'b' * 64, ROUTE)
        store.action(job['job_id'], 'bind', 0, ROUTE, '400')
        store.action(job['job_id'], 'toggle', 0, ROUTE, '400', 0)
        changed = store.action(job['job_id'], 'subtitles', 1, ROUTE, '400', 0)
        self.assertIs(changed['subtitles_enabled'], False)
        persisted = self.store().get(job['job_id'])
        request = store.selected_request(persisted)
        self.assertEqual(request.settings.caption_style, 'none')
        self.assertIs(request.settings.subtitles_enabled, False)
        self.assertEqual(load_preset(), preset_before)
        self.assertEqual(preset_before['render_settings']['caption_style'], 'plain')
        confirmed = store.action(job['job_id'], 'confirm', 2, ROUTE, '400')
        with self.assertRaises(ClipJobError):
            store.action(job['job_id'], 'subtitles', confirmed['revision'], ROUTE, '400', 1)
        # A separate job still inherits the permanent subtitle-on preset.
        other, _ = store.reserve(URL, 'c' * 64, {**ROUTE, 'message_id': '301'})
        store.action(other['job_id'], 'bind', 0, other['telegram'], '401')
        other = store.action(other['job_id'], 'toggle', 0, other['telegram'], '401', 0)
        self.assertEqual(store.selected_request(other).settings.caption_style, 'plain')

    def test_subtitle_override_rejects_invalid_stale_and_wrong_owner(self):
        from backend.services.clip_automation import ClipJobError
        self.cached()
        store = self.store()
        job, _ = store.reserve(URL, 'b' * 64, ROUTE)
        store.action(job['job_id'], 'bind', 0, ROUTE, '400')
        for value in (None, -1, 2, True):
            with self.assertRaises(ClipJobError):
                store.action(job['job_id'], 'subtitles', 0, ROUTE, '400', value)
        with self.assertRaises(ClipJobError):
            store.action(job['job_id'], 'subtitles', 0, {**ROUTE, 'user_id': '999'}, '400', 0)
        store.action(job['job_id'], 'subtitles', 0, ROUTE, '400', 0)
        with self.assertRaises(ClipJobError):
            store.action(job['job_id'], 'subtitles', 0, ROUTE, '400', 1)
        self.assertIs(store.get(job['job_id'])['subtitles_enabled'], False)

    def test_bulk_selection_is_explicit_bounded_and_revision_guarded(self):
        from backend.services.clip_automation import ClipJobError
        cached = self.cached()
        store = self.store()
        job, _ = store.reserve(URL, 'b' * 64, ROUTE)
        store.action(job['job_id'], 'bind', 0, ROUTE, '400')
        # Four eligible clips cannot be silently reduced to the maximum of three.
        with self.assertRaises(ClipJobError) as caught:
            store.action(job['job_id'], 'select_all', 0, ROUTE, '400')
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(store.get(job['job_id'])['selected_indices'], [])
        # A menu with three eligible candidates supports Select All.
        small = analysis(); small['clips'] = small['clips'][:3]
        self.manual.complete(cached['job_id'], small)
        second, _ = store.reserve(URL, 'c' * 64, {**ROUTE, 'message_id': '301'})
        route = second['telegram']
        store.action(second['job_id'], 'bind', 0, route, '401')
        selected = store.action(second['job_id'], 'select_all', 0, route, '401')
        self.assertEqual(selected['selected_indices'], [0, 1, 2])
        self.assertEqual(selected['state'], 'selecting')
        self.assertNotIn('render_requested', selected)
        cleared = store.action(second['job_id'], 'clear', 1, route, '401')
        self.assertEqual(cleared['selected_indices'], [])
        with self.assertRaises(ClipJobError):
            store.action(second['job_id'], 'select_all', 1, route, '401')
        self.assertEqual(store.get(second['job_id'])['selected_indices'], [])

    def test_confirm_queue_is_bounded_and_renews_render_retention(self):
        from backend.services.clip_automation import ClipJobError
        self.cached()
        store = self.store(max_active=1)
        first, _ = store.reserve(URL, 'b' * 64, ROUTE)
        second, _ = store.reserve(URL, 'c' * 64, {**ROUTE, 'message_id': '301'})
        for job in (first, second):
            store.action(job['job_id'], 'bind', 0, job['telegram'], '400')
            store.action(job['job_id'], 'toggle', 0, job['telegram'], '400', 0)
        self.now[0] += 100
        confirmed = store.action(first['job_id'], 'confirm', 1, ROUTE, '400')
        self.assertEqual(confirmed['expires_at'], self.now[0] + store.ttl)
        with self.assertRaises(ClipJobError) as caught:
            store.action(second['job_id'], 'confirm', 1, second['telegram'], '400')
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(store.get(second['job_id'])['state'], 'selecting')

    def test_owner_menu_revision_cas_and_cancel_are_fail_closed(self):
        from backend.services.clip_automation import ClipJobError
        self.cached()
        store = self.store()
        job, _ = store.reserve(URL, 'b' * 64, ROUTE)
        def act(action, revision=0, route=ROUTE, menu='400', index=None):
            return store.action(job['job_id'], action, revision, route, menu, index)
        self.assertTrue(hasattr(store, 'action'))
        for route in ({**ROUTE, 'chat_id': '-101'}, {**ROUTE, 'user_id': '201'},
                      {**ROUTE, 'thread_id': '1'}, {**ROUTE, 'message_id': '301'}):
            with self.assertRaises(ClipJobError) as caught:
                act('bind', route=route)
            self.assertEqual(caught.exception.status_code, 403)
        act('bind')
        self.assertEqual(store.get(job['job_id'])['revision'], 0)
        act('bind')  # exact rebind is idempotent
        for action, menu in (('bind', '401'), ('toggle', '401')):
            with self.assertRaises(ClipJobError):
                act(action, menu=menu, index=0)
        changed = act('toggle', index=0)
        self.assertEqual(changed['revision'], 1)
        self.assertEqual(changed['selected_indices'], [0])
        with self.assertRaises(ClipJobError) as caught:
            act('toggle', index=0)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(store.get(job['job_id'])['selected_indices'], [0])
        cancelled = act('cancel', revision=1)
        self.assertEqual(cancelled['state'], 'cancelled')
        with self.assertRaises(ClipJobError):
            act('toggle', revision=2, index=0)
        self.assertEqual(self.store().get(job['job_id'])['state'], 'cancelled')

    def test_confirm_requires_all_eligible_and_persists_intent_once_before_execution(self):
        from backend.services.clip_automation import ClipJobError
        self.cached()
        store = self.store()
        job, _ = store.reserve(URL, 'b' * 64, ROUTE)
        store.action(job['job_id'], 'bind', 0, ROUTE, '400')
        with self.assertRaises(ClipJobError) as caught:
            store.action(job['job_id'], 'confirm', 0, ROUTE, '400')
        self.assertEqual(caught.exception.status_code, 400)
        for i in range(3):
            store.action(job['job_id'], 'toggle', i, ROUTE, '400', i)
        with self.assertRaises(ClipJobError):
            store.action(job['job_id'], 'toggle', 3, ROUTE, '400', 3)
        confirmed = store.action(job['job_id'], 'confirm', 3, ROUTE, '400')
        self.assertEqual(confirmed['state'], 'rendering')
        self.assertTrue(confirmed['render_requested'])
        self.assertRegex(confirmed['render_request_id'], r'^[0-9a-f-]{36}$')
        duplicate = self.store().action(job['job_id'], 'confirm', 3, ROUTE, '400')
        self.assertEqual(duplicate['render_request_id'], confirmed['render_request_id'])
        self.assertEqual(duplicate['revision'], 4)
        with self.assertRaises(ClipJobError):
            store.action(job['job_id'], 'cancel', 4, ROUTE, '400')
        store.recover_interrupted()
        self.assertEqual(store.get(job['job_id'])['state'], 'interrupted')
        self.assertTrue(store.notification(job['job_id'], 'claim')['claimed'])
        self.assertFalse(store.notification(job['job_id'], 'claim')['claimed'])
        store.notification(job['job_id'], 'sent', '500')
        self.assertFalse(store.pending_notifications())

    def test_invalid_confirm_reports_all_indices_without_filtering(self):
        from backend.services.clip_automation import ClipJobError
        cached = self.cached()
        store = self.store()
        job, _ = store.reserve(URL, 'b' * 64, ROUTE)
        store.action(job['job_id'], 'bind', 0, ROUTE, '400')
        store.action(job['job_id'], 'toggle', 0, ROUTE, '400', 1)
        bad = analysis()
        bad['clips'][1]['end_time'] = 80
        self.manual.complete(cached['job_id'], bad)
        with self.assertRaises(ClipJobError) as caught:
            store.action(job['job_id'], 'confirm', 1, ROUTE, '400')
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(caught.exception.detail['invalid_indices'], [1])
        self.assertEqual(caught.exception.detail['code'], 'invalid_selection')
        self.assertEqual(store.get(job['job_id'])['state'], 'selecting')

    def test_metadata_symlink_storage_is_rejected_without_writes(self):
        self.cached()
        store = self.store()
        original = Path.is_symlink
        with patch.object(Path, 'is_symlink', lambda p: p == store.root or original(p)):
            from backend.services.clip_automation import ClipJobError
            with self.assertRaises(ClipJobError) as caught:
                store.reserve(URL, 'b' * 64, ROUTE)
            self.assertEqual(caught.exception.status_code, 503)
        self.assertFalse(store.root.exists())


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from backend.services.clip_automation import ClipJobStore
        from backend.services.clip_selection import SelectionStore
        self.tmp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        manual = ClipJobStore(self.root / 'handoffs')
        job, _ = manual.reserve(URL, 'a' * 64, ROUTE)
        manual.complete(job['job_id'], analysis())
        self.store = SelectionStore(self.root / 'selections', manual)
        self.exports = self.root / 'exports'
        self.exports.mkdir()

    async def test_expired_queued_job_is_terminalized_without_render_or_stuck_capacity(self):
        from unittest.mock import AsyncMock
        from backend.services.clip_selection import SelectionManager
        job, _ = self.store.reserve(URL, 'b' * 64, ROUTE)
        self.store.action(job['job_id'], 'bind', 0, ROUTE, '400')
        self.store.action(job['job_id'], 'toggle', 0, ROUTE, '400', 0)
        self.store.action(job['job_id'], 'confirm', 1, ROUTE, '400')
        self.store._change(job['job_id'], {'expires_at': 0})
        start = AsyncMock()
        manager = SelectionManager(self.store, self.root / 'results', start_render=start)
        await manager._run(job['job_id'])
        start.assert_not_awaited()
        record = self.store._read(self.store._path(job['job_id']))
        self.assertEqual(record['state'], 'failed')
        manager.submit(URL, 'c' * 64, {**ROUTE, 'message_id': '301'})
        self.assertFalse(self.store._path(job['job_id']).exists())

    async def test_crash_after_confirm_is_interrupted_without_automatic_retry_or_lost_outputs(self):
        from backend.services.clip_selection import SelectionManager, SelectionStore
        job, _ = self.store.reserve(URL, 'b' * 64, ROUTE)
        self.store.action(job['job_id'], 'bind', 0, ROUTE, '400')
        self.store.action(job['job_id'], 'toggle', 0, ROUTE, '400', 0)
        # Simulated crash after durable intent but before task creation.
        confirmed = self.store.action(job['job_id'], 'confirm', 1, ROUTE, '400')
        restored = SelectionStore(self.store.root, self.store.manual)
        manager = SelectionManager(restored, self.root / 'results',
            start_render=lambda *a, **k: self.fail('Must never retry accepted intent after restart'))
        manager.start()
        self.assertEqual(manager.status(job['job_id'])['state'], 'interrupted')
        retried = manager.action(job['job_id'], 'confirm', 1, ROUTE, '400')
        self.assertEqual(retried['state'], 'interrupted')
        self.assertFalse(manager.tasks)
        self.assertEqual(restored.get(job['job_id'])['render_request_id'], confirmed['render_request_id'])
        # An atomic intent failure cannot start a task or consume revision.
        other = manager.submit(URL, 'c' * 64, ROUTE)
        manager.action(other['job_id'], 'bind', 0, ROUTE, '401')
        manager.action(other['job_id'], 'toggle', 0, ROUTE, '401', 0)
        with patch.object(restored, '_atomic', side_effect=OSError('offline simulated storage failure')):
            with self.assertRaises(OSError):
                manager.action(other['job_id'], 'confirm', 1, ROUTE, '401')
        self.assertFalse(manager.tasks)
        self.assertEqual(restored.get(other['job_id'])['state'], 'selecting')

    async def test_zip_failure_preserves_verified_clips_as_completed_with_errors(self):
        from backend.services.clip_selection import SelectionManager
        bid = 'batch_123456_abcdef'
        batch = {'overall_status': 'completed', 'clips': [{'status': 'completed'}]}
        (self.exports / f'clip_1_{bid}.mp4').write_bytes(b'offline-not-real-media')
        async def fake_start(request, bg, idempotency_key):
            return {'batch_id': bid}
        manager = SelectionManager(self.store, self.root / 'results', start_render=fake_start,
            batch_get=lambda _: batch, exports_root=self.exports, validate=lambda p, d: {'structural_passed': True})
        job = manager.submit(URL, 'b' * 64, ROUTE)
        manager.action(job['job_id'], 'bind', 0, ROUTE, '400')
        manager.action(job['job_id'], 'toggle', 0, ROUTE, '400', 0)
        with patch.object(manager, '_zip', side_effect=OSError('fixture-secret-error')):
            manager.action(job['job_id'], 'confirm', 1, ROUTE, '400')
            await manager.wait_idle()
        status = manager.status(job['job_id'])
        self.assertEqual(status['state'], 'completed_with_errors')
        self.assertEqual(len(status['files']), 1)
        self.assertNotIn('zip_url', status)
        self.assertNotIn('fixture-secret-error', str(status))

    async def test_output_quota_never_evicts_unexpired_and_prunes_expired_only(self):
        from backend.services.clip_selection import SelectionManager
        from backend.services.clip_automation import ClipJobError
        manager = SelectionManager(self.store, self.root / 'results', exports_root=self.exports,
            validate=lambda p, d: {'structural_passed': True}, max_result_bytes=1024, max_results=1)
        job = manager.submit(URL, 'b' * 64, ROUTE)
        source = self.exports / 'fixture.mp4'
        source.write_bytes(b'offline' * 10)
        manager._pin(job['job_id'], source, 1, 20)
        manager._zip(job['job_id'], [{'name': 'clip_1.mp4'}])
        other = manager.submit(URL, 'c' * 64, ROUTE)
        with self.assertRaises(ClipJobError):
            manager._pin(other['job_id'], source, 1, 20)
        self.assertTrue((manager.results_root / job['job_id'] / 'clip_1.mp4').exists())
        source.write_bytes(b'x' * 2048)
        with self.assertRaises(ClipJobError):
            manager._pin(job['job_id'], source, 2, 20)
        # Only expired non-rendering results are pruned, never active output.
        self.store._change(job['job_id'], {'expires_at': self.store.clock() - 1, 'state': 'completed'})
        manager.submit(URL, 'd' * 64, ROUTE)
        self.assertFalse((manager.results_root / job['job_id']).exists())
        self.assertEqual(self.store.get(other['job_id'])['state'], 'selecting')

    async def test_rejects_symlinked_ancestor_and_never_publishes_failed_qc(self):
        from backend.services.clip_selection import SelectionManager
        manager = SelectionManager(self.store, self.root / 'results', exports_root=self.exports,
            validate=lambda p, d: {'structural_passed': False})
        job = manager.submit(URL, 'b' * 64, ROUTE)
        source = self.exports / 'fixture.mp4'
        source.write_bytes(b'offline-fake-not-media')
        with self.assertRaises(ValueError):
            manager._pin(job['job_id'], source, 1, 20)
        self.assertFalse((manager.results_root / job['job_id'] / 'clip_1.mp4').exists())
        manager.validate = lambda p, d: {'structural_passed': True}
        # Mock only link detection (Windows hosts may forbid test symlink creation).
        original = Path.is_symlink
        with patch.object(Path, 'is_symlink', lambda p: p == self.exports or original(p)):
            with self.assertRaises(ValueError):
                manager._pin(job['job_id'], source, 1, 20)

    async def test_existing_router_background_callable_runs_once_and_pins_only_verified_partial_results(self):
        import asyncio
        from unittest.mock import patch
        import backend.routers.render as render
        from backend.services.clip_selection import SelectionManager
        batches = render.RENDER_BATCHES
        calls = []
        async def fake_process(batch_id, request):
            # Fake media fixture: the injected QC validator is not a real probe.
            saved = self.store._jobs()[0]
            self.assertEqual(saved['batch_id'], batch_id, 'Persist batch before running BackgroundTasks')
            self.assertTrue(saved['render_requested'])
            calls.append(request)
            path = self.exports / f'clip_1_{batch_id}.mp4'
            path.write_bytes(b'offline-fixture-NOT-valid-media')
            batches[batch_id]['clips'][0].update(status='completed', output_path='UNTRUSTED-do-not-use')
            batches[batch_id]['clips'][1].update(status='error', error='provider-secret-do-not-persist')
            batches[batch_id]['overall_status'] = 'completed'
        verified = []
        def fake_validate(path, duration):
            verified.append((path.name, duration))
            return {'structural_passed': True, 'ready_for_production': False}
        manager = SelectionManager(self.store, self.root / 'results', start_render=render.start_batch_render,
            batch_get=lambda bid: batches.get(bid), exports_root=self.exports, validate=fake_validate)
        with patch.object(render, 'process_batch_rendering', fake_process), \
             patch.dict(render.REQUEST_IDS, {}, clear=True), patch.dict(batches, {}, clear=True):
            job = manager.submit(URL, 'b' * 64, ROUTE)
            manager.action(job['job_id'], 'bind', 0, ROUTE, '400')
            manager.action(job['job_id'], 'toggle', 0, ROUTE, '400', 0)
            manager.action(job['job_id'], 'toggle', 1, ROUTE, '400', 1)
            manager.action(job['job_id'], 'confirm', 2, ROUTE, '400')
            manager.action(job['job_id'], 'confirm', 2, ROUTE, '400')
            await manager.wait_idle()
            self.assertEqual(len(calls), 1)
            self.assertEqual([c['start_time'] for c in calls[0].clips], [0, 40])
            status = manager.status(job['job_id'])
            self.assertEqual(status['state'], 'completed_with_errors')
            self.assertEqual(status['files'], [{'name': 'clip_1.mp4', 'source_index': 0,
                'download_url': f"/api/clip/selections/{job['job_id']}/files/clip_1.mp4"}])
            self.assertTrue(status['zip_url'].endswith('/files/clips.zip'))
            self.assertNotIn('provider-secret', str(status))
            self.assertNotIn(str(self.root), str(status))
            self.assertEqual(verified[0][1], 20)
            self.assertEqual(manager.file(job['job_id'], 'clip_1.mp4').read_bytes(), b'offline-fixture-NOT-valid-media')
            self.exports.joinpath(verified[0][0]).unlink(missing_ok=True)
            self.assertTrue(manager.file(job['job_id'], 'clip_1.mp4').exists())
            manager.action(job['job_id'], 'confirm', 2, ROUTE, '400')
            await manager.wait_idle()
            self.assertEqual(len(calls), 1)
            manager.start()
            self.assertEqual(manager.status(job['job_id'])['state'], 'completed_with_errors')


class MediaGateTests(unittest.TestCase):
    def test_actual_probe_and_full_decode_commands_use_no_provider_and_reject_bad_media(self):
        import json
        import subprocess
        from unittest.mock import patch
        import backend.services.clip_selection as module
        self.assertTrue(hasattr(module, 'verify_media'))
        probe = {'streams': [{'codec_type': 'video', 'codec_name': 'h264', 'width': 1080, 'height': 1920,
                             'pix_fmt': 'yuv420p', 'sample_aspect_ratio': '1:1', 'start_time': '0'},
                            {'codec_type': 'audio', 'codec_name': 'aac', 'start_time': '0'}],
                 'format': {'format_name': 'mov,mp4', 'duration': '20', 'size': '1234'}}
        with patch.object(module.subprocess, 'run', side_effect=[
                subprocess.CompletedProcess([], 0, json.dumps(probe), ''),
                subprocess.CompletedProcess([], 0, '', '')]) as runner:
            verdict = module.verify_media(Path('fixture-not-real.mp4'), 20)
            self.assertTrue(verdict['structural_passed'])
            self.assertFalse(verdict['ready_for_production'])
            self.assertEqual(runner.call_args_list[0].args[0][0], 'ffprobe')
            self.assertEqual(runner.call_args_list[1].args[0][0], 'ffmpeg')
            self.assertIn('-xerror', runner.call_args_list[1].args[0])
        probe['streams'][0]['sample_aspect_ratio'] = '2:1'
        with patch.object(module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(probe), '')) as runner:
            with self.assertRaises(ValueError):
                module.verify_media(Path('fixture-not-real.mp4'), 20)
            self.assertEqual(runner.call_count, 1)


class ExplicitRequestTests(unittest.TestCase):
    def test_explicit_order_budget_and_every_invalid_index_are_preserved(self):
        import backend.voidcut as module
        self.assertTrue(hasattr(module, 'build_selected_request'))
        data = analysis()
        request = module.build_selected_request(data, [2, 0, 1])
        self.assertEqual([c['start_time'] for c in request.clips], [80, 0, 40])
        self.assertTrue(request.settings.force_square_pixels)
        self.assertEqual(request.settings.render_mode, 'separate')
        self.assertEqual(request.settings.original_audio_volume, 100)
        self.assertFalse(request.settings.bgm_enabled)
        self.assertIsNone(request.api_key if hasattr(request, 'api_key') else None)
        for indices in ([], [0, 1, 2, 3], [0, 0], [-1], [True], [8]):
            with self.assertRaises(ValueError):
                module.build_selected_request(data, indices)
        data['clips'][1]['end_time'] = 80
        with self.assertRaises(ValueError) as caught:
            module.build_selected_request(data, [0, 1, 9])
        self.assertEqual(caught.exception.invalid_indices, [1, 9])


if __name__ == '__main__':
    unittest.main()
