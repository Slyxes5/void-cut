"""Offline opt-in preset contracts. No application startup, provider or bot calls."""
import importlib.util
import unittest


class PresetTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('backend.voidcut'), 'Offline Voidcut preset module exists')
        import backend.voidcut as module
        return module

    def test_opt_in_settings_reuse_schema_without_changing_manual_defaults(self):
        from backend.schemas.render import RenderSettingsModel
        defaults = RenderSettingsModel().model_dump()
        module = self.module()
        preset = module.load_preset()
        settings = RenderSettingsModel.model_validate(preset['render_settings'])
        self.assertEqual(preset['name'], 'Voidcut Auto-Edit')
        self.assertFalse(preset['production_enabled'])
        self.assertEqual(preset['selection']['min_seconds'], 15)
        self.assertEqual(preset['selection']['max_seconds'], 30)
        self.assertEqual(settings.aspect_ratio, '16:9')  # Widescreen content inside portrait canvas.
        self.assertFalse(settings.enable_face_tracking)
        self.assertEqual(settings.caption_style, 'plain')
        self.assertEqual(settings.original_audio_volume, 100)
        self.assertFalse(settings.bgm_enabled)
        self.assertFalse(settings.hook_sfx_enabled)
        self.assertEqual(settings.timeline_sfx, [])
        self.assertEqual(RenderSettingsModel().model_dump(), defaults)
        preset['render_settings']['bgm_enabled'] = True
        self.assertFalse(module.load_preset()['render_settings']['bgm_enabled'])


    def test_plan_selects_existing_in_band_candidate_without_truncation_or_mutation(self):
        import copy
        module = self.module()
        self.assertTrue(hasattr(module, 'build_plan'), 'Offline plan builder exists')
        def clip(start, end, score):
            return {'title': 'Dialog RP', 'start_time': start, 'end_time': end, 'hook_time': start + 2,
                    'virality_score': score, 'key_quotes': ['Dialog asli.'], 'transcript': 'Dialog asli.'}
        data = {'video_id': 'n6no1RJnY8s', 'title': 'Fixture', 'duration': 120, 'summary': 'Fixture',
                'clips': [clip(0, 32.1, 99), clip(40, 66, 90), clip(70, 90, 80)],
                'heatmap': [], 'source_type': 'youtube', 'api_key': 'must-not-leave-input'}
        before = copy.deepcopy(data)
        plan = module.build_plan(data)
        self.assertFalse(plan['production_enabled'])
        self.assertFalse(plan['ready_for_production'])
        self.assertEqual(plan['selected_indices'], [1])
        self.assertEqual(plan['render_request']['clips'][0]['start_time'], 40)
        self.assertEqual(plan['render_request']['clips'][0]['end_time'], 66)
        self.assertEqual(plan['render_request']['video_url'], 'https://www.youtube.com/watch?v=n6no1RJnY8s')
        self.assertNotIn('must-not-leave-input', str(plan))
        self.assertEqual(data, before)
        data['clips'] = [clip(0, 32.1, 99)]
        blocked = module.build_plan(data)
        self.assertIsNone(blocked['render_request'])
        self.assertEqual(blocked['selection_status'], 'manual_review_required')


    def test_probe_gate_checks_real_encoding_band_and_does_not_claim_content_quality(self):
        import copy
        module = self.module()
        self.assertTrue(hasattr(module, 'check_media_probe'), 'Offline media gate exists')
        probe = {'streams': [{'codec_type': 'video', 'codec_name': 'h264', 'width': 1080, 'height': 1920,
                              'pix_fmt': 'yuv420p', 'sample_aspect_ratio': '1:1', 'start_time': '0.0'},
                             {'codec_type': 'audio', 'codec_name': 'aac', 'start_time': '0.0'}],
                 'format': {'format_name': 'mov,mp4,m4a,3gp,3g2,mj2', 'duration': '26.0', 'size': '12345'}}
        good = module.check_media_probe(probe, expected_duration=26)
        self.assertTrue(good['structural_passed'])
        self.assertFalse(good['ready_for_production'])
        self.assertIn('indonesian_transcription_accuracy', good['pending_content_checks'])
        self.assertIn('decode_integrity', good['pending_media_checks'])
        cases = [('duration', lambda p: p['format'].update(duration='32.1')),
                 ('portrait_1080x1920', lambda p: p['streams'][0].update(width=1920)),
                 ('h264', lambda p: p['streams'][0].update(codec_name='hevc')),
                 ('yuv420p', lambda p: p['streams'][0].update(pix_fmt='yuv420p10le')),
                 ('aac_audio', lambda p: p['streams'].pop()),
                 ('av_start_alignment', lambda p: p['streams'][1].update(start_time='1.0')),
                 ('square_pixels', lambda p: p['streams'][0].update(sample_aspect_ratio='2:1')),
                 ('duration', lambda p: p['format'].update(duration='NaN'))]
        for failed_check, mutate in cases:
            bad = copy.deepcopy(probe); mutate(bad)
            outcome = module.check_media_probe(bad, expected_duration=26)
            self.assertFalse(outcome['structural_passed'])
            self.assertIn(failed_check, outcome['failures'])
        self.assertFalse(module.check_media_probe({})['structural_passed'])


    def test_selection_boundaries_and_unsafe_metadata_fail_closed(self):
        module = self.module()
        def candidate(**overrides):
            return {'title': 'Fixture', 'start_time': 0, 'end_time': 15, 'hook_time': 1,
                    'virality_score': 90, 'key_quotes': ['Asli'], 'transcript': 'Asli', **overrides}
        def analyze(c):
            return {'video_id': 'n6no1RJnY8s', 'source_type': 'youtube', 'title': 'Fixture',
                    'duration': 90, 'heatmap': [], 'summary': 'Fixture', 'clips': [c]}
        for end in (15, 30):
            self.assertEqual(module.build_plan(analyze(candidate(end_time=end)))['selected_indices'], [0])
        for change in ({'end_time': 14.9}, {'end_time': 30.1}, {'start_time': -1}, {'hook_time': 16},
                       {'end_time': float('nan')}, {'hook_time': float('inf')}, {'virality_score': 101},
                       {'virality_score': 69}, {'key_quotes': []}, {'transcript': ' '}):
            self.assertIsNone(module.build_plan(analyze(candidate(**change)))['render_request'])
        unsafe = analyze(candidate()); unsafe['source_type'] = 'upload'
        with self.assertRaises(ValueError):
            module.build_plan(unsafe)
        unsafe['source_type'] = 'youtube'; unsafe['video_id'] = '../bad'
        with self.assertRaises(ValueError):
            module.build_plan(unsafe)

    def test_square_pixel_normalization_is_opt_in_and_legacy_graph_is_unchanged(self):
        from backend.schemas.render import RenderSettingsModel
        from backend.video_engine import build_ffmpeg_filtergraph
        import inspect
        self.assertIn('force_square_pixels', inspect.signature(build_ffmpeg_filtergraph).parameters)
        self.assertFalse(RenderSettingsModel().force_square_pixels)
        args = {'aspect_ratio': '16:9', 'background_style': 'black', 'face_center_ratio': 0.5}
        legacy = build_ffmpeg_filtergraph(**args)
        explicit_legacy = build_ffmpeg_filtergraph(**args, force_square_pixels=False)
        normalized, output = build_ffmpeg_filtergraph(**args, force_square_pixels=True)
        self.assertEqual(legacy, explicit_legacy)
        self.assertNotIn('setsar=1', legacy[0])
        self.assertIn('setsar=1', normalized)
        self.assertIn(output, normalized)
        self.assertEqual(normalized.count('setsar=1'), 1)


class ServiceWiringTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # Import heavy existing media dependencies outside the timed asyncio callback.
        import backend.services.render_service

    async def test_separate_and_merged_services_forward_opt_in_without_real_media_work(self):
        import os
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from backend.schemas.render import RenderSettingsModel
        import backend.services.render_service as service
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            settings = RenderSettingsModel(force_square_pixels=True, caption_style='none', title_position='none')
            clip = {'start_time': 0, 'end_time': 20, 'title': 'Fixture'}
            recorded = []
            def render(**kwargs):
                recorded.append(kwargs)
                Path(kwargs['output_mp4_path']).write_bytes(b'unit-fixture-not-real-media')
            for merged in (False, True):
                raw = root / 'raw.mp4'; raw.write_bytes(b'unit-fixture-not-real-media')
                with patch.dict(service.RENDER_BATCHES, {'unit': {'clips': [{'status': 'pending'}]}}, clear=True), \
                     patch.object(service, 'TEMP_DIR', root), patch.object(service, 'EXPORTS_DIR', root), \
                     patch.object(service, 'download_clip_segment', return_value=str(raw)), \
                     patch.object(service, 'is_valid_mp4', return_value=True), \
                     patch.object(service, 'render_clip_to_mp4', side_effect=render):
                    if merged:
                        await service.render_merged_batch_clips('unit', [clip], settings, 'fixture-local-only')
                    else:
                        await service.render_single_batch_clip('unit', 0, clip, settings, 'fixture-local-only')
                    self.assertEqual(service.RENDER_BATCHES['unit']['clips'][0]['status'], 'completed')
                    self.assertIs(recorded[-1].get('force_square_pixels'), True)


if __name__ == '__main__':
    unittest.main()
