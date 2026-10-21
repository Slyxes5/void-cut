"""Explicit post-render uploads. Persistent receipts prevent automatic duplicate posts."""
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests

from backend.schemas.social import SocialUploadRequest
from backend.services.social_accounts import SocialAccounts, SocialError, atomic_json

TERMINAL = {'completed', 'draft', 'failed', 'uncertain'}


def trusted_upload_url(value, platform):
    try:
        parsed = urlsplit(value)
        suffix = '.googleapis.com' if platform == 'youtube' else '.tiktokapis.com'
        if (parsed.scheme != 'https' or not parsed.hostname or not parsed.hostname.endswith(suffix)
                or parsed.username or parsed.password or parsed.port not in (None,443)):
            raise ValueError()
    except (ValueError, TypeError):
        raise SocialError('Platform mengembalikan alamat upload tidak valid.', 502) from None
    from backend.security import remember_secret
    remember_secret(value)
    return value


def probe_upload(path):
    try:
        result = subprocess.run(['ffprobe', '-v','error','-show_streams','-show_format','-of','json',str(path)],
                                capture_output=True, text=True, check=True, timeout=30)
        data = json.loads(result.stdout)
        stream = next(s for s in data['streams'] if s.get('codec_type') == 'video')
        width, height = int(stream['width']), int(stream['height'])
        sar = stream.get('sample_aspect_ratio','1:1')
        if sar not in ('N/A','0:1'):
            numerator,denominator = (float(value) for value in sar.split(':'))
            width = width*numerator/denominator
        # ffprobe width is storage size; Shorts classification follows display orientation.
        rotation = next((side.get('rotation', 0) for side in stream.get('side_data_list', [])
                         if 'rotation' in side), stream.get('tags', {}).get('rotate', 0))
        if int(float(rotation)) % 180:
            width, height = height, width
        duration = float(data['format']['duration'])
        if not (width > 0 and height > 0 and 0 < duration <= 600):
            raise ValueError()
        return dict(width=width,height=height,duration=duration)
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, StopIteration, TypeError, ZeroDivisionError):
        raise SocialError('Video tidak dapat diperiksa. Pastikan FFprobe tersedia dan MP4 valid (maksimal 10 menit).', 422) from None


class ProgressFile:
    def __init__(self, handle, size, progress):
        self.handle, self.size, self.progress = handle, size, progress

    def __len__(self):
        return self.size

    def tell(self):
        return self.handle.tell()

    def read(self, size=-1):
        data = self.handle.read(size)
        self.progress(self.handle.tell(), self.size)
        return data


