from __future__ import annotations

import time
import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.app.core import append_request_log, create_chat_completion, create_request_log, read_log_tail, stream_chat_completion
from backend.app.streaming import event_response
from backend.app.reasoning import split_reasoning, uses_reasoning

router = APIRouter(tags=["chat"])


class HistoryMessage(BaseModel):
    role: Literal['user', 'assistant']
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    model_id: str
    message: str = Field(min_length=1)
    system_prompt: str = ""
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=512, ge=1, le=32768)
    runtime: dict[str, Any] = Field(default_factory=dict)
    memory: bool = Field(default=True, description='Include supplied history. No history is stored on the API server.')
    history: list[HistoryMessage] = Field(default_factory=list, max_length=8192)


def chat_messages(payload: ChatRequest) -> list[dict[str, str]]:
    messages = []
    if payload.system_prompt.strip():
        messages.append({'role': 'system', 'content': payload.system_prompt.strip()})
    if payload.memory:
        messages.extend(item.model_dump() for item in payload.history)
    messages.append({'role': 'user', 'content': payload.message.strip()})
    return messages


@router.post("/api/chat")
def chat(payload: ChatRequest) -> dict:
    request_id, log_path = create_request_log(payload.model_id, "chat")
    messages = chat_messages(payload)
    started = time.perf_counter()
    try:
        result = create_chat_completion(
            model_id=payload.model_id,
            messages=messages,
            max_tokens=payload.max_tokens,
            temperature=payload.temperature,
            request_log_path=log_path,
            runtime_override=payload.runtime,
            truncate_history=payload.memory,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        append_request_log(log_path, "api_chat_failed", {"error": detail, "request_id": request_id})
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "message": detail,
                "request_id": request_id,
                "log_path": str(log_path),
                "log_excerpt": read_log_tail(log_path),
            },
        ) from exc
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    choice = (result.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = str(message.get("content") or "")
    usage = result.get("usage") or {}
    local_runtime = result.get("_local_ai_gpp", {}) if isinstance(result.get("_local_ai_gpp"), dict) else {}
    parsed = split_reasoning(content, uses_reasoning(local_runtime.get("runtime", {})))
    append_request_log(
        log_path,
        "api_chat_response",
        {
            "request_id": request_id,
            "answer_state": parsed["answer_state"],
            "reasoning_truncated": parsed["reasoning_truncated"],
            "answer_chars": len(parsed["answer"]),
            "reasoning_chars": len(parsed["reasoning"]),
            "answer": parsed["answer"][:8000],
            "reasoning": parsed["reasoning"][:8000],
            "elapsed_ms": elapsed_ms,
        },
    )
    return {
        "answer": parsed["answer"],
        "reasoning": parsed["reasoning"],
        "answer_state": parsed["answer_state"],
        "reasoning_truncated": parsed["reasoning_truncated"],
        "model_id": payload.model_id,
        "finish_reason": choice.get("finish_reason"),
        "usage": usage,
        "elapsed_ms": elapsed_ms,
        "request_id": request_id,
        "log_path": str(log_path),
        "log_excerpt": read_log_tail(log_path),
        "runtime": local_runtime.get("runtime", {}),
        "gpu_snapshot": local_runtime.get("gpu_snapshot_after_generation", {}),
        "history_dropped": local_runtime.get("history_dropped", 0),
    }


@router.post("/api/chat/stream")
def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    request_id, log_path = create_request_log(payload.model_id, "chat_stream")
    messages = chat_messages(payload)

    def events():
        yield sse({"type": "meta", "request_id": request_id, "log_path": str(log_path)})
        full_text = ""
        started = time.perf_counter()
        stream = stream_chat_completion(
            model_id=payload.model_id,
            messages=messages,
            max_tokens=payload.max_tokens,
            temperature=payload.temperature,
            request_log_path=log_path,
            runtime_override=payload.runtime,
            truncate_history=payload.memory,
        )
        try:
            yield from response_events(stream, log_path, request_id, started)
        finally:
            stream.close()

    return event_response(request, events())


def response_events(stream, log_path, request_id, started):
        full_text = ''
        for item in stream:
            if item.get("type") == "delta":
                full_text += str(item.get("text") or "")
            if item.get("type") == "done":
                parsed = split_reasoning(str(item.get("content") or full_text), uses_reasoning(item.get("runtime") or {}))
                elapsed_ms = item.get("elapsed_ms")
                if not isinstance(elapsed_ms, (int, float)):
                    elapsed_ms = round((time.perf_counter() - started) * 1000)
                usage = item.get("usage") if isinstance(item.get("usage"), dict) else {}
                append_request_log(
                    log_path,
                    "api_stream_response",
                    {
                        "request_id": request_id,
                        "answer_state": parsed["answer_state"],
                        "reasoning_truncated": parsed["reasoning_truncated"],
                        "answer_chars": len(parsed["answer"]),
                        "reasoning_chars": len(parsed["reasoning"]),
                        "answer": parsed["answer"][:8000],
                        "reasoning": parsed["reasoning"][:8000],
                        "elapsed_ms": elapsed_ms,
                        "usage": usage,
                    },
                )
                item = {
                    **item,
                    **parsed,
                    "usage": usage,
                    "elapsed_ms": elapsed_ms,
                    "request_id": request_id,
                    "log_path": str(log_path),
                    "log_excerpt": read_log_tail(log_path),
                }
            if item.get("type") == "error":
                item = {**item, "request_id": request_id, "log_path": str(log_path), "log_excerpt": read_log_tail(log_path)}
            yield sse(item)

def sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
