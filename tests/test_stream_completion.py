import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend.app import diagnostic_chat as desktop


class Response:
    def __init__(self, events):
        self.chunks = [f"data: {json.dumps(event)}\n\n".encode() for event in events]
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read1(self, _):
        return self.chunks.pop(0) if self.chunks else b''


class DesktopStreamTests(unittest.TestCase):
    def run_stream(self, events):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(desktop,'assistant_state_dir',return_value=Path(folder)), \
             patch.object(desktop,'server_port',8765), \
             patch.object(desktop,'_select_desktop_chat_config',return_value={
                 'model_id':'m','temperature':0,'max_tokens':64,'runtime':{}}), \
             patch.object(desktop.urllib.request,'urlopen',return_value=Response(events)):
            desktop.write_chat_state({'source':'new-conversation','activeConversationId':'dialog',
                'conversations':[{'id':'dialog','title':'Test','messages':[]}]})
            desktop._shared_chat_generation_worker({'message':'Question'},'answer','dialog',lock_already_acquired=False)
            return desktop.read_chat_state()['conversations'][0]['messages'][-1]

    def test_eof_without_done_preserves_partial_but_is_not_completed(self):
        message=self.run_stream([{'type':'delta','text':'partial answer'}])
        self.assertEqual(message['phase'],'error')
        self.assertFalse(message['pending'])
        self.assertIn('partial answer',message['text'])
        self.assertIn('done',message['error'])
        self.assertEqual(message['answer'], message['text'])

    def test_incomplete_reasoning_is_not_saved_as_final_answer(self):
        message=self.run_stream([{'type':'delta','text':'private thoughts'},
            {'type':'done','answer':'','content':'private thoughts','reasoning':'private thoughts',
             'answer_state':'missing_after_reasoning','reasoning_truncated':True,'finish_reason':'length'}])
        self.assertEqual(message['phase'],'error')
        self.assertNotIn('private thoughts',message['text'])
        self.assertEqual(message['reasoning'],'private thoughts')

    def test_terminal_event_saves_only_final_answer(self):
        message=self.run_stream([{'type':'delta','text':'raw'},
            {'type':'done','answer':'Four','content':'raw','finish_reason':'stop'}])
        self.assertEqual(message['phase'],'done')
        self.assertEqual(message['answer'],'Four')


if __name__=='__main__':unittest.main()
