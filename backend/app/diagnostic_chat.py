"""Shared diagnostic conversations; no window, tray or desktop launcher dependency."""
from __future__ import annotations
import codecs
import contextlib
import copy
import json
import os
import threading
import time
import uuid
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
from fastapi.responses import JSONResponse
from backend.app.context_budget import validate_context
from backend.app.core import MODELS_DIR

SHARED_CHAT_CONVERSATION_ID='conv-shared'
SHARED_CHAT_TITLE='Новый диалог'
PROTOCOL_MARKER='V67_4_DESKTOP_SYNC_MULTI_CONVERSATION'
GENERATION_OWNER=uuid.uuid4().hex
server_port=0


def assistant_state_dir():
    from backend.app.core import PROJECT_ROOT
    from backend.app.model_paths import data_root
    path=data_root(PROJECT_ROOT)/'assistant_state'
    path.mkdir(parents=True,exist_ok=True)
    return path


def chat_sync_file():
    return assistant_state_dir()/'shared_chat_v67.json'

def make_conversation_id() -> str:
    return f"conv-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"


def _new_conversation(*, conversation_id: str | None = None, title: str | None = None, created_at: str | None = None, messages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "id": str(conversation_id or make_conversation_id()),
        "title": str(title or SHARED_CHAT_TITLE),
        "createdAt": str(created_at or time.strftime("%Y-%m-%dT%H:%M:%S")),
        "messages": list(messages or []),
        "clearedVersion": 0,
    }


def _default_chat_state() -> dict[str, Any]:
    now = time.time() * 1000.0
    return {
        "version": 0,
        "storeId": uuid.uuid4().hex,
        "updatedAt": now,
        "source": "launcher-init",
        "activeConversationId": SHARED_CHAT_CONVERSATION_ID,
        "conversations": [
            _new_conversation(conversation_id=SHARED_CHAT_CONVERSATION_ID, title=SHARED_CHAT_TITLE, messages=[])
        ],
    }


chat_sync_lock = threading.RLock()
generation_lock = threading.Lock()
_chat_generation_context = threading.local()
_chat_state_cache: tuple[Any, dict[str, Any]] | None = None


class ChatStateFormatError(ValueError):
    pass


def _chat_file_signature(path: Path | None = None) -> Any:
    path = path if path is not None else chat_sync_file()
    try:
        info = path.stat()
        return (path, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    except FileNotFoundError:
        return None


def _read_cached_chat_state(path: Path) -> dict[str, Any]:
    """Called under chat_sync_lock; callers receive their own mutable state."""
    global _chat_state_cache
    with chat_sync_lock:
        signature = _chat_file_signature(path)
        cached = _chat_state_cache
        if cached is not None and cached[0] == signature:
            return copy.deepcopy(cached[1])
        raw = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(raw, dict) or not isinstance(raw.get('conversations'), list):
            raise ChatStateFormatError('Некорректная структура истории.')
        state = _normalize_chat_state(raw, touch=False)
        if signature == _chat_file_signature(path):
            _chat_state_cache = (signature, copy.deepcopy(state))
        else:
            _chat_state_cache = None
        return state


def message_completion_error(message: dict[str, Any]) -> str:
    if message.get('error'):
        return str(message['error'])
    if (message.get('phase') == 'error' or message.get('finish_reason') == 'length'
            or message.get('reasoning_truncated') or message.get('answer_state') == 'missing_after_reasoning'):
        return 'Ответ не завершён; он не включён в память.'
    return ''


def message_created_at(item: dict[str, Any]) -> str:
    """Legacy records can only recover their stored sync timestamp."""
    if item.get("createdAt"):
        return str(item["createdAt"])
    try:
        stamp = float(item.get("syncUpdatedAt") or 0)
        return datetime.fromtimestamp(stamp / 1000, timezone.utc).isoformat() if stamp > 0 else ""
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def format_message_time(item: dict[str, Any]) -> str:
    try:
        return datetime.fromisoformat(message_created_at(item).replace("Z", "+00:00")).astimezone().strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError, OverflowError, OSError):
        return "Время не сохранено"


def _normalize_chat_message(item: Any, fallback_index: int) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    role = str(item.get("role") or "assistant")
    if role not in {"user", "assistant"}:
        role = "assistant"
    text = str(item.get("text") or item.get("answer") or "").strip()
    if not text:
        return None
    normalized = dict(item)
    normalized["id"] = str(item.get("id") or f"msg-{fallback_index}-{int(time.time() * 1000)}")
    normalized["role"] = role
    normalized["text"] = text
    created_at = message_created_at(item)
    if created_at:
        normalized["createdAt"] = created_at
    return normalized


