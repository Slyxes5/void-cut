"""Offline OAuth/upload contracts. Fake provider HTTP only; never posts real videos."""
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import requests
from cryptography.fernet import Fernet
from pydantic import ValidationError

from backend.schemas.social import SocialUploadRequest, UploadTarget
from backend.services.social_accounts import SocialAccounts, SocialError
from backend.services.social_uploads import SocialUploads, trusted_upload_url

NOW = 1800000000
ORIGIN = 'https://clip.example.com'


class Response:
    def __init__(self, data=None, status=200, headers=None):
        self.data,self.status_code,self.headers = data or {},status,headers or {}

    def json(self):
        return self.data


class HTTP:
    def __init__(self):
        self.calls = []
        self.responses = []

    def request(self, method, url, **kwargs):
        self.calls.append((method,url,kwargs))
        if not self.responses:
            raise AssertionError('Unexpected provider request: '+method+' '+url)
        response = self.responses.pop(0)
        if isinstance(response,Exception):
            raise response
        body = kwargs.get('data')
        if hasattr(body,'read'):
            while body.read(65536):
                pass
        return response


class SocialTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.http = HTTP()
        self.env = dict(SOCIAL_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(),
            YOUTUBE_CLIENT_ID='youtube-test-id',YOUTUBE_CLIENT_SECRET='youtube-test-secret',
            YOUTUBE_REDIRECT_URI=ORIGIN+'/social/oauth/youtube/callback',
            TIKTOK_CLIENT_ID='tiktok-test-id',TIKTOK_CLIENT_SECRET='tiktok-test-secret',
            TIKTOK_REDIRECT_URI=ORIGIN+'/social/oauth/tiktok/callback')
        self.accounts = SocialAccounts(self.root/'accounts',environ=self.env,http=self.http,clock=lambda:NOW)
        self.video = self.root/'fixture.mp4'
        self.video.write_bytes(b'fixture-video-only')
        self.metadata = dict(width=1080,height=1920,duration=30)
        self.service = SocialUploads(self.root/'social',accounts=self.accounts,
            resolve_clip=lambda batch,index:self.video,probe=lambda path:self.metadata,clock=lambda:NOW)

    def account(self,platform,**overrides):
        value = dict(access_token=platform+'-test-access-token',refresh_token=platform+'-test-refresh-token',
                     expires_at=NOW+3600,account_id=platform+'-account',account_name='Test '+platform,
                     scope='video.upload,user.info.basic,video.publish' if platform=='tiktok' else
                     'https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly')
        value.update(overrides)
        self.accounts.save(platform,value)
        return value

    def request(self,targets=None,**overrides):
        data = dict(request_id=str(uuid.uuid4()),batch_id='batch_1800000000_abcdef',clip_index=0,
                    title='Judul klip',caption='Caption asli #test',confirmed=True,
                    targets=targets if targets is not None else [dict(platform='youtube',mode='upload',privacy='private')])
        data.update(overrides)
        data['targets'] = [dict(account_id=t['platform']+'-account',**t) if 'account_id' not in t else t for t in data['targets']]
        return SocialUploadRequest.model_validate(data)

    def accept(self,request=None):
        request = request or self.request()
        public,created = self.service.accept(request)
        self.assertTrue(created)
        return self.service.jobs[public['request_id']]


