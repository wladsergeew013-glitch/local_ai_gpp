import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import HTTPException
from backend.app import core, llama_worker
from backend.app.context_budget import validate_context


class ContextBudgetTests(unittest.TestCase):
    def test_current_message_overflow_has_an_actionable_error(self):
        tokenizer = SimpleNamespace(tokenize=lambda text, **kwargs: list(text))
        with self.assertRaisesRegex(ValueError, 'лимит — 512.*Сократите сообщение'):
            llama_worker.fit_messages(tokenizer, [{'role': 'user', 'content': 'x' * 900}], 128, 512, True)

    def test_strict_history_overflow_explains_how_to_enable_trimming(self):
        tokenizer = SimpleNamespace(tokenize=lambda text, **kwargs: list(text))
        messages = [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'x' * 600},
                    {'role': 'assistant', 'content': 'old'}, {'role': 'user', 'content': 'latest'}]
        with self.assertRaisesRegex(ValueError, 'автоматическое сокращение истории'):
            llama_worker.fit_messages(tokenizer, messages, 128, 512, False)
        kept, dropped = llama_worker.fit_messages(tokenizer, messages, 128, 512, True)
        self.assertEqual(dropped, 2)
        self.assertEqual([m['content'] for m in kept], ['sys', 'latest'])
        self.assertEqual(messages[1]['content'], 'x' * 600)

    def test_invalid_response_reserve_is_rejected_before_loading(self):
        with patch.object(core, 'find_model', return_value=([], {}, 0)), patch.object(core, 'load_settings', return_value={}), \
             patch.object(core, '_merge_runtime', return_value={'n_ctx': 512}), patch.object(core, '_get_runtime') as load:
            with self.assertRaises(HTTPException) as error:
                list(core._completion_events(model_id='m', messages=[], temperature=.2, max_tokens=512, stream=True))
        self.assertEqual(error.exception.status_code, 400)
        self.assertIn('длина ответа', error.exception.detail)
        load.assert_not_called()

    def test_invalid_context_and_answer_are_rejected(self):
        for context, answer in ((0, 128), (131073, 128), (512, 256), (4096, 4096), (4096, 0)):
            with self.assertRaises(ValueError):
                validate_context(context, answer)

    def test_ready_is_emitted_after_a_real_warmup_and_reset(self):
        events, calls = [], []
        class FakeLlama:
            def __init__(self, **kwargs): pass
            def create_completion(self, **kwargs):
                self_outer.assertFalse(any(e['type'] == 'ready' for e in events))
                calls.append(('probe', kwargs))
            def reset(self): calls.append(('reset', None))
            def close(self): pass
        self_outer = self
        with patch.dict(sys.modules, {'llama_cpp': SimpleNamespace(Llama=FakeLlama)}), \
             patch.object(llama_worker, 'configure_cuda_dlls'), \
             patch.object(llama_worker, 'llama_backend_details', return_value={'supports_gpu_offload': False}), \
             patch.object(llama_worker, 'apply_model_adapter', return_value={}), \
             patch.object(llama_worker, 'write_event', side_effect=events.append):
            llama_worker.serve([{'operation': 'load', 'model': {'path': 'fake'}, 'runtime': {'n_ctx': 4096}}])
        self.assertEqual([c[0] for c in calls], ['probe', 'reset'])
        self.assertEqual(calls[0][1]['max_tokens'], 2)
        self.assertEqual(events[-1]['type'], 'ready')
        self.assertTrue(events[-1]['runtime']['warmed_up'])