def _normalize_chat_conversation(item: Any, fallback_index: int) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    raw_id = str(item.get("id") or "").strip()
    conversation_id = raw_id or (SHARED_CHAT_CONVERSATION_ID if fallback_index == 0 else f"conv-import-{fallback_index}")
    title = str(item.get("title") or SHARED_CHAT_TITLE).strip() or SHARED_CHAT_TITLE
    created_at = str(item.get("createdAt") or time.strftime("%Y-%m-%dT%H:%M:%S"))
    messages: list[dict[str, Any]] = []
    raw_messages = item.get("messages")
    if isinstance(raw_messages, list):
        for msg_index, raw_message in enumerate(raw_messages):
            normalized = _normalize_chat_message(raw_message, fallback_index * 10000 + msg_index)
            if normalized:
                messages.append(normalized)
        messages = _merge_message_lists([], messages)
    result = _new_conversation(conversation_id=conversation_id, title=title, created_at=created_at, messages=messages)
    result['clearedVersion'] = max(0, int(item.get('clearedVersion') or 0))
    return result


def _message_count(state: dict[str, Any]) -> int:
    conversations = state.get("conversations") if isinstance(state.get("conversations"), list) else []
    count = 0
    for conv in conversations:
        if isinstance(conv, dict) and isinstance(conv.get("messages"), list):
            count += len(conv.get("messages") or [])
    return count


def _normalize_chat_state(payload: Any, *, touch: bool = False) -> dict[str, Any]:
    base = _default_chat_state()
    if not isinstance(payload, dict):
        return base

    conversations: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw_conversations = payload.get("conversations")
    if isinstance(raw_conversations, list):
        for conv_index, raw_conv in enumerate(raw_conversations):
            normalized = _normalize_chat_conversation(raw_conv, conv_index)
            if not normalized:
                continue
            conv_id = str(normalized.get("id") or "")
            if not conv_id:
                continue
            if conv_id in seen:
                for existing in conversations:
                    if existing.get("id") == conv_id:
                        existing["messages"] = _merge_message_lists(existing.get("messages") or [], normalized.get("messages") or [])
                        if normalized.get("title"):
                            existing["title"] = normalized.get("title")
                        break
            else:
                seen.add(conv_id)
                conversations.append(normalized)

    if not conversations:
        conversations = list(base["conversations"])

    requested_active_id = str(payload.get("activeConversationId") or payload.get("conversationId") or "").strip()
    ids = {str(conv.get("id") or "") for conv in conversations}
    active_id = requested_active_id if requested_active_id in ids else str(conversations[0].get("id") or SHARED_CHAT_CONVERSATION_ID)

    try:
        updated_at = float(payload.get("updatedAt") or base.get("updatedAt") or time.time() * 1000.0)
    except Exception:
        updated_at = time.time() * 1000.0
    if touch:
        updated_at = time.time() * 1000.0
    try:
        version = int(payload.get("version") or base.get("version") or 0)
    except Exception:
        version = 0
    return {
        "version": version,
        "storeId": str(payload.get('storeId') or 'legacy'),
        "resetVersion": max(0, int(payload.get('resetVersion') or 0)),
        "updatedAt": updated_at,
        "source": str(payload.get("source") or ""),
        "activeConversationId": active_id,
        "conversations": conversations,
    }


def _write_chat_state_raw(state: dict[str, Any]) -> dict[str, Any]:
    # V64+: file write only. Do NOT call Tk or WebView from FastAPI/worker threads.
    global _chat_state_cache
    with chat_sync_lock:
        path = chat_sync_file()
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        state['version'] = int(state.get('version') or 0) + 1
        state['updatedAt'] = time.time() * 1000.0
        try:
            temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        _chat_state_cache = None
        return state


def notify_main_chat_sync(state: dict[str, Any]) -> None:
    """Deprecated: polling of the shared store is the safe sync path."""
    return


