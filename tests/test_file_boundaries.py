import asyncio
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.staticfiles import StaticFiles
from backend.app import core
from backend.app import ui_resources as desktop


class FileBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.store=self.root/'models_storage'; self.store.mkdir()
        self.brand=self.store/'branding'; self.brand.mkdir()
        for name,value in {'MODELS_DIR':self.store,'MODELS_FILE':self.store/'models.json',
                           'BRANDING_DIR':self.brand,'SETTINGS_FILE':self.store/'settings.json',
                           'LOGS_DIR':self.root/'logs'}.items():
            item=patch.object(core,name,value);item.start();self.addCleanup(item.stop)
        (self.root/'logs').mkdir()
        (self.store/'models.json').write_text('[]')

    def test_upload_rejects_dot_names_and_filename_escape_forms(self):
        for name in ('.','..','...','.. ',' ... '):
            with self.subTest(name=name):
                with self.assertRaises(HTTPException) as error:
                    core.copy_uploaded_model(name,'outside.gguf',io.BytesIO(b'payload'))
                self.assertEqual(error.exception.status_code,400)
        for filename in ('../escape.gguf', '..\\escape.gguf', '/absolute.gguf',
                         'C:\\escape.gguf', 'C:escape.gguf', '\\escape.gguf',
                         'model.gguf:stream', 'CON', 'NUL.gguf', 'file.gguf.', 'file.gguf ', 'bad\x00.gguf'):
            with self.subTest(filename=filename):
                with self.assertRaises(HTTPException) as error:
                    core.copy_uploaded_model('Model',filename,io.BytesIO(b'payload'))
                self.assertEqual(error.exception.status_code,400)
        self.assertFalse((self.root/'outside.gguf').exists())
        self.assertEqual((self.store/'models.json').read_text(),'[]')

    def test_normal_and_branding_named_models_cannot_overwrite_branding(self):
        logo=self.brand/'logo.svg';logo.write_bytes(b'personal branding')
        first=core.copy_uploaded_model('Проект AI','model.gguf',io.BytesIO(b'GGUF original'))
        self.assertEqual(Path(first['path']).read_bytes(),b'GGUF original')
        for name in ('branding','BRANDING','branding.'):
            result=core.copy_uploaded_model(name,'logo.svg',io.BytesIO(b'model data'))
            self.assertNotEqual(Path(result['path']).resolve(),logo.resolve())
        self.assertEqual(logo.read_bytes(),b'personal branding')

    def test_replacement_never_truncates_file_mapped_by_another_alias(self):
        first=core.copy_uploaded_model('Model','file.gguf',io.BytesIO(b'old mapped bytes'))
        core.upsert_model(first)
        alias=core.register_model_path(name='Alias',model_type='LLM',model_path=first['path'])
        closed=[]
        class Worker:
            def close(self): closed.append(True)
        with patch.dict(core.RUNTIMES,{first['id']:{'runtime':Worker()}},clear=True):
            second=core.copy_uploaded_model('Model','file.gguf',io.BytesIO(b'new model'))
        self.assertNotEqual(first['path'],second['path'])
        self.assertEqual(Path(first['path']).read_bytes(),b'old mapped bytes')
        self.assertEqual(Path(second['path']).read_bytes(),b'new model')
        self.assertTrue(closed)
        core.delete_model(alias['id'])
        self.assertFalse(Path(first['path']).exists())
        self.assertTrue(Path(second['path']).exists())

    def test_ordinary_replacements_retire_only_unreferenced_managed_blobs(self):
        first=core.copy_uploaded_model('Model','file.gguf',io.BytesIO(b'old'))
        second=core.copy_uploaded_model('Model','file.gguf',io.BytesIO(b'new'))
        self.assertFalse(Path(first['path']).exists())
        self.assertEqual(len(list((self.store/'uploads').glob('*/*.gguf'))),1)
        core.delete_model(second['id'])
        self.assertEqual(list((self.store/'uploads').iterdir()),[])
        external=self.root/'external.gguf';external.write_bytes(b'external')
        row=core.register_model_path(name='External',model_type='LLM',model_path=str(external))
        core.delete_model(row['id'])
        self.assertEqual(external.read_bytes(),b'external')

    def test_busy_replacement_returns_409_without_changing_old_file_or_registry(self):
        first=core.copy_uploaded_model('Model','file.gguf',io.BytesIO(b'old'))
        core.upsert_model(first)
        core.RUNTIME_LOCK.acquire()
        try:
            with self.assertRaises(HTTPException) as error:
                core.copy_uploaded_model('Model','file.gguf',io.BytesIO(b'new'))
            self.assertEqual(error.exception.status_code,409)
        finally:core.RUNTIME_LOCK.release()
        self.assertEqual(Path(first['path']).read_bytes(),b'old')
        self.assertEqual(Path(core.load_models()[0]['path']).resolve(),Path(first['path']).resolve())

    def test_external_read_only_registration_is_supported(self):
        external=self.root/'external.gguf';external.write_bytes(b'GGUF')
        row=core.register_model_path(name='External',model_type='LLM',model_path=str(external))
        self.assertEqual(Path(row['path']),external)

    def test_prewarm_keeps_concurrently_registered_model(self):
        a=self.root/'a.gguf';a.write_bytes(b'GGUF')
        b=self.root/'b.gguf';b.write_bytes(b'GGUF')
        first=core.register_model_path(name='A',model_type='LLM',model_path=str(a))
        def warm(*args,**kwargs):
            core.register_model_path(name='B',model_type='LLM',model_path=str(b))
        with patch.object(core,'_get_runtime',side_effect=warm):
            core.prewarm_runtime(first['id'])
        self.assertEqual({row['name'] for row in core.load_models()},{'A','B'})

    def test_spa_rejects_traversal_and_preserves_static_and_client_routes(self):
        front=self.root/'front';front.mkdir()
        (front/'index.html').write_text('SPA')
        (front/'app.js').write_text('bundle')
        (self.root/'secret.txt').write_text('SECRET')
        app=FastAPI();app.add_api_route('/{full_path:path}',desktop.frontend_spa)
        with patch.object(desktop,'FRONTEND_DIST',front),TestClient(app) as client:
            for path in ('/%2e%2e/secret.txt','/..%5csecret.txt','/C:%5csecret.txt','/api/missing'):
                with self.subTest(path=path):self.assertEqual(client.get(path).status_code,404)
            self.assertEqual(client.get('/app.js').text,'bundle')
            self.assertEqual(client.get('/chat/saved').text,'SPA')

    def test_junction_or_symlink_cannot_escape_upload_or_frontend_roots(self):
        outside=self.root/'outside';outside.mkdir();(outside/'secret.txt').write_text('SECRET')
        def link(path):
            if os.name=='nt':
                source=str(path).replace("'","''");target=str(outside).replace("'","''")
                subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
                    f"New-Item -ItemType Junction -Path '{source}' -Target '{target}' | Out-Null"],
                    check=True,capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
                self.addCleanup(path.rmdir)
            else:
                path.symlink_to(outside,target_is_directory=True);self.addCleanup(path.unlink)
        link(self.store/'uploads')
        with self.assertRaises(HTTPException) as error:
            core.copy_uploaded_model('Safe','model.gguf',io.BytesIO(b'payload'))
        self.assertEqual(error.exception.status_code,400)
        self.assertEqual({p.name for p in outside.iterdir()},{'secret.txt'})
        front=self.root/'front';front.mkdir();(front/'index.html').write_text('SPA')
        link(front/'linked')
        app=FastAPI();app.add_api_route('/{full_path:path}',desktop.frontend_spa)
        with patch.object(desktop,'FRONTEND_DIST',front),TestClient(app) as client:
            self.assertEqual(client.get('/linked/secret.txt').status_code,404)

    def test_only_branding_is_exposed_as_assets(self):
        from backend.app.main import app
        mounts=[route for route in app.routes if isinstance(getattr(route,'app',None),StaticFiles)]
        self.assertFalse(any(route.path=='/assets' for route in mounts))
        mount=next(route for route in mounts if route.path=='/assets/branding')
        (self.brand/'logo.svg').write_bytes(b'logo')
        (self.store/'settings.json').write_text('secret settings')
        isolated=FastAPI();isolated.mount('/assets/branding',StaticFiles(directory=self.brand))
        with TestClient(isolated) as client:
            self.assertEqual(client.get('/assets/settings.json').status_code,404)
            self.assertEqual(client.get('/assets/models.json').status_code,404)
            self.assertEqual(client.get('/assets/branding/logo.svg').content,b'logo')
        self.assertTrue(str(mount.app.directory).endswith('branding'))

    def test_upload_namespace_cannot_alias_public_branding(self):
        link=self.store/'uploads'
        if os.name=='nt':
            source=str(link).replace("'","''");target=str(self.brand).replace("'","''")
            subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
                f"New-Item -ItemType Junction -Path '{source}' -Target '{target}' | Out-Null"],
                check=True,capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
            self.addCleanup(link.rmdir)
        else:
            link.symlink_to(self.brand,target_is_directory=True);self.addCleanup(link.unlink)
        with self.assertRaises(HTTPException) as error:
            core.copy_uploaded_model('M','private.gguf',io.BytesIO(b'private'))
        self.assertEqual(error.exception.status_code,400)
        self.assertEqual(list(self.brand.iterdir()),[])

    def test_hub_uses_the_same_validated_boundary_and_query_safe_filenames(self):
        result={}
        class Response:
            content=b'GGUF download'
            def json(self):return result
            def raise_for_status(self):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*_):pass
            async def aiter_bytes(self,*_):yield self.content
        class Client:
            def __init__(self,*args,**kwargs):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*_):pass
            async def post(self,*args,**kwargs):return Response()
            def stream(self,*args,**kwargs):return Response()
        settings={'hub':{'base_url':'http://fixture.invalid','enabled':True}}
        with patch.object(core.httpx,'AsyncClient',Client):
            for filename in (str(self.root/'escape.gguf'),'../escape.gguf','..\\escape.gguf','C:\\escape.gguf','file.gguf:stream','NUL.gguf'):
                result.update(file_url='http://fixture.invalid/model.gguf',filename=filename)
                with self.subTest(filename=filename),self.assertRaises(HTTPException):
                    asyncio.run(core.import_from_hub({'name':'Hub'},settings))
            self.assertFalse((self.root/'escape.gguf').exists())
            result.clear();result.update(file_url='http://fixture.invalid/model%20file.gguf?token=fixture')
            row=asyncio.run(core.import_from_hub({'name':'Hub'},settings))
            self.assertEqual(row['filename'],'model file.gguf')
            self.assertEqual(Path(row['path']).read_bytes(),b'GGUF download')

    def test_failed_or_oversized_upload_leaves_no_partial_model(self):
        class Broken:
            count=0
            def read(self,*_):
                self.count+=1
                if self.count==1:return b'partial'
                raise OSError('interrupted')
        with self.assertRaises(OSError):
            core.copy_uploaded_model('M','file.gguf',Broken())
        with patch.object(core,'MAX_MODEL_UPLOAD_BYTES',4),self.assertRaises(HTTPException) as error:
            core.copy_uploaded_model('M','file.gguf',io.BytesIO(b'oversized'))
        self.assertEqual(error.exception.status_code,413)
        self.assertEqual(list((self.store/'uploads').iterdir()),[])
        self.assertEqual(core.load_models(),[])


if __name__=='__main__':unittest.main()
