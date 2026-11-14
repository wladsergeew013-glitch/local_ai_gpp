import json
import unittest
from unittest.mock import Mock, patch
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from backend.app.main import app
from backend.app import core
from backend.app.compat_schema import OpenAIChatRequest
from backend.app.runtime_manager import runtime_manager
from backend.app.tool_calls import decode_qwen_tools, merge_tool_delta

TOOLS = [{'type':'function','function':{'name':'read_file','parameters':{'type':'object','properties':{'path':{'type':'string'}},'required':['path'],'additionalProperties':False}}}]
CALL = {'id':'call_known','type':'function','function':{'name':'read_file','arguments':'{"path":"app.py"}'}}


class ToolContractTests(unittest.TestCase):
    def tearDown(self):
        if runtime_manager.active:
            runtime_manager.active.close()

    def test_json_and_real_qwen35_xml_decode_with_ids_and_typed_arguments(self):
        for raw in ('<tool_call>{"name":"read_file","arguments":{"path":"app.py"}}</tool_call>',
                    '<tool_call>\n<function=read_file>\n<parameter=path>\napp.py\n</parameter>\n</function>\n</tool_call>'):
            content,calls=decode_qwen_tools(raw,TOOLS)
            self.assertIsNone(content)
            self.assertEqual(json.loads(calls[0]['function']['arguments']),{'path':'app.py'})
            self.assertTrue(calls[0]['id'].startswith('call_'))
        raw='<tool_call><function=read_file><parameter=path>\nx < y & z\n</parameter></function></tool_call>'
        self.assertEqual(json.loads(decode_qwen_tools(raw,TOOLS)[1][0]['function']['arguments'])['path'],'x < y & z')

    def test_incomplete_unknown_duplicate_or_truncated_tools_never_emit_action(self):
        for raw in ('<tool_call>{"name":"read_file"}',
                    '<tool_call>{"name":"delete_all","arguments":{}}</tool_call>',
                    '<tool_call><function=read_file><parameter=path>a</parameter><parameter=path>b</parameter></function></tool_call>',
                    '<tool_call><function=read_file></function></tool_call>'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):decode_qwen_tools(raw,TOOLS)
        with self.assertRaises(ValueError):decode_qwen_tools('<tool_call>{"name":"read_file","arguments":{}}</tool_call>',TOOLS,finish='length')
        self.assertEqual(decode_qwen_tools('<tool_call>example only</tool_call>',[]),('<tool_call>example only</tool_call>',[]))

    def test_transcript_rejects_orphans_duplicates_and_null_user_but_keeps_valid_parallel_cycle(self):
        valid=[{'role':'user','content':'Read'}, {'role':'assistant','content':None,'tool_calls':[CALL]},
               {'role':'tool','content':'file contents','tool_call_id':'call_known'}]
        request=OpenAIChatRequest(model='m',messages=valid,tools=TOOLS)
        self.assertEqual(request.messages[1].tool_calls[0].id,'call_known')
        for invalid in (valid[:-1],valid+valid[-1:],[valid[2]],[{'role':'user','content':None}]):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):OpenAIChatRequest(model='m',messages=invalid,tools=TOOLS)
        with self.assertRaises(ValidationError):OpenAIChatRequest(model='m',messages=valid,tools=TOOLS,memory=False)

    def test_stream_delta_assembler_preserves_fragmented_parallel_call_ids_and_json(self):
        calls={}
        merge_tool_delta(calls,{'tool_calls':[{'index':1,'id':'call_b','function':{'name':'read_','arguments':'{"path":'}},
                                             {'index':0,'id':'call_a','function':{'name':'read_file','arguments':'{}'}}]})
        merge_tool_delta(calls,{'tool_calls':[{'index':1,'function':{'name':'file','arguments':'"x"}'}}]})
        self.assertEqual(calls[1]['id'],'call_b');self.assertEqual(calls[1]['function'],{'name':'read_file','arguments':'{"path":"x"}'})

    def test_http_tool_calls_round_trip_and_stream_end_reason(self):
        result={'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[CALL]},'finish_reason':'tool_calls'}],'usage':{}}
        with patch('backend.app.routers.compat.load_settings',return_value=core.DEFAULT_SETTINGS),\
             patch('backend.app.routers.compat.find_model'),patch('backend.app.routers.compat.create_chat_completion',return_value=result) as complete,\
             TestClient(app,base_url='http://127.0.0.1') as client:
            response=client.post('/v1/chat/completions',json={'model':'m','messages':[{'role':'user','content':'Read'}],'tools':TOOLS})
            self.assertEqual(response.status_code,200,response.text)
            message=response.json()['choices'][0]['message'];self.assertEqual(message['tool_calls'][0]['id'],'call_known')
            self.assertIsNone(message['content']);self.assertEqual(complete.call_args.kwargs['tools'],[OpenAIChatRequest(model='m',messages=[{'role':'user','content':'Read'}],tools=TOOLS).tools[0].model_dump()])
            events=(event for event in [{'type':'tool_delta','tool_calls':[{'index':0,**CALL}]},{'type':'done','content':'','finish_reason':'tool_calls'}])
            with patch('backend.app.routers.compat.stream_chat_completion',return_value=events):
                response=client.post('/v1/chat/completions',json={'model':'m','messages':[{'role':'user','content':'Read'}],'tools':TOOLS,'stream':True})
                self.assertIn('"finish_reason": "tool_calls"',response.text);self.assertIn('call_known',response.text)

    def test_busy_is_http_409_before_sse_and_cancel_only_matches_owner(self):
        worker=Mock(); lease=runtime_manager.admit('owner-a','secret-a');lease.attach(worker)
        self.assertEqual(runtime_manager.cancel('owner-b','secret-a',owner=True)['cancelled'],0)
        with self.assertRaises(HTTPException):runtime_manager.cancel('owner-a','secret-b',owner=True)
        worker.close.assert_not_called()
        with patch('backend.app.routers.compat.load_settings',return_value=core.DEFAULT_SETTINGS),patch('backend.app.routers.compat.find_model'),TestClient(app,base_url='http://127.0.0.1') as client:
            result=client.post('/v1/chat/completions',json={'model':'m','messages':[{'role':'user','content':'Hi'}],'stream':True})
            self.assertEqual(result.status_code,409,result.text);self.assertEqual(result.json()['error']['code'],'RUNTIME_BUSY')
        self.assertEqual(runtime_manager.cancel('owner-a','secret-a',owner=True)['cancelled'],1)
        worker.close.assert_called_once()
        self.assertTrue(runtime_manager.snapshot()['busy'])
        lease.close();self.assertFalse(core.RUNTIME_LOCK.locked())


if __name__=='__main__':unittest.main()
