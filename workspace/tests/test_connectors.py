import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import httpx
from fastapi import HTTPException
from workspace.app.store import Store
from workspace.app.task_service import TaskService
from workspace.app.connectors import write_item
from workspace.app.connector_mcp import handle


class ConnectorTests(unittest.TestCase):
    def prepare(self, root):
        store = Store(root)
        with store.transaction() as db:
            db.execute(
                "INSERT INTO projects VALUES(?,?,?,?)", ("p", "P", str(root), "now")
            )
            db.execute(
                "INSERT INTO providers VALUES(?,?,?,?,?)",
                ("r", "R", "http://127.0.0.1:8765/v1", "m", ""),
            )
            db.execute(
                "INSERT INTO connectors VALUES(?,?,?,?,?)",
                ("c", "Notes", "http://127.0.0.1:8890", "", 1),
            )
        service = TaskService(store)
        task = service.create(
            "p", "r", "Use Notes", "connector-action-01", connector_ids=["c"]
        )
        return store, service, task

    def test_uncertain_committed_write_is_not_resent_after_response_loss(self):
        with tempfile.TemporaryDirectory() as root:
            store, service, task = self.prepare(Path(root))
            calls = []

            def lost(*args):
                calls.append(args)
                raise httpx.ReadTimeout("response lost after application commit")

            payload = {
                "title": "Example",
                "body": "Approved item",
                "action_id": "one-write-action-id",
                "approved": True,
            }
            with patch("workspace.app.connectors.request", side_effect=lost):
                first = write_item(store, "c", payload)
                second = write_item(store, "c", payload)
            self.assertEqual(len(calls), 1)
            self.assertEqual(first["status"], "uncertain")
            self.assertEqual(second["status"], "uncertain")
            self.assertFalse(second["resent"])
            with self.assertRaisesRegex(ValueError, "ACTION_CONFLICT"):
                write_item(store, "c", {**payload, "body": "Changed intent"})
            store.close()

    def test_mcp_write_waits_for_exact_user_grant_and_receipts_duplicate_request(self):
        with tempfile.TemporaryDirectory() as root:
            store, service, task = self.prepare(Path(root))
            service.tokens[task["id"]] = "task-secret"
            service.processes[task["id"]] = Mock()
            payload = {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "create_item",
                    "arguments": {
                        "connector_id": "c",
                        "title": "ORBIT",
                        "body": "Exact proposed content",
                    },
                },
            }
            output = []
            with patch(
                "workspace.app.connectors.request", return_value={"saved": True}
            ) as send:
                thread = threading.Thread(
                    target=lambda: output.append(
                        handle(service, task["id"], "task-secret", payload)
                    )
                )
                thread.start()
                deadline = time.monotonic() + 5
                approvals = []
                while time.monotonic() < deadline:
                    approvals = store.rows(
                        "SELECT * FROM approvals WHERE status='pending'"
                    )
                    if approvals:
                        break
                    time.sleep(0.01)
                self.assertTrue(approvals)
                send.assert_not_called()
                self.assertIn("Exact proposed content", approvals[0]["payload"])
                service.decide(approvals[0]["id"], {"outcome": "allowed-once"})
                thread.join(5)
                self.assertFalse(thread.is_alive())
                duplicate = handle(service, task["id"], "task-secret", payload)
                self.assertEqual(send.call_count, 1)
                self.assertFalse(duplicate["result"]["isError"])
                self.assertFalse(output[0]["result"]["isError"])
            service.tokens.clear()
            with self.assertRaises(HTTPException):
                handle(
                    service,
                    task["id"],
                    "task-secret",
                    {"id": 1, "method": "tools/list"},
                )
            service.processes.clear()
            store.close()

    def test_discovery_is_required_for_the_current_attempt(self):
        with tempfile.TemporaryDirectory() as root:
            store, service, task = self.prepare(Path(root))
            task_id = task["id"]
            service.tokens[task_id] = "first-attempt"
            query = {"id": 1, "method": "workspace/readiness"}
            self.assertFalse(
                handle(service, task_id, "first-attempt", query)["result"]["ready"]
            )
            handle(service, task_id, "first-attempt", {"id": 2, "method": "tools/list"})
            self.assertTrue(
                handle(service, task_id, "first-attempt", query)["result"]["ready"]
            )
            service.tokens[task_id] = "second-attempt"
            self.assertFalse(
                handle(service, task_id, "second-attempt", query)["result"]["ready"]
            )
            with self.assertRaises(HTTPException):
                handle(service, task_id, "first-attempt", query)
            store.close()


if __name__ == "__main__":
    unittest.main()
