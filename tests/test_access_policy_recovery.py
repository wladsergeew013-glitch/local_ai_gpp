"""Malformed generation settings must never make the model API anonymous."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from backend.app import core
from backend.app.main import app


class AccessPolicyRecoveryTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.root=Path(folder.name); (self.root/'branding').mkdir()
        patches=patch.multiple(core,SETTINGS_FILE=self.root/'settings.json',MODELS_FILE=self.root/'models.json',
                               BRANDING_DIR=self.root/'branding',LOGS_DIR=self.root/'logs')
        patches.start(); self.addCleanup(patches.stop)
        self.client=TestClient(app,base_url='http://127.0.0.1')
        self.protected={'server':{'api_key':'synthetic-key','openai_compat_enabled':False}}

    def write(self,value):
        core.SETTINGS_FILE.write_text(json.dumps(value),encoding='utf-8')

    def statuses(self,headers=None):
        payload={'model':'m','messages':[{'role':'user','content':'hello'}]}
        return [self.client.get('/v1/models',headers=headers).status_code,
                self.client.post('/v1/chat/completions',headers=headers,json=payload).status_code,
                self.client.post('/v1/chat/completions',headers=headers,json={**payload,'stream':True}).status_code]

    def test_unrelated_corruption_preserves_key_and_disabled_api_on_every_path(self):
        for section in ({'runtime':{'temperature':999}}, {'runtime':None}, {'branding':None},
                        {'hub':{'timeout_sec':0}}, {'server':{**self.protected['server'],'port':'bad'}}):
            with self.subTest(section=list(section)):
                self.write({**self.protected,**section})
                original=core.SETTINGS_FILE.read_bytes()
                self.assertEqual(core.load_settings()['server']['api_key'],'synthetic-key')
                self.assertEqual(self.statuses(),[401]*3)
                for headers in ({'Authorization':'Bearer synthetic-key'},{'X-API-Key':'synthetic-key'}):
                    self.assertEqual(self.statuses(headers),[404]*3)
                self.assertEqual(core.SETTINGS_FILE.read_bytes(),original)

    def test_unknown_or_malformed_policy_is_unavailable_even_without_prior_valid_read(self):
        for contents in (b'[]',b'null',b'{',b'\xff', b'{"server":null}',
                         b'{"server":{"api_key":12}}',b'{"server":{"openai_compat_enabled":"false"}}'):
            with self.subTest(contents=contents):
                core.SETTINGS_FILE.write_bytes(contents)
                self.assertEqual(self.statuses(),[503]*3)
                self.assertEqual(self.client.get('/api/health').status_code,200)
                self.assertEqual(self.client.get('/api/bootstrap').status_code,200)
                self.assertEqual(core.SETTINGS_FILE.read_bytes(),contents)
                with self.assertRaises(HTTPException): core.save_settings({'runtime':{'temperature':.4}})
                self.assertEqual(core.SETTINGS_FILE.read_bytes(),contents)

    def test_partial_repair_does_not_persist_widened_policy(self):
        self.write({**self.protected,'runtime':{'temperature':999}})
        core.save_settings({'runtime':{'temperature':.4}})
        self.assertEqual(self.statuses(),[401]*3)
        self.assertEqual(self.statuses({'Authorization':'Bearer synthetic-key'}),[404]*3)
        self.assertTrue(list(self.root.glob('settings.json.invalid-*')))

    def test_valid_partial_and_first_run_defaults_keep_working(self):
        with patch('backend.app.routers.compat.load_models',return_value=[]):
            self.write({}); self.assertEqual(self.client.get('/v1/models').status_code,200)
            self.write({'server':{'api_key':'synthetic-key'}})
            self.assertEqual(self.client.get('/v1/models').status_code,401)
            self.assertEqual(self.client.get('/v1/models',headers={'X-API-Key':'synthetic-key'}).status_code,200)
