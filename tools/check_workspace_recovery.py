"""Restore a completed acceptance session without replaying a single tool call."""

import argparse
import json
import time
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workspace.app.store import Store
from workspace.app.task_service import TaskService


def check(root):
    store = Store(root)
    service = TaskService(store)
    task = store.one(
        "tasks",
        store.rows("SELECT id FROM tasks ORDER BY created_at DESC LIMIT 1")[0]["id"],
    )

    def calls():
        return sum(
            json.loads(row["payload"]).get("type") == "tool/call"
            for row in store.rows(
                "SELECT payload FROM events WHERE task_id=? AND kind='dsh'",
                (task["id"],),
            )
        )

    before = calls()
    started = time.monotonic()
    try:
        service.start(task["id"], task["revision"], restore_only=True)
        while service.processes and time.monotonic() - started < 90:
            time.sleep(0.1)
        after = calls()
        current = store.one("tasks", task["id"])
        result = {
            "state": current["status"],
            "tool_calls_before": before,
            "tool_calls_after": after,
            "replayed": after != before,
            "history": current["details"].get("history"),
            "seconds": round(time.monotonic() - started, 2),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        assert (
            current["status"] == "restored" and before == after and before > 0
        ), result
    finally:
        service.close()
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("data_dir")
    args = parser.parse_args()
    check(Path(args.data_dir).resolve())
