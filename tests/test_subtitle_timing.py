"""Offline timing regressions. No model downloads or external video requests."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.subtitle_timing import normalize_words, transcript_words


class SubtitleTimingTests(unittest.TestCase):
    def test_crop_does_not_squeeze_words_before_the_cut_into_the_clip(self):
        line = dict(start=0, end=10, text='zero one two three four five six seven eight nine')
        words = transcript_words([line], 5, 7)
        self.assertEqual([(w['word'], w['start'], w['end']) for w in words],
                         [('five', 0, 1), ('six', 1, 2)])

    def test_acoustic_words_take_priority_over_estimated_line_timing(self):
        words = transcript_words([dict(start=0, end=10, text='different line text', words=[
            dict(word='asli', start=5.1, end=5.18), dict(word='cepat', start=5.18, end=5.24)])], 5, 6)
        self.assertEqual([w['word'] for w in words], ['asli', 'cepat'])
        self.assertAlmostEqual(words[1]['start'], .18)
        self.assertAlmostEqual(words[1]['end'], .24)

    def test_overlap_is_trimmed_without_delaying_the_next_word_or_extending_clip(self):
        words = normalize_words([dict(word='a', start=-1, end=.4), dict(word='b', start=.1, end=2),
                                 dict(word='invalid', start=float('nan'), end=2)], .3)
        self.assertEqual([(w['start'], w['end']) for w in words], [(0, .1), (.1, .3)])

    def test_rapid_speech_ass_events_keep_original_boundaries_and_silence(self):
        from backend.video_engine import generate_ass_file
        words = [dict(word=f'kata{i}', start=i*.06, end=(i+1)*.06) for i in range(9)]
        words.append(dict(word='setelah-jeda', start=1.5, end=1.6))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'captions.ass'
            generate_ass_file(words, 'viral_pop', 'Arial', str(output), duration_seconds=2)
            events = [line.split(',', 9) for line in output.read_text(encoding='utf-8').splitlines()
                      if line.startswith('Dialogue: 0,')]
        def seconds(value):
            h, m, s = value.split(':')
            return int(h)*3600 + int(m)*60 + float(s)
        self.assertEqual(len(events), 10)
        for event, word in zip(events, words):
            self.assertAlmostEqual(seconds(event[1]), word['start'], places=2)
            self.assertAlmostEqual(seconds(event[2]), word['end'], places=2)
        self.assertNotIn('setelah-jeda', events[-2][-1])

    def test_timestamp_rounding_carries_to_the_next_second_and_minute(self):
        from backend.video_engine import format_ass_timestamp
        self.assertEqual(format_ass_timestamp(.999), '0:00:01.00')
        self.assertEqual(format_ass_timestamp(59.999), '0:01:00.00')
        self.assertEqual(format_ass_timestamp(3599.999), '1:00:00.00')

    def test_whisper_fast_words_and_silence_are_not_replaced_with_estimates(self):
        from backend import video_engine as engine
        model = Mock()
        model.transcribe.return_value = {'segments': [{'words': [dict(word='cepat', start=.2, end=.25)]}]}
        fallback = [dict(start=0, end=2, text='this should not appear')]
        with patch.object(engine, 'get_whisper_model', return_value=model), patch.object(engine.os.path, 'exists', return_value=True):
            words = engine.transcribe_clip_words('fixture.mp4', fallback, 0, 2)
            self.assertEqual(words, [dict(word='cepat', start=.2, end=.25)])
            model.transcribe.return_value = {'segments': []}
            self.assertEqual(engine.transcribe_clip_words('fixture.mp4', fallback, 0, 2), [])
        with patch.object(engine, 'get_whisper_model', return_value=None):
            with self.assertRaises(RuntimeError):
                engine.transcribe_clip_words('fixture.mp4', fallback, 0, 2, require_acoustic=True)

    def test_local_transcription_retains_word_timing_for_editor(self):
        from backend import video_engine as engine
        model = Mock()
        model.transcribe.return_value = {'segments': [dict(text='Halo', start=0, end=1,
            words=[dict(word='Halo', start=.32, end=.65)])]}
        with tempfile.NamedTemporaryFile(suffix='.mp4') as media, patch.object(engine, 'get_whisper_model', return_value=model):
            lines = engine.transcribe_local_video_file(media.name)
        self.assertEqual(lines[0]['words'], [dict(word='Halo', start=.32, end=.65)])


class SubtitleSyncApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.routers.media import router
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.body = dict(video_url='https://www.youtube.com/watch?v=dQw4w9WgXcQ', start_time=10, end_time=12)

    def test_returns_absolute_acoustic_times_and_cleans_temporary_media(self):
        import backend.config as config
        import backend.routers.media as media
        import backend.services.render_service as service
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def download(source, start, end, filename, cookies):
                path = root / filename
                path.write_bytes(b'fixture')
                return str(path)
            with patch.object(media, 'TEMP_DIR', root), patch.object(config, 'download_clip_segment', side_effect=download), \
                 patch.object(config, 'transcribe_clip_words', return_value=[dict(word='Halo', start=.1, end=.17)]) as transcribe, \
                 patch.object(service, '_RENDER_SEMAPHORE', asyncio.Semaphore(1)):
                response = self.client.post('/api/subtitles/sync', json=self.body)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['timing_source'], 'audio')
            self.assertEqual(response.json()['words'][0]['start'], 10.1)
            self.assertEqual(response.json()['words'][0]['end'], 10.17)
            self.assertIs(transcribe.call_args.args[-1], True)
            self.assertEqual(list(root.iterdir()), [])

    def test_invalid_ranges_and_remote_or_arbitrary_local_sources_are_rejected(self):
        import backend.config as config
        with patch.object(config, 'download_clip_segment') as download:
            for change in (dict(start_time=12), dict(end_time=1000), dict(start_time=-1),
                           dict(video_url='http://127.0.0.1/private'), dict(video_url='C:/private.mp4'),
                           dict(video_url='https://youtube.com:bad/watch?v=example'),
                           dict(video_url='https://youtube.com.evil.example/video')):
                response = self.client.post('/api/subtitles/sync', json={**self.body, **change})
                self.assertEqual(response.status_code, 422, response.text)
            download.assert_not_called()

    def test_acoustic_failure_is_reported_instead_of_returning_guessed_words(self):
        import backend.config as config
        import backend.services.render_service as service
        with patch.object(config, 'download_clip_segment', return_value='fixture.mp4'), \
             patch.object(config, 'transcribe_clip_words', side_effect=RuntimeError('not available')), \
             patch.object(service, '_RENDER_SEMAPHORE', asyncio.Semaphore(1)):
            response = self.client.post('/api/subtitles/sync', json=self.body)
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('words', response.json())


class SubtitleRenderWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_separate_and_merged_renders_preserve_applied_short_word_timing(self):
        from backend.schemas.render import RenderSettingsModel
        import backend.services.render_service as service
        settings = RenderSettingsModel(caption_style='viral_pop', title_position='none')
        clips = [dict(title='Fixture', start_time=start, end_time=start+1, edited_subtitle_words=[
            dict(word='cepat', start=start+.02, end=start+.07)]) for start in (10,20)]
        for merged in (False,True):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                captured = []
                def download(source, start, end, filename, cookies):
                    path = root / filename
                    path.write_bytes(b'fixture')
                    return str(path)
                def render(**kwargs):
                    Path(kwargs['output_mp4_path']).write_bytes(b'fixture')
                def ass(**kwargs):
                    captured.extend(kwargs['words'])
                    Path(kwargs['output_ass_path']).write_text('fixture')
                with patch.dict(service.RENDER_BATCHES, {'unit': {'clips': [dict(status='pending')]}}, clear=True), \
                     patch.object(service, 'TEMP_DIR', root), patch.object(service, 'EXPORTS_DIR', root), \
                     patch.object(service, 'download_clip_segment', side_effect=download), \
                     patch.object(service, 'is_valid_mp4', return_value=True), \
                     patch.object(service, 'render_clip_to_mp4', side_effect=render), \
                     patch.object(service, 'generate_ass_file', side_effect=ass), \
                     patch.object(service, 'transcribe_clip_words') as transcribe, \
                     patch.object(service.subprocess, 'run'):
                    if merged:
                        await service.render_merged_batch_clips('unit', clips, settings, 'fixture')
                    else:
                        await service.render_single_batch_clip('unit', 0, clips[0], settings, 'fixture')
                    self.assertEqual(service.RENDER_BATCHES['unit']['clips'][0]['status'], 'completed')
                    transcribe.assert_not_called()
                self.assertAlmostEqual(captured[0]['start'], .02)
                self.assertAlmostEqual(captured[0]['end'], .07)
                if merged:
                    self.assertAlmostEqual(captured[1]['start'], 1.02)
                    self.assertAlmostEqual(captured[1]['end'], 1.07)


if __name__ == '__main__':
    unittest.main()
