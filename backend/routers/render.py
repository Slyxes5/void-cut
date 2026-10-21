import asyncio
import hashlib
import json
import re
import time
import uuid
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from backend.config import (
    ACTIVE_ENCODER_NAME,
    detect_hardware_support,
)
from backend.schemas.render import (
    RenderBatchRequest,
    RetryBatchRequest,
)
from backend.services.render_service import (
    BATCH_REQUESTS,
    RENDER_BATCHES,
    process_batch_rendering,
    process_batch_retry,
)

from backend.services.render_status import OUTPUT_NAME, RAW_DOWNLOAD_NAME, batch_snapshot, recent_batches, safe_export, finalized_zip

router = APIRouter(tags=["Render"])
# Only request digests and original response metadata, never a second copy of request secrets.
# Creation is await-free on the event loop: check + register + schedule are atomic per process.
REQUEST_IDS = {}


@router.get("/api/render-request/{request_id}")
async def get_render_request(request_id: str):
    accepted = REQUEST_IDS.get(request_id)
    snapshot = batch_snapshot(accepted['response']['batch_id']) if accepted else None
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Render request not found", headers={"Cache-Control": "no-store"})
    return JSONResponse(snapshot, headers={"Cache-Control": "no-store"})


@router.get("/api/render-batches")
async def get_render_batches():
    return JSONResponse({"batches": recent_batches()}, headers={"Cache-Control": "no-store"})


