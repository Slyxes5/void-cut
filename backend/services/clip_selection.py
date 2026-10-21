"""Bounded cached-analysis selections. No inference and no alternate render engine."""
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path
import re
import uuid

from backend.services.clip_automation import ClipJobStore, ClipJobError, PRESET, canonical_youtube_url
from backend.voidcut import build_plan, build_selected_request, InvalidSelection

TERMINAL = {'completed', 'completed_with_errors', 'failed', 'cancelled', 'interrupted'}


def safe_path(path):
    """Reject symlinks/junctions at every existing component, including ancestors."""
    path = Path(path).absolute()
    return not any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction())
                   for p in (path, *path.parents))


def invalid_selection(indices):
    return SelectionError(400, {'code': 'invalid_selection', 'invalid_indices': indices,
        'message': 'Select one to three eligible clips, or open the editor to adjust them.'})


class SelectionError(ClipJobError):
    def __init__(self, status_code, detail):
        super().__init__(status_code, detail)
        self.detail = detail


class SelectionStore(ClipJobStore):
    def __init__(self, root, manual, **kwargs):
        super().__init__(root, ttl_seconds=86400, **kwargs)
        self.manual = manual

    def _read(self, path):
        if not safe_path(path):
            raise ClipJobError(503, 'Selection storage requires attention on the laptop.')
        return super()._read(path)

    def _atomic(self, path, value):
        if not safe_path(path):
            raise ClipJobError(503, 'Selection storage requires attention on the laptop.')
        return super()._atomic(path, value)

    def reserve(self, url, delivery_id, telegram):
        source = canonical_youtube_url(url)
        from backend.routers.clip_automation import TelegramRoute
        try:
            route = TelegramRoute.model_validate(telegram).model_dump()
            if route != telegram or not re.fullmatch(r'[0-9a-f]{64}', delivery_id):
                raise ValueError()
        except ValueError:
            raise ClipJobError(400, 'A valid Telegram delivery identity and route are required.') from None
        with self.lock:
            now = self.clock()
            jobs = self._jobs()
            identity = hashlib.sha256(delivery_id.encode()).hexdigest()
            for job in jobs:
                if job['delivery_hash'] == identity:
                    if job['source_url'] != source or job['telegram'] != route:
                        raise ClipJobError(409, 'This delivery was already used for another selection.')
                    return self.get(job['job_id']), True
            for job in jobs:
                if job['expires_at'] <= now and job['state'] != 'rendering':
                    self._path(job['job_id']).unlink(missing_ok=True)
            jobs = self._jobs()
            if len(jobs) >= self.max_jobs:
                raise ClipJobError(429, 'Selection storage is full. Wait for existing selections to expire.')
            with self.manual.lock:
                cached = sorted((j for j in self.manual._jobs() if j['state'] == 'completed'
                    and j['expires_at'] > now and j['source_url'] == source and j['preset'] == PRESET),
                    key=lambda j: j['created_at'], reverse=True)
            if not cached:
                raise SelectionError(409, {'code': 'analysis_required',
                    'message': 'No completed cached analysis is available. Run /clip URL first.'})
            original = cached[0]
            summaries = []
            for index, clip in enumerate(original['analysis']['clips']):
                single = dict(original['analysis'], clips=[clip])
                plan = build_plan(single)
                summaries.append({'index': index, 'title': clip['title'][:240],
                    'start_time': clip['start_time'], 'end_time': clip['end_time'],
                    'score': clip['virality_score'], 'eligible': bool(plan['render_request']),
                    'reason': plan['rejected'][0]['reason'] if plan['rejected'] else None})
            job = {'job_id': uuid.uuid4().hex, 'editor_job_id': original['job_id'],
                'source_url': source, 'telegram': route, 'delivery_hash': identity,
                'created_at': now, 'expires_at': now + self.ttl, 'state': 'selecting',
                'revision': 0, 'selected_indices': [], 'menu_message_id': None,
                'notification_state': 'pending', 'clips': summaries, 'files': []}
            self._atomic(self._path(job['job_id']), job)
            return job, False

    def get(self, job_id):
        with self.lock:
            job = self._read(self._path(job_id))
            if not job:
                raise ClipJobError(404, 'Selection not found.')
            if job['expires_at'] <= self.clock():
                raise ClipJobError(410, 'Selection and downloads have expired. Open the editor for further work.')
            return job

    def selected_request(self, job):
        try:
            cached = self.manual.get(job['editor_job_id'])
            if (cached['state'] != 'completed' or cached['preset'] != PRESET
                    or cached['source_url'] != job['source_url']):
                raise ValueError()
            request = build_selected_request(cached['analysis'], job['selected_indices'])
            if job.get('subtitles_enabled', True) is False:
                # caption_style is the existing renderer's effective off-switch.
                request.settings.caption_style = 'none'
                request.settings.subtitles_enabled = False
            return request
        except InvalidSelection as exc:
            raise invalid_selection(exc.invalid_indices) from None
        except (ValueError, ClipJobError):
            raise invalid_selection(job['selected_indices']) from None

    def action(self, job_id, action, revision, telegram, menu_message_id, index=None):
        with self.lock:
            job = self.get(job_id)
            if telegram != job['telegram']:
                raise ClipJobError(403, 'This selection belongs to another Telegram delivery.')
            if type(revision) is not int or revision < 0 or not isinstance(menu_message_id, str) or not re.fullmatch(r'[0-9]{1,24}', menu_message_id):
                raise ClipJobError(400, 'A valid revision and menu message are required.')
            if action != 'bind' and (job['menu_message_id'] is None or menu_message_id != job['menu_message_id']):
                raise ClipJobError(403, 'This callback is not bound to the selection menu.')
            if action == 'confirm' and job.get('render_requested'):
                if revision not in {job['confirm_revision'], job['revision']}:
                    raise ClipJobError(409, 'Selection changed. Refresh the menu.')
                return job
            if revision != job['revision']:
                raise ClipJobError(409, 'Selection changed. Refresh the menu.')
            if job['state'] != 'selecting':
                raise ClipJobError(409, 'Selection is no longer editable. Open the editor for further work.')
            if action == 'bind':
                if job['menu_message_id'] not in {None, menu_message_id}:
                    raise ClipJobError(409, 'Selection menu is already bound.')
                job['menu_message_id'] = menu_message_id
            elif action == 'toggle':
                if type(index) is not int or not 0 <= index < len(job['clips']) or not job['clips'][index]['eligible']:
                    raise invalid_selection([index] if type(index) is int else [])
                selected = job['selected_indices']
                if index in selected:
                    selected.remove(index)
                elif len(selected) >= 3:
                    raise invalid_selection(selected + [index])
                else:
                    selected.append(index)
                job['revision'] += 1
            elif action == 'subtitles':
                if type(index) is not int or index not in (0, 1):
                    raise ClipJobError(400, 'Subtitle setting must be off or on.')
                job.update(subtitles_enabled=bool(index), revision=revision + 1)
            elif action in {'select_all', 'clear'}:
                selected = [clip['index'] for clip in job['clips'] if clip['eligible']] if action == 'select_all' else []
                if action == 'select_all' and not 1 <= len(selected) <= 3:
                    raise invalid_selection(selected)
                job.update(selected_indices=selected, revision=revision + 1)
            elif action == 'confirm':
                self.selected_request(job)  # All-or-nothing cached preflight, no network.
                if sum(j['state'] == 'rendering' for j in self._jobs()) >= self.max_active:
                    raise ClipJobError(429, 'The selected-render queue is full. Wait for existing jobs to finish.')
                job.update(state='rendering', render_requested=True, render_request_id=str(uuid.uuid4()),
                           expires_at=self.clock() + self.ttl, confirm_revision=revision, revision=revision + 1)
            elif action == 'cancel':
                job.update(state='cancelled', revision=revision + 1, finished_at=self.clock())
            else:
                raise ClipJobError(400, 'Unknown selection action.')
            self._atomic(self._path(job_id), job)
            return job

    def recover_interrupted(self):
        with self.lock:
            for job in self._jobs():
                if job['state'] == 'rendering':
                    job.update(state='interrupted', finished_at=self.clock(),
                        error='Rendering was interrupted by a backend restart. Open the editor; no automatic retry was performed.')
                    self._atomic(self._path(job['job_id']), job)

    def notification(self, job_id, action, message_id=None):
        with self.lock:
            job = self.get(job_id)
            if action == 'claim':
                claimed = job['state'] in TERMINAL and job['notification_state'] == 'pending'
                if claimed:
                    self._change(job_id, {'notification_state': 'claimed'})
                return {'claimed': claimed}
            if action not in {'sent', 'failed'} or job['notification_state'] != 'claimed':
                raise ClipJobError(409, 'Notification attempt is not claimable.')
            self._change(job_id, {'notification_state': action, 'notification_message_id': message_id})
            return {'state': action}


