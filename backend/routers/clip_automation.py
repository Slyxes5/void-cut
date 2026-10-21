"""Transport for the existing Telegram gateway and authenticated, read-only editor handoffs."""
import json
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from backend.services.clip_automation import ClipJobError, public_job

router = APIRouter(tags=['Clip automation'])


class TelegramRoute(BaseModel):
    model_config = ConfigDict(extra='forbid')
    chat_id: str = Field(pattern=r'^-?[0-9]{1,24}$', strict=True)
    user_id: str = Field(pattern=r'^[0-9]{1,24}$', strict=True)
    message_id: str = Field(pattern=r'^[0-9]{1,24}$', strict=True)
    thread_id: Optional[str] = Field(default=None, pattern=r'^[0-9]{1,24}$', strict=True)


class SubmitJob(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(max_length=2048, strict=True)
    delivery_id: str = Field(pattern=r'^[0-9a-f]{64}$', strict=True)
    telegram: TelegramRoute


class NotificationUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: str = Field(pattern=r'^(claim|sent|failed)$', strict=True)
    message_id: Optional[str] = Field(default=None, pattern=r'^[0-9]{1,24}$', strict=True)


def require_transport(request):
    if not (request.scope.get('cheat_clip_authenticated') or request.scope.get('cheat_clip_automation')):
        raise HTTPException(401, 'Sign in or use the authorized local gateway.')


async def read_payload(request, schema):
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
        raise HTTPException(415, 'JSON request required.')
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8192:
            raise HTTPException(413, 'Clip request is too large.')
    try:
        return schema.model_validate(json.loads(body))
    except (ValueError, UnicodeError, ValidationError):
        # Never reflect input values, which can accidentally contain credentials.
        raise HTTPException(400, 'Invalid clip request fields.') from None


def translate(function, *args):
    try:
        return function(*args)
    except ClipJobError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None
    except ValueError:
        raise HTTPException(400, 'Provide a valid supported YouTube video URL.') from None


@router.post('/api/clip/jobs')
async def submit_job(request: Request):
    require_transport(request)
    payload = await read_payload(request, SubmitJob)
    return translate(request.app.state.clip_jobs.submit, payload.url, payload.delivery_id, payload.telegram.model_dump())


@router.get('/api/clip/jobs')
async def list_jobs(request: Request, pending_notifications: bool = False):
    require_transport(request)
    store = request.app.state.clip_jobs.store
    jobs = store.pending_notifications() if pending_notifications else store._jobs()
    return {'jobs': [public_job(job) for job in jobs if job['expires_at'] > store.clock()]}


@router.get('/api/clip/jobs/{job_id}')
async def job_status(job_id: str, request: Request):
    require_transport(request)
    return translate(request.app.state.clip_jobs.status, job_id)


@router.post('/api/clip/jobs/{job_id}/notification')
async def notification(job_id: str, request: Request):
    require_transport(request)
    payload = await read_payload(request, NotificationUpdate)
    return translate(request.app.state.clip_jobs.store.notification, job_id, payload.action, payload.message_id)


@router.get('/api/clip/handoffs/{job_id}')
async def get_handoff(job_id: str, request: Request):
    if request.scope.get('cheat_clip_authenticated') is not True:
        raise HTTPException(401, 'Sign in to open this editor handoff.')
    return translate(request.app.state.clip_jobs.handoff, job_id)
