"""Failure transitions from the extended review; all files and models are synthetic."""
import asyncio
import contextlib
import copy
import importlib.util
import io
import json
import os
import queue
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from backend.app import core, llama_worker
from backend.app.model_paths import model_path
from backend.app.reasoning import split_reasoning
from backend.app.routers.compat import OpenAIChatRequest
from examples.client import LocalAI
from tools import exe_launcher as desktop

spec = importlib.util.spec_from_file_location('review_builder', Path(desktop.__file__).with_name('02_build_exe.py'))
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ExtendedReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = self.root / 'models_storage'
        self.brand = self.store / 'branding'
        self.brand.mkdir(parents=True)
        overrides = dict(PROJECT_ROOT=self.root, MODELS_DIR=self.store, MODELS_FILE=self.store/'models.json',
                         SETTINGS_FILE=self.store/'settings.json', BRANDING_DIR=self.brand, LOGS_DIR=self.root/'logs', RUNTIMES={})
        patches = patch.multiple(core, **overrides); patches.start(); self.addCleanup(patches.stop)

    def test_R08_native_submission_failure_preserves_draft_and_releases_busy(self):
        class Input:
            value = 'draft'
            def get(self): return self.value
            def set(self, value): self.value = value
        agent = desktop.NativeAssistantAgent.__new__(desktop.NativeAssistantAgent)
        agent.input_var = Input(); agent.busy_request = agent.awaiting_answer = False
        agent.runtime = {'n_ctx':4096}; agent.max_tokens = 128
        agent._set_state = lambda *_: None
        for target in ('read_chat_state', 'threading.Thread.start'):
            with patch('tools.exe_launcher.' + target, side_effect=OSError('failed')):
                if target.endswith('start'):
                    with patch.object(desktop, 'read_chat_state', return_value={'version':1}): agent._send_message()
                else: agent._send_message()
            self.assertEqual(agent.input_var.get(), 'draft')
            self.assertFalse(agent.busy_request or agent.awaiting_answer)

    def test_R18_orphan_assistant_can_be_trimmed_without_losing_latest_user(self):
        tokenizer = SimpleNamespace(tokenize=lambda value, **_: list(value))
        messages = [{'role':'system','content':'rules'}, {'role':'assistant','content':'x'*1000}, {'role':'user','content':'hello'}]
        kept, dropped = llama_worker.fit_messages(tokenizer, messages, 128, 512, True)
        self.assertEqual((kept, dropped), ([messages[0],messages[-1]],1))

    def test_R19_129th_request_is_valid_and_history_is_not_sliced(self):
        ai = LocalAI(model='m'); ai.history = [{'role':r,'content':'ok'} for _ in range(128) for r in ('user','assistant')]
        payloads = []
        response = {'choices':[{'message':{'content':'4'},'finish_reason':'stop'}]}
        def send(request, **_):
            payload = json.loads(request.data); payloads.append(payload)
            OpenAIChatRequest.model_validate(payload)
            return contextlib.closing(io.BytesIO(json.dumps(response).encode()))
        with patch('urllib.request.urlopen', side_effect=send): self.assertEqual(ai.ask('2+2?'),'4')
        self.assertEqual(len(payloads[0]['messages']),257)
        self.assertEqual(len(ai.history),258)

    def test_R20_only_explicit_reasoning_phase_is_split(self):
        for text in ('Code: <think>literal</think>', '```xml\n<think>x</think>\n```', 'Literal </think> marker'):
            self.assertEqual(split_reasoning(text)['answer'],text)
        parsed = split_reasoning('thoughts\n</think>\nFinal',True)
        self.assertEqual((parsed['answer'],parsed['reasoning']),('Final','thoughts'))
        unfinished = split_reasoning('Thoughts. Final answer: should not be guessed',True)
        self.assertEqual(unfinished['answer'],'')
        self.assertTrue(unfinished['reasoning_truncated'])

    def test_R21_incomplete_reasoning_never_changes_python_client_memory(self):
        ai = LocalAI(model='m'); original = list(ai.history)
        response = {'choices':[{'message':{'content':''},'finish_reason':'stop'}],
                    '_local_ai_gpp':{'answer':'','answer_state':'missing_after_reasoning','reasoning_truncated':True}}
        with patch('urllib.request.urlopen', return_value=contextlib.closing(io.BytesIO(json.dumps(response).encode()))):
            with self.assertRaisesRegex(RuntimeError,'final answer'): ai.ask('hello')
        self.assertEqual(ai.history, original)

    def test_R24_R25_R28_R29_cache_respects_fallback_file_identity_and_usage(self):
        path = self.root/'m.gguf'; path.write_bytes(b'old')
        model = {'id':'m','type':'LLM','path':str(path)}
        settings = copy.deepcopy(core.DEFAULT_SETTINGS)
        cfg = core._merge_runtime(model,settings)
        kwargs = llama_worker.build_llama_kwargs({'model':model,'runtime':cfg})
        worker = SimpleNamespace(process=SimpleNamespace(poll=lambda:None,pid=123),runtime={},close=lambda:None)
        entry = dict(runtime=worker,load_config=kwargs,model_signature=core.model_file_signature(path),model_loaded=True,
                     state='hot',last_used_ts=0,config=cfg)
        core.RUNTIMES['m']=entry
        with patch.object(core,'get_runtime_summary',return_value={}):
            self.assertIs(core._get_runtime(model,settings,runtime_override={'seed':9}),worker)
        self.assertGreater(entry['last_used_ts'],time.time()-2)
        replacement = self.root/'new.gguf'; replacement.write_bytes(b'new'); replacement.replace(path)
        with patch('backend.app.worker_client.WorkerClient', side_effect=RuntimeError('new constructor')) as constructor:
            with self.assertRaisesRegex(RuntimeError,'new constructor'): core._get_runtime(model,settings)
            constructor.assert_called_once()
        self.assertNotIn('m',core.RUNTIMES)
        entry['model_signature']=core.model_file_signature(path); worker.runtime['fallback_reason']='old fallback'; core.RUNTIMES['m']=entry
        with patch('backend.app.worker_client.WorkerClient', side_effect=RuntimeError('strict reload')):
            with self.assertRaisesRegex(RuntimeError,'strict reload'): core._get_runtime(model,settings,runtime_override={'gpu_fallback_to_cpu':False})

    def test_R23_failed_live_worker_is_not_reported_as_loaded(self):
        model = core.copy_uploaded_model('m','m.gguf',io.BytesIO(b'gguf'))
        closed = []
        worker = SimpleNamespace(process=SimpleNamespace(poll=lambda:None,pid=123),runtime={},close=lambda:closed.append(True))
        with patch('backend.app.worker_client.WorkerClient', return_value=worker), patch.object(core,'_worker_events',return_value=iter([{'type':'error','message':'constructor failed'}])):
            with self.assertRaises(HTTPException): core._get_runtime(model,core.DEFAULT_SETTINGS)
        self.assertFalse(core.RUNTIMES); self.assertTrue(closed)

    def test_R30_dll_context_preserves_body_error(self):
        with patch.object(sys,'frozen',True,create=True):
            with self.assertRaisesRegex(ValueError,'original body'):
                with core._clean_subprocess_dll_search_path(): raise ValueError('original body')

    def test_R31_cancelled_upload_cleans_all_owned_files(self):
        event=threading.Event(); event.set()
        with self.assertRaises(HTTPException) as error:
            core.copy_uploaded_model('m','m.gguf',io.BytesIO(b'payload'),cancel_event=event)
        self.assertEqual(error.exception.status_code,499)
        self.assertEqual(list((self.store/'uploads').iterdir()),[])

    def test_R32_invalid_settings_preserve_file_and_defaults_recover(self):
        core.save_settings({})
        original = core.SETTINGS_FILE.read_bytes()
        for payload in ({'runtime':None},{'server':None},{'runtime':{'temperature':999}}):
            with self.assertRaises(HTTPException) as error: core.save_settings(payload)
            self.assertEqual(error.exception.status_code,422)
            self.assertEqual(core.SETTINGS_FILE.read_bytes(),original)
        core.SETTINGS_FILE.write_text('[]')
        self.assertIsInstance(core.load_settings()['runtime'],dict)
        self.assertEqual(core.SETTINGS_FILE.read_text(),'[]')
        core.save_settings({'runtime':{'temperature':.3}})
        self.assertEqual(next(self.store.glob('settings.json.invalid-*')).read_text(),'[]')

    def test_R33_R34_atomic_logo_does_not_rewrite_concurrent_settings(self):
        core.save_settings({'runtime':{'temperature':.4}})
        old = self.brand/'logo.png'; old.write_bytes(b'old logo')
        settings = core.SETTINGS_FILE.read_bytes()
        replace = Path.replace
        def fail(source,target):
            if source.name.startswith('.logo-'): raise OSError('cannot rename')
            return replace(source,target)
        with patch.object(Path,'replace',fail):
            with self.assertRaises(HTTPException): core.upload_logo_file(io.BytesIO(b'new'),'new.png')
        self.assertEqual(old.read_bytes(),b'old logo')
        self.assertEqual(list(self.brand.glob('.logo-*')),[])
        core.upload_logo_file(io.BytesIO(b'new'),'new.webp')
        self.assertEqual(core.SETTINGS_FILE.read_bytes(),settings)
        self.assertFalse(old.exists())

    def test_R36_invalid_registry_never_becomes_an_empty_registry(self):
        core.MODELS_FILE.write_text('{}')
        with self.assertRaises(HTTPException) as error: core.load_models()
        self.assertEqual(error.exception.status_code,503)
        with self.assertRaises(HTTPException): core.copy_uploaded_model('m','m.gguf',io.BytesIO(b'payload'))
        self.assertEqual(core.MODELS_FILE.read_text(),'{}')

    def test_R38_utf8_filename_limit_precedes_creation(self):
        with self.assertRaises(HTTPException) as error: core.copy_uploaded_model('m','я'*130+'.gguf',io.BytesIO(b'payload'))
        self.assertEqual(error.exception.status_code,400)
        self.assertFalse((self.store/'uploads').exists())

    def test_R39_registration_retires_only_owned_unreferenced_upload(self):
        old = core.copy_uploaded_model('m','m.gguf',io.BytesIO(b'old'))
        path = self.root/'m.gguf'; path.write_bytes(b'external')
        core.register_model_path(name='m',model_type='LLM',model_path=str(path))
        self.assertFalse(Path(old['path']).exists())
        self.assertEqual(path.read_bytes(),b'external')

    def test_R40_log_write_failure_does_not_abort_inference_setup(self):
        with patch.object(core,'_append_request_log',side_effect=OSError('full disk')):
            request_id,path = core.create_request_log('m')
        self.assertTrue(request_id); self.assertEqual(path.parent,core.LOGS_DIR)

    def test_R42_R43_portable_names_and_paths_are_independent_of_cwd(self):
        self.assertNotEqual(builder._safe_model_folder_name({'name':'Model A','id':'1'},0),builder._safe_model_folder_name({'name':'Model_A','id':'2'},1))
        path=self.store/'x/m.gguf'; path.parent.mkdir(); path.write_bytes(b'gguf')
        self.assertEqual(model_path('models_storage\\x\\m.gguf',self.store,self.root),path)

    def test_R49_invalid_cached_zip_is_replaced_once(self):
        target = self.root/'embed.zip'; target.write_bytes(b'corrupt')
        content=io.BytesIO()
        with zipfile.ZipFile(content,'w') as archive: archive.writestr('python.exe',b'python')
        with patch.object(builder.urllib.request,'urlopen',return_value=contextlib.closing(io.BytesIO(content.getvalue()))) as download, patch.object(builder,'log'):
            builder.download_once('https://example.invalid/python.zip',target)
            builder.download_once('https://example.invalid/python.zip',target)
        download.assert_called_once()
        self.assertTrue(zipfile.is_zipfile(target))

    def test_C04_disabled_hub_never_opens_network(self):
        with patch.object(core.httpx,'AsyncClient') as client:
            self.assertEqual(asyncio.run(core.fetch_hub_models({'hub':{'enabled':False}})),[])
            with self.assertRaises(HTTPException): asyncio.run(core.import_from_hub({}, {'hub':{'enabled':False}}))
        client.assert_not_called()

    def test_R35_logo_upload_leaves_event_loop_available(self):
        import httpx
        from backend.app.main import app
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def upload(*_):
            entered.set(); release.wait(3); finished.set()
            return '/assets/branding/logo.png'
        async def exercise():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
                pending = asyncio.create_task(client.post('/api/settings/logo',files={'logo_file':('logo.png',b'png')}))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait,1))
                    self.assertEqual((await client.get('/api/health')).status_code,200)
                    self.assertFalse(finished.is_set(), 'Upload blocked the event loop')
                finally: release.set()
                self.assertEqual((await pending).status_code,200)
        with patch('backend.app.routers.settings.upload_logo_file', side_effect=upload): asyncio.run(exercise())

    def test_R37_bad_runtime_json_returns_400_without_registration(self):
        from fastapi.testclient import TestClient
        from backend.app.main import app
        with TestClient(app,base_url='http://127.0.0.1') as client:
            for value in ('{','[]','null','1'):
                response=client.post('/api/models/register-path',data={'model_name':'m','model_path':'absent','runtime_json':value})
                self.assertEqual(response.status_code,400)
        self.assertFalse(core.MODELS_FILE.exists())

    def test_R27_startup_preloads_one_model_after_lifespan_yields(self):
        module = __import__('backend.app.main',fromlist=['lifespan'])
        entered, release = threading.Event(), threading.Event()
        loaded=[]
        def preload(model_id):
            loaded.append(model_id); entered.set(); release.wait(2)
        async def exercise():
            async with module.lifespan(module.app):
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait,1))
                    self.assertEqual(loaded,['preferred'])
                finally: release.set()
        with patch.object(module,'load_settings',return_value={'runtime':{'preload_on_start':True}}), \
             patch.object(module,'load_models',return_value=[{'id':'first','type':'LLM','file_exists':True}, {'id':'preferred','type':'LLM','file_exists':True,'status':'warm'}]), \
             patch.object(module,'prewarm_runtime',side_effect=preload), \
             patch.object(module,'unload_all_runtimes'):
            asyncio.run(exercise())

    def test_R26_diagnostics_always_describe_selected_worker(self):
        with patch.object(core,'get_external_worker_llama_diagnostics',return_value={'package_installed':True,'package_version':'worker-version','supports_gpu_offload':True}), \
             patch.object(core,'external_worker_python',return_value='selected-worker'), patch.object(core.shutil,'which',return_value=None):
            data=core.get_runtime_diagnostics()
        self.assertEqual(data['python'],'selected-worker')
        self.assertEqual(data['package_version'],'worker-version')
        self.assertTrue(data['supports_gpu_offload'])

    def test_C05_plain_backend_serves_assistant_spa(self):
        from fastapi.testclient import TestClient
        from backend.app.main import app
        with TestClient(app,base_url='http://127.0.0.1') as client:
            for route in ('/assistant','/assistant-overlay'):
                result=client.get(route)
                # A source checkout only serves the SPA after its frontend build.
                frontend=desktop.FRONTEND_DIST
                self.assertEqual(result.status_code,200 if (frontend/'index.html').is_file() else 404)
