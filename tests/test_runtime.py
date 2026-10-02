"""Runtime regressions. No GGUF, GPU, external server or user data required."""
import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_data = tempfile.TemporaryDirectory()
os.environ['LOCAL_AI_GPP_DATA_DIR'] = _data.name

from fastapi import HTTPException
from fastapi.testclient import TestClient
from backend.app import core
from backend.app.main import app
from backend.app.llama_worker import build_llama_kwargs, fit_messages
from backend.app.routers.chat import ChatRequest, chat_messages, split_reasoning
from backend.app.streaming import event_response
from backend.app.worker_client import WorkerClient


class WorkerTests(unittest.TestCase):
    def test_offload_counts_survive_stderr_ring_rotation(self):
        worker = self.worker("import sys,json\nfor line in sys.stdin:\n sys.stderr.write('offloaded 12/33 layers to GPU\\n'+'diagnostic\\n'*100); sys.stderr.flush()\n print(json.dumps({'type':'result'}),flush=True)")
        list(worker.events({}, timeout=5))
        deadline = time.monotonic() + 2
        while not worker.offload_counts and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(worker.offload_counts, {'gpu_offloaded_layers': 12, 'model_layers': 33})
        self.assertLessEqual(len(worker.stderr), 80)

    def test_cpu_mode_disables_operation_and_kv_offload(self):
        kwargs = build_llama_kwargs({'model': {'path': 'test.gguf'}, 'runtime': {'n_gpu_layers': 0}})
        self.assertFalse(kwargs['offload_kqv'])
        self.assertFalse(kwargs['op_offload'])
        self.assertTrue(kwargs['verbose'], 'Quiet native loading redirects the JSON heartbeat pipe')

    def worker(self, code):
        worker = WorkerClient([sys.executable, '-u', '-c', code])
        self.addCleanup(worker.close)
        return worker

    def test_stderr_is_drained_and_worker_reused(self):
        worker = self.worker("import sys,json\nfor line in sys.stdin:\n sys.stderr.write('diagnostic\\n'*80000); sys.stderr.flush()\n print(json.dumps({'type':'result','pid':__import__('os').getpid()}),flush=True)")
        pids = [list(worker.events({}, timeout=8))[-1]['pid'] for _ in range(2)]
        self.assertEqual(pids[0], pids[1])
        self.assertIsNone(worker.process.poll())
        self.assertLessEqual(len(worker.stderr), 80)

    def test_total_timeout_kills_worker(self):
        worker = self.worker('import time,sys\nsys.stdin.readline(); time.sleep(30)')
        started = time.monotonic()
        with self.assertRaises(HTTPException) as result:
            list(worker.events({}, timeout=.35, idle_timeout=10))
        self.assertEqual(result.exception.status_code, 504)
        self.assertLess(time.monotonic() - started, 4)
        self.assertIsNotNone(worker.process.poll())

    def test_idle_timeout_kills_worker(self):
        worker = self.worker('import time,sys\nsys.stdin.readline(); time.sleep(30)')
        with self.assertRaises(HTTPException) as result:
            list(worker.events({}, timeout=10, idle_timeout=.25))
        self.assertEqual(result.exception.status_code, 504)

    def test_abandoned_stream_kills_worker(self):
        worker = self.worker("import sys,time\nsys.stdin.readline(); print('{\"type\":\"delta\",\"text\":\"hello\"}',flush=True); time.sleep(30)")
        stream = worker.events({}, timeout=10)
        self.assertEqual(next(stream)['type'], 'delta')
        stream.close()
        self.assertIsNotNone(worker.process.poll())

    def test_crash_returns_502(self):
        worker = self.worker('import sys\nsys.stdin.readline(); sys.exit(7)')
        with self.assertRaises(HTTPException) as result:
            list(worker.events({}, timeout=5))
        self.assertEqual(result.exception.status_code, 502)

    @unittest.skipUnless(os.name == 'nt', 'Windows process tree ownership')
    def test_cancel_stops_worker_child_process(self):
        import ctypes
        from ctypes import wintypes
        worker = self.worker("import sys,subprocess,json,time\nsys.stdin.readline(); child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); print(json.dumps({'type':'result','child':child.pid}),flush=True); time.sleep(30)")
        child = list(worker.events({}, timeout=5))[-1]['child']
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x100000, False, child)
        self.assertTrue(handle)
        try:
            worker.close()
            self.assertEqual(api.WaitForSingleObject(handle, 4000), 0)
        finally: api.CloseHandle(handle)


