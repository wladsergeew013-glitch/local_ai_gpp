import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from backend.app import core
from tools import exe_launcher as desktop
from backend.app import diagnostic_chat as shared_chat

spec=importlib.util.spec_from_file_location('m0_builder',Path(desktop.__file__).with_name('02_build_exe.py'))
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)


class RuntimeReviewTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.root=Path(folder.name);self.store=self.root/'models_storage';self.brand=self.store/'branding';self.brand.mkdir(parents=True)
        p=patch.multiple(core,PROJECT_ROOT=self.root,MODELS_DIR=self.store,MODELS_FILE=self.store/'models.json',SETTINGS_FILE=self.store/'settings.json',BRANDING_DIR=self.brand,RUNTIMES={})
        p.start();self.addCleanup(p.stop)

    def candidate(self,root):
        for name,contents in {'LocalAIGPP.exe':'exe','VERSION':'1.5.0','runtime_info.json':'{}','worker_runtime/python.exe':'python',
          'backend/app/llama_worker.py':'worker','models_storage/settings.json':'{"runtime":{"temperature":0.3}}',
          'models_storage/models.json':'[{"id":"m","path":"models_storage/m.gguf"}]','models_storage/m.gguf':'weights'}.items():
            file=root/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text(contents)

    def test_F01_first_build_keeps_prepared_models_and_settings(self):
        candidate=self.root/'out/candidate';self.candidate(candidate)
        (self.store/'source.gguf').write_bytes(b'source survives')
        with patch.multiple(builder,ROOT=self.root,OUT=self.root/'out'),patch.object(builder,'log'):
            builder.promote_distribution(candidate)
        self.assertEqual((self.root/'dist/models_storage/m.gguf').read_text(),'weights')
        self.assertEqual(json.loads((self.root/'dist/models_storage/settings.json').read_text())['runtime']['temperature'],.3)
        self.assertEqual((self.store/'source.gguf').read_bytes(),b'source survives')

    def test_F01_incomplete_candidate_does_not_replace_installation(self):
        candidate=self.root/'out/candidate';candidate.mkdir(parents=True)
        old=self.root/'dist/LocalAIGPP.exe';old.parent.mkdir();old.write_bytes(b'old')
        with patch.multiple(builder,ROOT=self.root,OUT=self.root/'out'):
            with self.assertRaises(ValueError):builder.promote_distribution(candidate)
        self.assertEqual(old.read_bytes(),b'old')

    def test_F02_retirement_failure_cannot_delete_new_committed_blob(self):
        old=core.copy_uploaded_model('m','m.gguf',io.BytesIO(b'old'))
        with patch.object(core,'_retire_managed_upload',side_effect=HTTPException(503,'registry read failure during GC')):
            new=core.copy_uploaded_model('m','m.gguf',io.BytesIO(b'new'))
        self.assertEqual(Path(core.load_models()[0]['path']).read_bytes(),b'new')
        self.assertEqual(Path(new['path']).read_bytes(),b'new')
        self.assertTrue(Path(old['path']).is_file())

    def test_F04_diagnostic_chat_uses_external_data_root(self):
        record=core.copy_uploaded_model('external','m.gguf',io.BytesIO(b'weights'))
        self.assertEqual(desktop._select_desktop_chat_config({'model_id':record['id']})['model_id'],record['id'])
        self.assertEqual(desktop._resolve_desktop_model_path('models_storage\\uploads\\'+Path(record['path']).parent.name+'\\m.gguf').resolve(),Path(record['path']).resolve())

    def test_F07_explicit_missing_model_is_rejected_without_fallback(self):
        core.copy_uploaded_model('available','m.gguf',io.BytesIO(b'weights'))
        with self.assertRaises(HTTPException) as error:desktop._select_desktop_chat_config({'model_id':'missing'})
        self.assertEqual(error.exception.status_code,404)
        self.assertTrue(desktop._select_desktop_chat_config({})['model_id'].startswith('available:'))

    def test_F06_revision_conflict_preserves_other_client_settings_and_key(self):
        first=core.save_settings({'server':{'api_key':'synthetic-secret'}})
        second=core.save_settings({'runtime':{'seed':17}},expected_revision=first['_revision'])
        with self.assertRaises(HTTPException) as error:core.save_settings({'runtime':{'temperature':.4}},expected_revision=first['_revision'])
        self.assertEqual(error.exception.status_code,409)
        final=core.save_settings({'runtime':{'temperature':.4}},expected_revision=second['_revision'])
        self.assertEqual(final['runtime']['seed'],17);self.assertEqual(final['server']['api_key'],'synthetic-secret')
        self.assertNotIn('api_key',core.public_settings()['server'])

    def test_F08_invalid_model_and_request_runtime_never_allocate_worker(self):
        file=self.root/'m.gguf';file.write_bytes(b'gguf')
        for runtime in ({'temperature':999},{'n_gpu_layers':-2},{'enable_thinking':'false'},{'n_ctx':0}):
            with self.subTest(runtime=runtime):
                with self.assertRaises(HTTPException):core.register_model_path(name='m',model_type='LLM',model_path=str(file),runtime=runtime)
                with patch('backend.app.worker_client.WorkerClient') as worker:
                    with self.assertRaises(HTTPException):core._get_runtime({'id':'m','type':'LLM','path':str(file)},core.DEFAULT_SETTINGS,runtime_override=runtime)
                worker.assert_not_called()

    def test_F03_backend_lifespan_recovers_pending_without_desktop(self):
        from fastapi.testclient import TestClient
        from backend.app.main import app
        with patch.object(shared_chat,'assistant_state_dir',return_value=self.root):
            state=shared_chat.write_chat_state({'source':'new-conversation','activeConversationId':'d','conversations':[
                {'id':'d','messages':[{'id':'pending','role':'assistant','text':'partial','pending':True,'generationOwner':'previous-process'}]}]})
            with TestClient(app,base_url='http://127.0.0.1') as client:
                state=client.get('/api/desktop/chat-sync').json()
                message=next(m for c in state['conversations'] for m in c['messages'] if m['id']=='pending')
                self.assertFalse(message['pending']);self.assertEqual(message['phase'],'error');self.assertEqual(message['answer'],'partial')
                self.assertNotIn('tools.exe_launcher',__import__('backend.app.main',fromlist=['app']).__dict__)

    def test_F03_nested_desktop_and_server_lease_does_not_allow_a_second_owner(self):
        from backend.app.instance_lock import owned_instance, InstanceLock, InstanceAlreadyRunning
        path=self.root/'owner.lock'
        with owned_instance(path),owned_instance(path):
            with self.assertRaises(InstanceAlreadyRunning):
                with InstanceLock(path):pass
        with InstanceLock(path):pass
