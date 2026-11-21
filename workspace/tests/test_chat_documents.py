import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from workspace.app import chat_documents as docs, skills
from workspace.app.chat_run import ChatRun
from workspace.app.control_access import UI_TOKEN_HEADER
from workspace.app.main import create_app
from workspace.app.skill_routing import choose, parse_plan, selections


class ChatDocumentsTest(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.app = create_app(Path(tmp))
        self.api = self.enterContext(TestClient(self.app, base_url="http://127.0.0.1"))
        self.api.headers[UI_TOKEN_HEADER] = self.app.state.ui_token
        self.db = self.app.state.store
        self.provider = self.api.post("/api/providers", json={"model": "test"}).json()["id"]
        self.chat = self.new_chat("first-chat-action-01")

    def new_chat(self, key):
        return self.api.post("/api/chats", json={"prompt":"test", "mode":"chat", "provider_id":self.provider,
                                                  "action_id":key}).json()["id"]

    def upload(self, name, contents):
        r = self.api.post(f"/api/chats/{self.chat}/documents", params={"filename": name}, content=contents)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def test_new_message_replaces_active_input_even_with_same_filename(self):
        first = self.upload("лист.txt", b"first")
        self.assertEqual(first, self.upload("лист.txt", b"first"))
        second = self.upload("лист.txt", b"second")
        a, b = docs.resolve(self.db, self.chat, [first, second])
        self.db.event(self.chat, "chat_message", {"role":"user","turn_id":"old","documents":[a]})
        self.assertEqual(docs.active_documents(self.db, self.chat, [b], "лист.txt", True), [b])
        self.assertEqual(docs.active_documents(self.db, self.chat, [], "продолжи", True), [a])
        self.assertEqual(docs.active_documents(self.db, self.chat, [], "продолжи", False), [])
        other = self.new_chat("other-chat-action-02")
        with self.assertRaises(ValueError):
            docs.resolve(self.db, other, [first])
        self.assertEqual(self.api.get(f"/api/chats/{other}/artifacts/{first}").status_code, 404)
        self.assertEqual(self.api.get(f"/api/chats/{self.chat}/artifacts/{first}").content, b"first")

    def test_router_selects_individual_or_group_inputs_and_can_skip_every_skill(self):
        docs_in = [{"id":"a"}, {"id":"b"}]
        def ctx(field):
            return skills.ChatSkill({"connection": {field: {"filename":"n"}}}, "", None, None)
        contexts = [ctx("file"), ctx("files")]
        self.assertEqual(selections({"use":[]}, contexts, docs_in), [])
        plan = {"use":[{"skill":1,"files":"all"}, {"skill":2,"files":[2,1]}]}
        self.assertEqual([batch for _, batch in selections(plan, contexts, docs_in)],
                         [[docs_in[0]], [docs_in[1]], list(reversed(docs_in))])
        for invalid in ({"skill":3},{"skill":1,"files":[0]},{"skill":2,"files":[True]}):
            with self.assertRaises(ValueError):
                selections({"use":[invalid]}, contexts, docs_in)

    def test_new_same_name_file_contents_travel_with_current_user_message(self):
        first, new = docs.resolve(self.db, self.chat, [
            self.upload("order.txt", b"MINT-7319"), self.upload("order.txt", b"IRIS-9042")])
        for role, text in [("user", "Read the code"), ("assistant", "MINT-7319")]:
            self.db.event(self.chat, "chat_message", {"turn_id": "old", "role": role, "text": text,
                          "phase": "done", "documents": [first] if role == "user" else []})
        captured = []
        def stream(req):
            captured.extend(json.loads(req.content)["messages"])
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n')
        transport = httpx.Client(transport=httpx.MockTransport(stream))
        run = ChatRun(self.db, self.db.one("tasks", self.chat), self.db.one("providers", self.provider),
                      "test", "Read this code", True, lambda *_: None, automatic=True, documents=[new], new_documents=[new])
        with patch("workspace.app.chat_run.RuntimeClient.capabilities", return_value={"max_output_tokens": 512}), \
             patch("workspace.app.chat_run.httpx.Client", return_value=transport):
            run._run()
        self.assertEqual(captured[-1]["role"], "user")
        self.assertIn("IRIS-9042", captured[-1]["content"])
        self.assertNotIn("MINT-7319", captured[-1]["content"])
        self.assertNotIn("IRIS-9042", captured[0]["content"])

    def test_router_recovers_only_complete_actions_missing_the_outer_brace(self):
        plan = {"use": [{"skill": 1, "files": "all"}]}
        text = json.dumps(plan)
        self.assertEqual(parse_plan(text[:-1]), plan)
        for incomplete in (text[:-2], '{"use":[{"skill":', text + " more"):
            with self.assertRaises(ValueError):
                parse_plan(incomplete)

    def test_optional_document_skill_clears_animation_even_on_error(self):
        item = skills.skill_definition(skills.SkillInput(filename="SKILL.md", markdown=
            "---\nname: build-file\nworkspace-activity: documents\n---\nMake a file.\n"
            '```workspace-mcp\n{"url":"http://localhost:9876/mcp","tool":"make"}\n```'))
        context = skills.ChatSkill(item,"Make a file.",None,None)
        from threading import Event
        run = SimpleNamespace(skill_contexts=[context], chat={"id":self.chat}, store=self.db,cancelled=Event())
        states = []
        with patch("workspace.app.chat_run.activity", side_effect=states.append), patch.object(skills, "invoke", side_effect=ValueError("offline")):
            with self.assertRaisesRegex(ValueError, "offline"):
                ChatRun.skill_prompt(run, "turn")
        self.assertEqual(states, ["documents","running"])

    def test_declared_formats_filter_all_and_reject_unrelated_handles(self):
        item = skills.skill_definition(skills.SkillInput(filename="table.md", markdown=
            "---\nname: Table\nworkspace-accepts: .CSV, .tsv\n---\nСверь таблицы.\n"
            '```workspace-mcp\n{"url":"http://localhost:9876/mcp","tool":"scan","files":{"argument":"batch","filename":"n","base64":"b"}}\n```'))
        context = skills.ChatSkill(item, item["instructions"], None, None)
        inputs = [{"name":"ввод.CsV"}, {"name":"заметка.txt"}, {"name":"итоги.tsv"}]
        result = selections({"use":[{"skill":1,"files":"all"}]}, [context], inputs)
        self.assertEqual(result[0][1], [inputs[0], inputs[2]])
        with self.assertRaises(ValueError):
            selections({"use":[{"skill":1,"files":[2]}]}, [context], inputs)
        from threading import Event
        fresh = [inputs[1]]
        self.assertEqual(choose(None, {}, [], [context], fresh, Event(), 512, True), ([], fresh))
