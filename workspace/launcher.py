"""Own only Workspace's server/window/agent tasks. Runtime has a separate lifetime."""

from __future__ import annotations
import argparse
import os
import sys
import threading
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Local AI Workspace")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--port", type=int, default=8776)
    parser.add_argument("--data-dir")
    parser.add_argument("--smoke-seconds", type=float, help=argparse.SUPPRESS)
    parser.add_argument(
        "--self-test-avatar", action="store_true", help=argparse.SUPPRESS
    )
    args = parser.parse_args()
    if args.data_dir:
        os.environ["LOCAL_AI_WORKSPACE_DATA_DIR"] = str(Path(args.data_dir).resolve())
    if sys.stdout is None or sys.stderr is None:
        from workspace.app.store import data_root

        logs = data_root() / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        stream = (logs / "desktop.log").open("a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream
    import uvicorn
    from workspace.app.main import create_app

    if args.self_test_avatar:
        import json
        from workspace.app.store import data_root
        from workspace.app.mini_avatar import MiniAvatar

        root = data_root()
        root.mkdir(parents=True, exist_ok=True)
        avatar = MiniAvatar(
            root, lambda *_: None, lambda *_: None, lambda *_: None, start_hidden=True
        )
        if not avatar.initialized.wait(15):
            raise RuntimeError("Avatar initialization timed out")
        try:
            if avatar.error:
                raise RuntimeError(avatar.error)
            if not avatar.frame_counts.get("ready"):
                raise RuntimeError("Avatar frames are missing")
        finally:
            avatar.close()
        result = {
            "passed": True,
            "owner": "workspace",
            "frames": avatar.frame_counts,
            "thread_stopped": not avatar.thread.is_alive(),
        }
        (root / "avatar-self-test.json").write_text(
            json.dumps(result), encoding="utf-8"
        )
        print(json.dumps(result))
        return 0

    if args.headless:
        uvicorn.run(
            create_app(),
            host="127.0.0.1",
            port=args.port,
            access_log=False,
            timeout_graceful_shutdown=8,
        )
        return 0
    import webview

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(),
            host="127.0.0.1",
            port=args.port,
            log_level="warning",
            access_log=False,
            timeout_graceful_shutdown=8,
        )
    )
    thread = threading.Thread(target=server.run, daemon=False)
    thread.start()
    for _ in range(300):
        if server.started:
            break
        if not thread.is_alive():
            raise RuntimeError(
                "Workspace could not start; port or data directory is already owned."
            )
        time.sleep(0.05)
    if not server.started:
        server.should_exit = True
        thread.join(10)
        raise RuntimeError("Workspace startup timed out.")
    if os.name == "nt":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "LocalAI.Workspace"
        )
    from workspace.app.desktop_shell import WorkspaceShell
    from workspace.app.store import data_root

    WorkspaceShell(server.config.app, server, thread, args.port, data_root()).run(
        args.smoke_seconds
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
