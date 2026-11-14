"""One task's official SDK client, behind a parent-owned process boundary.

No DSH process starts until the owner has assigned containment and sent stdin.
The JSON control protocol is private to Workspace, never an HTTP model API.
"""

from __future__ import annotations
import hashlib
import importlib.metadata
import json
import os
import queue
import sys
import threading
import traceback
from pathlib import Path
from workspace.app.dsh_profile import SDK_VERSION, profile


def main():
    first = sys.stdin.readline()
    if not first:
        return 2
    config = json.loads(first)
    from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig
    from deepseek_harness.models import UserQuestionAnswer
    from deepseek_harness_runtime import bundled_runtime_path

    for package in ("deepseek-harness-sdk", "deepseek-harness-runtime-bin"):
        if importlib.metadata.version(package) != SDK_VERSION:
            raise RuntimeError("DSH_VERSION_MISMATCH: expected " + SDK_VERSION)
    output_lock = threading.Lock()
    stopped = threading.Event()
    answers = {}
    answer_lock = threading.Lock()

    def emit(kind, **payload):
        with output_lock:
            sys.stdout.write(
                json.dumps({"kind": kind, **payload}, ensure_ascii=False, default=str)
                + "\n"
            )
            sys.stdout.flush()

    def ask(kind, key, payload, cancel=None):
        target = queue.Queue(maxsize=1)
        with answer_lock:
            answers[str(key)] = target
        emit(kind, id=str(key), payload=payload)
        try:
            while not stopped.is_set() and not (cancel is not None and cancel.is_set()):
                try:
                    return target.get(timeout=0.1)
                except queue.Empty:
                    pass
            return None
        finally:
            with answer_lock:
                answers.pop(str(key), None)

    def question(request, cancel):
        value = ask(
            "question", request.request_id, request.model_dump(by_alias=True), cancel
        )
        return UserQuestionAnswer.model_validate(value) if value else None

    task_home = Path(config["dsh_home"]).resolve()
    task_home.mkdir(parents=True, exist_ok=True)
    patch = profile(
        task_home / "local-profile.json",
        config["provider"],
        config["capabilities"],
        config["owner_id"],
        config["owner_token"],
        config.get("connector_endpoint"),
    )
    binary = Path(bundled_runtime_path())
    with binary.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    emit(
        "identity",
        sdk=SDK_VERSION,
        native_sha256=digest,
        profile="workspace-local-v1",
        session_format=4,
    )
    harness = DeepSeekHarness(
        DeepSeekHarnessConfig(
            provider="local-ai",
            model=config["provider"]["model"],
            max_tokens=int(config["capabilities"]["max_output_tokens"]),
            cwd=config["project_path"],
            runtime_cwd=config["project_path"],
            dsh_bin=str(binary),
            dsh_home=str(task_home),
            profile="sdk",
            patches=(str(patch),),
            env={
                "LOCAL_AI_API_KEY": config["provider"].get("api_key") or "local-no-key",
                "DEEPSEEK_API_KEY": "",
                "DEEPSEEK_BASE_URL": "",
                "DSH_PERMISSION_MODE": config.get("permission", "read-only"),
            },
            initialize_timeout_seconds=60,
            request_timeout_seconds=config.get("timeout_sec", 600),
            shutdown_timeout_seconds=1,
            on_user_question=question,
            approval_requests=True,
        )
    )

    def controls():
        try:
            for line in sys.stdin:
                message = json.loads(line)
                if message.get("kind") == "stop":
                    stopped.set()
                    harness.close()
                    return
                if message.get("kind") == "answer":
                    with answer_lock:
                        target = answers.get(str(message.get("id")))
                    if target is not None:
                        try:
                            target.put_nowait(message.get("value"))
                        except queue.Full:
                            pass
        except Exception:
            emit(
                "control_error",
                message="Control channel closed; execution will not be replayed.",
            )
        finally:
            stopped.set()
            harness.close()

    def approvals():
        try:
            while not stopped.is_set():
                request = harness.client.next_request()
                if request.method != "approval/request":
                    harness.client.respond_error(
                        request.id, code=-32601, message="Unsupported Workspace request"
                    )
                    continue
                result = ask("approval", request.id, request.payload)
                outcome = (result or {}).get("outcome", "cancelled")
                if outcome not in {"allowed-once", "rejected", "cancelled"}:
                    outcome = "rejected"
                harness.client.respond(request.id, {"outcome": outcome})
        except Exception:
            if not stopped.is_set():
                emit("approval_channel_closed")

    threading.Thread(target=controls, daemon=True).start()
    threading.Thread(target=approvals, daemon=True).start()
    try:
        harness.start()
        session = (
            harness.resume_session(config["session_id"])
            if config.get("resume")
            else harness.start_session(config["session_id"])
        )
        emit("started", session_id=config["session_id"])
        if config.get("restore_only"):
            emit("restored", session_id=config["session_id"], replayed=False)
            return 0
        result = session.run(
            config["prompt"],
            on_notification=lambda n: emit(
                "notification", method=n.method, payload=n.payload
            ),
        )
        emit(
            "result",
            session_id=result.session_id,
            final_response=result.final_response,
            finish_reason=result.finish_reason,
        )
        return 0
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        # Timeout/transport loss is uncertain: commands may already have committed.
        emit("uncertain", error=type(exc).__name__, message=str(exc), replayed=False)
        return 1
    finally:
        stopped.set()
        harness.close()


if __name__ == "__main__":
    raise SystemExit(main())
