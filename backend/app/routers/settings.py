from __future__ import annotations

from starlette.concurrency import run_in_threadpool

from fastapi import APIRouter, File, UploadFile, Header, HTTPException, Response

from backend.app.core import get_logo_url, load_settings, public_settings, save_settings, upload_logo_file

router = APIRouter(tags=["settings"])


@router.get("/api/settings")
def get_settings(response: Response) -> dict:
    data = public_settings()
    response.headers['ETag'] = '"' + data['_revision'] + '"'
    data["branding"]["logo_url"] = get_logo_url()
    return data


@router.put("/api/settings")
def put_settings(payload: dict, response: Response, if_match: str | None = Header(default=None)) -> dict:
    data = public_settings(save_settings(payload, expected_revision=if_match.strip('"') if if_match else None))
    response.headers['ETag']='"'+data['_revision']+'"' 
    data["branding"]["logo_url"] = get_logo_url()
    return data


@router.post("/api/settings/logo")
async def post_logo(logo_file: UploadFile = File(...)) -> dict:
    await run_in_threadpool(upload_logo_file, logo_file.file, logo_file.filename or 'logo.png')
    return public_settings()


@router.patch('/api/settings')
def patch_settings(payload: dict, response: Response, if_match: str | None = Header(default=None)) -> dict:
    if not if_match:raise HTTPException(428,'SETTINGS_REVISION_REQUIRED: send If-Match from GET /api/settings')
    return put_settings(payload,response,if_match)
