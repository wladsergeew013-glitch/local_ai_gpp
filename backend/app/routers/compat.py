from __future__ import annotations

import time
import uuid
from typing import Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from backend.app.core import append_request_log, create_chat_completion, create_request_log, load_models, load_settings

router = APIRouter(tags=["openai-compatible"])


class OpenAIMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"] | str
    content: str


class OpenAIChatRequest(BaseModel):
    model: str
    messages: list[OpenAIMessage] = Field(min_length=1)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=256, ge=1, le=8192)
    stream: bool = False


@router.get("/v1/models")
def openai_models(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict:
    _require_api_key(authorization, x_api_key)
    models = [
        {
            "id": item.get("id"),
            "object": "model",
            "created": 0,
            "owned_by": "local-ai-gpp",
        }
        for item in load_models()
        if item.get("type") == "LLM" and item.get("file_exists") is not False
    ]
    return {"object": "list", "data": models}


@router.post("/v1/chat/completions")
def openai_chat_completions(
    payload: OpenAIChatRequest,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict:
    settings = load_settings()
    _require_api_key(authorization, x_api_key, settings)
    if not settings.get("server", {}).get("openai_compat_enabled", True):
        raise HTTPException(status_code=404, detail="OpenAI-compatible API is disabled")
    if payload.stream:
        raise HTTPException(status_code=400, detail="Streaming is not implemented yet")
    request_id, log_path = create_request_log(payload.model, "openai_chat")
    messages = [{"role": msg.role, "content": msg.content} for msg in payload.messages]
    try:
        result = create_chat_completion(
            model_id=payload.model,
            messages=messages,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            request_log_path=log_path,
        )
    except HTTPException as exc:
        append_request_log(log_path, "openai_chat_failed", {"error": exc.detail, "request_id": request_id})
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        raise HTTPException(status_code=exc.status_code, detail=f"{detail} Лог выполнения: {log_path}") from exc
    answer = result["choices"][0]["message"]["content"]
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": payload.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": result["choices"][0].get("finish_reason", "stop"),
            }
        ],
        "usage": result.get("usage", {}),
    }


def _require_api_key(
    authorization: str | None,
    x_api_key: str | None,
    settings: dict | None = None,
) -> None:
    settings = settings or load_settings()
    expected = str(settings.get("server", {}).get("api_key") or "").strip()
    if not expected:
        return
    bearer = ""
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[7:].strip()
    if x_api_key == expected or bearer == expected:
        return
    raise HTTPException(status_code=401, detail="Invalid API key")
