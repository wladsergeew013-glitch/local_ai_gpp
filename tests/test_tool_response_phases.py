import json
import sys
import types
import unittest
from unittest.mock import patch

from backend.app import llama_worker
from backend.app.reasoning import split_reasoning


TOOL = {"type": "function", "function": {"name": "lookup_label"}}
CALL = '<tool_call>{"name":"lookup_label","arguments":{}}</tool_call>'


class ToolResponsePhaseTests(unittest.TestCase):
    def generate(self, text, *, stream, thinking, tool_choice="auto"):
        events, options = [], []

        class Llama:
            def __init__(self, **kwargs):
                pass

            def tokenize(self, value, **kwargs):
                return list(value)

            def close(self):
                pass

            def create_chat_completion(self, **kwargs):
                options.append(kwargs)
                if kwargs["stream"]:
                    return iter(
                        [
                            {
                                "choices": [
                                    {
                                        "delta": {"content": text},
                                        "finish_reason": "stop",
                                    }
                                ]
                            }
                        ]
                    )
                return {
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": text},
                            "finish_reason": "stop",
                        }
                    ]
                }

        with (
            patch.dict(sys.modules, {"llama_cpp": types.SimpleNamespace(Llama=Llama)}),
            patch.object(llama_worker, "configure_cuda_dlls"),
            patch.object(
                llama_worker,
                "llama_backend_details",
                return_value={"supports_gpu_offload": False},
            ),
            patch.object(
                llama_worker,
                "apply_model_adapter",
                return_value={
                    "model_adapter": "qwen" if thinking else "default",
                    "enable_thinking": thinking,
                    "tool_calling": "qwen35-xml" if thinking else None,
                },
            ),
            patch.object(llama_worker, "write_event", side_effect=events.append),
        ):
            llama_worker.serve(
                [
                    {
                        "model": {"path": "fixture"},
                        "messages": [{"role": "user", "content": "Find a label."}],
                        "runtime": {"n_ctx": 4096},
                        "tools": [TOOL],
                        "tool_choice": tool_choice,
                        "stream": stream,
                    }
                ]
            )
        self.assertFalse([e for e in events if e["type"] == "error"])
        if stream:
            done = next(e for e in events if e["type"] == "done")
            deltas = [
                c for e in events if e["type"] == "tool_delta" for c in e["tool_calls"]
            ]
            self.assertEqual(len(deltas), len(done["tool_calls"]))
            return (
                done["content"],
                done["finish_reason"],
                done["tool_calls"],
                options[0],
            )
        choice = next(e for e in events if e["type"] == "result")["result"]["choices"][
            0
        ]
        return (
            choice["message"]["content"],
            choice["finish_reason"],
            choice["message"].get("tool_calls", []),
            options[0],
        )

    def test_only_closed_reasoning_can_be_followed_by_a_tool_turn(self):
        for stream in (False, True):
            for raw, expected_answer, count in (
                (
                    "<think>Example: " + CALL + "</think>Just a label.",
                    "Just a label.",
                    0,
                ),
                ("Look up the label.</think>" + CALL, "", 1),
                ("<think>Still reasoning. Example: " + CALL, "", 0),
            ):
                with self.subTest(
                    stream=stream, count=count, has_end="</think>" in raw
                ):
                    text, finish, calls, _ = self.generate(
                        raw, stream=stream, thinking=True
                    )
                    self.assertEqual(
                        split_reasoning(text, True)["answer"], expected_answer
                    )
                    self.assertEqual(len(calls), count)
                    self.assertEqual(finish, "tool_calls" if count else "stop")
                    if count:
                        self.assertEqual(calls[0]["function"]["name"], "lookup_label")
                        self.assertEqual(
                            json.loads(calls[0]["function"]["arguments"]), {}
                        )

    def test_none_allows_plain_model_in_both_response_modes(self):
        for stream in (False, True):
            with self.subTest(stream=stream):
                text, finish, calls, options = self.generate(
                    "Plain reply.", stream=stream, thinking=False, tool_choice="none"
                )
                self.assertEqual((text, finish, calls), ("Plain reply.", "stop", []))
                self.assertNotIn("tools", options)
