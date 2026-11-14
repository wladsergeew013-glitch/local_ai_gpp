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

    if args.headless:
        uvicorn.run(create_app(), host="127.0.0.1", port=args.port, access_log=False)
        return 0
    import webview

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(),
            host="127.0.0.1",
            port=args.port,
            log_level="warning",
            access_log=False,
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
            "LocalAI.Workspace.0.1"
        )
    window = webview.create_window(
        "Local AI Workspace",
        f"http://127.0.0.1:{args.port}",
        width=1360,
        height=900,
        min_size=(900, 640),
        background_color="#f4f6fa",
    )

    def closing():
        # Uvicorn lifespan stops/observes the owned DSH tree before the host exits.
        server.should_exit = True

    window.events.closed += closing
    if args.smoke_seconds:

        def close_later():
            time.sleep(args.smoke_seconds)
            window.destroy()

        threading.Thread(target=close_later, daemon=True).start()
    try:
        webview.start(debug=False)
    finally:
        server.should_exit = True
        thread.join(20)
        if thread.is_alive():
            raise RuntimeError(
                "Workspace shutdown was not confirmed. Runtime was left independent."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
