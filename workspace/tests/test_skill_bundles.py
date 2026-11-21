from pathlib import Path
import base64
import json
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from workspace.app.chat_run import ChatRun
from workspace.app.control_access import UI_TOKEN_HEADER
from workspace.app.main import create_app
from workspace.app.projects import create_project
from workspace.app.skill_import import import_manifest
from workspace.app import skills
from workspace.app.store import Store
from workspace.local_ai_control import portable_profile


class SkillBundleTests(unittest.TestCase):
    def test_profile_move_relocates_managed_projects_and_keeps_external_folder(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        external = directory / "linked"
        external.mkdir()
        original = directory / "old-profile"
        moved = directory / "on-another-drive"
        store = Store(original)
        try:
            managed = create_project(store, "Chat project")
            linked = create_project(store, "External files", str(external))
            with store.transaction() as db:
                db.execute("INSERT INTO projects VALUES(?,?,?,?)",
                           ("__direct_chats__", "Chats", str(original / "chat-files"), "today"))
        finally:
            store.close()
        original.rename(moved)
        store = Store(moved)
        try:
            self.assertEqual(Path(store.one("projects", managed["id"])["path"]), moved / "projects" / managed["id"])
            self.assertEqual(Path(store.one("projects", "__direct_chats__")["path"]), moved / "chat-files")
            self.assertEqual(Path(store.one("projects", linked["id"])["path"]), external)
        finally:
            store.close()

    def test_binary_output_is_private_to_its_chat_and_only_summary_reaches_model(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        app = create_app(Path(directory))
        client = self.enterContext(TestClient(app, base_url="http://127.0.0.1"))
        headers = {UI_TOKEN_HEADER: app.state.ui_token}
        def post(path, body):
            response = client.post("/api"+path, json=body, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()
        provider = post("/providers", {"model": "local"})
        def chat(tag):
            return post("/chats", {"mode": "chat", "provider_id":provider["id"], "prompt":"test",
                                   "action_id":"create-test-chat-"+tag})
        first, other = chat("one"), chat("two")
        mapping = {"url":"http://127.0.0.1:4173/mcp","tool":"bundle",
                   "files":{"argument":"docs","filename":"title","base64":"data"}}
        item = post("/skills", {"filename":"bundle.md","markdown":"Make a result.\n```workspace-mcp\n"+json.dumps(mapping)+"\n```"})
        self.assertTrue(item["accepts_multiple"])
        raw = b"%PDF-" + b"demo " * 70000
        encoded = base64.b64encode(raw).decode()
        use = skills.SkillUse(id=item["id"], attachments=[
            {"filename": "one.txt", "base64":"QQ=="}, {"filename":"two.txt","base64":"Qg=="}])
        def handler(req):
            payload = json.loads(req.content)
            method = payload["method"]
            if method == "initialize": result = {"protocolVersion":"2025-11-25"}
            elif method == "notifications/initialized": return httpx.Response(202)
            elif method == "tools/list": result={"tools":[{"name":"bundle","annotations":{"readOnlyHint":True}}]}
            else:
                self.assertEqual(payload["params"]["arguments"]["docs"], [{"title":"one.txt","data":"QQ=="},{"title":"two.txt","data":"Qg=="}])
                result = {"structuredContent":{"count":2}, "content":[{"type":"text","text":"PRIVATE-OUTPUT-TEXT"},
                          {"type":"resource","resource":{"uri":"demo://exports/%D0%98%D1%82%D0%BE%D0%B3.pdf","mimeType":"application/pdf","blob":encoded}}]}
            return httpx.Response(200,json={"jsonrpc":"2.0","id":payload["id"],"result":result})
        store = app.state.store
        contexts, _ = skills.prepare_skills(store,store.one("tasks",first["id"]),[use])
        host = SimpleNamespace(chat=first,store=store,cancelled=threading.Event(),skill_contexts=contexts)
        original=skills.invoke
        with patch.object(skills,"invoke",side_effect=lambda s,a,**kw:original(s,a,transport=httpx.MockTransport(handler),**kw)):
            prompt = ChatRun.skill_prompt(host,"turn-a")
        self.assertIn('"count": 2',prompt)
        for private in ("PRIVATE-OUTPUT-TEXT", encoded[:60], "demo://exports", "Итог.pdf"):
            self.assertNotIn(private,prompt)
        event=json.loads(store.rows("SELECT payload FROM events WHERE kind='skill_tool'")[0]["payload"])
        self.assertNotIn(encoded[:60],json.dumps(event))
        attachment=event["result"]["artifacts"][0]
        self.assertEqual((attachment["name"],attachment["size"]),("Итог.pdf",len(raw)))
        def link(chat_id):return f"/api/chats/{chat_id}/artifacts/{attachment['id']}"
        response=client.get(link(first["id"]),headers=headers)
        self.assertEqual(response.content,raw)
        self.assertIn("attachment",response.headers["content-disposition"])
        self.assertEqual(client.get(link(other["id"]),headers=headers).status_code,404)
        self.assertEqual(client.get(link(first["id"])).status_code,401)
        contexts, _ = skills.prepare_skills(store,store.one("tasks",first["id"]),[skills.SkillUse(id=item["id"])])
        host.skill_contexts=contexts
        with patch.object(skills,"invoke",side_effect=AssertionError("cached report was not reused")):
            self.assertEqual(ChatRun.skill_prompt(host,"turn-b"),prompt)

    def test_manifest_uses_bundle_relative_paths_and_updates_the_same_skill(self):
        directory=Path(self.enterContext(tempfile.TemporaryDirectory()))
        bundle, profile=directory/"kit",directory/"profile"
        (bundle/"mcp").mkdir(parents=True)
        (bundle/"skills").mkdir()
        md=bundle/"skills"/"demo.md"
        key=bundle/"mcp"/"key.txt"
        key.write_text("test-token",encoding="ascii")
        definition={"url":"http://127.0.0.1:4166/mcp","tool":"read"}
        def write(text):
            md.write_text(text+"\n```workspace-mcp\n"+json.dumps(definition)+"\n```",encoding="utf-8")
        write("First version.")
        manifest=bundle/"mcp"/"settings.json"
        cfg={"url":definition["url"],"tokenFile":"mcp/key.txt","skills":[{"path":"skills/demo.md"}]}
        manifest.write_text(json.dumps(cfg),encoding="utf-8")
        self.assertEqual(import_manifest(profile,manifest,bundle)["added"],1)
        self.assertEqual(import_manifest(profile,manifest,bundle)["retained"],1)
        write("Revised version.")
        self.assertEqual(import_manifest(profile,manifest,bundle)["updated"],1)
        cfg["skills"].append({"path":"../other.md"})
        manifest.write_text(json.dumps(cfg),encoding="utf-8")
        with self.assertRaises(ValueError): import_manifest(profile,manifest,bundle)
        store=Store(profile)
        try:
            rows=skills.list_skills(store)
            self.assertEqual(len(rows),1)
            self.assertNotIn("api_key",rows[0])
        finally: store.close()
        self.assertEqual(portable_profile(bundle/"dist"/"worker"),bundle/"workspace-profile")

    def test_group_migrates_existing_operations_without_changing_identity(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        profile = root / "profile"
        manifest = root / "settings.json"
        config = {"url": "http://127.0.0.1:4166/mcp", "skills": []}
        for name in ("inventory", "receipt"):
            entry = root / (name + ".md")
            entry.write_text("Process " + name, encoding="utf-8")
            config["skills"].append({"path": entry.name})

        def update():
            manifest.write_text(json.dumps(config), encoding="utf-8")
            return import_manifest(profile, manifest)

        def library():
            store = Store(profile)
            try:
                return skills.list_skills(store)
            finally:
                store.close()

        self.assertEqual(update()["added"], 2)
        ids = [item["id"] for item in library()]
        config["skillGroup"] = {"id": "office", "name": "Office"}
        self.assertEqual(update()["updated"], 2)
        grouped = library()
        self.assertEqual([item["id"] for item in grouped], ids)
        self.assertEqual(grouped[0]["group"], grouped[1]["group"])
        self.assertEqual(grouped[0]["group"]["name"], "Office")
        self.assertEqual(update()["retained"], 2)
        config["skillGroup"]["name"] = "Orders"
        self.assertEqual(update()["updated"], 2)
        self.assertEqual(library()[0]["group"]["key"], grouped[0]["group"]["key"])
        config["skillGroup"]["id"] = ""
        with self.assertRaises(ValueError):
            update()
        self.assertEqual(library()[0]["group"]["name"], "Orders")
        del config["skillGroup"]
        self.assertEqual(update()["updated"], 2)
        self.assertIsNone(library()[0]["group"])
        self.assertEqual([item["id"] for item in library()], ids)