class AccountTests(SocialTestBase):
    def test_missing_config_is_explicit_and_public_status_never_contains_secrets(self):
        self.assertTrue(all(a['configured'] for a in self.accounts.status()))
        self.account('youtube')
        public = json.dumps(self.accounts.status())
        self.assertIn('Test youtube',public)
        self.assertNotIn('access_token',public)
        self.assertNotIn('test-secret',public)
        self.env.pop('SOCIAL_TOKEN_ENCRYPTION_KEY')
        self.assertFalse(any(a['configured'] for a in self.accounts.status()))

    def test_tokens_encrypted_at_rest_and_wrong_key_never_returns_plaintext(self):
        value = self.account('youtube')
        raw = (self.root/'accounts/youtube.enc').read_bytes()
        self.assertNotIn(value['access_token'].encode(),raw)
        self.assertEqual(self.accounts.read('youtube')['refresh_token'],value['refresh_token'])
        self.env['SOCIAL_TOKEN_ENCRYPTION_KEY'] = Fernet.generate_key().decode()
        with self.assertRaises(SocialError):
            self.accounts.read('youtube')

    def test_oauth_is_bound_to_origin_platform_state_cookie_and_pkce(self):
        with self.assertRaises(SocialError):
            self.accounts.begin('youtube','https://other.example')
        url,cookie,secure = self.accounts.begin('youtube',ORIGIN)
        args = parse_qs(urlsplit(url).query)
        state = args['state'][0]
        self.assertTrue(secure)
        self.assertEqual(args['code_challenge_method'],['S256'])
        for platform,supplied_cookie in [('youtube','wrong-cookie'),('tiktok',cookie)]:
            with self.assertRaises(SocialError):
                self.accounts.callback(platform,state,supplied_cookie,'untrusted-code')
        self.assertEqual(self.http.calls,[])
        self.http.responses = [Response(dict(access_token='test-access',refresh_token='test-refresh',expires_in=3600,
            scope='https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly')),
            Response({'items':[{'id':'channel-123','snippet':{'title':'My Channel'}}]})]
        self.accounts.callback('youtube',state,cookie,'test-auth-code')
        self.assertEqual(self.accounts.read('youtube')['account_name'],'My Channel')
        self.assertIn('code_verifier',self.http.calls[0][2]['data'])
        with self.assertRaises(SocialError):
            self.accounts.callback('youtube',state,cookie,'replayed-code')
        self.assertEqual(len(self.http.calls),2)

    def test_oauth_denial_expiry_and_missing_scope_never_connect(self):
        for denied,expired in ((True,False),(False,True)):
            url,cookie,_ = self.accounts.begin('tiktok',ORIGIN)
            state = parse_qs(urlsplit(url).query)['state'][0]
            if expired:
                self.accounts.states[state]['expires'] = NOW-1
            with self.assertRaises(SocialError):
                self.accounts.callback('tiktok',state,cookie,'test-code',denied)
        self.assertIsNone(self.accounts.read('tiktok'))
        self.assertEqual(self.http.calls,[])

    def test_refresh_rotates_tokens_and_disconnect_invalidates_pending_oauth(self):
        self.account('tiktok',expires_at=NOW-1)
        self.http.responses = [Response(dict(access_token='rotated-access',refresh_token='rotated-refresh',expires_in=7200))]
        account = self.accounts.token('tiktok')
        self.assertEqual(account['refresh_token'],'rotated-refresh')
        self.assertEqual(self.accounts.read('tiktok')['expires_at'],NOW+7200)
        url,cookie,_ = self.accounts.begin('tiktok',ORIGIN)
        state = parse_qs(urlsplit(url).query)['state'][0]
        self.accounts.disconnect('tiktok')
        self.assertIsNone(self.accounts.read('tiktok'))
        with self.assertRaises(SocialError):
            self.accounts.callback('tiktok',state,cookie,'code')

    def test_tiktok_requires_https_redirect_but_youtube_supports_loopback(self):
        self.env['TIKTOK_REDIRECT_URI'] = 'http://localhost:5173/social/oauth/tiktok/callback'
        self.env['YOUTUBE_REDIRECT_URI'] = 'http://localhost:5173/social/oauth/youtube/callback'
        self.assertFalse(self.accounts.configured('tiktok'))
        self.assertTrue(self.accounts.configured('youtube'))


