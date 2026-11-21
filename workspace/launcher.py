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
    parser.add_argument(
        "--ui-token-stdin",
        action="store_true",
        help="Read the headless operator token from stdin.",
    )
    parser.add_argument("--port", type=int, default=8776)
    profile = parser.add_mutually_exclusive_group()
    profile.add_argument("--data-dir")
    profile.add_argument("--portable", action="store_true", help="Use the workspace-profile folder on this drive.")
    parser.add_argument("--import-skills", metavar="MANIFEST", help="Import a local MCP skill manifest and exit.")
    parser.add_argument("--bundle-root", help="Directory for relative paths in a skill manifest.")
    parser.add_argument("--smoke-seconds", type=float, help=argparse.SUPPRESS)
    parser.add_argument(
        "--self-test-avatar", action="store_true", help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--self-test-bubble", action="store_true", help=argparse.SUPPRESS
    )
    args = parser.parse_args()
    if args.ui_token_stdin and (not args.headless or sys.stdin is None):
        parser.error("--ui-token-stdin requires --headless and an input stream")
    explicit_profile = args.data_dir or os.environ.get("LOCAL_AI_WORKSPACE_DATA_DIR")
    skills_directory = None
    if getattr(sys, "frozen", False) and (args.portable or not explicit_profile):
        skills_directory = Path(sys.executable).resolve().parent / "Skills"
    if args.portable:
        from workspace.local_ai_control import portable_profile
        os.environ["LOCAL_AI_WORKSPACE_DATA_DIR"] = str(portable_profile())
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
    if args.bundle_root and not args.import_skills:
        parser.error("--bundle-root requires --import-skills")
    if args.import_skills:
        import json
        from workspace.app.skill_import import import_manifest
        from workspace.app.store import data_root
        try:
            result = import_manifest(data_root(), args.import_skills, args.bundle_root, skills_directory=skills_directory)
        except (KeyError, TypeError, ValueError, OSError) as exc:
            parser.exit(1, f"Skill import failed: {exc}\n")
        print(json.dumps(result), flush=True)
        return 0
    import uvicorn
    from workspace.app.main import create_app

    if args.self_test_bubble:
        import json
        import tkinter as tk
        from workspace.app.native_chat import NativeChatBubble
        from workspace.app.store import data_root

        ui = tk.Tk()
        ui.withdraw()
        panel = NativeChatBubble(
            ui,
            "http://127.0.0.1:1",
            lambda: (1000, 500),
            lambda *_: None,
            lambda: None,
            hidden=True,
            transport=lambda *_: {"providers": [], "tasks": []},
        )
        try:
            panel.show()
            ui.update_idletasks()
            result = {
                "passed": True,
                "renderer": "native-tk",
                "modal": ui.grab_current() is not None,
                "side": panel.anchor["side"],
                "tail_polygons": len(panel.tail_canvas.find_all()),
                "width": panel.width,
                "height": panel.height,
            }
            assert (
                not result["modal"]
                and result["side"] == "left"
                and result["tail_polygons"] == 2
            )
        finally:
            panel.close()
            ui.destroy()
        root = data_root()
        root.mkdir(parents=True, exist_ok=True)
        (root / "bubble-self-test.json").write_text(
            json.dumps(result), encoding="utf-8"
        )
        print(json.dumps(result))
        return 0

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
        try:
            application = create_app(
                ui_token=(
                    sys.stdin.readline(130).strip() if args.ui_token_stdin else None
                ),
                control_port=args.port,
                skills_directory=skills_directory,
            )
        except ValueError as exc:
            parser.error(str(exc))
        if sys.stdout.isatty() and not args.ui_token_stdin:
            print(
                f"Workspace: http://127.0.0.1:{args.port}/#access={application.state.ui_token}",
                flush=True,
            )
        uvicorn.run(
            application,
            host="127.0.0.1",
            port=args.port,
            access_log=False,
            timeout_graceful_shutdown=8,
        )
        return 0
    import webview

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(control_port=args.port, skills_directory=skills_directory),
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
