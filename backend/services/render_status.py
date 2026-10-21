"""Read-only, file-verified public views of process-local render state."""
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
import re
import zipfile

from backend.config import EXPORTS_DIR
from backend.services.render_service import BATCH_REQUESTS, RENDER_BATCHES

BATCH_FIELDS = ('batch_id', 'request_id', 'total_clips', 'current_clip_index', 'overall_status',
                'is_merged', 'merged_segments_count', 'error_message', 'warning_message',
                'stage', 'progress_percent', 'recovered_from_files')
CLIP_FIELDS = ('clip_index', 'title', 'base_title', 'status', 'progress_percent', 'is_merged',
               'segments_count', 'error', 'error_message', 'stage')
BATCH_ID = re.compile(r'batch_([0-9]+)_[0-9a-f]{6}')
OUTPUT_NAME = re.compile(
    r'(?:clip_([1-9][0-9]*)|compilation_([^\\/*?:"<>|\x00-\x1f]{1,40}))_'
    r'(batch_[0-9]+_[0-9a-f]{6})\.mp4'
)


# Existing raw downloads share the download route, not render discovery.
RAW_DOWNLOAD_NAME = re.compile(
    r'[a-zA-Z0-9_-]+(?:_raw_[0-9]+|_clip_[0-9]+_[0-9]+_[0-9]+_raw)\.mp4'
)


def safe_export(name):
    """Only direct nonempty regular files; never follow export symlinks."""
    if not isinstance(name, str) or not name or any(c in name for c in '/\\:\x00') or name in ('.', '..'):
        return None
    try:
        root = Path(EXPORTS_DIR).resolve(strict=True)
        path = root / name
        if path.is_symlink() or path.resolve(strict=True).parent != root:
            return None
        if path.is_file() and path.stat().st_size > 0:
            return path
    except (OSError, ValueError, RuntimeError):
        pass
    return None


def clip_file(clip):
    output = clip.get('output_path')
    if output:
        try:
            path = Path(output)
            verified = safe_export(path.name) if OUTPUT_NAME.fullmatch(path.name) else None
            if verified and path.resolve(strict=True) == verified.resolve(strict=True) and not path.is_symlink():
                return verified
        except (OSError, ValueError, RuntimeError):
            pass
        return None
    url = clip.get('download_url', '')
    if isinstance(url, str) and url.startswith('/api/download-rendered/'):
        name = unquote(urlsplit(url).path[len('/api/download-rendered/'):])
        return safe_export(name) if OUTPUT_NAME.fullmatch(name) else None
    return None


def finalized_zip(batch_id):
    path = safe_export('cheat_clip_pro_' + batch_id + '.zip')
    if path:
        try:
            with zipfile.ZipFile(path) as archive:
                if archive.infolist():
                    return path
        except (OSError, zipfile.BadZipFile, ValueError):
            pass
    return None


def discovered_outputs():
    """Discover pipeline outputs only; never infer jobs, requests or missing clips."""
    groups = {}
    try:
        for entry in Path(EXPORTS_DIR).iterdir():
            match = OUTPUT_NAME.fullmatch(entry.name)
            if match and safe_export(entry.name):
                groups.setdefault(match[3], []).append((entry.name, match[1], match[2]))
    except OSError:
        pass
    return groups


def recovered_batch(batch_id, outputs):
    clips = []
    for name, number, title in sorted(outputs, key=lambda item: (int(item[1] or 0), item[0])):
        clips.append({'clip_index': len(clips), 'title': title.replace('_', ' ') if title else f'Clip {number}',
                      'status': 'completed', 'progress_percent': 100, 'is_merged': title is not None,
                      'download_url': '/api/download-rendered/' + quote(name)})
    return {'batch_id': batch_id, 'overall_status': 'completed', 'total_clips': len(clips),
            'current_clip_index': len(clips), 'clips': clips, 'recovered_from_files': True,
            'warning_message': 'Only available exports were recovered. The original job and total clip count are unknown.'}


def recent_batches():
    outputs = discovered_outputs()
    ids = set(RENDER_BATCHES) | set(outputs)
    def order(batch_id):
        match = BATCH_ID.fullmatch(batch_id)
        return (int(match[1]) if match else 0, batch_id)
    return [batch_snapshot(batch_id, outputs) for batch_id in sorted(ids, key=order, reverse=True)[:10]]


def batch_snapshot(batch_id, outputs=None):
    batch = RENDER_BATCHES.get(batch_id)
    if batch is None:
        if not BATCH_ID.fullmatch(batch_id):
            return None
        available = (outputs if outputs is not None else discovered_outputs()).get(batch_id)
        if not available:
            return None
        batch = recovered_batch(batch_id, available)
    # Workers may include downloader diagnostics. Never echo supplied cookies there.
    cookies = getattr(BATCH_REQUESTS.get(batch_id), 'cookies', None)
    secrets = [cookies] if isinstance(cookies, str) and cookies else []
    if cookies:
        secrets.extend(line.split('\t')[-1] for line in cookies.splitlines()
                       if '\t' in line and line.split('\t')[-1])
    def public_fields(source, fields):
        public = {}
        for key in fields:
            if key not in source:
                continue
            value = source[key]
            if isinstance(value, str) and key in ('title', 'base_title', 'error', 'error_message', 'warning_message'):
                for secret in secrets:
                    value = value.replace(secret, '[redacted]')
            if value is None or isinstance(value, (str, int, float, bool)):
                public[key] = value
        return public
    result = public_fields(batch, BATCH_FIELDS)
    result.setdefault('request_id', None)
    result['can_retry'] = batch_id in BATCH_REQUESTS and batch.get('overall_status') != 'running'
    result['clips'] = []
    for clip in batch.get('clips', []):
        public = public_fields(clip, CLIP_FIELDS)
        path = clip_file(clip) if clip.get('status') == 'completed' else None
        public['download_available'] = path is not None
        if path:
            public['download_url'] = '/api/download-rendered/' + quote(path.name)
        result['clips'].append(public)
    result['zip_available'] = batch.get('overall_status') != 'running' and finalized_zip(batch_id) is not None
    if result['zip_available']:
        result['zip_url'] = '/api/download-batch-zip/' + quote(batch_id)
    return result