class UploadTests(SocialTestBase):
    def test_account_must_match_the_account_reviewed_in_the_form(self):
        self.account('youtube',account_id='new-channel')
        with self.assertRaises(SocialError):
            self.service.accept(self.request())
        self.assertEqual(self.service.jobs,{})
        self.assertEqual(self.http.calls,[])

    def test_no_transfer_without_user_confirmation_or_invalid_metadata(self):
        for overrides in (dict(confirmed=False),dict(title=' '),dict(targets=[]),dict(caption='a'*5001),
                          dict(targets=[dict(platform='youtube',mode='draft',privacy='private')])):
            with self.assertRaises(ValidationError):
                self.request(**overrides)
        self.assertEqual(self.http.calls,[])

    def test_short_duration_and_framing_checked_before_queue(self):
        self.account('youtube')
        for metadata in (dict(width=1920,height=1080,duration=30),dict(width=1080,height=1920,duration=181)):
            self.metadata = metadata
            with self.assertRaises(SocialError):
                self.service.accept(self.request())
        self.assertEqual(self.http.calls,[])
        self.assertEqual(self.service.jobs,{})

    def test_idempotency_persists_and_changed_payload_is_rejected(self):
        self.account('youtube')
        request = self.request()
        job = self.accept(request)
        public,created = self.service.accept(request)
        self.assertFalse(created)
        self.assertEqual(public['request_id'],job['request_id'])
        with self.assertRaises(SocialError):
            self.service.accept(self.request(request_id=request.request_id,title='Changed'))
        public,created = self.service.accept(self.request())
        self.assertFalse(created)
        self.assertEqual(public['request_id'],job['request_id'])
        self.assertEqual(self.http.calls,[])
        restarted = SocialUploads(self.root/'social',accounts=self.accounts)
        restored = restarted.list()[0]
        self.assertEqual(restored['targets'][0]['status'],'uncertain')
        self.assertFalse((self.root/'social/spool'/f'{request.request_id}.mp4').exists())
        self.assertNotIn('digest',restored)

    def test_youtube_uploads_original_title_caption_visibility_and_returns_receipt(self):
        self.account('youtube')
        job = self.accept()
        self.http.responses = [Response(headers={'Location':'https://www.googleapis.com/upload/youtube/v3/videos?upload_id=test'}),
            Response(dict(id='dQw4w9WgXcQ',status={'privacyStatus':'private'}))]
        self.service.run(job['request_id'])
        self.assertEqual([c[0] for c in self.http.calls],['POST','PUT'])
        metadata = self.http.calls[0][2]['json']
        self.assertEqual(metadata['snippet']['title'],'Judul klip')
        self.assertEqual(metadata['snippet']['description'],'Caption asli #test')
        self.assertEqual(metadata['status']['privacyStatus'],'private')
        self.assertEqual(job['targets'][0]['status'],'completed')
        self.assertEqual(job['targets'][0]['url'],'https://www.youtube.com/shorts/dQw4w9WgXcQ')
        self.assertTrue(self.video.exists())
        self.assertFalse((self.root/'social/spool'/f'{job["request_id"]}.mp4').exists())

    def test_tiktok_draft_has_no_fake_caption_field_and_uses_correct_chunk_ranges(self):
        self.account('tiktok')
        with self.video.open('wb') as handle:
            handle.truncate(21*1024**2)
        job = self.accept(self.request([dict(platform='tiktok',mode='draft')]))
        self.http.responses = [Response(dict(data=dict(publish_id='draft-123',upload_url='https://open-upload.tiktokapis.com/video/?signed=test'),error={'code':'ok'})),
            Response(),Response(),Response(dict(data={'status':'SEND_TO_USER_INBOX'},error={'code':'ok'}))]
        self.service.run(job['request_id'])
        initial = self.http.calls[0]
        self.assertIn('/inbox/video/init/',initial[1])
        self.assertNotIn('post_info',initial[2]['json'])
        self.assertEqual(initial[2]['json']['source_info']['total_chunk_count'],2)
        uploads = [call for call in self.http.calls if call[0]=='PUT']
        self.assertEqual([call[2]['headers']['Content-Range'] for call in uploads],
            [f'bytes 0-{10*1024**2-1}/{21*1024**2}',f'bytes {10*1024**2}-{21*1024**2-1}/{21*1024**2}'])
        self.assertTrue(all('Authorization' not in call[2]['headers'] for call in uploads))
        self.assertEqual(job['targets'][0]['status'],'draft')

    def test_partial_platform_failure_never_repeats_successful_youtube(self):
        self.account('youtube'); self.account('tiktok')
        job = self.accept(self.request([dict(platform='youtube',mode='upload',privacy='private'),dict(platform='tiktok',mode='draft')]))
        self.http.responses = [Response(headers={'Location':'https://www.googleapis.com/upload/video?upload_id=first'}),
            Response(dict(id='dQw4w9WgXcQ')),Response(status=403)]
        self.service.run(job['request_id'])
        self.assertEqual([t['status'] for t in job['targets']],['completed','failed'])
        retry = self.accept(self.request([dict(platform='tiktok',mode='draft')]))
        self.assertEqual([t['platform'] for t in retry['targets']],['tiktok'])
        self.assertEqual(sum('upload/youtube' in c[1] for c in self.http.calls if c[0]=='POST'),1)

    def test_lost_upload_response_does_not_trigger_another_insert(self):
        self.account('youtube')
        request = self.request()
        job = self.accept(request)
        self.http.responses = [Response(headers={'Location':'https://www.googleapis.com/upload/video?upload_id=ambiguous'}),requests.Timeout('test-timeout')]
        self.service.run(job['request_id'])
        self.assertEqual(job['targets'][0]['status'],'uncertain')
        public,created = self.service.accept(request)
        self.assertFalse(created)
        public,created = self.service.accept(self.request())
        self.assertFalse(created)
        self.assertEqual(len(self.http.calls),2)

    def test_account_change_before_transfer_is_rejected_and_known_failure_can_retry(self):
        self.account('youtube')
        job = self.accept()
        self.account('youtube',account_id='other-channel')
        self.service.run(job['request_id'])
        self.assertEqual(job['targets'][0]['status'],'failed')
        self.assertEqual(self.http.calls,[])
        retry = self.accept(self.request([dict(platform='youtube',account_id='other-channel',mode='upload',privacy='private')]))
        self.assertNotEqual(retry['request_id'],job['request_id'])

    def test_upload_urls_cannot_redirect_video_or_credentials_to_arbitrary_servers(self):
        for url in ('http://www.googleapis.com/upload','https://evil.example/file',
                    'https://www.googleapis.com.evil.example/upload','https://user@www.googleapis.com/upload'):
            with self.assertRaises(SocialError):
                trusted_upload_url(url,'youtube')

    def test_direct_requires_fresh_creator_limits_explicit_privacy_and_music_consent(self):
        self.account('tiktok')
        with self.assertRaises(ValidationError):
            UploadTarget(platform='tiktok',mode='direct')
        self.env['TIKTOK_DIRECT_POST_ENABLED'] = 'true'
        info = dict(creator_nickname='Creator',privacy_level_options=['PUBLIC_TO_EVERYONE','SELF_ONLY'],
                    comment_disabled=True,duet_disabled=False,stitch_disabled=False,max_video_post_duration_sec=60)
        self.http.responses = [Response(dict(data=info,error={'code':'ok'}))]
        creator = self.service.creator_info()
        self.assertEqual(creator['privacy_level_options'],['SELF_ONLY'])
        self.http.responses = [Response(dict(data=info,error={'code':'ok'}))]
        with self.assertRaises(SocialError):
            self.service.accept(self.request([dict(platform='tiktok',mode='direct',privacy='SELF_ONLY',music_consent=True,allow_comment=True)]))

    def test_direct_post_transmits_metadata_and_rechecks_creator_before_transfer(self):
        self.account('tiktok')
        self.env['TIKTOK_DIRECT_POST_ENABLED'] = 'true'
        info = dict(creator_nickname='Creator',privacy_level_options=['SELF_ONLY'],comment_disabled=False,
                    duet_disabled=False,stitch_disabled=False,max_video_post_duration_sec=60)
        self.http.responses = [Response(dict(data=info,error={'code':'ok'})),Response(dict(data=info,error={'code':'ok'})),
            Response(dict(data=dict(publish_id='direct-123',upload_url='https://open-upload.tiktokapis.com/video/?signed=test'),error={'code':'ok'})),
            Response(),Response(dict(data={'status':'PUBLISH_COMPLETE'},error={'code':'ok'}))]
        job = self.accept(self.request([dict(platform='tiktok',mode='direct',privacy='SELF_ONLY',music_consent=True)]))
        self.service.run(job['request_id'])
        initial = next(call for call in self.http.calls if call[1].endswith('/video/init/'))
        post = initial[2]['json']['post_info']
        self.assertEqual(post['title'],'Judul klip\n\nCaption asli #test')
        self.assertEqual(post['privacy_level'],'SELF_ONLY')
        self.assertTrue(post['disable_comment'])
        self.assertTrue(post['disable_duet'])
        self.assertEqual(sum('/creator_info/' in call[1] for call in self.http.calls),2)
        self.assertEqual(job['targets'][0]['status'],'completed')

    def test_status_failure_after_transfer_stays_processing_not_failed_or_reuploaded(self):
        self.account('tiktok')
        job = self.accept(self.request([dict(platform='tiktok',mode='draft')]))
        self.http.responses = [Response(dict(data=dict(publish_id='draft-123',upload_url='https://open-upload.tiktokapis.com/video/?signed=test'),error={'code':'ok'})),
                               Response(),requests.Timeout('status-unavailable')]
        self.service.run(job['request_id'])
        self.assertEqual(job['targets'][0]['status'],'processing')
        self.http.responses = [Response(dict(data={'status':'SEND_TO_USER_INBOX'},error={'code':'ok'}))]
        self.service.refresh_tiktok(job)
        self.assertEqual(job['targets'][0]['status'],'draft')
        self.assertEqual(sum(call[0]=='PUT' for call in self.http.calls),1)


