import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from backend.config import (
    COOKIES_PATH,
    ROOT_COOKIES_PATH,
    get_effective_cookies_path,
    logger,
)
from backend.schemas.downloads import CookiesSaveRequest

router = APIRouter(tags=["Cookies"])


from backend.video_engine import normalize_to_netscape


@router.post("/api/cookies")
async def validate_youtube_cookies(request: Request):
    content = ""
    # Try reading as JSON first
    try:
        data = await request.json()
        if isinstance(data, dict):
            content = str(data.get("cookies_content") or data.get("cookies") or "")
        elif isinstance(data, list):
            content = json.dumps(data)
    except Exception:
        # Fallback to reading raw body text
        try:
            body_bytes = await request.body()
            content = body_bytes.decode("utf-8", errors="ignore")
        except Exception:
            pass

    normalized = normalize_to_netscape(content)
    if not normalized or len(normalized.strip()) < 10:
        raise HTTPException(
            status_code=400,
            detail="Cookies content is empty or invalid. Please paste or upload valid cookies (Netscape .txt or JSON format).",
        )

    # Ensure server remains stateless: do NOT write cookies to disk!
    # Purge any legacy cookie files on server for user privacy.
    for p in [COOKIES_PATH, ROOT_COOKIES_PATH]:
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass

    return {
        "success": True,
        "status": "valid",
        "storage": "localStorage",
        "size": len(normalized),
        "message": "Cookies validated successfully. Stored securely in your browser's LocalStorage only."
    }


@router.get("/api/cookies")
def get_youtube_cookies_status():
    # Read-only status. Legacy cookie deletion belongs to the CSRF-protected DELETE/POST routes.

    return {
        "exists": False,
        "has_cookies": False,
        "storage": "localStorage",
        "size": 0,
        "sample_lines": [],
        "cookies_content": ""
    }


@router.delete("/api/cookies")
def delete_youtube_cookies():
    # Purge any legacy cookie files on server
    for p in [COOKIES_PATH, ROOT_COOKIES_PATH]:
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass
    return {"success": True, "status": "deleted", "exists": False, "has_cookies": False, "storage": "localStorage"}