def verify_media(path, duration):
    """Real local ffprobe plus full decode; never a claim of dialogue/caption quality."""
    from backend.voidcut import check_media_probe
    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
        '-of', 'json', str(path)], check=True, capture_output=True, text=True, timeout=60)
    verdict = check_media_probe(json.loads(probe.stdout), expected_duration=duration)
    if not verdict['structural_passed']:
        raise ValueError('Structural media verification failed')
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-i', str(path),
        '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'],
        check=True, capture_output=True, timeout=180)
    return verdict


def selection_status(job):
    fields = ('job_id', 'editor_job_id', 'source_url', 'telegram', 'state', 'revision',
        'selected_indices', 'menu_message_id', 'notification_state', 'clips', 'created_at',
        'expires_at', 'finished_at', 'batch_id', 'error', 'files', 'zip_url', 'failures', 'subtitles_enabled')
    return {key: job[key] for key in fields if key in job}


class SelectionManager:
    def __init__(self, store, results_root, *, start_render=None, batch_get=None, exports_root=None, validate=None,
                 max_result_bytes=2 * 1024 ** 3, max_results=20):
        self.store = store
        self.results_root = Path(results_root)
        self.start_render = start_render
        self.batch_get = batch_get
        self.exports_root = Path(exports_root) if exports_root is not None else None
        self.validate = validate
        self.max_result_bytes = max_result_bytes
        self.max_results = max_results
        self.tasks = set()
        self.semaphore = asyncio.Semaphore(1)

    def start(self):
        self.store.recover_interrupted()

    def submit(self, url, delivery_id, telegram):
        with self.store.lock:
            for job in self.store._jobs():
                if job['expires_at'] <= self.store.clock() and job['state'] != 'rendering':
                    directory = self.results_root / job['job_id']
                    if directory.exists() and safe_path(directory):
                        shutil.rmtree(directory)
            job, reused = self.store.reserve(url, delivery_id, telegram)
            return {**selection_status(job), 'reused': reused}

    def status(self, job_id):
        return selection_status(self.store.get(job_id))

    def action(self, job_id, action, revision, telegram, menu_message_id, index=None):
        # Intent reservation and task creation have no await between them.
        with self.store.lock:
            before = self.store.get(job_id)
            job = self.store.action(job_id, action, revision, telegram, menu_message_id, index)
            if action == 'confirm' and not before.get('render_requested'):
                task = asyncio.create_task(self._run(job_id))
                self.tasks.add(task)
                task.add_done_callback(self._done)
            return selection_status(job)

    def _done(self, task):
        self.tasks.discard(task)
        if not task.cancelled():
            task.exception()

    async def wait_idle(self):
        while self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)

    def _finish_failure(self, job_id, state, message):
        # Expired queued work still needs a terminal record so it can be pruned.
        # Never extend an expired URL or erase already pinned partial outputs.
        try:
            with self.store.lock:
                job = self.store._read(self.store._path(job_id))
                if job:
                    job.update(state=state, finished_at=self.store.clock(), error=message)
                    self.store._atomic(self.store._path(job_id), job)
        except (OSError, ClipJobError):
            pass  # Storage fails closed; restart recovery remains available.

    async def _run(self, job_id):
        try:
            async with self.semaphore:
                await asyncio.wait_for(self._render(job_id), timeout=7200)
        except asyncio.CancelledError:
            self._finish_failure(job_id, 'interrupted', 'Rendering was interrupted. Open the editor; no automatic retry was performed.')
            raise
        except Exception:
            self._finish_failure(job_id, 'failed', 'Rendering or output verification failed. Open the editor to inspect or retry manually.')

    async def _render(self, job_id):
        from fastapi import BackgroundTasks
        from backend.routers.render import start_batch_render
        from backend.services.render_service import RENDER_BATCHES
        from backend.config import EXPORTS_DIR
        job = self.store.get(job_id)
        request = self.store.selected_request(job)
        bg = BackgroundTasks()
        accepted = await (self.start_render or start_batch_render)(request, bg, idempotency_key=job['render_request_id'])
        batch_id = accepted['batch_id']
        if not re.fullmatch(r'batch_[0-9]+_[0-9a-f]{6}', batch_id):
            raise ValueError('Invalid internal batch identity')
        self.store._change(job_id, {'batch_id': batch_id})
        await bg()  # The existing router's actual callable, not a second render implementation.
        get_batch = self.batch_get or RENDER_BATCHES.get
        batch = get_batch(batch_id)
        while batch and batch.get('overall_status') == 'running':
            await asyncio.sleep(0.25)  # Existing router may reuse an already-active identical batch.
            batch = get_batch(batch_id)
        if not batch:
            raise ValueError('Missing internal batch')
        root = self.exports_root or Path(EXPORTS_DIR)
        files, failures = [], []
        for output_index, source_index in enumerate(job['selected_indices']):
            clip = batch['clips'][output_index]
            try:
                if clip.get('status') != 'completed':
                    raise ValueError('Render failed')
                source = root / f'clip_{output_index + 1}_{batch_id}.mp4'
                duration = request.clips[output_index]['end_time'] - request.clips[output_index]['start_time']
                await asyncio.to_thread(self._pin, job_id, source, output_index + 1, duration)
                name = f'clip_{output_index + 1}.mp4'
                files.append({'name': name, 'source_index': source_index,
                    'download_url': f'/api/clip/selections/{job_id}/files/{name}'})
                self.store._change(job_id, {'files': files})
            except Exception:
                failures.append({'index': source_index, 'message': 'Clip rendering or structural verification failed. Open the editor for review.'})
        changes = {'files': files, 'failures': failures, 'finished_at': self.store.clock(),
            'state': ('completed_with_errors' if failures else 'completed') if files else 'failed'}
        if files:
            try:
                await asyncio.to_thread(self._zip, job_id, files)
                changes['zip_url'] = f'/api/clip/selections/{job_id}/files/clips.zip'
            except Exception:
                changes.update(state='completed_with_errors', error='ZIP creation failed. Verified individual clips remain available.')
        self.store._change(job_id, changes)

    def _quota(self, job_id, added_bytes):
        if not safe_path(self.results_root):
            raise ValueError('Unsafe result storage')
        directories = [p for p in self.results_root.iterdir() if p.is_dir()] if self.results_root.exists() else []
        if not (self.results_root / job_id).exists() and len(directories) >= self.max_results:
            raise ClipJobError(429, 'Selection result storage is full. Existing unexpired results are preserved.')
        used = sum(p.stat().st_size for p in self.results_root.rglob('*') if p.is_file()) if self.results_root.exists() else 0
        if used + added_bytes > self.max_result_bytes:
            raise ClipJobError(429, 'Selection result quota reached. Existing unexpired results are preserved.')

    def _pin(self, job_id, source, number, duration):
        if not safe_path(source) or not source.is_file():
            raise ValueError('Unsafe or missing rendered output')
        self._quota(job_id, source.stat().st_size)
        directory = self.results_root / job_id
        if not safe_path(directory):
            raise ValueError('Unsafe result directory')
        directory.mkdir(parents=True, exist_ok=True)
        stage = directory / ('.' + uuid.uuid4().hex + '.tmp')
        try:
            shutil.copyfile(source, stage)
            verdict = self.validate(stage, duration) if self.validate else verify_media(stage, duration)
            if not verdict.get('structural_passed'):
                raise ValueError('Structural QC failed')
            os.replace(stage, directory / f'clip_{number}.mp4')
        finally:
            stage.unlink(missing_ok=True)

    def _zip(self, job_id, files):
        directory = self.results_root / job_id
        # Stored ZIP: exact payload sizes plus bounded headers, no double-copy surprise.
        self._quota(job_id, sum((directory / item['name']).stat().st_size for item in files) + 512)
        stage = directory / ('.' + uuid.uuid4().hex + '.tmp')
        try:
            with zipfile.ZipFile(stage, 'w', compression=zipfile.ZIP_STORED) as archive:
                for item in files:
                    archive.write(directory / item['name'], arcname=item['name'])
            os.replace(stage, directory / 'clips.zip')
        finally:
            stage.unlink(missing_ok=True)

    def file(self, job_id, name):
        job = self.store.get(job_id)
        allowed = {item['name'] for item in job['files']}
        if job.get('zip_url'):
            allowed.add('clips.zip')
        path = self.results_root / job_id / name
        if (name not in allowed or not re.fullmatch(r'(?:clip_[1-3]\.mp4|clips\.zip)', name)
                or not safe_path(path) or not path.is_file()):
            raise ClipJobError(404, 'Selection result not found.')
        return path
