"""Single-user OAuth accounts. Tokens remain encrypted on the backend."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import requests
from cryptography.fernet import Fernet, InvalidToken

from backend.security import remember_secret

PLATFORMS = ('youtube', 'tiktok')


class SocialError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    os.replace(temporary, path)


class SocialAccounts:
    def __init__(self, root, environ=None, http=None, clock=None):
        self.root = Path(root)
        self.env = os.environ if environ is None else environ
        self.http = http or requests.Session()
        self.clock = clock or time.time
        self.lock = threading.RLock()
        self.states = {}

    def config(self, platform):
        if platform not in PLATFORMS:
            raise SocialError('Platform tidak didukung.', 404)
        prefix = 'YOUTUBE' if platform == 'youtube' else 'TIKTOK'
        return dict(client_id=self.env.get(prefix + '_CLIENT_ID', '').strip(),
                    client_secret=self.env.get(prefix + '_CLIENT_SECRET', '').strip(),
                    redirect_uri=self.env.get(prefix + '_REDIRECT_URI', '').strip())

    def cipher(self):
        try:
            key = self.env.get('SOCIAL_TOKEN_ENCRYPTION_KEY', '')
            remember_secret(key)
            return Fernet(key.encode('ascii'))
        except (ValueError, UnicodeError):
            raise SocialError('Atur SOCIAL_TOKEN_ENCRYPTION_KEY pada backend terlebih dahulu.', 503) from None

    def configured(self, platform):
        cfg = self.config(platform)
        try:
            self.cipher()
            parsed = urlsplit(cfg['redirect_uri'])
            expected = '/social/oauth/' + platform + '/callback'
            valid = (parsed.path == expected and not parsed.query and not parsed.fragment
                     and not parsed.username and not parsed.password and bool(parsed.hostname)
                     and (parsed.scheme == 'https' or platform == 'youtube' and parsed.scheme == 'http'
                          and parsed.hostname in {'localhost', '127.0.0.1'}))
            return bool(all(cfg.values()) and valid)
        except (SocialError, ValueError):
            return False

    def read(self, platform):
        path = self.root / (platform + '.enc')
        if not path.exists():
            return None
        try:
            if path.is_symlink() or path.stat().st_size > 65536:
                raise ValueError()
            value = json.loads(self.cipher().decrypt(path.read_bytes()))
            for key in ('access_token', 'refresh_token'):
                remember_secret(value.get(key))
            return value
        except (InvalidToken, ValueError, OSError):
            raise SocialError('Akun tersimpan tidak dapat dibaca. Hubungkan ulang dengan kunci enkripsi yang benar.', 503) from None

    def save(self, platform, value):
        with self.lock:
            payload = self.cipher().encrypt(json.dumps(value).encode('utf-8'))
            self.root.mkdir(parents=True, exist_ok=True)
            target = self.root / (platform + '.enc')
            temporary = target.with_suffix('.tmp')
            temporary.write_bytes(payload)
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, target)
            for key in ('access_token', 'refresh_token'):
                remember_secret(value.get(key))

    def status(self):
        result = []
        for platform in PLATFORMS:
            configured = self.configured(platform)
            error, account = '', None
            if configured:
                try:
                    account = self.read(platform)
                except SocialError as exc:
                    error = str(exc)
            result.append(dict(platform=platform, configured=configured, connected=bool(account),
                               account_name=account.get('account_name', '') if account else '',
                               account_id=account.get('account_id', '') if account else '', error=error,
                               direct_enabled=platform == 'tiktok' and self.env.get('TIKTOK_DIRECT_POST_ENABLED') == 'true',
                               direct_audited=self.env.get('TIKTOK_DIRECT_POST_AUDITED') == 'true'))
        return result

    def begin(self, platform, origin):
        if not self.configured(platform):
            raise SocialError('Konfigurasi developer belum lengkap. Ikuti panduan docs/social-upload-setup.md.', 503)
        cfg = self.config(platform)
        redirect = urlsplit(cfg['redirect_uri'])
        if origin.rstrip('/') != f'{redirect.scheme}://{redirect.netloc}':
            raise SocialError('Buka aplikasi melalui origin yang sama dengan redirect URI OAuth.', 400)
        state, cookie, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        with self.lock:
            self.states = {s: v for s, v in self.states.items() if v['expires'] > self.clock()}
            if len(self.states) >= 50:
                raise SocialError('Terlalu banyak permintaan login. Coba beberapa menit lagi.', 429)
            self.states[state] = dict(platform=platform, cookie=cookie, verifier=verifier,
                                      expires=self.clock()+600)
        if platform == 'youtube':
            scopes = ['https://www.googleapis.com/auth/youtube.upload', 'https://www.googleapis.com/auth/youtube.readonly']
            args = dict(client_id=cfg['client_id'], redirect_uri=cfg['redirect_uri'], response_type='code',
                        scope=' '.join(scopes), state=state, access_type='offline', prompt='consent',
                        code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='),
                        code_challenge_method='S256')
            url = 'https://accounts.google.com/o/oauth2/v2/auth?'
        else:
            scopes = ['user.info.basic', 'video.upload']
            if self.env.get('TIKTOK_DIRECT_POST_ENABLED') == 'true':
                scopes.append('video.publish')
            args = dict(client_key=cfg['client_id'], redirect_uri=cfg['redirect_uri'], response_type='code',
                        scope=','.join(scopes), state=state)
            url = 'https://www.tiktok.com/v2/auth/authorize/?'
        return url + urlencode(args), cookie, redirect.scheme == 'https'

    def json_request(self, method, url, **kwargs):
        try:
            response = self.http.request(method, url, timeout=(15, 60), allow_redirects=False, **kwargs)
            if not 200 <= response.status_code < 300:
                raise SocialError(f'Platform menolak permintaan (HTTP {response.status_code}). Periksa izin akun atau kuota.', 502)
            data = response.json()
            if data.get('error') and (not isinstance(data['error'], dict) or data['error'].get('code') not in (None, 'ok')):
                raise SocialError('Platform menolak permintaan. Periksa izin akun dan pengaturan developer.', 502)
            return data
        except (requests.RequestException, ValueError):
            raise SocialError('Platform tidak dapat dihubungi. Periksa koneksi lalu cek status sebelum mencoba ulang.', 502) from None

    def callback(self, platform, state, cookie, code, denied=False):
        with self.lock:
            item = self.states.get(state)
            if (not item or item['platform'] != platform or item['expires'] <= self.clock()
                    or not cookie or not hmac.compare_digest(item['cookie'], cookie)):
                raise SocialError('Sesi OAuth tidak valid/kedaluwarsa. Hubungkan akun lagi.', 400)
            del self.states[state]
            if denied or not code:
                raise SocialError('Izin akun dibatalkan.', 400)
            cfg = self.config(platform)
            body = dict(client_secret=cfg['client_secret'], code=code, grant_type='authorization_code',
                        redirect_uri=cfg['redirect_uri'])
            remember_secret(code)
            remember_secret(cfg['client_secret'])
            if platform == 'youtube':
                body.update(client_id=cfg['client_id'], code_verifier=item['verifier'])
                data = self.json_request('POST', 'https://oauth2.googleapis.com/token', data=body)
            else:
                body['client_key'] = cfg['client_id']
                data = self.json_request('POST', 'https://open.tiktokapis.com/v2/oauth/token/', data=body)
            if not data.get('access_token') or not data.get('refresh_token'):
                raise SocialError('Izin akses offline tidak tersedia. Hubungkan ulang dan setujui izin upload.', 400)
            remember_secret(data['access_token'])
            remember_secret(data['refresh_token'])
            auth = {'Authorization': 'Bearer ' + data['access_token']}
            scopes = set(data.get('scope', '').replace(',', ' ').split())
            required = ('https://www.googleapis.com/auth/youtube.upload' if platform == 'youtube' else 'video.upload')
            if required not in scopes:
                raise SocialError('Izin upload belum diberikan. Hubungkan ulang dengan izin yang diperlukan.', 400)
            if platform == 'youtube':
                profile = self.json_request('GET', 'https://www.googleapis.com/youtube/v3/channels',
                                            params={'part':'snippet', 'mine':'true'}, headers=auth).get('items', [])
                if not profile:
                    raise SocialError('Akun ini belum memiliki channel YouTube.', 400)
                account_id, name = profile[0]['id'], profile[0]['snippet']['title']
            else:
                profile = self.json_request('GET', 'https://open.tiktokapis.com/v2/user/info/',
                                            params={'fields':'open_id,display_name'}, headers=auth)['data']['user']
                account_id, name = profile['open_id'], profile['display_name']
            data.update(account_id=account_id, account_name=name, expires_at=self.clock()+float(data.get('expires_in', 0)))
            self.save(platform, data)

    def token(self, platform):
        with self.lock:
            account = self.read(platform)
            if not account:
                raise SocialError('Hubungkan akun terlebih dahulu.', 409)
            if account.get('expires_at', 0) <= self.clock()+120:
                cfg = self.config(platform)
                body = dict(client_secret=cfg['client_secret'], refresh_token=account['refresh_token'], grant_type='refresh_token')
                body['client_id' if platform == 'youtube' else 'client_key'] = cfg['client_id']
                url = 'https://oauth2.googleapis.com/token' if platform == 'youtube' else 'https://open.tiktokapis.com/v2/oauth/token/'
                data = self.json_request('POST', url, data=body)
                if not data.get('access_token'):
                    raise SocialError('Sesi akun kedaluwarsa. Hubungkan ulang.', 409)
                account.update(data)
                account['expires_at'] = self.clock()+float(data.get('expires_in', 0))
                self.save(platform, account)
            return account

    def disconnect(self, platform):
        with self.lock:
            self.config(platform)
            # Remove locally even if the provider is offline. The setup guide also
            # links provider account controls for revoking the remote grant.
            (self.root / (platform + '.enc')).unlink(missing_ok=True)
            self.states = {s:v for s,v in self.states.items() if v['platform'] != platform}
