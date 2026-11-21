"""Local status, inference checks and backups for Runtime and Workspace."""

from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

CONTROL_TOKEN_HEADER = "X-Workspace-Control-Token"
CONTROL_FILE = ".workspace-control.json"
CHECK_PROMPT = "Reply with exactly the two letters OK."


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes as ct
    from ctypes import wintypes

    class Blob(ct.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ct.POINTER(ct.c_byte))]

    buffer = (ct.c_byte * len(data)).from_buffer_copy(data)
    source, result = Blob(len(data), buffer), Blob()
    crypt32 = ct.WinDLL("crypt32", use_last_error=True)
    transform = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    transform.argtypes = [
        ct.POINTER(Blob), ct.c_void_p, ct.c_void_p, ct.c_void_p, ct.c_void_p,
        wintypes.DWORD, ct.POINTER(Blob),
    ]
    transform.restype = wintypes.BOOL
    local_free = ct.WinDLL("kernel32", use_last_error=True).LocalFree
    local_free.argtypes = [ct.c_void_p]
    local_free.restype = ct.c_void_p
    if not transform(ct.byref(source), None, None, None, None, 1, ct.byref(result)):
        raise ct.WinError(ct.get_last_error())
    try:
        return ct.string_at(result.data, result.size)
    finally:
        local_free(result.data)


def publish_control(root: Path, port: int, token: str) -> Path:
    if not 1 <= port <= 65535:
        raise ValueError("Control requires a fixed loopback port.")
    secret = token.encode("ascii")
    if os.name == "nt":
        secret = _dpapi(secret, protect=True)
    destination = root / CONTROL_FILE
    with tempfile.NamedTemporaryFile(dir=root, delete=False, mode="w", encoding="utf-8") as handle:
        temporary = Path(handle.name)
        try:
            json.dump({"version": 1, "port": port, "auth": base64.b64encode(secret).decode("ascii")}, handle)
            handle.close()
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    return destination


def read_control(root: Path) -> tuple[str, dict[str, str]]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(root / CONTROL_FILE, flags), "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Invalid Workspace control file.")
        if os.name != "nt" and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise PermissionError("Workspace control file must be private to the current user.")
        ticket = json.loads(handle.read(8192))
    if not isinstance(ticket, dict):
        raise ValueError("Invalid Workspace control file.")
    port = ticket.get("port")
    if ticket.get("version") != 1 or type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Unsupported Workspace control file.")
    secret = base64.b64decode(ticket["auth"], validate=True)
    if os.name == "nt":
        secret = _dpapi(secret, protect=False)
    token = secret.decode("ascii")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise ValueError("Invalid Workspace control credential.")
    return f"http://127.0.0.1:{port}/api/control", {CONTROL_TOKEN_HEADER: token}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def json_request(url: str, headers: dict, payload=None, timeout=15):
    address = urlsplit(url)
    try:
        local = ipaddress.ip_address(address.hostname).is_loopback
    except ValueError:
        local = address.hostname == "localhost"
    if address.scheme != "http" or not local or address.username is not None or address.query or address.fragment:
        raise ValueError("Use an http:// loopback URL without embedded credentials or query parameters.")
    if address.hostname == "localhost":
        port = f":{address.port}" if address.port else ""
        url = address._replace(netloc="127.0.0.1" + port).geturl()
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, body, {**headers, "Content-Type": "application/json"})
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        with exc:
            try:
                reply = json.loads(exc.read(16384))
                detail = reply.get("detail") or reply.get("error")
                if isinstance(detail, dict):
                    detail = detail.get("message")
            except (AttributeError, ValueError):
                detail = None
        raise ValueError(str(detail or f"Local API returned HTTP {exc.code}.")) from None


