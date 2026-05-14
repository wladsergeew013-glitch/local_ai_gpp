from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

MARKER = "V67_DESKTOP_SYNC_SINGLE_OWNER"
STATE_PATH = Path(os.getenv("LOCALAPPDATA") or os.getenv("APPDATA") or ".") / "LocalAIGPP" / "instance.json"


def load_instance_url() -> str:
    if not STATE_PATH.exists():
        return ""
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8", errors="replace"))
        url = str(data.get("url") or "").strip().rstrip("/")
        marker = str(data.get("desktop_sync_marker") or "")
        if url and marker == MARKER:
            return url
        if url:
            # Still try it, but the GET header check below remains authoritative.
            return url
    except Exception:
        return ""
    return ""


def request_json(method: str, url: str, payload: Any | None = None, timeout: float = 10.0) -> tuple[int, dict[str, str], Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json; charset=utf-8"} if body is not None else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
            parsed = json.loads(raw) if raw else None
            headers = {key: value for key, value in response.headers.items()}
            return int(response.status), headers, parsed
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(raw) if raw else raw
        except Exception:
            parsed = raw
        headers = {key: value for key, value in exc.headers.items()}
        return int(exc.code), headers, parsed


def find_message(state: Any, message_id: str) -> bool:
    if not isinstance(state, dict):
        return False
    for conv in state.get("conversations") or []:
        if not isinstance(conv, dict):
            continue
        for msg in conv.get("messages") or []:
            if isinstance(msg, dict) and str(msg.get("id") or "") == message_id:
                return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="", help="Running EXE URL, for example http://127.0.0.1:51234")
    args = parser.parse_args()

    base_url = (args.url or load_instance_url()).strip().rstrip("/")
    if not base_url:
        print("[ERROR] Running EXE instance URL was not found.")
        print(f"[INFO] Expected instance file: {STATE_PATH}")
        print("[INFO] Start dist\\LocalAIGPP.exe first, then rerun this check.")
        return 1

    sync_url = f"{base_url}/api/desktop/chat-sync"
    message_url = f"{base_url}/api/desktop/chat-message"

    print(f"[INFO] Checking desktop sync contract: {base_url}")

    status, headers, original = request_json("GET", f"{sync_url}?t={int(time.time() * 1000)}")
    marker = headers.get("X-Local-AI-GPP-Sync", "")
    if status != 200 or marker != MARKER:
        print(f"[ERROR] GET /api/desktop/chat-sync is not the canonical EXE route.")
        print(f"[ERROR] status={status}, marker={marker!r}, expected={MARKER!r}")
        return 2

    stamp = int(time.time() * 1000)
    message_id = f"sync-contract-user-{stamp}"
    message_text = f"SYNC_CONTRACT_TEST_{stamp}"
    payload = {
        "source": "sync-contract-test",
        "updatedAt": stamp,
        "activeConversationId": "conv-shared",
        "conversationId": "conv-shared",
        "conversationTitle": "Тестовый диалог",
        "conversationCreatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "message": {
            "id": message_id,
            "role": "user",
            "text": message_text,
            "syncUpdatedAt": stamp,
        },
    }

    status, _headers, posted = request_json("POST", message_url, payload)
    if status != 200:
        print(f"[ERROR] POST /api/desktop/chat-message failed: HTTP {status}")
        print(posted)
        return 3

    status, headers, state = request_json("GET", f"{sync_url}?t={int(time.time() * 1000)}")
    marker = headers.get("X-Local-AI-GPP-Sync", "")
    if status != 200 or marker != MARKER:
        print(f"[ERROR] GET after POST lost canonical marker: status={status}, marker={marker!r}")
        return 4

    if not find_message(state, message_id):
        print("[ERROR] Posted message was not found in shared desktop chat state.")
        return 5

    # Restore previous state so the smoke check does not pollute user's chat.
    if isinstance(original, dict):
        original["source"] = "sync-contract-restore"
        original["updatedAt"] = time.time() * 1000.0
        request_json("POST", sync_url, original)

    print("[OK] Desktop sync contract is canonical and round-trips through one shared store.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
