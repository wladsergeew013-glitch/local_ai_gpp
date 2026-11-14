"""Repeatable real DSH task on an isolated copy of a real Runtime module.

Never edits the user's repository. Requires the Workspace SDK dependencies.
"""

import argparse
import json
import os
import subprocess
import threading
import time
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workspace.app.store import Store, identifier
from workspace.app.task_service import TaskService


def fixture(root, source_root):
    root.mkdir(parents=True, exist_ok=False)
    (root / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
    source = (source_root / "backend/app/context_budget.py").read_text(encoding="utf-8")
    (root / "context_budget.py").write_text(
        source.replace("512 <= n_ctx", "1024 <= n_ctx", 1), encoding="utf-8"
    )
    (root / "test_budget.py").write_text(
        "import unittest\nfrom context_budget import validate_context\nclass BudgetTest(unittest.TestCase):\n    def test_small_context_is_supported(self):\n        validate_context(512,16)\n    def test_oversized_response_rejected(self):\n        with self.assertRaises(ValueError): validate_context(512,400)\n",
        encoding="utf-8",
    )
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    for command in (
        ["git", "init", "-q"],
        ["git", "add", "."],
        [
            "git",
            "-c",
            "user.name=Local Acceptance",
            "-c",
            "user.email=acceptance@example.invalid",
            "commit",
            "-qm",
            "Injected regression in Runtime context module",
        ],
    ):
        subprocess.run(command, cwd=root, check=True, capture_output=True, **flags)
    return root


def run(base, root, source):
    root.mkdir(parents=True, exist_ok=False)
    project = fixture(root / "project", source)
    store = Store(root / "workspace-data")
    service = TaskService(store)
    project_id = identifier()
    provider_id = identifier()
    import httpx

    with httpx.Client(timeout=20, trust_env=False) as client:
        model = client.get(base + "/models").json()["data"][0]["id"]
    with store.transaction() as db:
        db.execute(
            "INSERT INTO projects VALUES(?,?,?,?)",
            (project_id, "Runtime regression fixture", str(project), "acceptance"),
        )
        db.execute(
            "INSERT INTO providers VALUES(?,?,?,?,?)",
            (provider_id, "Test Runtime", base, model, ""),
        )
    prompt = "This is an isolated regression fixture copied from a real Local AI Runtime module. Run python -m unittest discover -v, find why the test fails, fix context_budget.py with the smallest correct change, then run the tests again. Do not change the tests. Report the test result and the change. Use the provided file and shell tools."
    task = service.create(
        project_id, provider_id, prompt, identifier(), "workspace-write"
    )
    started = time.monotonic()
    interventions = []
    cursor = 0
    try:
        service.start(task["id"], task["revision"])
        while time.monotonic() - started < 600:
            current = store.one("tasks", task["id"])
            for approval in store.rows(
                "SELECT id,payload FROM approvals WHERE task_id=? AND status='pending'",
                (task["id"],),
            ):
                payload = json.loads(approval["payload"])
                interventions.append(payload)
                # Acceptance is restricted to this disposable project. No cloud/external tools in the profile.
                service.decide(approval["id"], {"outcome": "allowed-once"})
            if current["status"] not in {
                "starting",
                "running",
                "awaiting_permission",
                "stopping",
            }:
                break
            time.sleep(0.5)
        else:
            service.stop(task["id"])
        result = subprocess.run(
            [
                os.environ.get("LOCAL_AI_TEST_PYTHON", "python"),
                "-m",
                "unittest",
                "discover",
                "-v",
            ],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=30,
        )
        diff = service.diff(task["id"])
        current = store.one("tasks", task["id"])
        output = {
            "task_id": task["id"],
            "state": current["status"],
            "elapsed_sec": round(time.monotonic() - started, 2),
            "test_exit_code": result.returncode,
            "test_output": result.stdout + result.stderr,
            "diff": diff,
            "details": current["details"],
            "permission_interventions": interventions,
            "fixture": "real Runtime module with an injected regression; not a general coding benchmark",
        }
        (root / "result.json").write_text(
            json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(output, ensure_ascii=False, indent=2))
        assert (
            result.returncode == 0
            and diff["files"]
            and current["status"] == "completed"
        ), "Agent acceptance failed; inspect result and durable events."
        return output
    finally:
        service.close()
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765/v1")
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()
    run(
        args.base_url,
        Path(args.output_root).resolve(),
        Path(__file__).resolve().parents[1],
    )
