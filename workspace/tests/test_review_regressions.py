import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from workspace.app import project_diff
from workspace.app.control_access import UI_TOKEN_HEADER
from workspace.app.dsh_profile import profile
from workspace.app.main import create_app
from workspace.app.owned_process import OwnedProcess


class WorkspaceReviewTests(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(tempfile.TemporaryDirectory())
        self.app = create_app(Path(self.root) / "data")
        self.client = self.enterContext(
            TestClient(self.app, base_url="http://127.0.0.1")
        )
        self.operator = {UI_TOKEN_HEADER: self.app.state.ui_token}
        self.store = self.app.state.store
        self.service = self.app.state.tasks
        self.addCleanup(self.service.processes.clear)

    def post(self, path, payload):
        return self.client.post("/api" + path, json=payload, headers=self.operator)

    def seed(self, mode, *, connector_ids=()):
        provider = self.post("/providers", {"model": "fixture"}).json()
        project = (
            self.post("/projects", {"name": "Fixture"}).json()
            if mode == "agent"
            else None
        )
        return self.post(
            "/chats",
            {
                "mode": mode,
                "provider_id": provider["id"],
                "prompt": "First draft",
                "project_id": project["id"] if project else None,
                "action_id": f"fixture-{mode}-initial",
                "connector_ids": list(connector_ids),
            },
        ).json()

    def test_skill_set_validation_precedes_start_and_old_single_is_accepted(self):
        task = self.seed("chat")
        registered = self.post("/skills/import", [
            {"filename": "writer.md", "markdown": "Write briefly."},
            {"filename": "reader.md", "markdown": "Read.\n```workspace-mcp\n"
             '{"url":"http://127.0.0.1:9092/mcp","tool":"read","file":{"filename":"n","base64":"b"}}\n```'},
        ]).json()
        writer, reader = [row["id"] for row in registered]
        url = f"/chats/{task['id']}/start"
        revision = {"revision": task["revision"]}
        for invalid in (
            {"skills": [{"id": writer}, {"id": writer}]},
            {"skills": [{"id": writer}, {"id": reader}], "attachments": ["missing-document"]},
            {"skills": [{"id": writer, "disabled_sections": ["missing"]}]},
            {"skills": [], "skill": {"id": writer}},
        ):
            response = self.post(url, {**revision, **invalid})
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(self.store.one("tasks", task["id"])["revision"], task["revision"])
        with patch.object(self.service, "_start_chat", return_value=task) as run:
            response = self.post(url, {**revision, "skill": {"id": writer}})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual([use.id for use in run.call_args.args[3]], [writer])

    def test_per_skill_files_and_legacy_report_survive_selection_changes(self):
        from workspace.app import skills
        task = self.seed("chat")
        def reader(name):
            doc = ('Read the file.\n```workspace-mcp\n{"url":"http://127.0.0.1:9092/mcp",'
                   '"tool":"read","file":{"filename":"n","base64":"b"}}\n```')
            return skills.register(self.store, skills.SkillInput(filename=name, markdown=doc))["id"]
        first, second = reader("first.md"), reader("second.md")
        old = {"tool": "read", "filename": "old.txt", "report": "Only first"}
        self.store.update_task(task["id"], "completed", {"skill_id": first, "skill_result": old})
        def state():
            return self.store.one("tasks", task["id"])
        def use(key, name=None):
            return skills.SkillUse(id=key, attachment={"filename": name, "base64": "QQ=="} if name else None)
        uses, selection = skills.prepare_skills(self.store, state(), [use(second, "second.txt"), use(first)])
        self.assertEqual(uses[0].attachment.filename, "second.txt")
        self.assertIsNone(uses[0].cached)
        self.assertEqual(uses[1].cached, old)
        self.assertEqual(selection["skill_ids"], [second, first])
        self.store.update_task(task["id"], "completed", selection)
        new = {"tool": "read", "filename": "second.txt", "report": "Only second"}
        skills.record_report(self.store, task["id"], "turn-two", uses[0].definition, new, single=False)
        again, _ = skills.prepare_skills(self.store, state(), [use(first, "replacement.txt"), use(second)])
        self.assertIsNone(again[0].cached)
        self.assertEqual(again[1].cached, new)
        _, cleared = skills.prepare_skills(self.store, state(), [])
        self.store.update_task(task["id"], "completed", cleared)
        self.assertEqual(state()["details"]["skill_reports"], {})
        history = self.store.rows("SELECT payload FROM events WHERE task_id=? AND kind='skill_tool' ORDER BY seq", (task["id"],))
        self.assertEqual([json.loads(e["payload"])["result"] for e in history], [old, new])

    def test_operator_api_and_task_bridge_use_separate_credentials(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        for path in ("/api/bootstrap", "/api/host/status"):
            self.assertEqual(self.client.get(path).status_code, 401)
            self.assertEqual(
                self.client.get(path, headers=self.operator).status_code, 200
            )
        task = self.seed("agent")
        self.service.connector_tokens[task["id"]] = "fixture-task-token"
        headers = {"X-Workspace-Task-Token": "fixture-task-token"}
        self.assertEqual(
            self.client.post(
                "/api/approvals/fixture", headers=headers, json={}
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.post("/api/snapshots", headers=headers, json={}).status_code,
            401,
        )
        self.assertEqual(self.post("/connectors/fixture/items", {}).status_code, 405)
        # The operator key alone cannot act as the task's connector credential.
        self.assertEqual(
            self.post(
                f"/chats/{task['id']}/mcp", {"id": 1, "method": "ping"}
            ).status_code,
            403,
        )

    def test_agent_launch_limits_connector_capability_to_attached_conversations(self):
        connector = self.post(
            "/connectors",
            {
                "name": "Test Notes",
                "base_url": "http://127.0.0.1:8890",
                "api_key": "fixture-connector-api-key",
            },
        )
        self.assertEqual(connector.status_code, 200, connector.text)
        connector_id = connector.json()["id"]
        task = self.seed("agent", connector_ids=[connector_id])
        unbound_response = self.post(
            "/chats",
            {
                "mode": "agent",
                "provider_id": task["provider_id"],
                "project_id": task["project_id"],
                "prompt": "Second conversation",
                "action_id": "fixture-unbound-agent",
            },
        )
        self.assertEqual(unbound_response.status_code, 200, unbound_response.text)
        unbound = unbound_response.json()
        caps = {
            "context_window": 8192,
            "max_output_tokens": 512,
            "tools": {"supported": True},
        }
        for conversation, attached in ((unbound, False), (task, True)):
            task_id = conversation["id"]
            with (
                self.subTest(attached=attached),
                patch(
                    "workspace.app.task_service.RuntimeClient.capabilities",
                    return_value=caps,
                ),
                patch("workspace.app.task_service.OwnedProcess") as spawn,
            ):
                started = self.post(
                    f"/chats/{task_id}/start", {"revision": conversation["revision"]}
                )
                self.assertEqual(started.status_code, 200, started.text)
                config = spawn.return_value.send.call_args.args[0]
                self.assertNotIn("fixture-connector-api-key", json.dumps(config))
                query = {"id": 1, "method": "tools/list"}
                url = "/api/chats/" + task_id + "/mcp"
                header = "X-Workspace-Task-Token"
                self.assertEqual(
                    self.client.post(
                        url, json=query, headers={header: config["owner_token"]}
                    ).status_code,
                    403,
                )
                self.assertEqual(attached, "connector_token" in config)
                self.assertEqual(attached, "connector_endpoint" in config)
                self.assertEqual(attached, task_id in self.service.connector_tokens)
                if attached:
                    token = config["connector_token"]
                    self.assertNotEqual(token, config["owner_token"])
                    allowed = self.client.post(url, json=query, headers={header: token})
                    self.assertEqual(allowed.status_code, 200, allowed.text)
                    self.assertEqual(len(allowed.json()["result"]["tools"]), 2)
                    self.assertNotIn("fixture-connector-api-key", allowed.text)
                    patch_path = Path(self.root) / "profile.json"
                    profile(
                        patch_path,
                        config["provider"],
                        caps,
                        task_id,
                        config["owner_token"],
                        connector_endpoint=config["connector_endpoint"],
                        connector_token=token,
                    )
                    profile_rows = json.loads(patch_path.read_text(encoding="utf-8"))
                    self.assertEqual(
                        profile_rows[-1]["insert"][0]["config"]["headers"],
                        {header: token},
                    )
                self.service._exit(task_id, 0, None)
                self.assertNotIn(task_id, self.service.connector_tokens)

    def test_busy_start_keeps_conversation_and_uses_edited_text_and_memory(self):
        task = self.seed("chat")
        endpoint = f"/chats/{task['id']}/start"
        self.service.processes["fixture-busy"] = Mock()
        self.assertEqual(
            self.post(endpoint, {"revision": task["revision"]}).status_code, 409
        )
        self.service.processes.clear()
        self.assertEqual(self.store.one("tasks", task["id"])["status"], "queued")
        with patch.object(self.service, "_start_chat", return_value=task) as run:
            result = self.post(
                endpoint,
                {
                    "revision": task["revision"],
                    "prompt": "Edited message",
                    "memory": False,
                },
            )
            self.assertEqual(result.status_code, 200)
        self.assertEqual(run.call_args.args[0]["id"], task["id"])
        self.assertEqual(run.call_args.args[1:], ("Edited message", False, None, []))

    def test_first_agent_start_validates_profile_before_admission_and_can_retry(self):
        task = self.seed("agent")
        endpoint = f"/chats/{task['id']}/start"
        caps = {
            "context_window": 4096,
            "max_output_tokens": 512,
            "tools": {"supported": True},
        }
        with (
            patch(
                "workspace.app.task_service.RuntimeClient.capabilities",
                return_value=caps,
            ),
            patch("workspace.app.task_service.OwnedProcess") as spawn,
        ):
            failed = self.post(endpoint, {"revision": task["revision"]})
            self.assertEqual(failed.status_code, 400)
            self.assertIn("MODEL_CONTEXT_TOO_SMALL", failed.text)
            self.assertEqual(
                self.store.one("tasks", task["id"])["revision"], task["revision"]
            )
            spawn.assert_not_called()
            caps["context_window"] = 8192
            retry = self.post(
                endpoint, {"revision": task["revision"], "prompt": "New instruction"}
            )
            self.assertEqual(retry.status_code, 200, retry.text)
            config = spawn.return_value.send.call_args.args[0]
            self.assertFalse(config["resume"])
            self.assertEqual(config["prompt"], "New instruction")

    def test_retryable_start_requires_evidence_that_no_session_received_input(self):
        task = self.seed("agent")
        key = task["id"]
        self.store.update_task(
            key, "starting", {"session_opened": False, "input_dispatched": False}
        )
        self.service._event(
            key, {"kind": "startup_failed", "message": "Fixture startup error"}
        )
        self.service._exit(key, 1, None)
        self.assertEqual(self.store.one("tasks", key)["status"], "retryable")

        history = (
            Path(self.root) / "data" / "tasks" / key / "dsh" / "sessions" / "fixture"
        )
        history.mkdir(parents=True)
        header = {"type": "session", "version": 4, "id": task["session_id"]}
        event = {"type": "user/message", "seq": 0, "data": {"content": "Accepted"}}
        (history / "log.v4.jsonl").write_text(
            json.dumps(header) + "\n" + json.dumps(event) + "\n", encoding="utf-8"
        )
        self.store.update_task(
            key, "running", {"session_opened": True, "input_dispatched": True}
        )
        self.service._event(key, {"kind": "result", "finish_reason": "stop"})
        self.service._exit(key, 7, None)
        current = self.store.one("tasks", key)
        self.assertEqual(current["status"], "uncertain")
        self.assertEqual(current["details"]["exit_code"], 7)
        self.assertEqual(current["details"]["history"]["canonical_events"], 1)
        blocked = self.post(
            f"/chats/{key}/start", {"revision": current["revision"], "prompt": "Next"}
        )
        self.assertEqual(blocked.status_code, 400)

    def test_probe_distinguishes_absent_cold_and_loaded_model(self):
        provider = self.post("/providers", {"model": "fixture"}).json()
        for model_ids, loaded_ids, expected in (
            (["other"], ["other"], (False, False)),
            (["fixture"], [], (True, False)),
            (["fixture"], ["fixture"], (True, True)),
        ):
            with (
                self.subTest(expected=expected),
                patch(
                    "workspace.app.runtime_client.RuntimeClient.request",
                    side_effect=[
                        {"data": [{"id": i} for i in model_ids]},
                        {"ready_model_ids": loaded_ids},
                    ],
                ),
            ):
                result = self.post(
                    f"/providers/{provider['id']}/probe", {"model": "fixture"}
                )
                self.assertEqual(result.status_code, 200)
                self.assertEqual(
                    (result.json()["model_available"], result.json()["model_loaded"]),
                    expected,
                )


class FileDiffReviewTests(unittest.TestCase):
    def test_empty_file_changes_and_non_text_files_are_visible(self):
        with tempfile.TemporaryDirectory() as root:
            project, baseline = Path(root) / "project", Path(root) / "baseline"
            project.mkdir()
            (project / "deleted").touch()
            (project / "converted.txt").write_text("before", encoding="utf-8")
            project_diff.capture(project, baseline)
            (project / "deleted").unlink()
            (project / "created").touch()
            (project / "converted.txt").write_bytes(b"\xff")
            result = project_diff.compare(project, baseline)
            changes = {row["path"]: row for row in result["files"]}
            self.assertEqual(changes["deleted"]["status"], "deleted")
            self.assertEqual(changes["created"]["status"], "added")
            self.assertEqual(changes["converted.txt"]["text_omitted"], "non_utf8")
            self.assertEqual(result["diff"], "")

    def test_links_and_limits_show_what_was_not_compared(self):
        with tempfile.TemporaryDirectory() as root:
            project, baseline = Path(root) / "project", Path(root) / "baseline"
            project.mkdir()
            target = Path(root) / "outside.txt"
            target.write_text("Outside fixture", encoding="utf-8")
            (project / "entry").write_text("before", encoding="utf-8")
            (project / "text").write_text("before", encoding="utf-8")
            project_diff.capture(project, baseline)
            (project / "entry").unlink()
            try:
                (project / "entry").symlink_to(target)
            except OSError as exc:
                self.skipTest(f"Symbolic links unavailable: {exc}")
            (project / "text").write_text("after", encoding="utf-8")
            with patch.object(project_diff, "MAX_DIFF", 1):
                result = project_diff.compare(project, baseline)
            self.assertEqual(result["files"][0]["status"], "type_changed")
            self.assertEqual(result["files"][0]["after_type"], "link")
            self.assertTrue(result["truncated"])
            self.assertFalse(result["complete"])
            self.assertNotIn("Outside fixture", json.dumps(result))


class ProcessExitReviewTests(unittest.TestCase):
    def test_result_followed_by_nonzero_exit_retains_actual_code(self):
        events, exits = [], []
        child = OwnedProcess(
            [
                sys.executable,
                "-c",
                'import json, sys; print(json.dumps({"kind": "result"}), flush=True); sys.exit(7)',
            ],
            os.getcwd(),
            os.environ.copy(),
            events.append,
            lambda code, error: exits.append((code, error)),
        )
        try:
            self.assertTrue(child.finished.wait(8))
            self.assertEqual(events, [{"kind": "result"}])
            self.assertEqual(exits, [(7, None)])
        finally:
            if not child.finished.is_set():
                child.stop()