def read_chat_state() -> dict[str, Any]:
    with chat_sync_lock:
        path = chat_sync_file()
        if not path.exists():
            state = _default_chat_state()
            _write_chat_state_raw(state)
            return state
        try:
            return _read_cached_chat_state(path)
        except (json.JSONDecodeError, UnicodeError, ChatStateFormatError):
            # Keep the damaged history for recovery instead of overwriting it.
            path.replace(path.with_name(path.name + f'.corrupt-{time.time_ns()}'))
            state = _default_chat_state()
            _write_chat_state_raw(state)
            return state


def recover_interrupted_chat() -> None:
    with chat_sync_lock:
        state = read_chat_state_unlocked()
        changed = False
        for conversation in state.get('conversations', []):
            for message in conversation.get('messages', []):
                if message.get('pending') and not (message.get('generationOwner') == GENERATION_OWNER and generation_lock.locked()):
                    partial = str(message.get('answer') or message.get('text') or 'Ответ не получен.')
                    message.update(pending=False, phase='error', text=partial, answer=partial,
                                   error='Генерация прервалась при закрытии приложения. Отправьте запрос снова.')
                    changed = True
        if changed:
            _write_chat_state_raw(state)


def _conversation_message_key(message: dict[str, Any], fallback_index: int) -> str:
    existing = str(message.get("id") or "").strip()
    if existing:
        return existing
    role = str(message.get("role") or "assistant")
    text = str(message.get("text") or message.get("answer") or "")
    return f"legacy-{role}-{fallback_index}-{abs(hash(text))}"


def _message_sync_stamp(message: dict[str, Any]) -> float:
    for key in ("syncUpdatedAt", "updatedAt", "serverUpdatedAt"):
        try:
            value = message.get(key)
            if value is not None:
                return float(value)
        except Exception:
            pass
    return 0.0


def _upsert_message_list(messages: list[dict[str, Any]], incoming_message: dict[str, Any]) -> list[dict[str, Any]]:
    return _merge_message_lists(messages, [incoming_message])


