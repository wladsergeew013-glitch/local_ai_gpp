import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path

import httpx
from workspace.app import skills
from workspace.app.store import Store


class MarkdownSkillsTest(unittest.TestCase):
    def test_use_an_unrelated_tool_declared_in_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            doc = '''---
name: local-catalog
description: Summarize the attached catalog.
---
Read the item codes and list them.

```workspace-mcp
{"url":"http://localhost:9092/catalog/mcp","tool":"read_product_codes","file":{"filename":"name","base64":"bytes"}}
```
'''
            store = Store(Path(directory))
            try:
                registered = skills.register(store, skills.SkillInput(filename='SKILL.md', markdown=doc, api_key='local-test-key'))
                info, attachment = skills.selected_skill(store, skills.SkillUse(id=registered['id'], attachment={'filename': 'sample.txt', 'base64': 'QQ=='}))
                self.assertEqual(skills.list_skills(store), [registered])
                self.assertNotIn('api_key', registered)
                methods = []

                def handler(request):
                    self.assertEqual(str(request.url), 'http://127.0.0.1:9092/catalog/mcp')
                    self.assertEqual(request.headers['Authorization'], 'Bearer local-test-key')
                    data = json.loads(request.content)
                    method = data['method']
                    methods.append(method)
                    timeouts = request.extensions["timeout"]
                    self.assertEqual(timeouts["connect"], 4)
                    self.assertEqual(timeouts["read"], 900 if method == "tools/call" else 60)
                    if method == 'initialize':
                        result = {'protocolVersion': '2025-11-25'}
                    elif method == 'notifications/initialized':
                        return httpx.Response(202)
                    elif method == 'tools/list':
                        result = {'tools': [{'name': 'read_product_codes', 'annotations': {'readOnlyHint': True}}]}
                    else:
                        self.assertEqual(data['params']['arguments'], {'name': 'sample.txt', 'bytes': 'QQ=='})
                        result = {'structuredContent': {'codes': ['A']}}
                    return httpx.Response(200, json={'jsonrpc': '2.0', 'id': data['id'], 'result': result})

                result = skills.invoke(info, attachment, transport=httpx.MockTransport(handler))
                self.assertEqual(result['report'], {'codes': ['A']})
                self.assertEqual(methods, ['initialize', 'notifications/initialized', 'tools/list', 'tools/call'])
            finally:
                store.close()

    def test_stop_interrupts_wait_for_mcp_headers(self):
        waiting, stopped, closed = threading.Event(), threading.Event(), threading.Event()
        definition = {"connection": {"url": "http://localhost:9092/mcp", "tool": "read"}, "api_key": ""}
        async def server(request):
            rpc = json.loads(request.content)
            if rpc["method"] == "initialize":
                result = {"protocolVersion": skills.REVISION}
            elif rpc["method"] == "notifications/initialized":
                return httpx.Response(202)
            elif rpc["method"] == "tools/list":
                result = {"tools": [{"name": "read", "annotations": {"readOnlyHint": True}}]}
            else:
                waiting.set()
                try:
                    await asyncio.sleep(10)
                finally:
                    closed.set()
                self.fail("Cancelled tool call ran to completion")
            return httpx.Response(200, json={"id": rpc["id"], "result": result})

        errors = []
        def invoke():
            try:
                skills.invoke(definition, None, transport=httpx.MockTransport(server), cancelled=stopped)
            except ValueError as exc:
                errors.append(str(exc))

        thread = threading.Thread(target=invoke)
        thread.start()
        try:
            self.assertTrue(waiting.wait(3))
        finally:
            stopped.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(closed.is_set())
        self.assertEqual(errors, ["Выполнение навыка остановлено."])

    def test_read_timeout_distinguished_from_connect_failure(self):
        definition = {"connection": {"url": "http://localhost:9092/mcp", "tool": "read"}, "api_key": ""}
        for failure, message in [(httpx.ReadTimeout, "60 с при запросе initialize"),
                                 (httpx.ConnectError, "подключиться к локальному MCP")]:
            def fail(request):
                raise failure("timed out", request=request)
            with self.subTest(failure=failure), self.assertRaisesRegex(ValueError, message):
                skills.invoke(definition, None, transport=httpx.MockTransport(fail))

    def test_import_does_not_allow_a_remote_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            try:
                doc = 'Read the document.\n\n```workspace-mcp\n{"url":"https://example.org/mcp","tool":"read"}\n```'
                with self.assertRaises(ValueError):
                    skills.register(store, skills.SkillInput(filename='Read.md', markdown=doc))
                self.assertEqual(skills.list_skills(store), [])
            finally:
                store.close()


    def test_sections_are_explicit_and_keep_unmarked_instructions(self):
        document = (
            "Core rules.\n## People <!-- option: people -->\nNames.\n"
            "### Roles\nRole codes.\n## Dates <!-- option: dates -->\nDates only.\n"
            "## Format\nKeep the table.\n~~~md\n## Example <!-- option: fake -->\n~~~"
        )
        options, default = skills.instruction_sections(document)
        self.assertEqual(options, [{"id": "people", "title": "People"}, {"id": "dates", "title": "Dates"}])
        _, reduced = skills.instruction_sections(document, ["people"])
        self.assertIn("Names.", default)
        for text in ("Names.", "Role codes.", "<!-- option: dates -->"):
            self.assertNotIn(text, reduced)
        for text in ("Core rules.", "Dates only.", "Keep the table.", "<!-- option: fake -->"):
            self.assertIn(text, reduced)
        with self.assertRaises(ValueError):
            skills.instruction_sections(document, ["unknown"])
        with self.assertRaises(ValueError):
            skills.instruction_sections(document + "\n## Duplicate <!-- option: people -->")
        ordinary = "# Title\n\n## Always included\n- Normal checklist"
        self.assertEqual(skills.instruction_sections(ordinary), ([], ordinary))

    def test_batch_import_is_atomic_and_preserves_order(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            try:
                first = skills.SkillInput(filename="first.md", markdown="First.")
                second = skills.SkillInput(filename="second.md", markdown="Second.")
                with self.assertRaisesRegex(ValueError, "bad.txt"):
                    skills.register_many(store, [first, skills.SkillInput(filename="bad.txt", markdown="x")])
                self.assertEqual(skills.list_skills(store), [])
                added = skills.register_many(store, [second, first])
                self.assertEqual([item["name"] for item in added], ["second", "first"])
                self.assertEqual(skills.list_skills(store), added)
            finally:
                store.close()

if __name__ == '__main__':
    unittest.main()
