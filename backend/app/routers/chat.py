from __future__ import annotations

import re
import time
import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.app.core import append_request_log, create_chat_completion, create_request_log, read_log_tail, stream_chat_completion

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    model_id: str
    message: str = Field(min_length=1)
    system_prompt: str = ""
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=256, ge=16, le=32768)
    runtime: dict[str, Any] = Field(default_factory=dict)


THINK_BLOCK_RE = re.compile(r"<think>(.*?)</think>", re.IGNORECASE | re.DOTALL)
OPEN_THINK_RE = re.compile(r"<think>", re.IGNORECASE)
FINAL_MARKER_RE = re.compile(
    r"(?:финальный ответ|итоговый ответ|ответ должен быть|final answer|answer|ответ)\s*[:：]\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)


def split_reasoning(content: str) -> dict:
    text = content or ""
    match = THINK_BLOCK_RE.search(text)
    if match:
        before = text[: match.start()].strip()
        reasoning = match.group(1).strip()
        after = text[match.end() :].strip()
        answer = "\n\n".join(part for part in (before, after) if part).strip()
        return {
            "answer": answer,
            "reasoning": reasoning,
            "answer_state": "final_answer" if answer else "missing_after_reasoning",
            "reasoning_truncated": False,
        }

    open_match = OPEN_THINK_RE.search(text)
    if open_match:
        before = text[: open_match.start()].strip()
        reasoning = text[open_match.end() :].strip()
        candidate = extract_answer_candidate(reasoning)
        return {
            "answer": before or candidate,
            "reasoning": reasoning,
            "answer_state": "final_answer" if before else ("extracted_from_reasoning" if candidate else "missing_after_reasoning"),
            "reasoning_truncated": True,
        }

    return {
        "answer": text.strip(),
        "reasoning": "",
        "answer_state": "final_answer" if text.strip() else "empty",
        "reasoning_truncated": False,
    }


def extract_answer_candidate(reasoning: str) -> str:
    match = FINAL_MARKER_RE.search(reasoning or "")
    if not match:
        return ""
    candidate = match.group(1).strip()
    return candidate[:4000].strip()


@router.post("/api/chat")
def chat(payload: ChatRequest) -> dict:
    request_id, log_path = create_request_log(payload.model_id, "chat")
    messages = []
    if payload.system_prompt.strip():
        messages.append({"role": "system", "content": payload.system_prompt.strip()})
    messages.append({"role": "user", "content": payload.message.strip()})
    started = time.perf_counter()
    try:
        result = create_chat_completion(
            model_id=payload.model_id,
            messages=messages,
            max_tokens=payload.max_tokens,
            temperature=payload.temperature,
            request_log_path=log_path,
            runtime_override=payload.runtime,
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
    parsed = split_reasoning(content)
    usage = result.get("usage") or {}
    local_runtime = result.get("_local_ai_gpp", {}) if isinstance(result.get("_local_ai_gpp"), dict) else {}
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
    }


@router.post("/api/chat/stream")
def chat_stream(payload: ChatRequest) -> StreamingResponse:
    request_id, log_path = create_request_log(payload.model_id, "chat_stream")
    messages = []
    if payload.system_prompt.strip():
        messages.append({"role": "system", "content": payload.system_prompt.strip()})
    messages.append({"role": "user", "content": payload.message.strip()})

    def events():
        yield sse({"type": "meta", "request_id": request_id, "log_path": str(log_path)})
        full_text = ""
        started = time.perf_counter()
        for item in stream_chat_completion(
            model_id=payload.model_id,
            messages=messages,
            max_tokens=payload.max_tokens,
            temperature=payload.temperature,
            request_log_path=log_path,
            runtime_override=payload.runtime,
        ):
            if item.get("type") == "delta":
                full_text += str(item.get("text") or "")
            if item.get("type") == "done":
                parsed = split_reasoning(str(item.get("content") or full_text))
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

    return StreamingResponse(events(), media_type="text/event-stream")


def sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