class MemoryAndStorageTests(unittest.TestCase):
    def test_worker_uses_current_python_without_windows_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            python = root / 'python'
            python.write_bytes(b'python')
            with patch.object(core, 'source_root', return_value=root), \
                 patch.object(core, 'PROJECT_ROOT', root), \
                 patch.object(sys, 'executable', str(python)), \
                 patch.dict(os.environ, {'LOCAL_AI_GPP_WORKER_PYTHON': ''}):
                self.assertEqual(core.external_worker_python(), str(python))

    def test_prompt_supplied_open_think_tag(self):
        result = split_reasoning('Thinking Process: 2+2=4.\n</think>\nFour.')
        self.assertEqual(result['answer'], 'Four.')
        self.assertEqual(result['reasoning'], 'Thinking Process: 2+2=4.')
        self.assertFalse(result['reasoning_truncated'])

    def test_memory_on_and_off(self):
        payload = ChatRequest(model_id='m', message='new', system_prompt='sys', history=[{'role':'user','content':'old'}, {'role':'assistant','content':'reply'}])
        self.assertEqual([m['content'] for m in chat_messages(payload)], ['sys','old','reply','new'])
        payload.memory = False
        self.assertEqual([m['content'] for m in chat_messages(payload)], ['sys','new'])

    def test_trim_complete_turn_preserves_system_and_latest_user(self):
        class Tokenizer:
            def tokenize(self, text, **kwargs): return list(text)
        messages = [{'role':'system','content':'sys'}, {'role':'user','content':'x'*500}, {'role':'assistant','content':'old'}, {'role':'user','content':'latest'}]
        kept, dropped = fit_messages(Tokenizer(), messages, 100, 600, True)
        self.assertEqual(dropped, 2)
        self.assertEqual([m['content'] for m in kept], ['sys', 'latest'])
        with self.assertRaises(ValueError):
            fit_messages(Tokenizer(), messages, 100, 600, False)
        with self.assertRaises(ValueError):
            fit_messages(Tokenizer(), [{'role':'user','content':'x'*900}], 100, 600, True)

    def test_corrupt_json_is_preserved(self):
        path = Path(_data.name) / 'corrupt.json'
        path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(HTTPException) as result:
            core._read_json(path, [])
        self.assertEqual(result.exception.status_code, 503)
        self.assertEqual(path.read_text(), '{broken')

    def test_atomic_json_and_no_leftover_temporary_file(self):
        path = Path(_data.name) / 'atomic.json'
        core._write_json(path, {'content': 'проверка'})
        self.assertEqual(core._read_json(path, {}), {'content': 'проверка'})
        self.assertFalse(list(path.parent.glob('atomic.json.*.tmp')))

    def test_packaged_model_path_survives_registry_updates_and_move(self):
        with tempfile.TemporaryDirectory() as original, tempfile.TemporaryDirectory() as moved:
            original_dir = Path(original) / 'models_storage'
            original_dir.mkdir()
            model_file = original_dir / 'test.gguf'
            model_file.write_bytes(b'GGUF')
            registry = Path(original) / 'models.json'
            with patch.object(core, 'MODELS_DIR', original_dir), patch.object(core, 'MODELS_FILE', registry):
                core.save_models([{'id': 'test', 'path': str(model_file), 'resolved_path': str(model_file)}])
            stored = json.loads(registry.read_text())
            self.assertFalse(Path(stored[0]['path']).is_absolute())
            self.assertNotIn('resolved_path', stored[0])
            moved_dir = Path(moved) / 'models_storage'
            moved_dir.mkdir()
            (moved_dir / 'test.gguf').write_bytes(b'GGUF')
            with patch.object(core, 'MODELS_DIR', moved_dir):
                self.assertEqual(core.resolve_model_file_path(stored[0]['path']), moved_dir / 'test.gguf')

    def test_busy_gate_is_bounded_and_recovers(self):
        with core.runtime_operation():
            with self.assertRaises(HTTPException) as result:
                with core.runtime_operation(): pass
            self.assertEqual(result.exception.status_code, 409)
        with core.runtime_operation(): pass


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_auth_and_disabled_models(self):
        with patch('backend.app.routers.compat.load_settings', return_value={'server':{'api_key':'test'}}):
            self.assertEqual(self.client.get('/v1/models').status_code, 401)
            self.assertEqual(self.client.get('/v1/models', headers={'Authorization':'Bearer test'}).status_code, 200)
        with patch('backend.app.routers.compat.load_settings', return_value={'server':{'openai_compat_enabled':False}}):
            self.assertEqual(self.client.get('/v1/models').status_code, 404)

    def test_memory_false_does_not_send_other_dialogue(self):
        result = {'choices':[{'message':{'content':'answer'},'finish_reason':'stop'}], 'usage':{}}
        with patch('backend.app.routers.compat.find_model'), patch('backend.app.routers.compat.create_chat_completion', return_value=result) as complete:
            response = self.client.post('/v1/chat/completions', json={'model':'m','memory':False,'messages':[{'role':'system','content':'sys'}, {'role':'user','content':'other'}, {'role':'assistant','content':'old'}, {'role':'user','content':'latest'}]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([m['content'] for m in complete.call_args.kwargs['messages']], ['sys','latest'])

    def test_standard_sse_and_usage(self):
        closed = []
        def events(**kwargs):
            try:
                yield {'type':'delta','text':'answer'}
                yield {'type':'done','finish_reason':'stop','usage':{'total_tokens':2,'estimated':True}}
            finally: closed.append(True)
        with patch('backend.app.routers.compat.find_model'), patch('backend.app.routers.compat.stream_chat_completion', events):
            response = self.client.post('/v1/chat/completions', json={'model':'m','messages':[{'role':'user','content':'x'}],'stream':True,'stream_options':{'include_usage':True}})
        data = [line[6:] for line in response.text.splitlines() if line.startswith('data: ')]
        self.assertEqual(data[-1], '[DONE]')
        chunks = [json.loads(line) for line in data[:-1]]
        self.assertTrue(all(c['object'] == 'chat.completion.chunk' for c in chunks))
        self.assertEqual(chunks[1]['choices'][0]['delta']['content'], 'answer')
        self.assertEqual(chunks[-1]['choices'], [])
        self.assertEqual(closed, [True])

    def test_invalid_roles_rejected(self):
        response = self.client.post('/v1/chat/completions', json={'model':'m','messages':[{'role':'invented','content':'x'}]})
        self.assertEqual(response.status_code, 422)


class DisconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_disconnect_closes_sync_iterator(self):
        closed = []
        class Request:
            async def is_disconnected(self): return True
        def events():
            try:
                yield 'data: test\n\n'
            finally: closed.append(True)
        response = event_response(Request(), events())
        self.assertEqual([item async for item in response.body_iterator], [])
        self.assertEqual(closed, [True])


if __name__ == '__main__':
    unittest.main()
