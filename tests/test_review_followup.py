"""Concurrency, rendered errors and large saved-history regressions."""
import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import core
from tools import exe_launcher as desktop


class TextWidget:
    def __init__(self): self.text = ''
    def configure(self, **_): pass
    def insert(self, _position, text, *_tags): self.text += text
    def delete(self, *_): self.text = ''
    def index(self, *_): return '1.0'
    def yview(self, *_): return (0.0, 1.0)
    def see(self, *_): pass


def renderer():
    agent = desktop.NativeAssistantAgent.__new__(desktop.NativeAssistantAgent)
    agent.history_box = TextWidget()
    agent.title_label = None
    agent.reveal_message_id = ''
    agent.last_chat_sync_updated_at = 0
    agent.last_rendered_conversation_id = ''
    agent.awaiting_answer = False
    agent.pending_answer_id = ''
    agent.last_revealed_answer_id = ''
    agent.state = 'thinking'
    agent._refresh_dialog_list = lambda _state: None
    def set_state(state, *_): agent.state = state
    agent._set_state = set_state
    return agent


class FollowupTests(unittest.TestCase):
    def test_validation_cannot_restore_a_replaced_model_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); store = root / 'models_storage'; store.mkdir()
            with patch.multiple(core, MODELS_DIR=store, MODELS_FILE=store/'models.json',
                                LOGS_DIR=root/'logs', BRANDING_DIR=store/'branding'):
                original = core.copy_uploaded_model('Model', 'm.gguf', io.BytesIO(b'old'))
                entered, release, uploaded = threading.Event(), threading.Event(), threading.Event()
                errors, results = [], []
                validate = core.validate_model_record
                def paused(record):
                    result = validate(record)
                    if threading.current_thread().name == 'registry-validation':
                        entered.set()
                        if not release.wait(4): raise AssertionError('validation was not released')
                    return result
                def run_validation():
                    try: core.validate_model_registry(save=True)
                    except BaseException as error: errors.append(error)
                def upload():
                    try: results.append(core.copy_uploaded_model('Model', 'm.gguf', io.BytesIO(b'new')))
                    except BaseException as error: errors.append(error)
                    finally: uploaded.set()
                with patch.object(core, 'validate_model_record', side_effect=paused):
                    first = threading.Thread(target=run_validation, name='registry-validation')
                    second = threading.Thread(target=upload)
                    first.start()
                    try:
                        self.assertTrue(entered.wait(2))
                        second.start()
                        uploaded.wait(.3)
                    finally:
                        release.set(); first.join(4)
                        if second.ident is not None: second.join(4)
                self.assertFalse(first.is_alive() or second.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(len(results), 1)
                registered = core.load_models()[0]
                self.assertEqual(Path(registered['path']).resolve(), Path(results[0]['path']).resolve())
                self.assertEqual(Path(registered['path']).read_bytes(), b'new')
                self.assertNotEqual(registered['status'], 'missing')
                self.assertFalse(Path(original['path']).exists())

    def test_actual_renderer_shows_stream_and_legacy_length_errors(self):
        cases = [
            {'text': 'partial\n\nОшибка: EOF without done', 'answer': 'partial', 'phase': 'error'},
            {'text': 'partial', 'answer': 'partial', 'phase': 'error', 'error': 'Ответ не завершён'},
            {'text': 'partial', 'phase': 'done', 'finish_reason': 'length'},
        ]
        for message in cases:
            with self.subTest(message=message):
                agent = renderer()
                state = {'updatedAt': 1, 'activeConversationId': 'd', 'conversations': [
                    {'id': 'd', 'messages': [{'id': 'a', 'role': 'assistant', **message}]}]}
                with patch.object(desktop, 'read_chat_state', return_value=state):
                    agent._render_shared_history(force=True)
                self.assertIn('partial', agent.history_box.text)
                self.assertTrue('Ошибка:' in agent.history_box.text or 'не завершён' in agent.history_box.text)
                self.assertEqual(agent.state, 'error')

    def test_unchanged_reads_reuse_normalization_without_exposing_mutable_cache(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(desktop, 'assistant_state_dir', return_value=Path(folder)):
            payload = {'updatedAt': 1, 'activeConversationId': 'd', 'conversations': [
                {'id': 'd', 'messages': [{'id': 'u', 'role': 'user', 'text': 'original'}]}]}
            path = desktop.chat_sync_file(); path.write_text(json.dumps(payload), encoding='utf-8')
            with patch.object(desktop, '_normalize_chat_state', wraps=desktop._normalize_chat_state) as normalize:
                first = desktop.read_chat_state()
                first['conversations'][0]['messages'][0]['text'] = 'caller mutation'
                self.assertEqual(desktop.read_chat_state()['conversations'][0]['messages'][0]['text'], 'original')
                self.assertEqual(normalize.call_count, 1)
                payload['conversations'][0]['messages'][0]['text'] = 'external'
                temporary = path.with_suffix('.replacement')
                before = path.stat()
                temporary.write_text(json.dumps(payload), encoding='utf-8')
                os.utime(temporary, ns=(before.st_atime_ns, before.st_mtime_ns))
                temporary.replace(path)
                self.assertEqual(desktop.read_chat_state()['conversations'][0]['messages'][0]['text'], 'external')
                self.assertEqual(normalize.call_count, 2)
                desktop.reset_chat_state()
                self.assertEqual(desktop.read_chat_state()['conversations'][0]['messages'], [])

    def test_actual_sse_failure_remains_visible_in_the_actual_renderer(self):
        from test_stream_completion import DesktopStreamTests
        message = DesktopStreamTests().run_stream([{'type': 'delta', 'text': 'partial answer'}])
        agent = renderer()
        state = {'updatedAt': 1, 'activeConversationId': 'd', 'conversations': [
            {'id': 'd', 'messages': [message]}]}
        with patch.object(desktop, 'read_chat_state', return_value=state):
            agent._render_shared_history(force=True)
        self.assertIn('partial answer', agent.history_box.text)
        self.assertIn('done', agent.history_box.text)
        self.assertEqual(agent.state, 'error')

    def test_idle_context_hint_skips_history_but_reacts_to_typed_input(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(desktop, 'assistant_state_dir', return_value=Path(folder)):
            desktop.read_chat_state()
            agent = renderer(); agent.context_label = TextWidget()
            agent.runtime = {'n_ctx': 4096}; agent.max_tokens = 512
            value = ['']
            agent.input_var = type('Input', (), {'get': lambda _: value[0]})()
            agent._refresh_context_hint()
            with patch.object(desktop, 'read_chat_state', wraps=desktop.read_chat_state) as read:
                agent._refresh_context_hint(); read.assert_not_called()
                value[0] = 'new question'
                agent._refresh_context_hint(); self.assertEqual(read.call_count, 1)

    def test_idle_ui_poll_does_not_read_unchanged_history(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(desktop, 'assistant_state_dir', return_value=Path(folder)):
            desktop.read_chat_state()
            agent = renderer(); agent._render_shared_history(force=True)
            with patch.object(desktop, 'read_chat_state', wraps=desktop.read_chat_state) as read:
                agent._render_shared_history()
                read.assert_not_called()

    def test_normalization_and_merge_do_linear_id_lookups(self):
        lookups = []
        class Counted(dict):
            def get(self, key, default=None):
                if key == 'id': lookups.append(1)
                return super().get(key, default)
        normalize = desktop._normalize_chat_message
        def counted(*args):
            value = normalize(*args)
            return Counted(value) if value else value
        rows = [{'id': str(i), 'role': 'user', 'text': str(i)} for i in range(1000)]
        with patch.object(desktop, '_normalize_chat_message', side_effect=counted):
            result = desktop._normalize_chat_conversation({'id': 'd', 'messages': rows}, 0)
            merged = desktop._merge_message_lists(result['messages'], rows)
        self.assertEqual(len(merged), len(rows))
        self.assertLess(len(lookups), 30 * len(rows))

    def test_indexed_merge_preserves_order_timestamps_and_stale_update_rules(self):
        current = [{'id': 'a', 'role': 'assistant', 'text': 'new', 'createdAt': 'original', 'syncUpdatedAt': 20},
                   {'id': 'b', 'role': 'user', 'text': 'second'}]
        result = desktop._merge_message_lists(current, [
            {'id': 'a', 'role': 'assistant', 'text': 'stale', 'syncUpdatedAt': 10},
            {'id': 'a', 'role': 'assistant', 'text': 'latest', 'createdAt': 'changed', 'syncUpdatedAt': 30},
            {'id': 'c', 'role': 'user', 'text': 'third'}])
        self.assertEqual([m['id'] for m in result], ['a', 'b', 'c'])
        self.assertEqual(result[0]['text'], 'latest')
        self.assertEqual(result[0]['createdAt'], 'original')
        self.assertEqual(current[0]['text'], 'new')


if __name__ == '__main__': unittest.main()
