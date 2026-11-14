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

MARKER = "V67_4_DESKTOP_SYNC_MULTI_CONVERSATION"
PATCH_VERSION = "v67.4"
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


def get_header(headers: dict[str, str], name: str) -> str:
    """Return a HTTP header value case-insensitively.

    urllib/http.client does not guarantee the original header key casing after
    a response passes through ASGI/WebView/proxy layers. The browser Fetch API
    is case-insensitive, but this CLI test used a case-sensitive dict lookup and
    could falsely report marker='' while the server was actually canonical.
    """
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value)
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

    print(f"[INFO] Checking desktop sync contract ({PATCH_VERSION}): {base_url}")

    status, headers, original = request_json("GET", f"{sync_url}?t={int(time.time() * 1000)}")
    marker = get_header(headers, "X-Local-AI-GPP-Sync")
    if status != 200 or marker != MARKER:
        print(f"[ERROR] GET /api/desktop/chat-sync is not the canonical EXE route.")
        print(f"[ERROR] status={status}, marker={marker!r}, expected={MARKER!r}")
        print("[INFO] response headers:")
        print(json.dumps(headers, ensure_ascii=False, indent=2))
        try:
            d_status, d_headers, diag = request_json("GET", f"{base_url}/api/desktop/diagnostics?t={int(time.time() * 1000)}")
            print(f"[INFO] diagnostics status={d_status}, marker={get_header(d_headers, 'X-Local-AI-GPP-Sync')!r}")
            print(json.dumps(diag, ensure_ascii=False, indent=2)[:4000])
        except Exception as exc:
            print(f"[INFO] diagnostics request failed: {exc}")
        print("[HINT] Close all LocalAIGPP.exe processes, rebuild, start dist\\LocalAIGPP.exe again, then rerun this check.")
        return 2

    if not isinstance(original, dict) or not isinstance(original.get('conversations'), list):
        print('[ERROR] Invalid shared history response')
        return 3
    print('[OK] Canonical sync marker and history schema verified (read-only; no user history changed).')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
