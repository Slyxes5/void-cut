"""Opt-in real-media integration check; never part of unittest discovery.
Run from project cwd with venv Python, -B -E. All inputs are authored local
lavfi media. Only download/ASR boundaries are replaced, not renderer or QC.
No main app/lifespan or live stores are imported. No external network allowed.
"""
import asyncio
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('--subtitles-off', action='store_true')
SUBTITLES_OFF = parser.parse_args().subtitles_off
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
from unittest.mock import patch
import zipfile

SCRATCH = Path('C:/Users/vrandly/AppData/Local/hermes/cache/scratch')
ROOT = Path(tempfile.mkdtemp(prefix='selected-real-fixture-', dir=SCRATCH))
os.environ.update(GEMINI_API_KEY='', ADMIN_API_KEY='', CHEAT_CLIP_API_KEY='',
                  TMPDIR=str(SCRATCH), LOCALAPPDATA=str(ROOT / 'isolated-appdata'))
_original_connect = socket.socket.connect

def local_only(sock, address):
    if isinstance(address, tuple) and address[0] in {'127.0.0.1', '::1'}:
        return _original_connect(sock, address)  # asyncio socketpair/TestClient only
    raise AssertionError('External network forbidden in authored-fixture verification')

socket.socket.connect = local_only
from backend import config, video_engine
from backend.services import render_service as service
from backend.routers import render as renderer
from backend.services.clip_automation import ClipJobStore
from backend.services.clip_selection import SelectionStore, SelectionManager, verify_media

exports, temp = ROOT / 'exports', ROOT / 'temp'
exports.mkdir(); temp.mkdir()
for module in (config, video_engine, service):
    setattr(module, 'EXPORTS_DIR', exports)
    setattr(module, 'TEMP_DIR', temp)
source = ROOT / 'authored-source.mp4'
subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
    'testsrc2=size=640x360:rate=25:duration=48', '-f', 'lavfi', '-i',
    'sine=frequency=440:sample_rate=48000:duration=48', '-c:v', 'libx264',
    '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest',
    '-movflags', '+faststart', str(source)], check=True, timeout=90)

URL = 'https://www.youtube.com/watch?v=fixture1234'  # Fictional; never fetched.
ROUTE = {'chat_id': '100', 'user_id': '100', 'thread_id': None, 'message_id': '300'}
ranges = [(0, 16), (16, 31), (31, 48)]
analysis = {'video_id': 'fixture1234', 'source_type': 'youtube', 'title': 'Authored fixture, not YouTube content',
    'duration': 48, 'heatmap': [], 'summary': 'Synthetic colour pattern and sine audio, not natural speech.',
    'clips': [{'title': 'Authored clip ' + str(i), 'start_time': start, 'end_time': end,
        'hook_time': start + 1, 'virality_score': 90, 'key_quotes': ['Authored caption'],
        'transcript': 'Authored caption, not ASR evidence.'} for i, (start, end) in enumerate(ranges)]}
manual = ClipJobStore(ROOT / 'handoffs')
original, _ = manual.reserve(URL, 'a' * 64, ROUTE)
manual.complete(original['job_id'], analysis)
before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in manual.root.iterdir()}
store = SelectionStore(ROOT / 'selections', manual)
submitted, sliced, settings_seen = [], [], []

async def counted_start(request, background_tasks, **kwargs):
    submitted.append(request)
    return await renderer.start_batch_render(request, background_tasks, **kwargs)

def local_slice(url, start, end, filename, cookies=None):
    assert url == URL and not cookies
    sliced.append([start, end])
    path = temp / filename
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-ss', str(start), '-i', str(source),
        '-t', str(end - start), '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-shortest', '-movflags', '+faststart', str(path)], check=True, timeout=60)
    return str(path)

def authored_captions(raw, transcript, start, end):
    # Authored fixture timing only, deliberately not a substitute for real ASR.
    return [{'word': 'Authored', 'start': 0.5, 'end': 1.1}, {'word': 'fixture', 'start': 1.2, 'end': 1.8}]

real_encode = service.render_clip_to_mp4

def recorded_encode(**kwargs):
    settings_seen.append({key: kwargs[key] for key in ('aspect_ratio', 'force_square_pixels',
        'original_audio_volume', 'bgm_enabled', 'hook_sfx_enabled', 'hardware_accel')})
    settings_seen[-1]['subtitle_path_present'] = bool(kwargs.get('ass_subtitles_path'))
    if SUBTITLES_OFF:
        assert kwargs.get('ass_subtitles_path') is None
    return real_encode(**kwargs)

manager = SelectionManager(store, ROOT / 'results', start_render=counted_start, exports_root=exports)

