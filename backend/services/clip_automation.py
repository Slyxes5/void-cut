"""Database-free, bounded clip handoffs; reuses the existing analysis stream."""
import re
import asyncio
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from backend.schemas.analyze import AnalyzeRequest, AnalyzeResponse
from backend.security import redact_secrets

PRESET = {'duration': '30s', 'model': 'gemini-3.8-flash', 'target_clip_count': 'auto',
          'range_start': None, 'range_end': None}
TERMINAL = {'completed', 'failed'}
ERRORS = {
    'analysis_failed': 'Analysis failed. Check the laptop/provider configuration before submitting a new command.',
    'interrupted': 'The backend restarted before completion. No automatic analysis retry was performed.',
    'timed_out': 'Analysis exceeded the two-hour limit. No automatic retry was performed.',
    'too_large': 'Analysis metadata exceeded the handoff size limit. The previous history was not changed.',
}


class ClipJobError(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code



def canonical_youtube_url(value: str) -> str:
    message = 'Provide a supported YouTube video URL (youtube.com or youtu.be).'
    if not isinstance(value, str) or not value or len(value) > 2048 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c == '\\' for c in value):
        raise ValueError(message)
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {'http', 'https'} or parsed.netloc.lower() not in
                {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be'}
                or parsed.username is not None or parsed.password is not None or parsed.port is not None):
            raise ValueError(message)
        if len(parse_qs(parsed.query, keep_blank_values=True).get('v', [])) > 1:
            raise ValueError(message)
        if parsed.hostname == 'youtu.be':
            matched = re.fullmatch(r'/([A-Za-z0-9_-]{11})/?', parsed.path)
            video_id = matched[1] if matched else ''
        elif parsed.path == '/watch':
            ids = parse_qs(parsed.query, keep_blank_values=True).get('v', [])
            video_id = ids[0] if len(ids) == 1 else ''
        else:
            matched = re.fullmatch(r'/(?:shorts|live|embed)/([A-Za-z0-9_-]{11})/?', parsed.path)
            video_id = matched[1] if matched else ''
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
            raise ValueError(message)
        return 'https://www.youtube.com/watch?v=' + video_id
    except (ValueError, TypeError):
        raise ValueError(message) from None