def _merge_message_lists(current: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages = [item for item in current if isinstance(item, dict)]
    positions = {str(item.get('id') or ''): index for index, item in enumerate(messages) if item.get('id')}
    for raw_message in incoming:
        normalized = _normalize_chat_message(raw_message, len(messages))
        if not normalized:
            continue
        message_id = normalized['id']
        index = positions.get(message_id)
        if index is None:
            positions[message_id] = len(messages)
            messages.append(normalized)
        else:
            existing = messages[index]
            if _message_sync_stamp(normalized) < _message_sync_stamp(existing):
                continue
            merged = {**existing, **normalized}
            created_at = message_created_at(existing)
            if created_at:
                merged['createdAt'] = created_at
            messages[index] = merged
    return messages


def _find_conversation(conversations: list[dict[str, Any]], conversation_id: str) -> dict[str, Any] | None:
    for conversation in conversations:
        if isinstance(conversation, dict) and str(conversation.get("id") or "") == conversation_id:
            return conversation
    return None


def _merge_incoming_chat_state_unlocked(incoming: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    result = _normalize_chat_state(existing, touch=False)
    incoming = _normalize_chat_state(incoming, touch=False)
    source = str(incoming.get("source") or "")

    if source == "reset":
        state = _default_chat_state()
        state['version'] = result['version']
        state['storeId'] = result.get('storeId', 'legacy')
        state['resetVersion'] = result['version'] + 1
        state["source"] = "reset"
        return state

    if incoming['version'] < result.get('resetVersion', 0):
        return result
    if source == 'import-browser' and result.get('resetVersion', 0):
        return result

    result_conversations = [dict(conv) for conv in result.get("conversations", []) if isinstance(conv, dict)]
    if not result_conversations:
        result_conversations = list(_default_chat_state()["conversations"])

    incoming_conversations = [dict(conv) for conv in incoming.get("conversations", []) if isinstance(conv, dict)]
    incoming_active_id = str(incoming.get("activeConversationId") or "").strip()

    if source == "clear-conversation":
        target_id = incoming_active_id or str(incoming.get("conversationId") or "")
        if not target_id:
            target_id = str(result.get("activeConversationId") or result_conversations[0].get("id") or SHARED_CHAT_CONVERSATION_ID)
        target = _find_conversation(result_conversations, target_id)
        if target is None:
            incoming_target = incoming_conversations[0] if incoming_conversations else _new_conversation(conversation_id=target_id, title=SHARED_CHAT_TITLE)
            incoming_target["messages"] = []
            result_conversations.insert(0, incoming_target)
        else:
            target["messages"] = []
        target = _find_conversation(result_conversations, target_id)
        if target is not None:
            target['clearedVersion'] = result['version'] + 1
        active_id = target_id
    else:
        incoming_order = [str(conv.get("id") or "") for conv in incoming_conversations if str(conv.get("id") or "")]
        for incoming_conv in incoming_conversations:
            conv_id = str(incoming_conv.get("id") or "")
            if not conv_id:
                continue
            existing_conv = _find_conversation(result_conversations, conv_id)
            if existing_conv is None:
                result_conversations.insert(0, incoming_conv)
                continue
            if incoming['version'] < existing_conv.get('clearedVersion', 0):
                continue
            if source == 'import-browser' and existing_conv.get('clearedVersion', 0):
                continue
            if incoming['version'] >= result['version']:
                existing_conv["title"] = incoming_conv.get("title") or existing_conv.get("title") or SHARED_CHAT_TITLE
            existing_conv["createdAt"] = existing_conv.get("createdAt") or incoming_conv.get("createdAt") or time.strftime("%Y-%m-%dT%H:%M:%S")
            incoming_messages = incoming_conv.get("messages") if isinstance(incoming_conv.get("messages"), list) else []
            if incoming_messages:
                existing_conv["messages"] = _merge_message_lists(existing_conv.get("messages") or [], incoming_messages)
            elif source in {"new-conversation", "main-ui-snapshot"}:
                existing_conv.setdefault("messages", [])
        if incoming_order and source in {"new-conversation", "main-ui-snapshot"}:
            by_id = {str(conv.get("id") or ""): conv for conv in result_conversations}
            ordered = [by_id.pop(conv_id) for conv_id in incoming_order if conv_id in by_id]
            ordered.extend(by_id.values())
            result_conversations = ordered
        ids = {str(conv.get("id") or "") for conv in result_conversations}
        active_id = incoming_active_id if incoming_active_id in ids else str(result.get("activeConversationId") or result_conversations[0].get("id") or SHARED_CHAT_CONVERSATION_ID)

    version = int(result.get("version") or 0)
    return {
        "version": version,
        "storeId": result.get('storeId', 'legacy'),
        "resetVersion": result.get('resetVersion', 0),
        "updatedAt": time.time() * 1000.0,
        "source": source,
        "activeConversationId": active_id,
        "conversations": result_conversations,
    }


def upsert_shared_chat_message(payload: Any, *, begin_generation: bool = False) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return read_chat_state()
    message = payload.get("message")
    if not isinstance(message, dict):
        return read_chat_state()
    if not message.get("syncUpdatedAt"):
        message = dict(message)
        message["syncUpdatedAt"] = float(payload.get("updatedAt") or time.time() * 1000.0)
    with chat_sync_lock:
        existing = read_chat_state_unlocked()
        requested_id = str(payload.get("conversationId") or payload.get("activeConversationId") or "").strip()
        active_id = str(existing.get("activeConversationId") or "")
        conversation_id = requested_id or active_id or SHARED_CHAT_CONVERSATION_ID
        existing_conv = _find_conversation(existing['conversations'], conversation_id) or {}
        token = message.get('generationId')
        if token and not begin_generation:
            original = next((m for m in existing_conv.get('messages', []) if m.get('id') == message.get('id')), {})
            if original.get('generationId') != token:
                return existing
        conversation_title = str(payload.get("conversationTitle") or existing_conv.get('title') or SHARED_CHAT_TITLE)
        created_at = str(payload.get("conversationCreatedAt") or time.strftime("%Y-%m-%dT%H:%M:%S"))
        conv = _new_conversation(conversation_id=conversation_id, title=conversation_title, created_at=created_at, messages=[message])
        state = {
            "version": int(payload.get('baseVersion', existing['version'])),
            "updatedAt": time.time() * 1000.0,
            "source": str(payload.get("source") or ""),
            "activeConversationId": conversation_id if payload.get("activateConversation", True) else active_id,
            "conversations": [conv],
        }
        merged = _merge_incoming_chat_state_unlocked(state, existing)
        # Partial updates contain only the answering dialog; normalization uses
        # that dialog as its active fallback. Restore the viewer's selection.
        if not payload.get('activateConversation', True) and _find_conversation(merged['conversations'], active_id):
            merged['activeConversationId'] = active_id
        return _write_chat_state_raw(merged)


def write_chat_state(payload: Any, *, allow_empty_replace: bool = False) -> dict[str, Any]:
    with chat_sync_lock:
        incoming = _normalize_chat_state(payload, touch=False)
        existing = read_chat_state_unlocked()
        merged = _merge_incoming_chat_state_unlocked(incoming, existing)
        return _write_chat_state_raw(merged)


def read_chat_state_unlocked() -> dict[str, Any]:
    with chat_sync_lock:
        path = chat_sync_file()
        if not path.exists():
            return _default_chat_state()
        try:
            return _read_cached_chat_state(path)
        except (json.JSONDecodeError, UnicodeError, ChatStateFormatError):
            path.replace(path.with_name(path.name + f'.corrupt-{time.time_ns()}'))
            return _default_chat_state()


def reset_chat_state() -> dict[str, Any]:
    with chat_sync_lock:
        existing = read_chat_state_unlocked()
        state = _default_chat_state()
        state['version'] = existing['version']
        state['storeId'] = existing.get('storeId', 'legacy')
        state['resetVersion'] = existing['version'] + 1
        state["updatedAt"] = time.time() * 1000.0
        state["source"] = "reset"
        return _write_chat_state_raw(state)


def clear_chat_conversation(conversation_id: str | None = None) -> dict[str, Any]:
    with chat_sync_lock:
        existing = read_chat_state_unlocked()
        target_id = str(conversation_id or existing.get("activeConversationId") or SHARED_CHAT_CONVERSATION_ID)
        incoming = {
            "source": "clear-conversation",
            "version": existing['version'],
            "activeConversationId": target_id,
            "conversations": [_new_conversation(conversation_id=target_id, title=SHARED_CHAT_TITLE, messages=[])],
        }
        merged = _merge_incoming_chat_state_unlocked(incoming, existing)
        return _write_chat_state_raw(merged)


def append_shared_message(role: str, text: str, *, message_id: str | None = None, conversation_id: str | None = None, conversation_title: str | None = None, conversation_created_at: str | None = None, base_version: int | None = None, _begin_generation: bool = False, **extra: Any) -> dict[str, Any]:
    role = "user" if role == "user" else "assistant"
    clean = str(text or "").strip()
    message = {
        "id": message_id or f"native-{role}-{int(time.time() * 1000)}",
        "role": role,
        "text": clean,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "syncUpdatedAt": time.time() * 1000.0,
    }
    message.update(extra)
    generation = getattr(_chat_generation_context, 'value', None)
    if generation and role == 'assistant' and message['id'] == generation[0]:
        message['generationId'] = generation[1]
    state = read_chat_state_unlocked()
    target_id = str(conversation_id or state.get("activeConversationId") or SHARED_CHAT_CONVERSATION_ID)
    conversation = _find_conversation(state.get('conversations') or [], target_id) or {}
    title = conversation_title or conversation.get('title') or SHARED_CHAT_TITLE
    created_at = conversation_created_at or conversation.get('createdAt') or time.strftime("%Y-%m-%dT%H:%M:%S")
    return upsert_shared_chat_message(
        {
            "source": "assistant",
            "baseVersion": state.get('version', 0) if base_version is None else base_version,
            "activeConversationId": target_id,
            "conversationId": target_id,
            "conversationTitle": title,
            "conversationCreatedAt": created_at,
            "activateConversation": False,
            "message": message,
        }, begin_generation=_begin_generation
    )




def _resolve_desktop_model_path(path_value: Any) -> Path:
    from backend.app.core import resolve_model_file_path
    return resolve_model_file_path(path_value)


def _desktop_model_is_available(item: dict[str, Any]) -> bool:
    if str(item.get("type") or "") != "LLM":
        return False
    path_value = str(item.get("path") or "")
    if path_value.startswith("HUB::"):
        return False
    return _resolve_desktop_model_path(path_value).is_file()

def _read_json_file(path: Path, fallback: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return fallback


def _select_desktop_chat_config(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.app.core import load_models, load_settings, _merge_runtime
    from fastapi import HTTPException
    from backend.app.settings_schema import validate_runtime
    from backend.app.model_registry import model_registry
    settings=load_settings()
    requested_id=str(payload.get('model_id') or '').strip()
    selected=model_registry.select(requested_id or None)
    runtime=_merge_runtime(selected,settings,payload.get('runtime'))
    runtime['max_tokens']=payload.get('max_tokens',runtime.get('max_tokens',1024))
    runtime['temperature']=payload.get('temperature',runtime.get('temperature',.2))
    validate_runtime(runtime,complete=True)
    return {'model_id':selected['id'],'temperature':runtime['temperature'],'max_tokens':runtime['max_tokens'],'runtime':runtime}


def _shared_chat_generation_worker(payload: dict[str, Any], assistant_id: str, conversation_id: str, lock_already_acquired: bool = True) -> None:
    _chat_generation_context.value = (assistant_id, payload.get('_generation_id')) if payload.get('_generation_id') else None
    try:
        config = _select_desktop_chat_config(payload)
        port = int(server_port or 0)
        if not port:
            raise RuntimeError("Локальный сервер ещё не готов.")
        body = json.dumps({
            "model_id": config["model_id"],
            "message": str(payload.get("message") or ""),
            "system_prompt": str(payload.get("system_prompt") or "Ты локальный корпоративный помощник Local AI GPP. Отвечай кратко и по делу на русском языке."),
            "temperature": config["temperature"],
            "max_tokens": config["max_tokens"],
            "runtime": config["runtime"],
            "history": payload.get('history', []),
            "memory": payload.get('memory', True),
        }, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/chat/stream",
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        buffer = ""
        content = ""
        last_write = 0.0

        def update_answer(text_value: str, *, pending: bool, phase: str, extra: dict[str, Any] | None = None) -> None:
            clean = str(text_value or "").strip()
            if not clean:
                clean = "Модель отвечает..." if pending else "Ответ не сформировался."
            payload_extra = dict(extra or {})
            payload_extra.update({"pending": pending, "phase": phase, "answer": clean})
            append_shared_message("assistant", clean, message_id=assistant_id, conversation_id=conversation_id, **payload_extra)

        with urllib.request.urlopen(request, timeout=int(config['runtime'].get('request_timeout_sec', 180)) + 10) as response:
            while True:
                chunk = response.read1(4096)
                if not chunk:
                    buffer += decoder.decode(b"", final=True)
                    break
                buffer += decoder.decode(chunk)
                while "\n\n" in buffer:
                    frame, buffer = buffer.split("\n\n", 1)
                    for line in frame.splitlines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if not raw:
                            continue
                        event = json.loads(raw)
                        event_type = event.get("type")
                        if event_type == "delta":
                            content += str(event.get("text") or "")
                            now = time.time()
                            if now - last_write >= 0.25:
                                last_write = now
                                update_answer(content, pending=True, phase="typing")
                        elif event_type == "worker_status":
                            append_shared_message("assistant", str(event.get("message") or "Runtime запускается."), message_id=assistant_id, conversation_id=conversation_id, pending=True, phase="thinking")
                        elif event_type == "runtime":
                            append_shared_message("assistant", "Модель запущена, готовлю ответ.", message_id=assistant_id, conversation_id=conversation_id, pending=True, phase="thinking", runtime_mode=event.get("mode") or (event.get("runtime") or {}).get("mode"))
                        elif event_type == "done":
                            final_answer = str(event.get("answer") or "") if "answer" in event else str(event.get("content") or content)
                            incomplete = (event.get("finish_reason") == "length" or event.get("reasoning_truncated")
                                          or event.get("answer_state") == "missing_after_reasoning" or not final_answer.strip())
                            update_answer(final_answer or "Финальный ответ не получен. Увеличьте лимит ответа и повторите запрос.",
                                          pending=False, phase="error" if incomplete else "done", extra={
                                "elapsed_ms": event.get("elapsed_ms"),
                                "usage": event.get("usage"),
                                "history_dropped": event.get("history_dropped", 0),
                                "finish_reason": event.get("finish_reason"),
                                "reasoning": event.get("reasoning"),
                                "reasoning_truncated": event.get("reasoning_truncated", False),
                                "answer_state": event.get("answer_state"),
                                "error": "Ответ не завершён; он не включён в память." if incomplete else None,
                                "request_id": event.get("request_id"),
                                "log_path": event.get("log_path"),
                                "log_excerpt": event.get("log_excerpt"),
                                "runtime_mode": (event.get("runtime") or {}).get("mode") if isinstance(event.get("runtime"), dict) else None,
                            })
                            return
                        elif event_type == "error":
                            raise RuntimeError(str(event.get("message") or "Ошибка генерации"))
        raise RuntimeError("Поток завершился без финального события done. Ответ не завершён.")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        message = raw or str(exc)
        with contextlib.suppress(Exception):
            data = json.loads(raw)
            detail = data.get("detail", raw)
            if isinstance(detail, dict):
                message = str(detail.get("message") or json.dumps(detail, ensure_ascii=False))
            else:
                message = str(detail)
        partial = locals().get("content", "")
        append_shared_message("assistant", partial or 'Ответ не получен.', answer=partial or 'Ответ не получен.',
                              error=message, message_id=assistant_id, conversation_id=conversation_id, pending=False, phase="error")
    except Exception as exc:
        partial = locals().get("content", "")
        append_shared_message("assistant", partial or 'Ответ не получен.', answer=partial or 'Ответ не получен.',
                              error=str(exc), message_id=assistant_id, conversation_id=conversation_id, pending=False, phase="error")
    finally:
        _chat_generation_context.value = None
        if lock_already_acquired:
            with contextlib.suppress(Exception):
                generation_lock.release()


def submit_shared_chat_message(payload: dict[str, Any]) -> dict[str, Any] | JSONResponse:
    text = str(payload.get("message") or "").strip()
    if not text:
        return JSONResponse({"ok": False, "message": "Пустой запрос."}, status_code=400)
    if not generation_lock.acquire(blocking=False):
        return JSONResponse({"ok": False, "busy": True, "message": "Уже идёт генерация. Вторую модель не запускаю."}, status_code=409)
    started = False
    assistant_id = str(payload.get('assistant_id') or uuid.uuid4())
    conversation_id = ''
    try:
        # Check input before publishing a pending turn. Every failure releases the lease.
        _select_desktop_chat_config(payload)
        with chat_sync_lock:
            current_state = read_chat_state_unlocked()
            conversation_id = str(payload.get('conversationId') or payload.get('activeConversationId') or current_state['activeConversationId'])
            conversation = _find_conversation(current_state['conversations'], conversation_id) or {}
            base = int(payload.get('baseVersion', current_state['version']))
            if base < max(current_state.get('resetVersion', 0), conversation.get('clearedVersion', 0)):
                return JSONResponse({'ok': False, 'message': 'Диалог был очищен. Обновите состояние и повторите запрос.', 'state': current_state}, status_code=409)
            title = str(payload.get('conversationTitle') or conversation.get('title') or SHARED_CHAT_TITLE)
            created = str(payload.get('conversationCreatedAt') or conversation.get('createdAt') or time.strftime('%Y-%m-%dT%H:%M:%S'))
            payload = dict(payload)
            payload['_generation_id'] = uuid.uuid4().hex
            payload['history'] = [
                {'role': item['role'], 'content': str(item.get('answer') if item.get('answer') is not None else item.get('text', ''))}
                for item in conversation.get('messages', [])
                if item.get('role') in {'user', 'assistant'} and not item.get('pending')
                and not message_completion_error(item) and (item.get('answer') if item.get('answer') is not None else item.get('text'))
            ]
            user_id = str(payload.get('user_id') or uuid.uuid4())
            append_shared_message('user', text, message_id=user_id, conversation_id=conversation_id,
                                  conversation_title=title, conversation_created_at=created)
            state = append_shared_message('assistant', 'Модель готовит ответ. Финальный текст появится здесь же.',
                message_id=assistant_id, conversation_id=conversation_id, conversation_title=title,
                conversation_created_at=created, pending=True, phase='thinking',
                generationId=payload['_generation_id'], generationOwner=GENERATION_OWNER, _begin_generation=True)
        threading.Thread(target=_shared_chat_generation_worker, args=(payload, assistant_id, conversation_id, True), daemon=True).start()
        started = True
        return {'ok': True, 'user_id': user_id, 'assistant_id': assistant_id, 'conversationId': conversation_id, 'state': state}
    except Exception as exc:
        if conversation_id:
            with contextlib.suppress(Exception):
                append_shared_message('assistant', 'Запрос не запущен.', message_id=assistant_id,
                    conversation_id=conversation_id, pending=False, phase='error', error=str(exc))
        return JSONResponse({'ok': False, 'message': str(getattr(exc,'detail',exc))}, status_code=getattr(exc,'status_code',400))
    finally:
        if not started:
            generation_lock.release()



class DiagnosticChatService:
    read=staticmethod(read_chat_state)
    sync=staticmethod(write_chat_state)
    send=staticmethod(submit_shared_chat_message)
    recover=staticmethod(recover_interrupted_chat)


diagnostic_chat=DiagnosticChatService()
