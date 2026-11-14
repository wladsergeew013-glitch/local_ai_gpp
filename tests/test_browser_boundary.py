import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from tools import exe_launcher as desktop


class BrowserBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        p = patch.object(desktop, 'assistant_state_dir', return_value=self.root); p.start(); self.addCleanup(p.stop)
        self.state = desktop.write_chat_state({'source': 'new-conversation', 'conversations': [
            {'id': 'safe', 'messages': [{'id': 'u', 'role': 'user', 'text': 'Preserve history'}]}]})
        self.client = TestClient(desktop.app, base_url='http://127.0.0.1:8765')

    def test_R53_foreign_forms_text_json_null_and_duplicate_origins_never_mutate(self):
        original = desktop.chat_sync_file().read_bytes()
        for path, method in [('/api/desktop/chat-reset', 'POST'), ('/api/desktop/chat-reset/', 'POST'),
                             ('/api/desktop/chat-sync', 'POST'), ('/api/desktop/chat-sync', 'PUT'),
                             ('/api/desktop/chat-sync', 'DELETE'), ('/api/desktop/chat-clear', 'POST')]:
            for value in ['https://foreign.invalid', 'null', 'http://127.0.0.1:8766']:
                with self.subTest(path=path, method=method, origin=value):
                    response = self.client.request(method, path, content='{"source":"reset"}',
                        headers={'Origin': value, 'Content-Type': 'text/plain'})
                    self.assertEqual(response.status_code, 403)
        response = self.client.post('/api/desktop/chat-reset', headers=[('Origin', 'http://127.0.0.1:8765'), ('Origin', 'https://foreign.invalid')])
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post('/api/desktop/chat-reset', data={'x': 'reset'}, headers={'Origin': 'https://foreign.invalid'}).status_code, 403)
        self.assertEqual(desktop.chat_sync_file().read_bytes(), original)

    def test_R53_bad_host_dns_rebinding_and_forwarded_headers_do_not_establish_trust(self):
        for host in ['127.0.0.1:8765/?x=', '127.0.0.1:8765#x', 'foreign.invalid:8765', '127.0.0.1:8765@foreign.invalid']:
            with self.subTest(host=host):
                result = self.client.post('/api/desktop/chat-reset', headers={'Host': host, 'Origin': 'http://' + host})
                self.assertEqual(result.status_code, 400)
        result = self.client.post('/api/desktop/chat-reset', headers={'Origin': 'https://foreign.invalid',
            'X-Forwarded-Host': 'foreign.invalid', 'X-Forwarded-Proto': 'https'})
        self.assertEqual(result.status_code, 403)

    def test_R53_originless_and_same_origin_clients_and_vite_keep_working(self):
        for origin in [None, 'http://127.0.0.1:8765', 'http://127.0.0.1:5173']:
            with self.subTest(origin=origin):
                headers = {'Origin': origin} if origin else {}
                result = self.client.post('/api/desktop/chat-sync', json=self.state, headers=headers)
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.headers['X-Local-AI-GPP-Sync'], desktop.LAUNCHER_VERSION_MARKER)
                if origin == 'http://127.0.0.1:5173':
                    self.assertEqual(result.headers['access-control-allow-origin'], origin)
                    self.assertIn('X-Local-AI-GPP-Sync', result.headers['access-control-expose-headers'])
