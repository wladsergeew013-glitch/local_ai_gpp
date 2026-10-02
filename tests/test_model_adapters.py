import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.model_adapters import qwen_thinking_template


class QwenAdapterTests(unittest.TestCase):
    def test_only_capable_qwen_templates_are_adapted(self):
        template = '{% if enable_thinking is not defined or enable_thinking %}<think>{% else %}<think></think>{% endif %}'
        for architecture in ('qwen3', 'qwen35'):
            metadata = {'general.architecture': architecture, 'tokenizer.chat_template': template}
            self.assertTrue(qwen_thinking_template(metadata).startswith('{% set enable_thinking = false %}'))
            self.assertTrue(qwen_thinking_template(metadata, True).startswith('{% set enable_thinking = true %}'))
        self.assertIsNone(qwen_thinking_template({'general.architecture': 'llama', 'tokenizer.chat_template': template}))
        self.assertIsNone(qwen_thinking_template({'general.architecture': 'qwen2', 'tokenizer.chat_template': 'normal chat'}))
        self.assertIsNone(qwen_thinking_template({'general.name': 'Qwen', 'tokenizer.chat_template': template}))

    def test_openai_template_flag_reaches_both_generation_paths(self):
        client = TestClient(app)
        payload = {'model': 'm', 'messages': [{'role': 'user', 'content': 'hello'}],
                   'chat_template_kwargs': {'enable_thinking': True}}
        result = {'choices': [{'message': {'content': 'hello'}, 'finish_reason': 'stop'}]}
        with patch('backend.app.routers.compat.find_model'), \
             patch('backend.app.routers.compat.create_chat_completion', return_value=result) as completion:
            self.assertEqual(client.post('/v1/chat/completions', json=payload).status_code, 200)
            self.assertEqual(completion.call_args.kwargs['runtime_override'], {'enable_thinking': True})
        with patch('backend.app.routers.compat.find_model'), \
             patch('backend.app.routers.compat.stream_chat_completion', return_value=(event for event in [{'type': 'done'}])) as stream:
            response = client.post('/v1/chat/completions', json={**payload, 'stream': True})
            self.assertIn('[DONE]', response.text)
            self.assertEqual(stream.call_args.kwargs['runtime_override'], {'enable_thinking': True})

    def test_unknown_template_flags_fail_before_stream_headers(self):
        with patch('backend.app.routers.compat.find_model'):
            response = TestClient(app).post('/v1/chat/completions', json={
                'model': 'm', 'messages': [{'role': 'user', 'content': 'hi'}], 'stream': True,
                'chat_template_kwargs': {'invented': False}})
        self.assertEqual(response.status_code, 400)
