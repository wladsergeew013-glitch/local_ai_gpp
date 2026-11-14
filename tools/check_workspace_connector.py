"""Real Harness -> Workspace MCP -> disposable Notes application acceptance.

Start examples/notes_app.py separately. Only the exact synthetic note below
can be approved by this script; other permission requests are rejected.
"""

import argparse
import json
import subprocess
import time
import uuid
from pathlib import Path
import httpx


def check(workspace_url, notes_url, runtime_url, model, project, output):
    project.mkdir(parents=True, exist_ok=False)
    (project / "README.md").write_text(
        "Disposable connector acceptance. Do not modify this project.\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(project)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(project), "add", "."], check=True, capture_output=True
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(project),
            "-c",
            "user.name=Local Acceptance",
            "-c",
            "user.email=acceptance@example.invalid",
            "commit",
            "-qm",
            "Connector fixture",
        ],
        check=True,
        capture_output=True,
    )
    title, body = "Local AI connector check", "ORBIT-7429"
    with httpx.Client(base_url=workspace_url, timeout=90, trust_env=False) as client:

        def post(path, payload):
            response = client.post(path, json=payload)
            response.raise_for_status()
            return response.json()

        project_row = post(
            "/api/projects", {"name": "Connector acceptance", "path": str(project)}
        )
        provider = post(
            "/api/providers",
            {"name": "Acceptance Runtime", "base_url": runtime_url, "model": model},
        )
        connector = post(
            "/api/connectors",
            {"name": "Disposable Notes", "base_url": notes_url, "allow_write": True},
        )
        before = (
            httpx.get(notes_url + "/items", trust_env=False)
            .raise_for_status()
            .json()["items"]
        )
        task = post(
            "/api/tasks",
            {
                "project_id": project_row["id"],
                "provider_id": provider["id"],
                "action_id": uuid.uuid4().hex,
                "permission": "read-only",
                "connector_ids": [connector["id"]],
                "prompt": (
                    "Use only the supplied application connector tools, no shell or file tools. "
                    f"First read_items from connector_id {connector['id']}. "
                    f"Then create_item with that connector_id, exact title {json.dumps(title)}, "
                    f"and exact body {json.dumps(body)}. Wait for the one-operation approval. "
                    "After the tool succeeds, read_items again to verify the saved note, "
                    "then report the result briefly."
                ),
            },
        )
        task_id = task["id"]
        started = time.monotonic()
        approvals = []
        post(f"/api/tasks/{task_id}/start", {"revision": task["revision"]})
        while time.monotonic() - started < 420:
            task = client.get(f"/api/tasks/{task_id}").raise_for_status().json()
            for approval in task.get("approvals", []):
                payload = approval["payload"].get("payload", approval["payload"])
                allowed = (
                    approval["kind"] == "connector"
                    and payload.get("title") == title
                    and payload.get("body") == body
                )
                approvals.append(
                    {"kind": approval["kind"], "exact_synthetic_note": allowed}
                )
                post(
                    "/api/approvals/" + approval["id"],
                    {"outcome": "allowed-once" if allowed else "rejected"},
                )
            if task["status"] not in {
                "starting",
                "running",
                "awaiting_permission",
                "stopping",
            }:
                break
            time.sleep(0.3)
        else:
            post(f"/api/tasks/{task_id}/stop", {"revision": task["revision"]})
        task = client.get(f"/api/tasks/{task_id}").raise_for_status().json()
        events = (
            client.get(f"/api/tasks/{task_id}/events")
            .raise_for_status()
            .json()["events"]
        )
        after = (
            httpx.get(notes_url + "/items", trust_env=False)
            .raise_for_status()
            .json()["items"]
        )
        writes = [e for e in events if e["kind"] == "connector_write_result"]
        result = {
            "task_id": task_id,
            "status": task["status"],
            "seconds": round(time.monotonic() - started, 3),
            "items_before": len(before),
            "items_after": len(after),
            "matching_notes": sum(
                n["title"] == title and n["body"] == body for n in after
            ),
            "approvals": approvals,
            "write_receipts": len(writes),
            "tool_calls": sum(
                e["kind"] == "dsh" and e["payload"].get("type") == "tool/call"
                for e in events
            ),
            "details": task["details"],
        }
        output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        assert (
            task["status"] == "completed"
            and len(after) == len(before) + 1
            and writes
            and any(a["exact_synthetic_note"] for a in approvals)
        ), result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-url", default="http://127.0.0.1:8776")
    parser.add_argument("--notes-url", default="http://127.0.0.1:8890")
    parser.add_argument("--runtime-url", default="http://127.0.0.1:8765/v1")
    parser.add_argument("--model", required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    check(
        args.workspace_url,
        args.notes_url,
        args.runtime_url,
        args.model,
        args.project.resolve(),
        args.output,
    )