class ClipJobStore:
    """One backend process; atomic files plus bounded persistent delivery tombstones."""
    def __init__(self, root, clock=time.time, ttl_seconds=7 * 86400, max_jobs=50, max_deliveries=1000, max_active=5):
        self.root = Path(root)
        self.clock = clock
        self.ttl = ttl_seconds
        self.max_jobs = max_jobs
        self.max_deliveries = max_deliveries
        self.max_active = max_active
        self.lock = threading.RLock()
        self.max_bytes = 4 * 1024 * 1024

    def _path(self, job_id):
        if not re.fullmatch(r'[0-9a-f]{32}', str(job_id)):
            raise ClipJobError(404, 'Handoff not found.')
        return self.root / ('job-' + job_id + '.json')

    def _read(self, path):
        if not path.exists():
            return None
        if path.is_symlink() or path.stat().st_size > self.max_bytes:
            raise ClipJobError(503, 'Handoff storage requires attention on the laptop.')
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, OSError):
            raise ClipJobError(503, 'Handoff storage requires attention on the laptop.') from None

    def _atomic(self, path, value):
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
        if len(encoded) > self.max_bytes:
            raise ClipJobError(413, ERRORS['too_large'])
        self.root.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.clip-', suffix='.tmp', dir=self.root)
        try:
            with os.fdopen(fd, 'wb') as out:
                out.write(encoded)
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _jobs(self):
        if not self.root.exists():
            return []
        jobs = []
        for path in self.root.glob('job-*.json'):
            data = self._read(path)
            if data and path == self._path(data.get('job_id')):
                jobs.append(data)
        return jobs

    def _index(self):
        data = self._read(self.root / 'delivery-index.json') or {'version': 1, 'deliveries': {}}
        if data.get('version') != 1 or not isinstance(data.get('deliveries'), dict):
            raise ClipJobError(503, 'Delivery ledger requires attention on the laptop.')
        return data

    def reserve(self, url, delivery_id, telegram):
        source = canonical_youtube_url(url)
        required = {'chat_id', 'user_id', 'thread_id', 'message_id'}
        if (set(telegram) != required or not re.fullmatch(r'-?[0-9]{1,24}', str(telegram.get('chat_id', '')))
                or any(not re.fullmatch(r'[0-9]{1,24}', str(telegram.get(k, ''))) for k in ('user_id', 'message_id'))
                or (telegram.get('thread_id') is not None and not re.fullmatch(r'[0-9]{1,24}', str(telegram['thread_id'])))):
            raise ClipJobError(400, 'A valid Telegram delivery route is required.')
        if not re.fullmatch(r'[0-9a-f]{64}', delivery_id):
            raise ClipJobError(400, 'A valid Telegram delivery identity is required.')
        with self.lock:
            now = self.clock()
            index = self._index()
            index['deliveries'] = {k: v for k, v in index['deliveries'].items() if v['expires_at'] > now}
            delivery_hash = hashlib.sha256(delivery_id.encode()).hexdigest()
            route_hash = hashlib.sha256(json.dumps(telegram, sort_keys=True).encode()).hexdigest()
            existing = index['deliveries'].get(delivery_hash)
            if existing:
                if existing['source_url'] != source or existing['route_hash'] != route_hash:
                    raise ClipJobError(409, 'This Telegram delivery was already used for a different request.')
                return self.get(existing['job_id']), True
            jobs = self._jobs()
            for job in jobs:
                if job['expires_at'] <= now and job['state'] in TERMINAL:
                    self._path(job['job_id']).unlink(missing_ok=True)
            jobs = [j for j in jobs if j['expires_at'] > now or j['state'] not in TERMINAL]
            if sum(j['state'] not in TERMINAL for j in jobs) >= self.max_active:
                raise ClipJobError(429, 'The analysis queue is full. Wait for the existing jobs to finish.')
            if len(index['deliveries']) >= self.max_deliveries:
                raise ClipJobError(429, 'The retained delivery limit is reached. Existing jobs remain protected from duplicate analysis.')
            while len(jobs) >= self.max_jobs:
                terminal = sorted((j for j in jobs if j['state'] in TERMINAL), key=lambda j: j['created_at'])
                if not terminal:
                    raise ClipJobError(429, 'The handoff queue is full.')
                oldest = terminal[0]
                self._path(oldest['job_id']).unlink(missing_ok=True)
                jobs.remove(oldest)
            job = {'job_id': uuid.uuid4().hex, 'created_at': now, 'expires_at': now + self.ttl,
                   'source_url': source, 'preset': dict(PRESET), 'state': 'accepted', 'stage': 'Queued',
                   'telegram': dict(telegram), 'notification_state': 'pending'}
            index['deliveries'][delivery_hash] = {'job_id': job['job_id'], 'source_url': source,
                'route_hash': route_hash, 'expires_at': job['expires_at']}
            # Reserve delivery before recording a runnable job. A crash in between fails closed.
            self._atomic(self.root / 'delivery-index.json', index)
            self._atomic(self._path(job['job_id']), job)
            return job, False

    def get(self, job_id):
        with self.lock:
            job = self._read(self._path(job_id))
            if not job:
                # An evicted handoff can still have a tombstone; never rerun that delivery.
                if any(v['job_id'] == job_id for v in self._index()['deliveries'].values()):
                    raise ClipJobError(410, 'This handoff expired or was removed by bounded retention.')
                raise ClipJobError(404, 'Handoff not found.')
            if job['expires_at'] <= self.clock():
                raise ClipJobError(410, 'This handoff has expired. Existing browser history is unchanged.')
            return job

    def _change(self, job_id, changes):
        with self.lock:
            job = self.get(job_id)
            job.update(changes)
            self._atomic(self._path(job_id), job)
            return job

    def analyzing(self, job_id, stage='Analyzing'):
        return self._change(job_id, {'state': 'analyzing', 'stage': stage[:160], 'started_at': self.clock()})

    def progress(self, job_id, stage):
        current = self.get(job_id)
        if current['stage'] != stage[:160]:
            self._change(job_id, {'stage': stage[:160]})

    def complete(self, job_id, analysis):
        clean = AnalyzeResponse.model_validate(analysis).model_dump(mode='json')
        job = self.get(job_id)
        if clean['video_id'] != job['source_url'].split('v=')[1] or clean.get('source_type') != 'youtube':
            raise ClipJobError(502, 'Analysis did not match the submitted YouTube video.')
        clean = json.loads(redact_secrets(json.dumps(clean, ensure_ascii=False)))
        return self._change(job_id, {'state': 'completed', 'stage': 'Completed', 'finished_at': self.clock(), 'analysis': clean})

    def fail(self, job_id, reason='analysis_failed'):
        return self._change(job_id, {'state': 'failed', 'stage': 'Failed', 'finished_at': self.clock(),
                                     'error': ERRORS.get(reason, ERRORS['analysis_failed'])})

    def recover_interrupted(self):
        with self.lock:
            for job in self._jobs():
                if job['expires_at'] <= self.clock():
                    self._path(job['job_id']).unlink(missing_ok=True)
                elif job['state'] not in TERMINAL:
                    self.fail(job['job_id'], 'interrupted')

    def pending_notifications(self):
        with self.lock:
            return [j for j in self._jobs() if j['expires_at'] > self.clock() and j.get('notification_state') == 'pending']

    def notification(self, job_id, action, message_id=None):
        with self.lock:
            job = self.get(job_id)
            if action == 'claim':
                claimed = job['state'] in TERMINAL and job.get('notification_state') == 'pending'
                if claimed:
                    self._change(job_id, {'notification_state': 'claimed'})
                return {'claimed': claimed}
            if action not in {'sent', 'failed'} or job.get('notification_state') != 'claimed':
                raise ClipJobError(409, 'Notification attempt is not claimable.')
            self._change(job_id, {'notification_state': action, 'notification_message_id': message_id})
            return {'state': action}


