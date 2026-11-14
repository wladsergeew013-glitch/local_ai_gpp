from __future__ import annotations

import time
import uuid
import json

from fastapi import APIRouter, Header, HTTPException, Request, Response
from backend.app.compat_schema import OpenAIMessage, OpenAIChatRequest
from backend.app.runtime_manager import runtime_manager

from backend.app.core import append_request_log, create_chat_completion, create_request_log, find_model, load_models, load_settings, stream_chat_completion
from backend.app.streaming import event_response
from backend.app.reasoning import split_reasoning, uses_reasoning

router = APIRouter(tags=["openai-compatible"])


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
    response: Response,
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
    messages = [msg.model_dump(exclude_none=True) for msg in payload.messages]
    if not payload.memory:
        latest_user = next((item for item in reversed(messages) if item['role'] == 'user'), None)
        if latest_user is None:
            raise HTTPException(400, 'memory=false requires a user message')
        messages = [item for item in messages if item['role'] == 'system'] + [latest_user]
    lease = runtime_manager.admit(request.headers.get('X-Local-AI-Owner-ID', ''), request.headers.get('X-Local-AI-Owner-Token', ''))
    headers = {'X-Local-AI-Request-ID': lease.request_id, 'X-Local-AI-Cancel-Token': lease.token, 'X-Local-AI-API-Version': '1.1'}
    if payload.stream:
        return event_response(request, openai_events(payload, messages, log_path, lease), on_close=lease.close, headers=headers)
    response.headers.update(headers)
    try:
        result = create_chat_completion(
            model_id=payload.model,
            messages=messages,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            request_log_path=log_path,
            runtime_override=template_runtime(payload),
            tools=[tool.model_dump() for tool in payload.tools], tool_choice=payload.tool_choice, parallel_tool_calls=payload.parallel_tool_calls, lease=lease,
        )
    except HTTPException as exc:
        append_request_log(log_path, "openai_chat_failed", {"error": exc.detail, "request_id": request_id})
        raise
    finally:
        lease.close()
    result_message = result['choices'][0]['message']
    raw = result_message.get('content') or ''
    runtime = result.get('_local_ai_gpp', {}).get('runtime', {})
    parsed = split_reasoning(raw, uses_reasoning(runtime))
    if result_message.get('tool_calls'):
        parsed['answer_state'] = 'tool_calls'
    answer = parsed['answer']
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": payload.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer or (None if result_message.get('tool_calls') else ''),
                            "reasoning_content": parsed["reasoning"],
                            **({'tool_calls': result_message['tool_calls']} if result_message.get('tool_calls') else {})},
                "finish_reason": result["choices"][0].get("finish_reason", "stop"),
            }
        ],
        "usage": result.get("usage", {}),
        "_local_ai_gpp": {**parsed, "runtime": runtime},
    }


def openai_events(payload, messages, log_path, lease=None):
    completion_id = f'chatcmpl-{uuid.uuid4().hex}'
    created = int(time.time())

    def chunk(delta, finish=None, usage=None, metadata=None):
        data = {'id': completion_id, 'object': 'chat.completion.chunk', 'created': created,
                'model': payload.model,
                'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
        if metadata is not None:
            data['_local_ai_gpp'] = metadata
        if usage is not None:
            data.update(choices=[], usage=usage)
        return 'data: ' + json.dumps(data, ensure_ascii=False) + '\n\n'

    yield chunk({'role': 'assistant', 'content': ''})
    stream = stream_chat_completion(model_id=payload.model, messages=messages,
                                   temperature=payload.temperature, max_tokens=payload.max_tokens,
                                   request_log_path=log_path, runtime_override=template_runtime(payload),
                                   tools=[tool.model_dump() for tool in payload.tools], tool_choice=payload.tool_choice, parallel_tool_calls=payload.parallel_tool_calls, lease=lease)
    full_text, sent_answer, runtime = '', 0, {}
    try:
        for event in stream:
            kind = event.get('type')
            if kind == 'runtime':
                runtime = event.get('runtime') or {}
            elif kind == 'delta':
                full_text += event['text']
                if uses_reasoning(runtime):
                    # Wait for the phase delimiter; never emit reasoning as answer content.
                    parsed = split_reasoning(full_text, True)
                    answer = parsed['answer']
                    if len(answer) > sent_answer:
                        yield chunk({'content': answer[sent_answer:]})
                        sent_answer = len(answer)
                else:
                    yield chunk({'content': event['text']})
            elif kind == 'done':
                runtime = event.get('runtime') or runtime
                parsed = split_reasoning(event.get('content') or full_text, uses_reasoning(runtime))
                if event.get('tool_calls'):
                    parsed['answer_state'] = 'tool_calls'
                yield chunk({}, event.get('finish_reason') or 'stop', metadata={**parsed, 'runtime': runtime})
                if payload.stream_options.get('include_usage'):
                    yield chunk({}, usage=event.get('usage') or {})
            elif kind == 'tool_delta':
                yield chunk({'tool_calls': event['tool_calls']})
            elif kind == 'error':
                from backend.app.api_errors import error_code
                yield 'data: ' + json.dumps({'error': {'message': event.get('message'),
                    'type': 'inference_error', 'code': error_code(event.get('status_code',500),event.get('message'))}}) + '\n\n'
            else:
                yield ': keepalive\n\n'
        yield 'data: [DONE]\n\n'
    finally:
        stream.close()
        if lease is not None:
            lease.close()


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
    if settings.get('_access_policy_error'):
        raise HTTPException(503, 'SETTINGS_POLICY_UNAVAILABLE: repair the server access policy')
    expected = str(settings.get("server", {}).get("api_key") or "").strip()
    if not expected:
        return
    bearer = ""
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[7:].strip()
    if x_api_key == expected or bearer == expected:
        return
    raise HTTPException(status_code=401, detail="Invalid API key")