@router.get("/api/render-status/{batch_id}")
async def get_render_status(batch_id: str):
    snapshot = batch_snapshot(batch_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Batch not found", headers={"Cache-Control": "no-store"})
    return JSONResponse(snapshot, headers={"Cache-Control": "no-store"})


@router.post("/api/render-batch")
async def start_batch_render(
    request: RenderBatchRequest,
    background_tasks: BackgroundTasks,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    if not request.clips:
        raise HTTPException(status_code=400, detail="No clips provided for rendering")

    digest = None
    if idempotency_key is not None:
        if not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', idempotency_key):
            raise HTTPException(status_code=422, detail="Idempotency-Key must be a UUID")
        normalized = json.dumps(request.model_dump(mode='json'), sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        digest = hashlib.sha256(normalized.encode('utf-8')).hexdigest()
        accepted = REQUEST_IDS.get(idempotency_key)
        if accepted:
            if accepted['digest'] != digest:
                raise HTTPException(status_code=409, detail="Idempotency-Key already used for a different request")
            return {**accepted['response'], 'reused': True}
        # Two open tabs may generate different keys for the same active request.
        # Reuse that exact request only while it is queued/running; a deliberately
        # new batch after completion remains supported with a fresh key.
        active = next((entry for entry in REQUEST_IDS.values()
                       if entry['digest'] == digest
                       and RENDER_BATCHES.get(entry['response']['batch_id'], {}).get('overall_status') == 'running'), None)
        if active:
            REQUEST_IDS[idempotency_key] = active
            return {**active['response'], 'reused': True}

    batch_id = f"batch_{int(time.time())}_{uuid.uuid4().hex[:6]}"

    is_merged = bool(request.settings and getattr(request.settings, "render_mode", "separate") == "merged")
    pfx = (request.settings.title_prefix or "") if request.settings else ""
    sfx = (request.settings.title_suffix or "") if request.settings else ""

    clips_status = []
    if is_merged:
        raw_comp_title = (request.settings.compilation_title or "").strip()
        if not raw_comp_title:
            first_clip = request.clips[0]
            first_base = (first_clip.get("custom_title") or first_clip.get("title_suggestion") or first_clip.get("title") or "Highlight Video").strip()
            raw_comp_title = f"{first_base} (Merged)"
        full_comp_title = f"{pfx}{raw_comp_title}{sfx}".strip() if (pfx or sfx) else raw_comp_title
        clips_status.append({
            "clip_index": 0,
            "title": full_comp_title,
            "base_title": raw_comp_title,
            "status": "pending",
            "progress_percent": 0,
            "is_merged": True,
            "segments_count": len(request.clips)
        })
        RENDER_BATCHES[batch_id] = {
            "batch_id": batch_id,
            "total_clips": 1,
            "current_clip_index": 0,
            "overall_status": "running",
            "clips": clips_status,
            "zip_url": None,
            "is_merged": True,
            "merged_segments_count": len(request.clips)
        }
    else:
        for idx, c in enumerate(request.clips):
            base_t = (c.get("custom_title") or c.get("title_suggestion") or c.get("title") or f"Clip {idx+1}").strip()
            full_t = f"{pfx}{base_t}{sfx}".strip() if (pfx or sfx) else base_t
            clips_status.append({
                "clip_index": idx,
                "title": full_t,
                "base_title": base_t,
                "status": "pending",
                "progress_percent": 0
            })
        RENDER_BATCHES[batch_id] = {
            "batch_id": batch_id,
            "total_clips": len(request.clips),
            "current_clip_index": 0,
            "overall_status": "running",
            "clips": clips_status,
            "zip_url": None,
            "is_merged": False
        }

    BATCH_REQUESTS[batch_id] = request
    RENDER_BATCHES[batch_id]['request_id'] = idempotency_key
    response = {"batch_id": batch_id, "total_clips": 1 if is_merged else len(request.clips), "is_merged": is_merged}
    if idempotency_key is not None:
        REQUEST_IDS[idempotency_key] = {'digest': digest, 'response': response}

    background_tasks.add_task(process_batch_rendering, batch_id, request)
    return response


@router.post("/api/render-batch/{batch_id}/retry")
async def retry_batch_rendering(
    batch_id: str,
    background_tasks: BackgroundTasks,
    body: Optional[RetryBatchRequest] = None
):
    if batch_id not in RENDER_BATCHES:
        raise HTTPException(status_code=404, detail="Batch not found")
    batch = RENDER_BATCHES[batch_id]
    if batch.get("overall_status") == "running":
        raise HTTPException(status_code=409, detail="Batch is running or queued. Please wait for it to finish.")
    if batch_id not in BATCH_REQUESTS:
        raise HTTPException(status_code=400, detail="Batch configuration expired. Please start a new render.")

    req = BATCH_REQUESTS[batch_id]
    indices_to_retry: List[int] = []
    if body and body.clip_indices is not None:
        indices_to_retry = sorted(set(body.clip_indices))
        if any(i < 0 or i >= len(batch['clips']) or batch['clips'][i].get('status') != 'error'
               for i in indices_to_retry):
            raise HTTPException(status_code=400, detail="Only failed clips can be retried.")
    else:
        indices_to_retry = [i for i, c in enumerate(batch["clips"]) if c.get("status") == "error"]

    if not indices_to_retry:
        raise HTTPException(status_code=400, detail="No failed clips to retry in this batch.")

    if body and body.cookies:
        req.cookies = body.cookies

    for idx in indices_to_retry:
        batch["clips"][idx]["status"] = "pending"
        batch["clips"][idx]["progress_percent"] = 0
        batch["clips"][idx]["error_message"] = None
        batch["clips"][idx]["error"] = None

    batch["overall_status"] = "running"
    batch["error_message"] = None
    batch["warning_message"] = None

    background_tasks.add_task(process_batch_retry, batch_id, indices_to_retry)
    return {
        "status": "started",
        "batch_id": batch_id,
        "retrying_clips": indices_to_retry
    }


@router.get("/api/render-progress/{batch_id}")
async def get_render_progress(batch_id: str):
    if batch_snapshot(batch_id) is None:
        raise HTTPException(status_code=404, detail="Batch not found", headers={"Cache-Control": "no-store"})

    async def stream():
        last_sent_json = None
        idle_count = 0
        while True:
            batch = batch_snapshot(batch_id)
            if not batch:
                break
            batch_json = json.dumps(batch)
            if batch_json != last_sent_json:
                yield f"data: {batch_json}\n\n"
                last_sent_json = batch_json
                idle_count = 0
            else:
                idle_count += 1
                # Send SSE keep-alive comment every 5 iterations (~2.5s) to prevent client/proxy timeout during lag
                if idle_count % 5 == 0:
                    yield ": keep-alive\n\n"
            if batch.get("overall_status") in ["completed", "error"]:
                # Yield final state once and break
                yield f"data: {batch_json}\n\n"
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.get("/api/download-rendered/{file_name}")
def download_rendered_file(file_name: str, title: Optional[str] = None):
    safe_name = file_name
    file_path = safe_export(safe_name) if (OUTPUT_NAME.fullmatch(safe_name) or RAW_DOWNLOAD_NAME.fullmatch(safe_name)) else None
    if file_path is None:
        raise HTTPException(status_code=404, detail="Rendered clip not found")

    # If title provided, sanitize and use as download filename
    dl_filename = safe_name
    if title and title.strip():
        clean_title = re.sub(r'[\\/*?:"<>|]', "", title.strip())
        if clean_title:
            dl_filename = f"{clean_title}.mp4" if not clean_title.lower().endswith(".mp4") else clean_title

    return FileResponse(file_path, media_type="video/mp4", filename=dl_filename)


@router.get("/api/download-batch-zip/{batch_id}")
def download_batch_zip(batch_id: str):
    if RENDER_BATCHES.get(batch_id, {}).get('overall_status') == 'running':
        raise HTTPException(status_code=409, detail="Batch ZIP is not finalized while rendering")
    file_path = finalized_zip(batch_id)
    if file_path is None:
        raise HTTPException(status_code=404, detail="Batch zip file not found")
    return FileResponse(file_path, media_type="application/zip", filename=file_path.name)


@router.get("/api/hardware-accel")
def get_hardware_acceleration_status():
    """Returns detected GPU/CPU hardware acceleration options and recommendations."""
    support = detect_hardware_support()
    rec = support.get("recommended", "cpu")
    return {
        "status": "success",
        "active_default": ACTIVE_ENCODER_NAME,
        "recommended": rec,
        "support": support,
        "options": [
            {
                "id": "auto",
                "label": "Auto Detect",
                "sub": f"Recommended ({rec.upper()})",
                "available": True,
            },
            {
                "id": "nvenc",
                "label": "NVIDIA NVENC",
                "sub": "GeForce & RTX Hardware Acceleration",
                "available": support.get("nvenc", False),
            },
            {
                "id": "amf",
                "label": "AMD AMF",
                "sub": "Radeon RX & APU Hardware Acceleration",
                "available": support.get("amf", False),
            },
            {
                "id": "qsv",
                "label": "Intel QuickSync",
                "sub": "Intel Arc & UHD Hardware Acceleration",
                "available": support.get("qsv", False),
            },
            {
                "id": "cpu",
                "label": "CPU Software (libx264)",
                "sub": "Multi-threaded CPU (100% Universal)",
                "available": True,
            },
        ],
    }