def public_job(job):
    fields = ('job_id', 'state', 'stage', 'created_at', 'started_at', 'finished_at', 'expires_at',
              'source_url', 'telegram', 'error', 'notification_state')
    response = {key: job[key] for key in fields if key in job}
    if job['state'] == 'completed':
        response['title'] = job['analysis']['title']
        response['clip_count'] = len(job['analysis']['clips'])
    return response


class ClipJobManager:
    def __init__(self, store, analyze):
        self.store = store
        self.analyze = analyze
        self.tasks = set()
        self.semaphore = asyncio.Semaphore(1)

    def start(self):
        self.store.recover_interrupted()

    def submit(self, url, delivery_id, telegram):
        key = (os.environ.get('GEMINI_API_KEY') or '').strip()
        if not key or key.lower() == 'mock':
            raise ClipJobError(503, 'Configure a real backend GEMINI_API_KEY before using /clip.')
        job, reused = self.store.reserve(url, delivery_id, telegram)
        if not reused:
            task = asyncio.create_task(self._run(job['job_id']))
            self.tasks.add(task)
            task.add_done_callback(self._done)
        return {**public_job(job), 'reused': reused}

    def _done(self, task):
        self.tasks.discard(task)
        if not task.cancelled():
            task.exception()  # Retrieve failures; never print raw provider exceptions.

    async def _run(self, job_id):
        try:
            async with self.semaphore:
                self.store.analyzing(job_id)
                await asyncio.wait_for(self._consume(job_id), timeout=7200)
        except asyncio.CancelledError:
            self._safe_fail(job_id, 'interrupted')
            raise
        except asyncio.TimeoutError:
            self._safe_fail(job_id, 'timed_out')
        except ClipJobError as exc:
            self._safe_fail(job_id, 'too_large' if exc.status_code == 413 else 'analysis_failed')
        except Exception:
            self._safe_fail(job_id, 'analysis_failed')

    def _safe_fail(self, job_id, reason):
        try:
            self.store.fail(job_id, reason)
        except (OSError, ClipJobError):
            # Keep the delivery reservation; storage errors must never cause automatic re-analysis.
            logging.getLogger('void-cut').error('Clip handoff could not be updated; check private storage on the laptop.')

    async def _consume(self, job_id):
        job = self.store.get(job_id)
        request = AnalyzeRequest(url=job['source_url'], **job['preset'])
        response = await self.analyze(request)  # The existing router owns all Gemini/transcript logic.
        buffer = ''
        async for chunk in response.body_iterator:
            buffer += chunk.decode('utf-8') if isinstance(chunk, bytes) else str(chunk)
            buffer = buffer.replace('\r\n', '\n')
            if len(buffer.encode('utf-8')) > self.store.max_bytes:
                raise ClipJobError(413, ERRORS['too_large'])
            while '\n\n' in buffer:
                event_text, buffer = buffer.split('\n\n', 1)
                lines = [line[5:].lstrip() for line in event_text.splitlines() if line.startswith('data:')]
                if not lines:
                    continue
                event = json.loads('\n'.join(lines))
                if event.get('error'):
                    raise ClipJobError(502, ERRORS['analysis_failed'])
                if event.get('stage'):
                    self.store.progress(job_id, redact_secrets(str(event['stage'])))
                if event.get('done') and isinstance(event.get('result'), dict):
                    self.store.complete(job_id, event['result'])
                    return
        raise ClipJobError(502, 'The existing analysis stream ended without a completed result.')

    def status(self, job_id):
        return public_job(self.store.get(job_id))

    def handoff(self, job_id):
        job = self.store.get(job_id)
        if job['state'] != 'completed':
            raise ClipJobError(409, job.get('error') or 'Analysis has not completed yet.')
        return {key: job[key] for key in ('job_id', 'source_url', 'preset', 'created_at', 'expires_at', 'analysis')}

    async def wait_idle(self):
        if self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)

