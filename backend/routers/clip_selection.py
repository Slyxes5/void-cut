"""Cached Telegram selections; machine transport explicitly excludes file access."""
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.routers.clip_automation import (TelegramRoute, SubmitJob, NotificationUpdate,
    require_transport, read_payload)
from backend.services.clip_automation import ClipJobError
from backend.services.clip_selection import selection_status

router = APIRouter(tags=['Clip selections'])


class SelectionAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: str = Field(pattern=r'^(bind|toggle|select_all|clear|subtitles|confirm|cancel)$', strict=True)
    revision: int = Field(ge=0, strict=True)
    telegram: TelegramRoute
    menu_message_id: str = Field(pattern=r'^[0-9]{1,24}$', strict=True)
    index: Optional[int] = Field(default=None, strict=True)


def invoke(function, *args):
    try:
        return function(*args)
    except ClipJobError as exc:
        raise HTTPException(exc.status_code, getattr(exc, 'detail', str(exc))) from None
    except ValueError:
        raise HTTPException(400, 'Provide a valid supported YouTube video URL.') from None


@router.post('/api/clip/selections')
async def submit(request: Request):
    require_transport(request)
    payload = await read_payload(request, SubmitJob)
    return invoke(request.app.state.clip_selections.submit, payload.url, payload.delivery_id, payload.telegram.model_dump())


@router.get('/api/clip/selections')
async def listing(request: Request, pending_notifications: bool = False):
    require_transport(request)
    store = request.app.state.clip_selections.store
    with store.lock:
        jobs = store.pending_notifications() if pending_notifications else store._jobs()
        return {'jobs': [selection_status(j) for j in jobs if j['expires_at'] > store.clock()]}


@router.get('/api/clip/selections/{job_id}')
async def status(job_id: str, request: Request):
    require_transport(request)
    return invoke(request.app.state.clip_selections.status, job_id)


@router.post('/api/clip/selections/{job_id}/actions')
async def action(job_id: str, request: Request):
    require_transport(request)
    payload = await read_payload(request, SelectionAction)
    return invoke(request.app.state.clip_selections.action, job_id, payload.action, payload.revision,
        payload.telegram.model_dump(), payload.menu_message_id, payload.index)


@router.post('/api/clip/selections/{job_id}/notification')
async def notification(job_id: str, request: Request):
    require_transport(request)
    payload = await read_payload(request, NotificationUpdate)
    return invoke(request.app.state.clip_selections.store.notification, job_id, payload.action, payload.message_id)


@router.get('/api/clip/selections/{job_id}/files/{name}')
async def download(job_id: str, name: str, request: Request):
    if request.scope.get('cheat_clip_authenticated') is not True:
        raise HTTPException(401, 'Sign in to download selection results.')
    path = invoke(request.app.state.clip_selections.file, job_id, name)
    return FileResponse(path, media_type='application/zip' if name == 'clips.zip' else 'video/mp4', filename=name)
