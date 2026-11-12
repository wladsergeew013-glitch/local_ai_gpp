import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tools import exe_launcher as desktop

spec = importlib.util.spec_from_file_location('update_builder', Path(desktop.__file__).with_name('02_build_exe.py'))
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class UpdateSafetyTests(unittest.TestCase):
    def test_rebuild_preserves_user_store_including_when_source_is_destination(self):
        for same_source in (False, True):
            with self.subTest(same_source=same_source), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                app = root / 'backend/app'
                app.mkdir(parents=True)
                for name in ('core.py', 'llama_worker.py', 'main.py'):
                    (app/name).write_text('# code', encoding='utf-8')
                (root/'VERSION').write_text('1.4.1')
                source = root/'models_storage'
                source.mkdir()
                (source/'models.json').write_text('[]')
                (source/'settings.json').write_text('{}')
                store = root/'dist/models_storage'
                store.mkdir(parents=True)
                original = {'models.json': '[{"id":"personal-model"}]',
                            'settings.json': '{"server":{"api_key":"personal-setting"}}',
                            'private.gguf': 'model contents'}
                for name, content in original.items():
                    (store/name).write_text(content, encoding='utf-8')
                data_root = root/'dist' if same_source else root
                with patch.object(builder, 'ROOT', root), patch.object(builder, 'log'), \
                     patch.dict(builder.os.environ, {'LOCAL_AI_GPP_DATA_DIR': str(data_root)}):
                    builder.copy_portable_backend_and_metadata()
                for name, content in original.items():
                    self.assertEqual((store/name).read_text(encoding='utf-8'), content)
                self.assertTrue((root/'dist/backend/app/core.py').exists())

    def test_history_is_not_truncated_after_120_messages(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(desktop, 'assistant_state_dir', return_value=Path(folder)):
            messages = [{'id':f'm-{i}', 'role':'user' if i % 2 == 0 else 'assistant',
                         'text':f'Message {i}', 'phase':'done'} for i in range(122)]
            desktop.write_chat_state({'source':'new-conversation','activeConversationId':'long',
                'conversations':[{'id':'long','title':'Long','messages':messages}]})
            saved = desktop.read_chat_state()
            self.assertEqual(len(saved['conversations'][0]['messages']),122)
            self.assertEqual(saved['conversations'][0]['messages'][0]['id'],'m-0')
            desktop.append_shared_message('user','Next',conversation_id='long',message_id='next')
            self.assertEqual(len(desktop.read_chat_state()['conversations'][0]['messages']),123)

    def test_41st_dialog_remains_active_without_deleting_others(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(desktop, 'assistant_state_dir', return_value=Path(folder)):
            old = [{'id':f'c-{i}','title':f'Chat {i}','messages':[]} for i in range(40)]
            desktop._write_chat_state_raw(desktop._normalize_chat_state({
                'source':'new-conversation','activeConversationId':'c-0','conversations':old}))
            desktop.write_chat_state({'source':'new-conversation','activeConversationId':'new',
                'conversations':[{'id':'new','title':'New','messages':[]}]+old})
            state=desktop.read_chat_state()
            self.assertEqual(state['activeConversationId'],'new')
            self.assertEqual({item['id'] for item in state['conversations']},{'new',*(c['id'] for c in old)})


if __name__ == '__main__':
    unittest.main()
