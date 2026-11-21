import base64
import asyncio
import json
import shutil
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from workspace.app.agent_plugins import AgentPluginContext
from workspace.app.connector_mcp import handle
from workspace.app.main import create_app
from workspace.app.plugin_catalog import TDMS_ENDPOINT, TDMS_PRODUCT, TDMS_TOOLS
from workspace.app.plugin_execution import PluginCall, PluginGateway, task_files
from workspace.app.plugin_manifest import package_from_snapshot, read_package
from workspace.app.plugin_mcp import McpError
from workspace.app.plugin_workflow import run_workflow
from workspace.app.skill_resources import Resource, save_resources
from workspace.app.skills import ChatSkill, SkillUse, all_definitions, selected_skill
from workspace.app.store import Store, identifier
from workspace.app.task_service import TaskService


EXAMPLE = Path(__file__).resolve().parents[2] / "examples/workspace-skill-package"
CAPABILITIES = {"tools": {"listChanged": True}, "resources": {}}


def example_config():
    return json.loads((EXAMPLE / "manifest.json").read_text(encoding="utf-8"))


def make_package(config):
    return package_from_snapshot(json.dumps(config), {entry["path"]: "# Инструкция" for entry in config["skills"]})


def embedded(filename, data):
    return {"type": "resource", "resource": {
        "uri": "workspace-test://results/" + filename, "mimeType": "text/plain",
        "blob": base64.b64encode(data).decode("ascii"),
    }}


def file_tool(name):
    return {"name": name, "inputSchema": {
        "type": "object", "properties": {
            "filename": {"type": "string"}, "contentBase64": {"type": "string"},
        }, "required": ["filename", "contentBase64"], "additionalProperties": False,
    }}


class Server:
    def __init__(self, *, name="Workspace Document Example", tools=None, perform=None):
        self.name = name
        self.tools = tools if tools is not None else [file_tool("prepare"), file_tool("inspect")]
        self.perform = perform or (lambda params: {"content": [], "structuredContent": {"done": True}})
        self.calls = []
        self.requests = []

    def transport(self, request):
        if request.method == "GET":
            return httpx.Response(405)
        rpc = json.loads(request.content)
        method, params = rpc["method"], rpc.get("params", {})
        self.requests.append((method, str(request.url), request.headers))
        if method == "initialize":
            result = {"protocolVersion": "2025-11-25", "serverInfo": {"name": self.name, "version": "1"},
                      "capabilities": CAPABILITIES}
        elif method.startswith("notifications/"):
            return httpx.Response(202)
        elif method == "tools/list":
            result = {"tools": self.tools[1:]} if params.get("cursor") else {
                "tools": self.tools[:1], **({"nextCursor": "next"} if len(self.tools) > 1 else {})
            }
        elif method == "tools/call":
            self.calls.append(params)
            result = self.perform(params)
        elif method == "resources/read":
            if params["uri"] == "workspace-test://results/extra.txt":
                result = {"contents": [embedded("extra.txt", b"another file")["resource"]]}
            else:
                return httpx.Response(404)
        else:
            raise AssertionError(method)
        reply = {"jsonrpc": "2.0", "id": rpc["id"], "result": result}
        if method == "tools/call":
            progress = {"jsonrpc": "2.0", "method": "notifications/progress", "params": {
                "progressToken": params["_meta"]["progressToken"], "progress": 1, "total": 1,
            }}
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                                  text=f"data: {json.dumps(progress)}\n\ndata: {json.dumps(reply)}\n\n")
        headers = {"MCP-Session-Id": "workspace-test-session"} if method == "initialize" else {}
        return httpx.Response(200, json=reply, headers=headers)


class PluginPackagesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "profile")
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.store.close)
        self.provider_id = identifier()
        with self.store.transaction() as db:
            db.execute("INSERT INTO providers VALUES(?,?,?,?,?)",
                       (self.provider_id, "model", "http://127.0.0.1:8765/v1", "test", ""))
        self.service = TaskService(self.store)
        self.chat = self.new_chat()

    def new_chat(self):
        return self.service.create(None, self.provider_id, "test", identifier(), mode="chat")["id"]

    def install(self, config=None):
        config = config or example_config()
        self.store.plugins.install(make_package(config))
        return next(item for item in self.store.plugins.definitions().values()
                    if item and item["product"] == config["product"])

    def file(self, name="source.txt", data=b"original", task=None):
        with self.store.transaction() as db:
            return save_resources(db, task or self.chat, [Resource(name, "text/plain", data)])[0]

    def call(self, skill, tool="prepare", files=None, arguments=None, task=None):
        return PluginCall(identifier(), task or self.chat, "test-agent", "turn", skill, tool, arguments or {}, files or [])

    def test_one_manifest_offline_and_portable_snapshot(self):
        fixture = shutil.copytree(EXAMPLE, self.root / "bundle")
        package = read_package(fixture / "manifest.json")
        self.store.plugins.install(package)
        self.store.plugins.set_enabled(package.manifest["product"], "", False)
        self.store.plugins.set_credential(package.manifest["product"], "profile-secret")
        (fixture / "prepare-and-check.md").unlink()
        portable = self.root / "other"
        shutil.copytree(self.store.plugins.folder, portable / "Skills")
        other = Store(portable)
        self.addCleanup(other.close)
        restored, = other.plugins.definitions().values()
        self.assertTrue(restored["enabled"])
        self.assertIn("идентификатор", restored["instructions"])
        self.assertEqual(list(self.store.plugins.definitions()), [restored["id"]])
        self.assertEqual(other.plugins.credential(restored["product"], restored["mcp"]["url"]), "")

    def test_version_update_with_new_port_keeps_state_and_tombstones(self):
        config = example_config()
        one = self.install(config)
        self.store.plugins.set_enabled(one["product"], one["name"], False)
        self.store.plugins.set_enabled(one["product"], "", False)
        self.store.plugins.set_credential(one["product"], "old-key")
        config["packageVersion"] = "1.1"
        config["mcp"]["url"] = "http://127.0.0.1:19765/mcp"
        config["skills"][0]["title"] = "Changed"
        config["skills"][0]["path"] = "renamed.md"
        config["skills"].append({**config["skills"][0], "name": "new"})
        self.install(config)
        data = self.store.plugins.definitions()
        old = data[one["id"]]
        self.assertEqual(old["title"], "Changed")
        self.assertEqual(old["mcp"]["url"], config["mcp"]["url"])
        self.assertNotEqual(old["revision"], one["revision"])
        self.assertEqual(self.store.plugins.credential(one["product"], old["mcp"]["url"]), "")
        self.assertFalse(old["skill_enabled"])
        self.assertTrue(next(row for row in data.values() if row["name"] == "new")["skill_enabled"])
        self.assertFalse(any(row["enabled"] for row in data.values()))
        config["skills"].pop(0)
        self.store.plugins.install(make_package(config))
        with self.assertRaisesRegex(ValueError, "удалён"):
            selected_skill(self.store, SkillUse(id=one["id"]))

    def test_reject_bad_update_and_rollback_sqlite(self):
        config = example_config()
        first = self.install(config)
        for mutate in (
            lambda cfg: cfg.update(schemaVersion=3),
            lambda cfg: cfg.update(secret="invalid"),
            lambda cfg: cfg["skills"].append(dict(cfg["skills"][0])),
            lambda cfg: cfg["skills"][0].update(path="../escape.md"),
            lambda cfg: cfg["skills"][0].update(tool="inspect"),
        ):
            bad = example_config()
            mutate(bad)
            with self.assertRaises(ValueError):
                self.store.plugins.install(make_package(bad))
        with self.assertRaisesRegex(ValueError, "Повторяется"):
            package_from_snapshot('{"schemaVersion":2,"schemaVersion":2}', {})
        with self.store.plugins._database(create=True) as db:
            db.execute("CREATE TRIGGER deny_update BEFORE UPDATE ON packages BEGIN SELECT RAISE(ABORT,'disk failure'); END")
        config["packageVersion"] = "2"
        with self.assertRaises(sqlite3.IntegrityError):
            self.install(config)
        self.assertEqual(self.store.plugins.definitions()[first["id"]], first)
        fixture = shutil.copytree(EXAMPLE, self.root / "broken")
        (fixture / "prepare-and-check.md").unlink()
        with self.assertRaisesRegex(ValueError, "prepare-and-check.md"):
            read_package(fixture / "manifest.json")

    def test_legacy_normalization_and_exact_migration(self):
        config = example_config()
        config.pop("packageVersion")
        config["mcp"].pop("transport")
        config["product"] = TDMS_PRODUCT
        config["skillGroup"] = {"name": "TDMS", "id": "tdms"}
        config["mcp"]["url"] = TDMS_ENDPOINT
        config["skills"] = []
        ids = {}
        with self.store.transaction() as db:
            for name, tool in TDMS_TOOLS.items():
                key = ids[name] = identifier()
                legacy = {"id": key, "name": name, "group": {"id": "tdms", "name": "TDMS"},
                          "instructions": "legacy", "description": "", "bundle_source": TDMS_ENDPOINT,
                          "connection": {"tool": tool, "url": TDMS_ENDPOINT}}
                db.execute("INSERT INTO markdown_skills VALUES(?,?)", (key, json.dumps(legacy)))
                config["skills"].append({"name": name, "title": name, "description": name, "tool": tool,
                                         "path": name + ".md"})
        package = make_package(config)
        self.assertEqual(self.store.plugins.install(package)["migrated"], 5)
        self.assertEqual(self.store.plugins.install(package)["retained"], 5)
        self.assertEqual({row["name"]: key for key, row in all_definitions(self.store).items()}, ids)
        self.assertTrue(all(row["package_version"] is None for row in self.store.plugins.definitions().values()))

    def test_one_action_import_via_desktop_route_and_probe_offline(self):
        app = create_app(self.root / "api-profile")
        auth = {"X-Workspace-UI-Token": app.state.ui_token}
        with TestClient(app, base_url="http://127.0.0.1") as client, patch(
            "workspace.app.desktop_host.invoke", return_value={"path": str(EXAMPLE / "manifest.json")}
        ):
            self.assertEqual(client.post("/api/skills/packages/import", json={}).status_code, 401)
            response = client.post("/api/skills/packages/import", headers=auth, json={})
            self.assertEqual(response.json()["installed"], 1)
            server = Server(name="wrong")
            app.state.tasks.plugin_gateway.transport = httpx.MockTransport(server.transport)
            product = example_config()["product"]
            probe = client.post(f"/api/skills/packages/{product}/probe", headers=auth, json={})
            self.assertEqual(probe.json()["skills"]["prepare-and-check"]["connection"], "protocol_error")
            status = client.get("/api/bootstrap", headers=auth).json()["skills"][0]
            self.assertTrue(status["enabled"])
            self.assertEqual(server.calls, [])

    def test_multistep_model_actions_and_generated_files(self):
        skill = self.install()
        initial = self.file()

        def perform(call):
            fields = call["arguments"]
            if call["name"] == "prepare":
                self.assertEqual(base64.b64decode(fields["contentBase64"]), b"original")
                return {"content": [embedded("prepared.txt", b"prepared")]}
            self.assertEqual(fields["filename"], "prepared.txt")
            self.assertEqual(base64.b64decode(fields["contentBase64"]), b"prepared")
            return {"structuredContent": {"verified": True}, "content": []}

        server = Server(perform=perform)
        requests = []

        class Model:
            model = "deterministic"

            def chat_payload(self, value):
                return value

            def request(self, _method, _path, **request):
                payload = request["json"]
                requests.append(payload)
                state = json.loads(payload["messages"][-1]["content"].split("\n", 1)[1])
                self_test.assertNotIn("contentBase64", payload["messages"][-1]["content"])
                index = len(requests)
                if index <= 2:
                    outcome = {"tool": "prepare" if index == 1 else "inspect", "arguments": {},
                               "files": [state["files"][-1]["id"]]}
                else:
                    outcome = {"done": True, "message": "Проверено."}
                return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(outcome)}}]}

        self_test = self
        workflow, note = run_workflow(
            PluginGateway(self.store, transport=httpx.MockTransport(server.transport)),
            ChatSkill(skill, skill["instructions"], None, None), self.chat, "turn", [initial], Model(), {},
            [{"role": "user", "content": "Подготовь и проверь этот файл"}], 1024, threading.Event(),
        )
        self.assertEqual([item["tool"] for item in workflow], ["prepare", "inspect"])
        self.assertEqual(note, "Проверено.")
        self.assertEqual(len(task_files(self.store, self.chat)), 2)
        self.assertEqual(len(self.store.rows("SELECT 1 FROM events WHERE kind='plugin_progress'")), 2)

    def test_bad_schema_file_selection_and_task_scope_precede_send(self):
        skill = self.install()
        initial = self.file()
        other = self.new_chat()
        foreign = self.file(task=other)
        server = Server()
        gateway = PluginGateway(self.store, transport=httpx.MockTransport(server.transport))
        for call in (
            self.call(skill, files=[initial["id"], foreign["id"]]),
            self.call(skill, files=[foreign["id"]]),
            self.call(skill, files=[initial["id"]], arguments={"contentBase64": "foreign"}),
            self.call(skill, files=[initial["id"]], arguments={"undeclared": True}),
        ):
            with self.assertRaises(McpError):
                gateway.invoke(call)
        self.assertEqual(server.calls, [])
        server.tools.pop()
        with self.assertRaisesRegex(McpError, "inspect"):
            gateway.invoke(self.call(skill, files=[initial["id"]]))
        self.assertEqual(server.calls, [])

    def test_unknown_outcome_stops_only_the_affected_package(self):
        first = self.install()
        config = example_config()
        config["product"] = "Second-App"
        config["mcp"]["url"] = "http://127.0.0.1:19555/mcp"
        second = self.install(config)
        initial = self.file()
        server = Server()

        def transport(request):
            data = json.loads(request.content) if request.method == "POST" else {}
            if data.get("method") == "tools/call" and request.url.port == 8795:
                raise httpx.ReadTimeout("response lost")
            return server.transport(request)

        gateway = PluginGateway(self.store, transport=httpx.MockTransport(transport))
        with self.assertRaisesRegex(McpError, "Исход операции неизвестен"):
            gateway.invoke(self.call(first, files=[initial["id"]]))
        with self.assertRaisesRegex(McpError, "Исход предыдущей операции неизвестен"):
            gateway.invoke(self.call(first, files=[initial["id"]]))
        self.assertEqual(gateway.invoke(self.call(second, files=[initial["id"]]))["status"], "ok")
        self.assertEqual(gateway.public_status(first)["paused"], True)
        gateway.recover(first["product"])
        self.assertFalse(gateway.public_status(first)["paused"])

    def test_partial_error_preserves_files_and_warnings(self):
        skill = self.install()
        initial = self.file()
        profile = {"workspaceResultVersion": 1, "data": {"rows": 2}, "warnings": [{"message": "Сверить страницу", "page": 2}],
                   "artifacts": [
                       {"filename": "shared.txt", "mimeType": "text/plain", "contentBase64": "c2hhcmVk"},
                       {"filename": "broken.txt", "mimeType": "text/plain", "contentBase64": "not base64"},
                   ]}
        server = Server(perform=lambda _: {
            "isError": True,
            "structuredContent": profile,
            "content": [embedded("shared.txt", b"shared"), {"type": "text", "text": json.dumps(profile)},
                        {"type": "resource_link", "uri": "workspace-test://results/extra.txt"},
                        {"type": "resource_link", "uri": "workspace-test://results/missing.txt"}],
        })
        result = PluginGateway(self.store, transport=httpx.MockTransport(server.transport)).invoke(
            self.call(skill, files=[initial["id"]])
        )
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["warnings"], profile["warnings"])
        self.assertEqual(len(result["errors"]), 2)
        self.assertEqual([file["name"] for file in result["artifacts"]], ["shared.txt", "extra.txt"])
        self.assertNotIn("c2hhcmVk", json.dumps(result))

    def test_task_bridge_uses_the_same_snapshot_and_checks_live_disabling(self):
        item = self.install()
        with self.store.transaction() as db:
            db.execute("UPDATE tasks SET details=json_set(details,'$.mode','agent') WHERE id=?", (self.chat,))
        self.service.connector_tokens[self.chat] = "test-token"
        initial = self.file()
        self.service.agent_plugins[self.chat] = AgentPluginContext({item["id"]: item}, {initial["id"]})
        server = Server()
        self.service.plugin_gateway.transport = httpx.MockTransport(server.transport)

        def invoke(name, **args):
            result = handle(self.service, self.chat, "test-token", {
                "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args},
            })["result"]
            return result, json.loads(result["content"][0]["text"]) if not result["isError"] else {}

        self.assertEqual(invoke("list_workspace_skills")[1]["skills"][0]["key"], item["key"])
        self.assertIn("instructions", invoke("open_workspace_skill", skill=item["key"])[1])
        result, _ = invoke("call_workspace_skill", skill=item["key"], tool="inspect",
                           arguments={}, files=[initial["id"]])
        self.assertFalse(result["isError"])
        self.store.plugins.set_enabled(item["product"], item["name"], False)
        self.assertEqual(invoke("list_workspace_skills")[1]["skills"], [])
        self.assertTrue(invoke("call_workspace_skill", skill=item["key"], tool="prepare",
                               arguments={}, files=[initial["id"]])[0]["isError"])

    def test_queued_calls_keep_task_files_and_current_snapshot(self):
        config = example_config()
        skill = self.install(config)
        first = self.file(data=b"first")
        other = self.new_chat()
        second = self.file(data=b"second", task=other)
        first_sent, release, thread_started = threading.Event(), threading.Event(), threading.Event()
        sent, errors = [], []

        def perform(request):
            text = base64.b64decode(request["arguments"]["contentBase64"])
            sent.append(text)
            if text == b"first":
                first_sent.set()
                if not release.wait(5):
                    raise AssertionError("Test did not release the first call.")
            return {"content": [embedded("result.txt", text)]}

        server = Server(perform=perform)
        gateway = PluginGateway(self.store, transport=httpx.MockTransport(server.transport))

        def work(call):
            if call.task_id == other:
                thread_started.set()
            try:
                gateway.invoke(call)
            except Exception as exc:
                errors.append(exc)

        one = threading.Thread(target=work, args=(self.call(skill, files=[first["id"]]),))
        two = threading.Thread(target=work, args=(self.call(skill, files=[second["id"]], task=other),))
        one.start()
        try:
            self.assertTrue(first_sent.wait(2))
            two.start()
            self.assertTrue(thread_started.wait(2))
            config["skills"][0]["tools"] = []
            self.install(config)
            self.assertEqual(sent, [b"first"])
        finally:
            release.set()
            one.join(5)
            if two.ident is not None:
                two.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(sent, [b"first", b"second"])
        for chat, data in [(self.chat, b"first"), (other, b"second")]:
            saved, = self.store.rows("SELECT data FROM skill_artifacts WHERE task_id=? AND name='result.txt'", (chat,))
            self.assertEqual(saved["data"], data)
        self.assertEqual(gateway.describe(self.store.plugins.definitions()[skill["id"]]), [])

    def test_arguments_only_and_model_provider_independence(self):
        from workspace.app.runtime_client import RuntimeClient

        config = example_config()
        config["skills"][0]["tools"] = [{"name": "inspect"}]
        skill = self.install(config)
        schema = {"name": "inspect", "inputSchema": {"type": "object", "required": ["revision"],
                  "properties": {"revision": {"type": "integer", "minimum": 1}}}}
        server = Server(tools=[schema])
        gateway = PluginGateway(self.store, transport=httpx.MockTransport(server.transport))
        with self.assertRaisesRegex(McpError, "revision"):
            gateway.invoke(self.call(skill, tool="inspect", arguments={"revision": 0}))
        local = RuntimeClient({"base_url": "http://127.0.0.1:8765/v1", "model": "one"})
        remote = RuntimeClient({"base_url": "https://model.example/v1", "model": "two"})
        payload = {"model": "one", "messages": [], "memory": True, "chat_template_kwargs": {}}
        self.assertEqual(local.chat_payload(payload), payload)
        self.assertEqual(remote.chat_payload(payload), {"model": "one", "messages": []})
        self.assertEqual(gateway.invoke(self.call(skill, tool="inspect", arguments={"revision": 1}))["status"], "ok")
        self.assertEqual(server.calls[0]["arguments"], {"revision": 1})

    def test_native_model_decisions_are_scoped_before_workspace_calls_mcp(self):
        from workspace.app.skill_routing import choose

        skill = self.install()
        initial = self.file()
        provider = self

        class Model:
            model = "native-test"

            def chat_payload(self, payload):
                return payload

            def request(self, method, path, **options):
                payload = options["json"]
                stage = payload["tool_choice"]["function"]["name"]
                if stage == "workspace_select":
                    chosen = {"use": [{"skill": 1, "files": ["all"]}], "read": ["all"]}
                else:
                    provider.assertEqual(stage, "workspace_step")
                    chosen = {"tool": "unlisted", "arguments": {}, "files": ["f1"]}
                return {"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [
                    {"function": {"name": stage, "arguments": json.dumps(chosen)}},
                ]}}]}

        cancelled = threading.Event()
        context = ChatSkill(skill, skill["instructions"], None, None)
        messages = [{"role": "user", "content": "Подготовь файл"}]
        chosen, plain = choose(Model(), {}, messages, [context], [initial], cancelled, 768, True, native=True)
        self.assertEqual(chosen, [(context, [initial])])
        self.assertEqual(plain, [initial])
        server = Server()
        gateway = PluginGateway(self.store, transport=httpx.MockTransport(server.transport))
        with self.assertRaisesRegex(McpError, "неизвестный инструмент"):
            run_workflow(gateway, context, self.chat, "turn", [initial], Model(), {}, messages,
                         768, cancelled, native=True)
        self.assertEqual(server.calls, [])

    def test_child_agent_uses_only_delegated_task_files(self):
        item = self.install()
        wanted = self.file("wanted.txt")
        other = self.file("private.txt")
        with self.store.transaction() as db:
            db.execute("UPDATE tasks SET details=json_set(details,'$.mode','agent') WHERE id=?", (self.chat,))
        self.service.connector_tokens[self.chat] = "delegate-token"
        context = AgentPluginContext({item["id"]: item}, {wanted["id"], other["id"]}, opened={item["key"]})
        self.service.agent_plugins[self.chat] = context
        server = Server(perform=lambda _: {"content": [embedded("child.txt", b"child")]})
        self.service.plugin_gateway.transport = httpx.MockTransport(server.transport)
        actions = iter(({"tool": "prepare", "arguments": {}, "files": ["f1"]}, {"done": True}))

        def decide(model, method, path, **options):
            self.assertNotIn(other["name"], json.dumps(options["json"], ensure_ascii=False))
            return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(next(actions))}}]}

        with patch("workspace.app.runtime_client.RuntimeClient.capabilities", return_value={"max_output_tokens": 512}), patch(
            "workspace.app.runtime_client.RuntimeClient.request", autospec=True, side_effect=decide
        ):
            value = handle(self.service, self.chat, "delegate-token", {"id": 1, "method": "tools/call", "params": {
                "name": "delegate_workspace_skill",
                "arguments": {"skill": item["key"], "prompt": "Подготовь разрешённый файл", "files": [wanted["id"]]},
            }})["result"]
        self.assertFalse(value["isError"], value)
        delegated = json.loads(value["content"][0]["text"])
        self.assertEqual(server.calls[0]["arguments"]["filename"], wanted["name"])
        self.assertIn(delegated["operations"][0]["artifacts"][0]["id"], context.files)
        booked, = self.store.rows("SELECT agent_id FROM plugin_calls")
        self.assertEqual(booked["agent_id"], delegated["agent_id"])
        self.assertTrue(booked["agent_id"].startswith("child-"))

    def test_stopping_a_task_disconnects_a_silent_model_decision(self):
        from workspace.app.runtime_client import RuntimeClient

        entered, cancelled, disconnected = threading.Event(), threading.Event(), threading.Event()
        errors = []
        model = RuntimeClient({"base_url": "http://127.0.0.1:8765/v1", "model": "test"})

        async def silent_http(*_args, **_kwargs):
            entered.set()
            try:
                await asyncio.sleep(4)
            finally:
                disconnected.set()

        def run():
            try:
                model.request("POST", "chat/completions", cancelled=cancelled)
            except Exception as exc:
                errors.append(exc)

        with patch("workspace.app.runtime_client.httpx.AsyncClient.request", new=silent_http):
            worker = threading.Thread(target=run)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
            finally:
                cancelled.set()
                worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(disconnected.is_set())
        self.assertRegex(str(errors[0]), "остановлен")
