import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import psutil
from fastapi.testclient import TestClient
from workspace.app.store import Store, identifier
from workspace.app.task_service import TaskService
from workspace.app.history import canonical_events, reconcile
from workspace.app.owned_process import OwnedProcess
from workspace.app.main import create_app
from workspace.app import project_diff


def seed(store):
    with store.transaction() as db:
        db.execute(
            "INSERT INTO projects VALUES(?,?,?,?)",
            ("p", "Project", str(store.root / "project"), "now"),
        )
        db.execute(
            "INSERT INTO providers VALUES(?,?,?,?,?)",
            (
                "r",
                "Runtime",
                "http://127.0.0.1:8765/v1",
                "model",
                "do-not-return-secret",
            ),
        )
    return TaskService(store)


class LifecycleTests(unittest.TestCase):
    def test_future_database_refused_without_reset(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "workspace.sqlite3"
            db = sqlite3.connect(path)
            with db:
                db.execute("PRAGMA user_version=500")
                db.execute("CREATE TABLE private_data(value TEXT)")
                db.execute("INSERT INTO private_data VALUES('keep')")
            db.close()
            with self.assertRaisesRegex(RuntimeError, "WORKSPACE_DATA_NEWER"):
                Store(Path(root))
            db = sqlite3.connect(path)
            try:
                self.assertEqual(
                    db.execute("SELECT value FROM private_data").fetchone()[0], "keep"
                )
            finally:
                db.close()

    def test_task_admission_idempotency_revision_and_recovery_never_replay(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            service = seed(store)
            task = service.create("p", "r", "Do work", "unique-action-id-01")
            self.assertEqual(
                service.create("p", "r", "Do work", "unique-action-id-01")["id"],
                task["id"],
            )
            store.update_task(task["id"], "running")
            with self.assertRaisesRegex(ValueError, "TASK_CONFLICT"):
                store.update_task(task["id"], "failed", expected=1)
            with patch.object(service, "start") as start:
                store.recover()
                start.assert_not_called()
            self.assertEqual(store.one("tasks", task["id"])["status"], "interrupted")
            self.assertIn(
                "No input or action was replayed",
                store.one("tasks", task["id"])["details"]["recovery"],
            )
            store.close()

    def test_canonical_history_deduplicates_and_refuses_future_or_gapped_format(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            service = seed(store)
            task = service.create("p", "r", "Do work", "unique-action-id-02")
            home = store.root / "tasks" / task["id"] / "dsh"
            log = home / "sessions" / "project" / "session" / "log.v4.jsonl"
            log.parent.mkdir(parents=True)
            rows = [
                {"type": "session", "version": 4, "id": task["session_id"]},
                {"type": "tool/call", "seq": 0, "data": {"name": "write"}},
                {"type": "tool/result", "seq": 1, "data": {"ok": True}},
            ]
            log.write_text(
                "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
            )
            reconcile(store, task)
            reconcile(store, task)
            self.assertEqual(
                len(store.rows("SELECT * FROM events WHERE kind='dsh'")), 2
            )
            log.write_text(
                json.dumps({**rows[0], "version": 5}) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "UNSUPPORTED"):
                canonical_events(home)
            log.write_text(
                json.dumps(rows[0]) + "\n" + json.dumps({**rows[1], "seq": 2}) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "CORRUPT"):
                canonical_events(home)
            store.close()

    def test_permission_is_once_and_not_implicitly_granted(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            service = seed(store)
            task = service.create("p", "r", "Do work", "unique-action-id-03")
            process = Mock()
            service.processes[task["id"]] = process
            service._event(
                task["id"],
                {
                    "kind": "approval",
                    "id": "sdk-req-1",
                    "payload": {"toolName": "run", "reason": "Write a file"},
                },
            )
            process.send.assert_not_called()
            approval = store.rows("SELECT id FROM approvals")[0]["id"]
            service.decide(approval, {"outcome": "rejected"})
            self.assertEqual(
                process.send.call_args.args[0]["value"]["outcome"], "rejected"
            )
            with self.assertRaisesRegex(ValueError, "APPROVAL_FINISHED"):
                service.decide(approval, {"outcome": "allowed-once"})
            service.processes.clear()
            store.close()

    def test_api_redacts_provider_key_rejects_foreign_origin_and_has_per_window_selection(
        self,
    ):
        with tempfile.TemporaryDirectory() as root, TestClient(
            create_app(root), base_url="http://127.0.0.1"
        ) as client:
            response = client.post(
                "/api/providers",
                json={
                    "base_url": "http://127.0.0.1:8765/v1",
                    "model": "test",
                    "api_key": "secret-marker",
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn("secret-marker", response.text)
            self.assertNotIn("secret-marker", client.get("/api/bootstrap").text)
            self.assertNotIn("active_task", client.get("/api/bootstrap").json())
            self.assertEqual(
                client.post(
                    "/api/snapshots",
                    json={},
                    headers={"Origin": "https://evil.invalid"},
                ).status_code,
                403,
            )
            self.assertEqual(
                client.get(
                    "/api/bootstrap", headers={"Host": "evil.invalid"}
                ).status_code,
                400,
            )

    def test_stop_owned_descendants_and_keep_unrelated_process_alive(self):
        events = []
        ready = threading.Event()
        exited = threading.Event()
        child_code = "import json,subprocess,sys,time,os;sys.stdin.readline();p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'],start_new_session=os.name!='nt');print(json.dumps({'kind':'child','pid':p.pid}),flush=True);time.sleep(120)"
        kwargs = (
            {"creationflags": subprocess.CREATE_NO_WINDOW}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        unrelated = subprocess.Popen(
            [sys.executable, "-c", "import time;time.sleep(120)"], **kwargs
        )

        def event(value):
            events.append(value)
            ready.set()

        owned = OwnedProcess(
            [sys.executable, "-c", child_code],
            os.getcwd(),
            os.environ.copy(),
            event,
            lambda *args: exited.set(),
        )
        try:
            owned.send({"start": True})
            self.assertTrue(ready.wait(10))
            time.sleep(0.3)
            child = psutil.Process(events[0]["pid"])
            created = child.create_time()
            result = owned.stop(grace=0.1)
            self.assertTrue(result["confirmed"])
            self.assertTrue(exited.is_set())
            self.assertIsNone(unrelated.poll())
            try:
                alive = (
                    child.is_running()
                    and child.create_time() == created
                    and child.status() != psutil.STATUS_ZOMBIE
                )
            except psutil.NoSuchProcess:
                alive = False
            self.assertFalse(alive, "Owned child survived stop")
        finally:
            unrelated.kill()
            unrelated.wait()
            owned.stop(grace=0.1)

    def test_snapshot_preserves_metadata_and_canonical_sessions(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            service = seed(store)
            task = service.create("p", "r", "Work", "unique-action-id-04")
            directory = store.root / "tasks" / task["id"] / "dsh"
            directory.mkdir(parents=True)
            (directory / "example").write_text("durable")
            (store.root / "mini-helper.json").write_text('{"chat":{"selected":"keep"}}')
            result = service.snapshot()
            snapshot = Path(result["path"])
            db = sqlite3.connect(snapshot / "workspace.sqlite3")
            try:
                self.assertEqual(
                    db.execute("SELECT prompt FROM tasks").fetchone()[0], "Work"
                )
                self.assertEqual(
                    result["schema_version"],
                    db.execute("PRAGMA user_version").fetchone()[0],
                )
                self.assertEqual(
                    result["schema_version"],
                    json.loads((snapshot / "manifest.json").read_text())[
                        "schema_version"
                    ],
                )
            finally:
                db.close()
            self.assertEqual(
                (snapshot / "tasks" / task["id"] / "dsh" / "example").read_text(),
                "durable",
            )
            self.assertEqual(
                json.loads((snapshot / "mini-helper.json").read_text())["chat"][
                    "selected"
                ],
                "keep",
            )
            store.close()

    def test_blocked_stdin_does_not_block_stop(self):
        owned = OwnedProcess(
            [sys.executable, "-c", "import time;time.sleep(120)"],
            os.getcwd(),
            os.environ.copy(),
            lambda event: None,
            lambda *args: None,
        )
        try:
            owned.send({"payload": "x" * 800000})
            started = time.monotonic()
            result = owned.stop(grace=0.1)
            self.assertTrue(result["confirmed"])
            self.assertLess(time.monotonic() - started, 8)
        finally:
            owned.stop(grace=0.1)


if __name__ == "__main__":
    unittest.main()
