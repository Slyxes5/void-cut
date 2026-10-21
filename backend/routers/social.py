import asyncio
import html
import re
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from backend.schemas.social import SocialUploadRequest
from backend.services.social_accounts import SocialError

router = APIRouter(tags=['Social uploads'])
Platform = Literal['youtube','tiktok']


def manager(request):
    return request.app.state.social_uploads


async def invoke(function, *args):
    try:
        return await asyncio.to_thread(function,*args)
    except SocialError as exc:
        raise HTTPException(exc.status,str(exc)) from None


@router.get('/api/social/accounts')
async def accounts(request: Request):
    return {'accounts':await invoke(manager(request).accounts.status)}


@router.post('/api/social/accounts/{platform}/connect')
async def connect(platform: Platform, request: Request):
    # OAuth must be started by the existing same-origin authenticated/CSRF flow.
    origin = request.headers.get('origin','').rstrip('/')
    if not origin:
        origin = str(request.base_url).rstrip('/')
    url,cookie,secure = await invoke(manager(request).accounts.begin,platform,origin)
    response = JSONResponse({'authorization_url':url})
    response.set_cookie('cheat_clip_social_'+platform,cookie,max_age=600,httponly=True,
                        secure=secure,samesite='lax',path='/social/oauth/'+platform+'/callback')
    return response


@router.get('/social/oauth/{platform}/callback',include_in_schema=False)
async def oauth_callback(platform: Platform, request: Request):
    # A public, exact OAuth callback: one-time state AND the short-lived HttpOnly
    # Lax cookie are required. Existing app sessions remain SameSite=Strict.
    params = request.query_params
    if any(len(params.getlist(key)) != 1 for key in ('state',)) or len(params.getlist('code')) > 1:
        raise HTTPException(400,'Callback OAuth tidak valid.')
    try:
        await asyncio.to_thread(manager(request).accounts.callback,platform,params.get('state',''),
            request.cookies.get('cheat_clip_social_'+platform,''),params.get('code',''),bool(params.get('error')))
        message,code = 'Akun berhasil terhubung. Tutup tab ini dan klik Perbarui akun di aplikasi.',200
    except SocialError as exc:
        message,code = str(exc),exc.status
    except Exception:
        message,code = 'Koneksi akun gagal. Periksa pengaturan developer lalu hubungkan ulang.',502
    response = HTMLResponse('<!doctype html><html lang="id"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Koneksi akun</title>'
        '<body style="font:18px system-ui;background:#111827;color:#e2e8f0;padding:40px">'
        '<h1>Koneksi akun</h1><p>'+html.escape(message)+'</p><a style="color:#7dd3fc" href="/">Kembali ke aplikasi</a></body></html>',status_code=code)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"
    response.delete_cookie('cheat_clip_social_'+platform,path='/social/oauth/'+platform+'/callback',samesite='lax')
    return response


@router.delete('/api/social/accounts/{platform}')
async def disconnect(platform: Platform,request: Request):
    service = manager(request)
    if any(not task.done() for task in service.tasks):
        raise HTTPException(409,'Tunggu transfer upload selesai sebelum memutuskan akun.')
    await invoke(service.accounts.disconnect,platform)
    return {'disconnected':True}


@router.get('/api/social/tiktok/creator-info')
async def creator_info(request: Request):
    return await invoke(manager(request).creator_info)


@router.get('/api/social/uploads')
async def uploads(request: Request,batch_id: Optional[str] = None,clip_index: Optional[int] = None):
    return {'uploads':await invoke(manager(request).list,batch_id,clip_index)}


@router.post('/api/social/uploads')
async def upload(body: SocialUploadRequest,request: Request):
    service = manager(request)
    job,created = await invoke(service.accept,body)
    if created:
        service.start(job['request_id'])
    return JSONResponse(job,status_code=202 if created else 200)


@router.post('/api/social/uploads/{request_id}/refresh')
async def refresh(request_id: str,request: Request):
    if not re.fullmatch(r'[0-9a-f-]{36}',request_id):
        raise HTTPException(404,'Upload tidak ditemukan.')
    service = manager(request)
    await invoke(service.load)
    job = service.jobs.get(request_id)
    if not job:
        raise HTTPException(404,'Upload tidak ditemukan.')
    return await invoke(service.refresh_tiktok,job)
