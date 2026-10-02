from __future__ import annotations

import time
import uuid
import json
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from backend.app.core import append_request_log, create_chat_completion, create_request_log, find_model, load_models, load_settings, stream_chat_completion
from backend.app.streaming import event_response

router = APIRouter(tags=["openai-compatible"])


class OpenAIMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class OpenAIChatRequest(BaseModel):
    model: str
    messages: list[OpenAIMessage] = Field(min_length=1, max_length=256)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=256, ge=1, le=8192)
    stream: bool = False
    memory: bool = Field(default=True, description='Use all supplied messages; false keeps system messages and the latest user message.')
    stream_options: dict = Field(default_factory=dict)
    chat_template_kwargs: dict[str, bool] = Field(default_factory=dict)


@router.get("/v1/models")
def openai_models(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict:
    settings = load_settings()
    _require_api_key(authorization, x_api_key, settings)
    if not settings.get("server", {}).get("openai_compat_enabled", True):
        raise HTTPException(404, "OpenAI-compatible API is disabled")
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
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
):
    settings = load_settings()
    _require_api_key(authorization, x_api_key, settings)
    if not settings.get("server", {}).get("openai_compat_enabled", True):
        raise HTTPException(status_code=404, detail="OpenAI-compatible API is disabled")
    find_model(payload.model)
    template_runtime(payload)
    request_id, log_path = create_request_log(payload.model, "openai_chat")
    messages = [{"role": msg.role, "content": msg.content} for msg in payload.messages]
    if not payload.memory:
        latest_user = next((item for item in reversed(messages) if item['role'] == 'user'), None)
        if latest_user is None:
            raise HTTPException(400, 'memory=false requires a user message')
        messages = [item for item in messages if item['role'] == 'system'] + [latest_user]
    if payload.stream:
        return event_response(request, openai_events(payload, messages, log_path))
    try:
        result = create_chat_completion(
            model_id=payload.model,
            messages=messages,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            request_log_path=log_path,
            runtime_override=template_runtime(payload),
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


def openai_events(payload, messages, log_path):
    completion_id = f'chatcmpl-{uuid.uuid4().hex}'
    created = int(time.time())

    def chunk(delta, finish=None, usage=None):
        data = {'id': completion_id, 'object': 'chat.completion.chunk', 'created': created,
                'model': payload.model,
                'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
        if usage is not None:
            data.update(choices=[], usage=usage)
        return 'data: ' + json.dumps(data, ensure_ascii=False) + '\n\n'

    yield chunk({'role': 'assistant', 'content': ''})
    stream = stream_chat_completion(model_id=payload.model, messages=messages,
                                   temperature=payload.temperature, max_tokens=payload.max_tokens,
                                   request_log_path=log_path, runtime_override=template_runtime(payload))
    try:
        for event in stream:
            kind = event.get('type')
            if kind == 'delta':
                yield chunk({'content': event['text']})
            elif kind == 'done':
                yield chunk({}, event.get('finish_reason') or 'stop')
                if payload.stream_options.get('include_usage'):
                    yield chunk({}, usage=event.get('usage') or {})
            elif kind == 'error':
                yield 'data: ' + json.dumps({'error': {'message': event.get('message'),
                    'type': 'inference_error', 'code': event.get('status_code', 500)}}) + '\n\n'
            else:
                yield ': keepalive\n\n'
        yield 'data: [DONE]\n\n'
    finally:
        stream.close()


def template_runtime(payload: OpenAIChatRequest) -> dict:
    unknown = set(payload.chat_template_kwargs) - {'enable_thinking'}
    if unknown:
        raise HTTPException(400, 'Supported chat_template_kwargs: enable_thinking')
    return dict(payload.chat_template_kwargs)


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