async def exercise():
    job = manager.submit(URL, 'b' * 64, ROUTE)
    identifier = job['job_id']
    manager.action(identifier, 'bind', 0, ROUTE, '400')
    manager.action(identifier, 'toggle', 0, ROUTE, '400', 2)
    manager.action(identifier, 'toggle', 1, ROUTE, '400', 0)
    assert not submitted and not manager.tasks, 'Selection must not render'
    revision = 2
    if SUBTITLES_OFF:
        manager.action(identifier, 'subtitles', revision, ROUTE, '400', 0)
        revision += 1
    manager.action(identifier, 'confirm', revision, ROUTE, '400')
    manager.action(identifier, 'confirm', revision, ROUTE, '400')
    await manager.wait_idle()
    job = manager.status(identifier)
    assert job['state'] == 'completed', job
    assert len(submitted) == 1 and sliced == [[31.0, 48.0], [0.0, 16.0]], sliced
    assert job['selected_indices'] == [2, 0] and len(job['files']) == 2
    request = submitted[0]
    assert request.settings.force_square_pixels and request.settings.original_audio_volume == 100
    assert not request.settings.bgm_enabled and not request.settings.hook_sfx_enabled and not request.settings.timeline_sfx
    assert request.settings.render_mode == 'separate'
    return job

with patch.object(service, 'download_clip_segment', side_effect=local_slice), \
     patch.object(service, 'transcribe_clip_words', side_effect=authored_captions) as asr, \
     patch.object(service, 'generate_ass_file', wraps=service.generate_ass_file) as captions, \
     patch.object(service, 'render_clip_to_mp4', side_effect=recorded_encode):
    job = asyncio.run(exercise())
    if SUBTITLES_OFF:
        asr.assert_not_called()
        captions.assert_not_called()
        assert not list(temp.glob('*.ass'))

assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in manual.root.iterdir()}
qc = []
for item, duration in zip(job['files'], [17, 16]):
    path = manager.file(job['job_id'], item['name'])
    verdict = verify_media(path, duration)
    atoms = []
    with path.open('rb') as stream:
        while stream.tell() < path.stat().st_size:
            header = stream.read(8)
            assert len(header) == 8
            size, name = struct.unpack('>I4s', header); header_size = 8
            if size == 1:
                size = struct.unpack('>Q', stream.read(8))[0]; header_size = 16
            assert size >= header_size
            atoms.append(name.decode('ascii', errors='replace'))
            stream.seek(size - header_size, 1)
    assert atoms.index('moov') < atoms.index('mdat')
    qc.append({'name': item['name'], 'source_index': item['source_index'], 'structural_qc': verdict,
               'full_decode': True, 'faststart': True, 'bytes': path.stat().st_size})

# Same real auth middleware and download router, isolated in-process ASGI app.
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.security import AuthenticationMiddleware, SecurityManager, LOCAL_COOKIE
from backend.routers.clip_selection import router
app = FastAPI()
security = SecurityManager(password_hash='')
app.state.clip_selections = manager
app.add_middleware(AuthenticationMiddleware, manager=security)
app.include_router(router)
status_codes = {}
with patch('backend.clip_credentials.load_automation_token', return_value='f' * 64):
    with TestClient(app, base_url='http://127.0.0.1:8000', client=('127.0.0.1', 54000)) as client:
        url = job['files'][0]['download_url']
        status_codes['unauthenticated_mp4'] = client.get(url).status_code
        status_codes['machine_key_mp4'] = client.get(url, headers={'x-clip-automation-key': 'f' * 64}).status_code
        assert status_codes['unauthenticated_mp4'] == status_codes['machine_key_mp4'] == 401
        token, _ = security.new_session()
        client.cookies.set(LOCAL_COOKIE, token)
        response = client.get(url)
        status_codes['session_mp4'] = response.status_code
        assert response.status_code == 200 and response.content == manager.file(job['job_id'], 'clip_1.mp4').read_bytes()
        response = client.get(url, headers={'Range': 'bytes=0-31'})
        status_codes['session_range'] = response.status_code
        assert response.status_code == 206 and len(response.content) == 32
        response = client.get(job['zip_url'])
        status_codes['session_zip'] = response.status_code
        assert response.status_code == 200
        with zipfile.ZipFile(manager.file(job['job_id'], 'clips.zip')) as archive:
            assert archive.namelist() == ['clip_1.mp4', 'clip_2.mp4'] and archive.testzip() is None
        security.sessions.clear()
        status_codes['revoked_session_mp4'] = client.get(url).status_code
        assert status_codes['revoked_session_mp4'] == 401

result = {'fixture': 'Authored 48-second test pattern + sine audio; authored caption timing, no natural speech.',
    'root': str(ROOT), 'render_submissions': len(submitted), 'confirmed_source_indices': [2, 0],
    'downloaded_local_ranges': sliced, 'encoder': video_engine.ACTIVE_ENCODER_NAME, 'render_settings': settings_seen,
    'manual_cache_unchanged': True, 'outputs': qc, 'download_checks': status_codes,
    'gemini_calls': 0, 'subtitles_off': SUBTITLES_OFF,
    'asr_calls': asr.call_count, 'ass_generator_calls': captions.call_count,
    'live_services_reloaded': False, 'production_activated': False,
    'limitations': ['Download and ASR boundaries replaced by authored local fixtures.',
        'No real Telegram inbound command or physical iPhone test.',
        'Does not certify Indonesian ASR, scene-aware framing, social overlay clearance or audio loudness.']}
(ROOT / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result, indent=2))
