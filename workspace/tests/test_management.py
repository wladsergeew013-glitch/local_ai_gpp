import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from workspace.app.control_access import UI_TOKEN_HEADER
from workspace.app.main import create_app
from workspace.local_ai_control import CONTROL_FILE, CONTROL_TOKEN_HEADER, read_control


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.addCleanup(lambda: self.assertFalse((self.root / CONTROL_FILE).exists()))
        self.app = create_app(self.root, control_port=8776)
        self.client = self.enterContext(TestClient(self.app, base_url="http://127.0.0.1"))
        self.ui = {UI_TOKEN_HEADER: self.app.state.ui_token}
        self.base, self.control = read_control(self.root)

    def post(self, uri, json=None, *, headers=None):
        return self.client.post(uri, json=json, headers=headers or self.ui)

    def provider(self):
        response = self.post("/api/providers", {"model": "old-model", "api_key": "fixture-key"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"]

    def test_control_credential_is_restricted_and_expires_with_owner(self):
        self.assertEqual(self.base, "http://127.0.0.1:8776/api/control")
        self.assertEqual(self.client.get("/api/control/status").status_code, 401)
        self.assertEqual(self.client.get("/api/control/status", headers=self.ui).status_code, 401)
        self.assertEqual(self.client.get("/api/bootstrap", headers=self.control).status_code, 401)
        self.assertEqual(self.post("/api/approvals/fixture", {}, headers=self.control).status_code, 401)
        response = self.client.get("/api/control/status", headers={**self.control, "Origin": "http://outside.invalid"})
        self.assertEqual(response.status_code, 403)
        ticket = self.root / CONTROL_FILE
        if os.name == "nt":
            blob = base64.b64decode(json.loads(ticket.read_text(encoding="utf-8"))["auth"])
            self.assertNotEqual(blob, self.control[CONTROL_TOKEN_HEADER].encode("ascii"))
        else:
            self.assertEqual(ticket.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.client.get("/api/control/status", headers=self.control).status_code, 200)

    def test_status_and_model_changes_use_the_current_runtime_id(self):
        provider_id = self.provider()
        response = self.post("/api/projects", {"name": "Folderless project"})
        self.assertEqual(response.status_code, 200, response.text)
        project_id = response.json()["id"]
        task = self.post("/api/chats", {
            "project_id": project_id, "provider_id": provider_id,
            "prompt": "fixture-private-dialog-text", "action_id": "fixture-status-001",
        })
        self.assertEqual(task.status_code, 200, task.text)
        with patch("workspace.app.management.RuntimeClient.probe") as probe:
            probe.return_value = {
                "models": [{"id": "new-model"}], "model_available": False, "model_loaded": False,
            }
            response = self.client.get("/api/control/status", headers=self.control)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn("fixture-key", response.text)
            self.assertNotIn("fixture-private-dialog-text", response.text)
            state = response.json()
            self.assertEqual(state["providers"][0]["state"], "model_missing")
            self.assertEqual(state["providers"][0]["latest_dialog"]["status"], "queued")
            self.assertEqual(state["projects"][0]["dialog_count"], 1)

            rejected = self.post("/api/control/select-model", {"model": "typo"}, headers=self.control)
            self.assertEqual(rejected.status_code, 400, rejected.text)
            self.assertEqual(self.app.state.store.one("providers", provider_id)["model"], "old-model")

            probe.return_value.update(model_available=True, model_loaded=True)
            saved = self.post("/api/control/select-model", {"model": "new-model"}, headers=self.control)
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()["state"], "model_ready")
            updated = self.app.state.store.one("providers", provider_id)
            self.assertEqual((updated["model"], updated["api_key"]), ("new-model", "fixture-key"))

    def test_check_and_backup_do_not_create_dialogues(self):
        self.provider()
        with (
            patch("workspace.app.management.RuntimeClient.capabilities"),
            patch("workspace.app.management.RuntimeClient.request") as runtime,
        ):
            runtime.return_value = {
                "choices": [{"finish_reason": "stop", "message": {"content": "OK"}}],
                "usage": {"completion_tokens": 2},
            }
            check = self.post("/api/control/check", {}, headers=self.control)
            self.assertEqual(check.status_code, 200, check.text)
            self.assertTrue(check.json()["ok"])
            self.assertEqual(runtime.call_args.args, ("POST", "chat/completions"))
            self.assertFalse(runtime.call_args.kwargs["json"]["memory"])
        self.assertEqual(self.app.state.store.rows("SELECT id FROM tasks"), [])
        backup = self.post("/api/control/backup", {}, headers=self.control)
        self.assertEqual(backup.status_code, 200, backup.text)
        snapshot = Path(backup.json()["path"])
        self.assertTrue(snapshot.resolve().is_relative_to((self.root / "snapshots").resolve()))
        self.assertTrue((snapshot / "workspace.sqlite3").is_file())
        self.assertFalse((snapshot / CONTROL_FILE).exists())