class ApiTests(SocialTestBase):
    def setUp(self):
        super().setUp()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.routers.social import router
        from backend.security import AuthenticationMiddleware, SecurityManager
        app = FastAPI()
        app.state.social_uploads = self.service
        app.include_router(router)
        app.add_middleware(AuthenticationMiddleware,manager=SecurityManager(password_hash='',origins={ORIGIN}))
        self.client = TestClient(app,base_url=ORIGIN)
        self.addCleanup(self.client.close)
        self.admin = {'X-API-Key':'social-test-admin','Origin':ORIGIN}
        environ = patch.dict(os.environ,{'ADMIN_API_KEY':'social-test-admin'})
        environ.start()
        self.addCleanup(environ.stop)

    def test_accounts_and_uploads_require_login_and_do_not_expose_oauth_secrets(self):
        for method,path in (('GET','/api/social/accounts'),('GET','/api/social/uploads'),('POST','/api/social/accounts/youtube/connect')):
            self.assertEqual(self.client.request(method,path).status_code,401)
        self.account('youtube')
        response = self.client.get('/api/social/accounts',headers=self.admin)
        self.assertEqual(response.status_code,200)
        self.assertNotIn('test-access-token',response.text)
        self.assertNotIn('CLIENT_SECRET',response.text)

    def test_public_callback_still_requires_one_time_state_and_http_only_cookie(self):
        response = self.client.get('/social/oauth/youtube/callback?state=fake&code=fake')
        self.assertEqual(response.status_code,400)
        response = self.client.post('/api/social/accounts/youtube/connect',headers=self.admin)
        self.assertEqual(response.status_code,200)
        self.assertIn('HttpOnly',response.headers['set-cookie'])
        self.assertIn('SameSite=lax',response.headers['set-cookie'])
        self.assertIn('Secure',response.headers['set-cookie'])
        args = parse_qs(urlsplit(response.json()['authorization_url']).query)
        state = args['state'][0]
        self.http.responses = [Response(dict(access_token='test-access',refresh_token='test-refresh',expires_in=3600,
            scope='https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly')),
            Response({'items':[{'id':'channel-id','snippet':{'title':'My Channel'}}]})]
        callback = self.client.get('/social/oauth/youtube/callback',params={'state':state,'code':'test-code'})
        self.assertEqual(callback.status_code,200)
        self.assertIn('berhasil terhubung',callback.text)
        self.assertNotIn('test-access',callback.text)
        self.assertEqual(callback.headers['referrer-policy'],'no-referrer')
        self.assertEqual(self.client.get('/social/oauth/youtube/callback',params={'state':state,'code':'test-code'}).status_code,400)

    def test_submit_is_idempotent_schedules_once_and_accepts_only_render_identifiers(self):
        self.account('youtube')
        body = self.request().model_dump(mode='json')
        with patch.object(self.service,'start') as start:
            first = self.client.post('/api/social/uploads',headers=self.admin,json=body)
            second = self.client.post('/api/social/uploads',headers=self.admin,json=body)
            self.assertEqual(first.status_code,202,first.text)
            self.assertEqual(second.status_code,200,second.text)
            start.assert_called_once_with(body['request_id'])
            bad = self.client.post('/api/social/uploads',headers=self.admin,json={**body,'file_path':'C:/secret.mp4'})
            self.assertEqual(bad.status_code,422)
        self.assertEqual(self.http.calls,[])

    def test_real_resolver_rejects_unfinished_or_missing_clip(self):
        import backend.services.render_status as render_status
        service = SocialUploads(self.root/'resolver',accounts=self.accounts)
        for clip in (None,dict(clip_index=0,status='rendering',download_available=False),
                     dict(clip_index=0,status='completed',download_available=False)):
            with patch.object(render_status,'batch_snapshot',return_value={'clips':[clip] if clip else []}):
                with self.assertRaises(SocialError):
                    service._resolve_clip('batch_1800000000_abcdef',0)


class SetupTests(unittest.TestCase):
    def test_key_setup_preserves_existing_env_and_is_idempotent_without_printing(self):
        from backend.setup_social_uploads import ensure_encryption_key
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'.env'
            path.write_text('EXISTING_SETTING=keep-me\n')
            self.assertTrue(ensure_encryption_key(path))
            first = path.read_text()
            self.assertIn('EXISTING_SETTING=keep-me',first)
            self.assertFalse(ensure_encryption_key(path))
            self.assertEqual(path.read_text(),first)


if __name__ == '__main__':
    unittest.main()
