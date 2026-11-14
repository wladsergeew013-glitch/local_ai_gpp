import sys
import types
import unittest
from unittest.mock import patch
from backend.app import llama_worker


class ToolUsageTests(unittest.TestCase):
    def test_streamed_tool_turn_preserves_native_template_and_envelope_usage(self):
        events = []
        raw = "<tool_call><function=read_file><parameter=path>app.py</parameter></function></tool_call>"
        usage = {"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168}

        class FakeLlama:
            def __init__(self, **kwargs):
                pass

            def close(self):
                pass

            def tokenize(self, value, **kwargs):
                return list(value)

            def create_chat_completion(self, **kwargs):
                if kwargs.get("stream"):
                    return iter(
                        [
                            {
                                "choices": [
                                    {"delta": {"content": raw}, "finish_reason": "stop"}
                                ]
                            }
                        ]
                    )
                return {
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": raw},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": usage,
                }

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            }
        ]
        with patch.dict(
            sys.modules, {"llama_cpp": types.SimpleNamespace(Llama=FakeLlama)}
        ), patch.object(llama_worker, "configure_cuda_dlls"), patch.object(
            llama_worker,
            "llama_backend_details",
            return_value={"supports_gpu_offload": False},
        ), patch.object(
            llama_worker,
            "apply_model_adapter",
            return_value={"tool_calling": "qwen35-xml"},
        ), patch.object(
            llama_worker, "write_event", side_effect=events.append
        ):
            llama_worker.serve(
                [
                    {
                        "model": {"path": "fake"},
                        "runtime": {"n_ctx": 4096},
                        "messages": [{"role": "user", "content": "Read"}],
                        "tools": tools,
                        "max_tokens": 256,
                        "stream": True,
                    }
                ]
            )
        done = next(event for event in events if event["type"] == "done")
        self.assertEqual(done["usage"], usage)
        self.assertEqual(done["finish_reason"], "tool_calls")
        self.assertTrue(any(event["type"] == "tool_delta" for event in events))
        self.assertFalse(
            any(
                event["type"] == "delta" and "<tool_call>" in event.get("text", "")
                for event in events
            )
        )


if __name__ == "__main__":
    unittest.main()