def check_payload(model: str) -> dict:
    return {
        "model": model,
        "messages": [{"role": "user", "content": CHECK_PROMPT}],
        "max_tokens": 32,
        "temperature": 0,
        "memory": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def check_summary(model: str, result: dict) -> dict:
    choices = result.get("choices") or []
    choice = choices[0] if choices else {}
    text = (choice.get("message") or {}).get("content") or ""
    return {
        "ok": choice.get("finish_reason") == "stop" and bool(text.strip()),
        "model": model,
        "finish_reason": choice.get("finish_reason"),
        "completion_tokens": (result.get("usage") or {}).get("completion_tokens"),
    }


def portable_profile(package_dir: Path | None = None) -> Path:
    if package_dir is None:
        package_dir = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    if package_dir.name.lower() == "worker" and package_dir.parent.name.lower() == "dist":
        return package_dir.parent.parent / "workspace-profile"
    if package_dir.name == "workspace" and (package_dir / "launcher.py").is_file():
        return package_dir.parent / "workspace-profile"
    return package_dir / "workspace-profile"


def workspace_root() -> Path:
    configured = os.getenv("LOCAL_AI_WORKSPACE_DATA_DIR")
    if configured:
        return Path(configured).resolve()
    portable = portable_profile()
    if (portable / "workspace.sqlite3").is_file():
        return portable
    return Path(os.getenv("LOCALAPPDATA", Path.home() / ".local/share")) / "LocalAIWorkspace"


def run(args) -> dict:
    if args.product == "workspace":
        try:
            base, headers = read_control(args.data_dir or workspace_root())
        except FileNotFoundError:
            raise ValueError("Start Workspace with support for the local control client.") from None
        if args.action == "status":
            return json_request(base + "/status", headers, timeout=120)
        payload = {"provider_id": args.provider} if args.provider else {}
        if args.action == "select-model":
            if not args.model:
                raise ValueError("Specify the exact Runtime ID with --model.")
            payload["model"] = args.model
        return json_request(base + "/" + args.action, headers, payload, timeout=210)

    if args.action not in {"status", "check"}:
        raise ValueError("Only status and check apply to Runtime.")
    base = args.runtime_url.rstrip("/")
    if not urlsplit(base).path.endswith("/v1"):
        raise ValueError("Specify Runtime's API base URL ending with /v1.")
    key = os.getenv("LOCAL_AI_API_KEY")
    headers = {"Authorization": "Bearer " + key} if key else {}
    models = [row["id"] for row in json_request(base + "/models", headers)["data"]]
    if args.action == "status":
        readiness = json_request(base + "/runtime/readiness", headers)
        return {
            "models": models,
            "state": readiness.get("state"),
            "ready_model_ids": readiness.get("ready_model_ids", []),
            "runtime": [
                {key: row[key] for key in ("model_id", "state", "runtime_mode", "gpu_offloaded_layers", "model_layers") if key in row}
                for row in readiness.get("models", [])
            ],
        }
    model = args.model or (models[0] if len(models) == 1 else "")
    if model not in models:
        raise ValueError("Choose an available model with --model. Run runtime status to list IDs.")
    result = json_request(base + "/chat/completions", headers, check_payload(model), timeout=200)
    return check_summary(model, result)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("product", choices=("runtime", "workspace"))
    parser.add_argument("action", choices=("status", "check", "select-model", "backup"))
    parser.add_argument("--runtime-url", default="http://127.0.0.1:8765/v1")
    parser.add_argument("--data-dir", type=Path, help="Workspace data directory")
    parser.add_argument("--provider", help="Workspace provider ID")
    parser.add_argument("--model", help="Exact model ID from Runtime")
    parser.add_argument("--output", type=Path, help="Write the JSON result to a new file")
    args = parser.parse_args(argv)
    try:
        result = run(args)
        output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            with args.output.open("x", encoding="utf-8") as handle:
                handle.write(output)
        print(output, end="")
        return 0 if result.get("ok", True) else 1
    except (ValueError, KeyError, TypeError, OSError, URLError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
