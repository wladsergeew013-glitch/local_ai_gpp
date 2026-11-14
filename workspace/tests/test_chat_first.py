import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import httpx
from fastapi.testclient import TestClient
from workspace.app.main import create_app
from workspace.app.store import Store, SCHEMA_VERSION
from workspace.app.task_service import TaskService
from workspace.app.chat_run import completed_messages
from workspace.app.projects import create_project
from workspace.app.project_diff import capture, compare


class ChatFirstTests(unittest.TestCase):
    def wait(self, service):
        deadline = time.monotonic() + 10
        while service.processes and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(service.processes)

    def test_plain_dialogue_needs_no_project_git_harness_or_tools_and_keeps_memory(
        self,
    ):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            service = TaskService(store)
            with store.transaction() as db:
                db.execute(
                    "INSERT INTO providers VALUES(?,?,?,?,?)",
                    ("p", "Local", "http://127.0.0.1:8765/v1", "m", ""),
                )
            requests = []
            real_client = httpx.Client

            def reply(request):
                data = json.loads(request.content)
                requests.append(data)
                answer = "ORBIT" if len(requests) == 2 else "OK"
                frames = [
                    {
                        "choices": [
                            {"delta": {"content": answer}, "finish_reason": None}
                        ]
                    },
                    {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                ]
                return httpx.Response(
                    200,
                    content="".join("data: " + json.dumps(f) + "\n\n" for f in frames)
                    + "data: [DONE]\n\n",
                )

            with patch(
                "workspace.app.task_service.OwnedProcess",
                side_effect=AssertionError("No Harness in chat"),
            ), patch(
                "workspace.app.runtime_client.RuntimeClient.capabilities",
                return_value={"max_output_tokens": 128},
            ), patch(
                "workspace.app.chat_run.httpx.Client",
                side_effect=lambda **kw: real_client(
                    transport=httpx.MockTransport(reply), **kw
                ),
            ):
                task = service.create(
                    None, "p", "Remember ORBIT", "plain-chat-action-1", mode="chat"
                )
                service.start(task["id"], task["revision"])
                self.wait(service)
                task = store.one("tasks", task["id"])
                service.start(task["id"], task["revision"], new_prompt="Project name?")
                self.wait(service)
                task = store.one("tasks", task["id"])
                service.start(
                    task["id"], task["revision"], new_prompt="Independent", memory=False
                )
                self.wait(service)
            self.assertEqual([len(r["messages"]) for r in requests], [1, 3, 1])
            self.assertEqual(requests[1]["messages"][1]["content"], "OK")
            self.assertTrue(all("tools" not in r for r in requests))
            self.assertEqual([r["memory"] for r in requests], [True, True, False])
            self.assertEqual(store.one("tasks", task["id"])["status"], "completed")
            self.assertFalse((Path(root) / "chat-files/.git").exists())
            store.close()

    def test_incomplete_turn_is_visible_but_not_part_of_next_request_memory(self):
        rows = [
            {"payload": json.dumps(x)}
            for x in [
                {"turn_id": "a", "role": "user", "text": "first"},
                {
                    "turn_id": "a",
                    "role": "assistant",
                    "text": "complete",
                    "phase": "done",
                },
                {"turn_id": "b", "role": "user", "text": "failed question"},
                {
                    "turn_id": "b",
                    "role": "assistant",
                    "text": "partial",
                    "phase": "error",
                },
            ]
        ]
        self.assertEqual(
            completed_messages(rows),
            [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "complete"},
            ],
        )

    def test_project_can_be_an_organization_or_a_folder_without_git(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root) / "data")
            virtual = create_project(store, "Ideas")
            folder = Path(root) / "notes"
            folder.mkdir()
            (folder / "note.txt").write_text("before")
            connected = create_project(store, "Notes", str(folder))
            baseline = Path(root) / "baseline"
            capture(folder, baseline)
            (folder / "note.txt").write_text("after")
            self.assertTrue(compare(folder, baseline)["files"])
            self.assertEqual(virtual["source"], "virtual")
            self.assertEqual(connected["source"], "folder")
            self.assertFalse((folder / ".git").exists())
            store.close()

    def test_schema_1_migration_preserves_existing_conversations(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            with store.transaction() as db:
                db.execute(
                    "INSERT INTO providers VALUES(?,?,?,?,?)",
                    ("p", "Local", "http://127.0.0.1:8765/v1", "m", "kept-secret"),
                )
                db.execute("DROP TABLE project_sources")
                db.execute("PRAGMA user_version=1")
            store.close()
            migrated = Store(Path(root))
            self.assertEqual(
                migrated.rows("PRAGMA user_version")[0]["user_version"], SCHEMA_VERSION
            )
            self.assertEqual(migrated.one("providers", "p")["api_key"], "kept-secret")
            migrated.close()

    def test_api_hides_internal_project_and_uses_chat_default(self):
        with tempfile.TemporaryDirectory() as root, TestClient(
            create_app(root), base_url="http://127.0.0.1"
        ) as client:
            provider = client.post("/api/providers", json={"model": "m"}).json()
            chat = client.post(
                "/api/chats",
                json={
                    "provider_id": provider["id"],
                    "prompt": "Hello",
                    "action_id": "chat-action-0000001",
                },
            )
            self.assertEqual(chat.status_code, 200, chat.text)
            boot = client.get("/api/bootstrap").json()
            self.assertEqual(boot["projects"], [])
            self.assertEqual(boot["tasks"][0]["mode"], "chat")
            self.assertIsNone(boot["tasks"][0]["project_id"])
            p = client.post("/api/projects", json={"name": "Ideas"})
            self.assertEqual(p.status_code, 200, p.text)
            self.assertEqual(p.json()["source"], "virtual")

    def test_github_url_cannot_embed_credentials_or_command_options(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            for url in (
                "https://user:pass@github.com/a/b",
                "https://github.com/a/b?token=secret",
                "https://example.com/a/b",
                "--upload-pack=bad",
            ):
                with self.assertRaises(ValueError):
                    create_project(store, "Repo", github_url=url)
            store.close()

    def test_interrupted_plain_reply_keeps_partial_without_replaying_or_remembering_it(
        self,
    ):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            with store.transaction() as db:
                db.execute(
                    "INSERT INTO providers VALUES(?,?,?,?,?)",
                    ("p", "Local", "http://127.0.0.1:8765/v1", "m", ""),
                )
            task = TaskService(store).create(
                None, "p", "Question", "recovery-chat-action-01", mode="chat"
            )
            store.event(
                task["id"],
                "chat_message",
                {"turn_id": "t", "role": "user", "text": "Question", "phase": "done"},
            )
            store.update_task(task["id"], "running", {"partial": "Saved partial"})
            store.recover()
            store.recover()
            rows = store.rows(
                "SELECT payload FROM events WHERE task_id=? AND kind='chat_message' ORDER BY seq",
                (task["id"],),
            )
            self.assertEqual(len(rows), 2)
            self.assertEqual(json.loads(rows[-1]["payload"])["text"], "Saved partial")
            self.assertEqual(completed_messages(rows), [])
            self.assertEqual(store.one("tasks", task["id"])["status"], "interrupted")
            store.close()

    def test_committed_reply_survives_crash_before_status_commit(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            with store.transaction() as db:
                db.execute(
                    "INSERT INTO providers VALUES(?,?,?,?,?)",
                    ("p", "Local", "http://127.0.0.1:8765/v1", "m", ""),
                )
            task = TaskService(store).create(
                None, "p", "Question", "recovery-chat-action-02", mode="chat"
            )
            for role, value in (("user", "Question"), ("assistant", "Complete answer")):
                store.event(
                    task["id"],
                    "chat_message",
                    {"turn_id": "t", "role": role, "text": value, "phase": "done"},
                )
            store.update_task(task["id"], "running")
            store.recover()
            rows = store.rows(
                "SELECT payload FROM events WHERE task_id=? AND kind='chat_message' ORDER BY seq",
                (task["id"],),
            )
            self.assertEqual(len(rows), 2)
            self.assertEqual(store.one("tasks", task["id"])["status"], "completed")
            self.assertEqual(len(completed_messages(rows)), 2)
            store.close()


if __name__ == "__main__":
    unittest.main()
