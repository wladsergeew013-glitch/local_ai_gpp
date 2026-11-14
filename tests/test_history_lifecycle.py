import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import diagnostic_chat as desktop
from backend.app.instance_lock import InstanceAlreadyRunning, InstanceLock


class HistoryLifecycleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        patched = patch.object(desktop, 'assistant_state_dir', return_value=self.root)
        patched.start(); self.addCleanup(patched.stop)
        self.state = desktop._write_chat_state_raw(desktop._normalize_chat_state({'source': 'new-conversation', 'activeConversationId': 'a', 'conversations': [
            {'id': 'a', 'title': 'Named dialog', 'messages': [{'id': 'u', 'role': 'user', 'text': 'Keep this'}]},
            {'id': 'b', 'title': 'Other dialog', 'messages': []}]}))

    def test_R01_cache_reader_and_writer_keep_both_dialogs(self):
        desktop.read_chat_state()
        entered, release = threading.Event(), threading.Event()
        deepcopy = copy.deepcopy; failures = []
        def paused(value):
            if threading.current_thread().name == 'cache-reader':
                entered.set(); self.assertTrue(release.wait(2))
            return deepcopy(value)
        def read():
            try: desktop.read_chat_state_unlocked()
            except Exception as error: failures.append(error)
        def write():
            try: desktop.append_shared_message('user', 'new', conversation_id='a')
            except Exception as error: failures.append(error)
        with patch.object(desktop.copy, 'deepcopy', side_effect=paused):
            reader = threading.Thread(target=read, name='cache-reader'); reader.start()
            self.assertTrue(entered.wait(2))
            writer = threading.Thread(target=write); writer.start()
            release.set(); reader.join(3); writer.join(3)
        self.assertEqual(failures, [])
        self.assertFalse(reader.is_alive() or writer.is_alive())
        self.assertEqual({c['id'] for c in desktop.read_chat_state()['conversations']}, {'a', 'b'})
        self.assertEqual(list(self.root.glob('*.corrupt-*')), [])

    def test_R01_program_error_does_not_quarantine_valid_json(self):
        original = desktop.chat_sync_file().read_bytes()
        with patch.object(desktop, '_chat_state_cache', None), patch.object(desktop, '_normalize_chat_state', side_effect=TypeError('program bug')):
            with self.assertRaises(TypeError): desktop.read_chat_state()
            with self.assertRaises(TypeError): desktop.read_chat_state_unlocked()
        self.assertEqual(desktop.chat_sync_file().read_bytes(), original)
        self.assertEqual(list(self.root.glob('*.corrupt-*')), [])

    def test_R03_clear_and_reset_fence_stale_snapshots_and_workers(self):
        stale = desktop.append_shared_message('assistant', 'Pending', message_id='job', conversation_id='a',
            pending=True, generationId='generation', _begin_generation=True)
        cleared = desktop.clear_chat_conversation('a')
        self.assertGreater(cleared['version'], stale['version'])
        desktop.write_chat_state({**stale, 'source': 'main-ui'})
        with patch.object(desktop._chat_generation_context, 'value', ('job', 'generation'), create=True):
            desktop.append_shared_message('assistant', 'late answer', message_id='job', conversation_id='a')
        self.assertEqual(next(c for c in desktop.read_chat_state()['conversations'] if c['id'] == 'a')['messages'], [])
        reset = desktop.reset_chat_state()
        desktop.write_chat_state(stale)
        self.assertEqual(desktop.read_chat_state()['conversations'], reset['conversations'])

    def test_R07_read_write_and_thread_start_failures_release_generation(self):
        for target in ['read_chat_state_unlocked', 'append_shared_message', 'threading.Thread.start']:
            with self.subTest(target=target):
                if target.startswith('threading.'):
                    failing = patch.object(desktop.threading.Thread, 'start', side_effect=OSError('start failed'))
                else: failing = patch.object(desktop, target, side_effect=OSError('I/O failed'))
                with patch.object(desktop, '_select_desktop_chat_config', return_value={}), failing:
                    result = desktop.submit_shared_chat_message({'message': 'hello', 'conversationId': 'a'})
                self.assertFalse(desktop.generation_lock.locked())
                self.assertNotEqual(result.status_code, 200)

    def test_R10_send_keeps_existing_title_when_native_payload_omits_it(self):
        with patch.object(desktop, '_select_desktop_chat_config', return_value={}), patch.object(desktop.threading.Thread, 'start'):
            try:
                desktop.submit_shared_chat_message({'message': 'hello', 'conversationId': 'a'})
                self.assertEqual(next(c for c in desktop.read_chat_state()['conversations'] if c['id'] == 'a')['title'], 'Named dialog')
            finally:
                if desktop.generation_lock.locked(): desktop.generation_lock.release()

    def test_R09_instance_lock_rejects_a_second_owner(self):
        path = self.root / 'instance.lock'
        with InstanceLock(path):
            with self.assertRaises(InstanceAlreadyRunning):
                with InstanceLock(path): pass
        with InstanceLock(path): pass
