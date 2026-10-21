"""Offline, opt-in Voidcut preparation. Not imported by app routes or Telegram handlers."""
import json
import math
import re
from pathlib import Path
from backend.schemas.analyze import AnalyzeResponse
from backend.schemas.render import RenderBatchRequest, RenderSettingsModel


def load_preset():
    path = Path(__file__).with_name('presets') / 'voidcut-auto-edit-v1.json'
    preset = json.loads(path.read_text(encoding='utf-8'))
    RenderSettingsModel.model_validate(preset['render_settings'])
    return preset


def build_plan(analysis):
    """Prepare a schema-compatible request from cached analysis, never render or infer.

    Scores are ranking hints, not evidence that a moment is good. Content checks
    remain blocking; invalid duration never earns an automatic mid-dialogue cut.
    """
    preset = load_preset()
    data = AnalyzeResponse.model_validate(analysis)
    if (data.source_type != 'youtube' or not re.fullmatch(r'[A-Za-z0-9_-]{11}', data.video_id)
            or not math.isfinite(data.duration) or data.duration <= 0):
        raise ValueError('A valid completed YouTube analysis is required.')
    policy = preset['selection']
    selected, rejected = [], []
    ranked = sorted(enumerate(data.clips), key=lambda pair: (-pair[1].virality_score, pair[0]))
    for index, clip in ranked:
        reason = None
        start, end, hook = clip.start_time, clip.end_time, clip.hook_time
        if not all(math.isfinite(v) for v in (start, end, hook)) or not (0 <= start < end <= data.duration):
            reason = 'invalid_source_bounds'
        elif not policy['min_seconds'] <= end - start <= policy['max_seconds']:
            reason = 'outside_15_to_30_seconds_no_automatic_truncation'
        elif not start <= hook <= end:
            reason = 'hook_outside_clip'
        elif not policy['min_score'] <= clip.virality_score <= 100:
            reason = 'score_below_policy_or_invalid'
        elif not clip.transcript.strip() or not clip.key_quotes or not all(q.strip() for q in clip.key_quotes):
            reason = 'missing_dialogue_evidence'
        elif any(start < prior.end_time and end > prior.start_time for _, prior in selected):
            reason = 'overlaps_selected_clip'
        elif len(selected) >= policy['max_clips']:
            reason = 'per_request_budget'
        if reason:
            rejected.append({'index': index, 'reason': reason})
        else:
            selected.append((index, clip))
    request = None
    if selected:
        request = RenderBatchRequest(
            video_url='https://www.youtube.com/watch?v=' + data.video_id,
            video_id=data.video_id,
            clips=[clip.model_dump(mode='json') for _, clip in selected],
            settings=RenderSettingsModel.model_validate(preset['render_settings']),
            transcript=[line.model_dump(mode='json', exclude_none=True) for line in data.transcript] if data.transcript else None,
        ).model_dump(mode='json', exclude_none=True)
    return {
        'preset_id': preset['id'], 'production_enabled': False, 'ready_for_production': False,
        'selection_status': 'prepared_for_review' if selected else 'manual_review_required',
        'selected_indices': [index for index, _ in selected], 'rejected': rejected,
        'render_request': request, 'output_canvas': preset['output_canvas'],
        'pending_content_checks': preset['required_content_checks'],
        'pending_media_checks': preset['required_media_checks'],
        'timing_status': 'requires_acoustic_alignment_and_review',
    }


class InvalidSelection(ValueError):
    def __init__(self, indices):
        super().__init__('Select one to three eligible clips, or open the editor to adjust them.')
        self.invalid_indices = indices


def build_selected_request(analysis, indices):
    """Explicit selection only: preserve order and never rerank, truncate or trim.

    Validate each candidate with the existing one-clip policy, lifting only the
    automatic per-request budget. Overlapping explicit choices remain explicit.
    """
    if (not isinstance(indices, list) or not 1 <= len(indices) <= 3
            or any(type(i) is not int for i in indices) or len(set(indices)) != len(indices)):
        raise InvalidSelection([i for i in indices if type(i) is int and i >= 0] if isinstance(indices, list) else [])
    data = AnalyzeResponse.model_validate(analysis).model_dump(mode='json')
    invalid, clips, first = [], [], None
    for index in indices:
        if index < 0 or index >= len(data['clips']):
            invalid.append(index)
            continue
        plan = build_plan(dict(data, clips=[data['clips'][index]]))
        if plan['render_request'] is None:
            invalid.append(index)
        else:
            first = first or plan['render_request']
            clips.append(plan['render_request']['clips'][0])
    if invalid:
        raise InvalidSelection(invalid)
    first['clips'] = clips
    first['settings']['force_square_pixels'] = True
    first['settings']['render_mode'] = 'separate'
    return RenderBatchRequest.model_validate(first)


def check_media_probe(probe, expected_duration=None):
    """Check supplied ffprobe JSON only; headers never prove subtitle/dialogue quality."""
    def number(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return float('nan')
    preset = load_preset()
    streams = probe.get('streams', [])
    video = next((s for s in streams if s.get('codec_type') == 'video'), {})
    audio = next((s for s in streams if s.get('codec_type') == 'audio'), {})
    info = probe.get('format', {})
    duration = number(info.get('duration'))
    vstart, astart = number(video.get('start_time')), number(audio.get('start_time'))
    checks = {
        'mp4_container': 'mp4' in str(info.get('format_name', '')).split(','),
        'nonempty': number(info.get('size')) > 0,
        'portrait_1080x1920': video.get('width') == 1080 and video.get('height') == 1920,
        'h264': video.get('codec_name') == 'h264',
        'yuv420p': video.get('pix_fmt') == 'yuv420p',
        'square_pixels': video.get('sample_aspect_ratio') == '1:1',
        'aac_audio': audio.get('codec_name') == 'aac',
        'duration': math.isfinite(duration) and 15 <= duration <= 30,
        'av_start_alignment': all(math.isfinite(v) for v in (vstart, astart)) and abs(vstart - astart) <= 0.1,
    }
    if expected_duration is not None:
        expected = number(expected_duration)
        checks['expected_duration'] = math.isfinite(expected) and math.isfinite(duration) and abs(duration - expected) <= 0.15
    failures = [name for name, passed in checks.items() if not passed]
    return {'structural_passed': not failures, 'checks': checks, 'failures': failures,
            'ready_for_production': False, 'pending_content_checks': preset['required_content_checks'],
            'pending_media_checks': ['decode_integrity', 'faststart']}