class SocialUploads:
    def __init__(self, root, accounts=None, resolve_clip=None, probe=None, clock=None):
        self.root = Path(root)
        self.accounts = accounts or SocialAccounts(self.root / 'accounts')
        self.resolve_clip = resolve_clip or self._resolve_clip
        self.probe = probe or probe_upload
        self.clock = clock or time.time
        self.lock = threading.RLock()
        self.jobs = {}
        self.tasks = set()
        self.loaded = False

    def _resolve_clip(self, batch_id, clip_index):
        from backend.services.render_status import batch_snapshot, clip_file
        snapshot = batch_snapshot(batch_id)
        clip = next((c for c in (snapshot or {}).get('clips', []) if c['clip_index'] == clip_index), None)
        if not clip or clip.get('status') != 'completed' or not clip.get('download_available'):
            raise SocialError('Klip belum selesai atau file hasil render sudah tidak tersedia.', 409)
        path = clip_file(clip)
        if not path:
            raise SocialError('File hasil render tidak tersedia.', 404)
        return path

    def load(self):
        if self.loaded:
            return
        with self.lock:
            if self.loaded:
                return
            folder = self.root / 'jobs'
            for path in sorted(folder.glob('*.json'), key=lambda p:p.stat().st_mtime, reverse=True)[:200]:
                try:
                    if path.is_symlink() or path.stat().st_size > 100000:
                        continue
                    job = json.loads(path.read_text(encoding='utf-8'))
                    if path.stem != job['request_id'] or not re.fullmatch(r'[0-9a-f-]{36}', path.stem):
                        continue
                    for target in job['targets']:
                        if target['status'] not in TERMINAL:
                            target.update(status='uncertain', message='Backend restart. Periksa platform sebelum mengirim ulang; upload tidak diulang otomatis.')
                    self.jobs[job['request_id']] = job
                    self._persist(job)
                    (self.root / 'spool' / (job['request_id']+'.mp4')).unlink(missing_ok=True)
                except (OSError, ValueError, KeyError, TypeError):
                    continue
            self.loaded = True

    def _persist(self, job):
        atomic_json(self.root / 'jobs' / (job['request_id']+'.json'), job)

    def public(self, job):
        with self.lock:
            return json.loads(json.dumps({key:value for key,value in job.items() if key not in {'digest','account_ids'}}))

    def list(self, batch_id=None, clip_index=None):
        self.load()
        with self.lock:
            return [self.public(j) for j in sorted(self.jobs.values(), key=lambda j:j['created_at'], reverse=True)
                    if (batch_id is None or j['batch_id'] == batch_id) and
                    (clip_index is None or j['clip_index'] == clip_index)][:30]

    def update(self, job, platform, **values):
        with self.lock:
            target = next(t for t in job['targets'] if t['platform'] == platform)
            target.update(values)
            job['updated_at'] = self.clock()
            self._persist(job)

    def creator_info(self):
        if self.accounts.env.get('TIKTOK_DIRECT_POST_ENABLED') != 'true':
            raise SocialError('Direct Post TikTok belum diaktifkan. Gunakan Kirim ke draf.', 409)
        account = self.accounts.token('tiktok')
        if 'video.publish' not in account.get('scope','').replace(',',' ').split():
            raise SocialError('Hubungkan ulang TikTok dengan izin video.publish.', 409)
        data = self.accounts.json_request('POST','https://open.tiktokapis.com/v2/post/publish/creator_info/query/',
                                          headers={'Authorization':'Bearer '+account['access_token']}, json={})['data']
        keys = ('creator_nickname','creator_username','privacy_level_options','comment_disabled',
                'duet_disabled','stitch_disabled','max_video_post_duration_sec')
        info = {k:data[k] for k in keys if k in data}
        if not info.get('privacy_level_options') or not info.get('max_video_post_duration_sec'):
            raise SocialError('Informasi izin kreator TikTok tidak tersedia.', 502)
        if self.accounts.env.get('TIKTOK_DIRECT_POST_AUDITED') != 'true':
            info['privacy_level_options'] = [v for v in info['privacy_level_options'] if v == 'SELF_ONLY']
        return info

    def _validate_target(self, target, metadata):
        account = self.accounts.token(target.platform)
        if target.account_id != account['account_id']:
            raise SocialError('Akun tujuan berubah. Perbarui akun dan konfirmasi ulang form upload.',409)
        if target.platform == 'youtube':
            if metadata['duration'] > 180 or metadata['width'] > metadata['height']:
                raise SocialError('YouTube Shorts memerlukan video persegi/vertikal dengan durasi maksimal 3 menit.', 422)
        elif target.mode == 'direct':
            info = self.creator_info()
            if target.privacy not in info['privacy_level_options']:
                raise SocialError('Privasi TikTok tidak tersedia untuk akun/aplikasi ini.', 422)
            if metadata['duration'] > info['max_video_post_duration_sec']:
                raise SocialError('Durasi video melebihi batas akun TikTok.', 422)
            for name in ('comment','duet','stitch'):
                if getattr(target, 'allow_'+name) and info.get(name+'_disabled'):
                    raise SocialError('Pengaturan '+name+' dinonaktifkan pada akun TikTok.', 422)
            account = {**account, 'creator_username': info.get('creator_username','')}
        return account

    def accept(self, request: SocialUploadRequest):
        self.load()
        raw = request.model_dump(mode='json')
        digest = hashlib.sha256(json.dumps({k:v for k,v in raw.items() if k != 'request_id'},sort_keys=True).encode()).hexdigest()
        with self.lock:
            previous = self.jobs.get(request.request_id)
            if previous:
                if previous['digest'] != digest:
                    raise SocialError('ID upload sudah digunakan untuk metadata berbeda.', 409)
                return self.public(previous), False
            if sum(any(t['status'] not in TERMINAL for t in j['targets']) for j in self.jobs.values()) >= 5:
                raise SocialError('Antrean upload penuh. Tunggu upload yang sedang berjalan.', 429)
            if len(self.jobs) >= 200:
                raise SocialError('Riwayat upload mencapai batas 200. Arsipkan riwayat pada folder social sebelum menambah upload.', 409)
            path = Path(self.resolve_clip(request.batch_id, request.clip_index))
            size = path.stat().st_size
            if not 0 < size <= 4*1024**3:
                raise SocialError('Ukuran video tidak valid atau lebih dari 4 GB.', 422)
            metadata = self.probe(path)
            account_ids = {}
            for target in request.targets:
                account_ids[target.platform] = self._validate_target(target, metadata)['account_id']
            # A deliberate retry of known failures is allowed with a fresh ID.
            # Completed/ambiguous deliveries are never repeated just because a
            # browser created another key. Account switches are separate targets.
            duplicate = next((j for j in self.jobs.values() if j['digest'] == digest
                              and j['account_ids'] == account_ids
                              and any(t['status'] != 'failed' for t in j['targets'])),None)
            if duplicate:
                return self.public(duplicate), False
            spool = self.root / 'spool' / (request.request_id+'.mp4')
            spool.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copyfile(path, spool)
                job = dict(request_id=request.request_id, digest=digest, account_ids=account_ids,
                           batch_id=request.batch_id, clip_index=request.clip_index, title=request.title.strip(),
                           caption=request.caption.strip(), created_at=self.clock(), updated_at=self.clock(),
                           media=metadata, targets=[dict(**t.model_dump(),status='queued',progress=0,
                           message='Menunggu upload', remote_id=None, url=None) for t in request.targets])
                self._persist(job)
                self.jobs[request.request_id] = job
            except Exception:
                spool.unlink(missing_ok=True)
                raise
            return self.public(job), True

    def start(self, job_id):
        task = asyncio.create_task(asyncio.to_thread(self.run, job_id))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def run(self, job_id):
        job = self.jobs[job_id]
        path = self.root / 'spool' / (job_id+'.mp4')
        try:
            for target in job['targets']:
                platform = target['platform']
                try:
                    account = self.accounts.token(platform)
                    if account['account_id'] != job['account_ids'][platform]:
                        raise SocialError('Akun berubah setelah upload dikonfirmasi. Buka ulang form upload.',409)
                    self.update(job,platform,status='uploading',message='Mengirim video')
                    if platform == 'youtube':
                        self._youtube(job,target,path,account)
                    else:
                        self._tiktok(job,target,path,account)
                except SocialError as exc:
                    self.update(job,platform,status='failed',message=str(exc))
                except Exception:
                    self.update(job,platform,status='uncertain',message='Upload terputus. Periksa platform sebelum mencoba lagi; pengiriman tidak diulang otomatis.')
        finally:
            path.unlink(missing_ok=True)

    def _youtube(self, job, target, path, account):
        size = path.stat().st_size
        headers = {'Authorization':'Bearer '+account['access_token'],'X-Upload-Content-Length':str(size),
                   'X-Upload-Content-Type':'video/mp4','Content-Type':'application/json; charset=UTF-8'}
        metadata = dict(snippet=dict(title=job['title'],description=job['caption'],categoryId='22'),
                        status=dict(privacyStatus=target['privacy'],selfDeclaredMadeForKids=target['made_for_kids'],
                                    containsSyntheticMedia=target['contains_synthetic_media']))
        response = self.accounts.http.request('POST','https://www.googleapis.com/upload/youtube/v3/videos',
                    params={'uploadType':'resumable','part':'snippet,status','notifySubscribers':'false'},
                    json=metadata,headers=headers,timeout=(15,60),allow_redirects=False)
        if response.status_code not in (200,201):
            raise SocialError(f'YouTube menolak inisialisasi upload (HTTP {response.status_code}). Periksa API, izin, dan kuota.',502)
        url = trusted_upload_url(response.headers.get('Location',''),'youtube')
        last_progress = [-1,0.0]
        def progress(sent,total):
            percent = min(95,int(sent*95/total))
            if percent > last_progress[0] and self.clock()-last_progress[1] >= .5:
                self.update(job,'youtube',progress=percent)
                last_progress[:] = [percent,self.clock()]
        # Once transfer begins, an ambiguous network response never starts a new
        # videos.insert call. The durable receipt remains uncertain for review.
        with path.open('rb') as handle:
            response = self.accounts.http.request('PUT',url,data=ProgressFile(handle,size,progress),
                headers={'Authorization':'Bearer '+account['access_token'],'Content-Type':'video/mp4','Content-Length':str(size)},
                timeout=(15,180),allow_redirects=False)
        if response.status_code not in (200,201):
            if response.status_code >= 500 or response.status_code == 308:
                raise RuntimeError('Ambiguous upload result')
            raise SocialError(f'YouTube menolak video (HTTP {response.status_code}).',502)
        data = response.json()
        video_id = data.get('id','')
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id):
            raise RuntimeError('Missing video receipt')
        actual_privacy = data.get('status',{}).get('privacyStatus',target['privacy'])
        self.update(job,'youtube',status='completed',progress=100,remote_id=video_id,
                    url='https://www.youtube.com/shorts/'+video_id,actual_privacy=actual_privacy,
                    message='Diterima YouTube ('+actual_privacy+'). Pemrosesan video berlangsung di YouTube.')

    def _tiktok(self, job, target, path, account):
        if target['mode'] == 'direct':
            # Recheck current creator limits immediately before posting.
            from backend.schemas.social import UploadTarget
            validated = self._validate_target(UploadTarget.model_validate({k:v for k,v in target.items()
                if k in UploadTarget.model_fields}), job['media'])
            self.update(job,'tiktok',creator_username=validated.get('creator_username',''))
        size = path.stat().st_size
        chunk_size = min(size,10*1024**2)
        count = max(1,size//chunk_size)
        body = dict(source_info=dict(source='FILE_UPLOAD',video_size=size,chunk_size=chunk_size,total_chunk_count=count))
        if target['mode'] == 'direct':
            body['post_info'] = dict(title='\n\n'.join(x for x in (job['title'],job['caption']) if x),
                privacy_level=target['privacy'],disable_comment=not target['allow_comment'],
                disable_duet=not target['allow_duet'],disable_stitch=not target['allow_stitch'],
                brand_organic_toggle=target['commercial'] and target['own_brand'],
                brand_content_toggle=target['commercial'] and target['branded_content'],
                is_aigc=target['contains_synthetic_media'])
        endpoint = 'video/init/' if target['mode'] == 'direct' else 'inbox/video/init/'
        data = self.accounts.json_request('POST','https://open.tiktokapis.com/v2/post/publish/'+endpoint,
                headers={'Authorization':'Bearer '+account['access_token']},json=body)['data']
        publish_id = data['publish_id']
        self.update(job,'tiktok',remote_id=publish_id)
        url = trusted_upload_url(data['upload_url'],'tiktok')
        with path.open('rb') as handle:
            sent = 0
            for i in range(count):
                length = size-sent if i == count-1 else chunk_size
                chunk = handle.read(length)
                if len(chunk) != length:
                    raise RuntimeError('Spool incomplete')
                response = self.accounts.http.request('PUT',url,data=chunk,
                    headers={'Content-Type':'video/mp4','Content-Length':str(length),
                             'Content-Range':f'bytes {sent}-{sent+length-1}/{size}'},
                    timeout=(15,180),allow_redirects=False)
                if not 200 <= response.status_code < 300:
                    raise RuntimeError('Transfer result uncertain')
                sent += length
                self.update(job,'tiktok',progress=min(95,int(sent*95/size)))
        self.update(job,'tiktok',status='processing',progress=100,
                    message='Video diterima, menunggu status dari TikTok. Gunakan Periksa status.')
        try:
            self.refresh_tiktok(job)
        except SocialError:
            self.update(job,'tiktok',message='Video terkirim. Status TikTok belum dapat diperiksa; gunakan Periksa status nanti.')

    def refresh_tiktok(self, job):
        target = next((t for t in job['targets'] if t['platform'] == 'tiktok'),None)
        if not target or not target.get('remote_id') or target['status'] in {'completed','draft','failed'}:
            return self.public(job)
        account = self.accounts.token('tiktok')
        if account['account_id'] != job['account_ids']['tiktok']:
            raise SocialError('Hubungkan akun TikTok yang digunakan pada upload ini.',409)
        data = self.accounts.json_request('POST','https://open.tiktokapis.com/v2/post/publish/status/fetch/',
                    headers={'Authorization':'Bearer '+account['access_token']},json={'publish_id':target['remote_id']})['data']
        state = data.get('status')
        if state == 'PUBLISH_COMPLETE':
            ids = data.get('publicaly_available_post_id',[])
            username = target.get('creator_username','')
            post_url = None
            if ids and str(ids[0]).isdigit() and re.fullmatch(r'[A-Za-z0-9_.]{1,64}',username):
                post_url = 'https://www.tiktok.com/@'+quote(username)+'/video/'+str(ids[0])
            self.update(job,'tiktok',status='completed',progress=100,message='TikTok mengonfirmasi posting selesai.',
                        url=post_url)
        elif state == 'SEND_TO_USER_INBOX':
            self.update(job,'tiktok',status='draft',progress=100,
                        message='Draf dikirim. Buka notifikasi inbox TikTok, tempel caption, lalu selesaikan posting di TikTok.')
        elif state == 'FAILED':
            reason = str(data.get('fail_reason','unknown'))
            reason = reason if re.fullmatch(r'[a-z0-9_]{1,100}',reason) else 'unknown'
            self.update(job,'tiktok',status='failed',message='TikTok menolak posting: '+reason)
        return self.public(job)
