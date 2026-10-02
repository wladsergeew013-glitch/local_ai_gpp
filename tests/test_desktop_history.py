"""Creation timestamps survive desktop stream updates and legacy history reads."""
import unittest
from unittest.mock import patch
from tools import exe_launcher as desktop


class DesktopHistoryTests(unittest.TestCase):
    def test_assistant_update_preserves_conversation_title_and_date(self):
        state = {'activeConversationId': 'dialog', 'conversations': [
            {'id': 'dialog', 'title': 'Question about CUDA', 'createdAt': '2026-10-02T17:00:00Z'}]}
        with patch.object(desktop, 'read_chat_state_unlocked', return_value=state), \
             patch.object(desktop, 'upsert_shared_chat_message') as upsert:
            desktop.append_shared_message('assistant', 'Done', conversation_id='dialog')
        payload = upsert.call_args.args[0]
        self.assertEqual(payload['conversationTitle'], 'Question about CUDA')
        self.assertEqual(payload['conversationCreatedAt'], '2026-10-02T17:00:00Z')

    def test_stream_update_preserves_creation_time(self):
        original = {'id': 'answer', 'role': 'assistant', 'text': 'Preparing',
                    'createdAt': '2026-10-02T17:00:00+00:00', 'syncUpdatedAt': 1000}
        completed = dict(original, text='Done', createdAt='2026-10-02T17:00:05+00:00', syncUpdatedAt=2000)
        merged = desktop._upsert_message_list([original], completed)[0]
        self.assertEqual(merged['text'], 'Done')
        self.assertEqual(merged['createdAt'], original['createdAt'])

    def test_legacy_time_uses_saved_stamp_and_unknown_remains_unknown(self):
        message = {'id': 'old', 'role': 'user', 'text': 'Hello', 'syncUpdatedAt': 1790962268321}
        first = desktop._normalize_chat_message(message, 0)
        second = desktop._normalize_chat_message(first, 0)
        self.assertEqual(first['createdAt'], second['createdAt'])
        self.assertEqual(first['createdAt'], '2026-10-02T17:31:08.321000+00:00')
        self.assertEqual(desktop.format_message_time({'text': 'Unknown'}), 'Время не сохранено')
        conversation = desktop._normalize_chat_conversation({'id': 'legacy', 'messages': [{'id': 'unknown', 'role': 'user', 'text': 'Old'}]}, 0)
        self.assertEqual(desktop.format_message_time(conversation['messages'][0]), 'Время не сохранено')
