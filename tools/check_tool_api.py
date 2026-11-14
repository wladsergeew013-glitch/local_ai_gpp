"""Real model tool-call round trip. No tool is executed by Runtime.

python tools/check_tool_api.py --base-url http://127.0.0.1:8765/v1
"""

import argparse
import json
import os
import time
from pathlib import Path
import httpx


def check(base_url, model=None):
    headers = (
        {"Authorization": "Bearer " + os.environ["LOCAL_AI_API_KEY"]}
        if os.environ.get("LOCAL_AI_API_KEY")
        else {}
    )
    with httpx.Client(
        base_url=base_url.rstrip("/") + "/",
        headers=headers,
        timeout=240,
        trust_env=False,
    ) as client:

        def post(payload):
            response = client.post("chat/completions", json=payload)
            response.raise_for_status()
            return response.json()

        response = client.get("models")
        response.raise_for_status()
        model = model or response.json()["data"][0]["id"]
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_project_marker",
                    "description": "Read the project marker from a local test fixture.",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            }
        ]
        messages = [
            {
                "role": "system",
                "content": "Use the read_project_marker tool to answer. Do not invent the marker.",
            },
            {
                "role": "user",
                "content": "Read the project marker using the provided tool. What is its value?",
            },
        ]
        started = time.monotonic()
        first = post(
            {
                "model": model,
                "messages": messages,
                "tools": tools,
                "max_tokens": 256,
                "temperature": 0,
            }
        )
        answer = first["choices"][0]
        assert answer["finish_reason"] == "tool_calls", first
        calls = answer["message"].get("tool_calls") or []
        assert (
            len(calls) == 1 and calls[0]["function"]["name"] == "read_project_marker"
        ), first
        assert isinstance(json.loads(calls[0]["function"]["arguments"]), dict), first
        messages.extend(
            [
                answer["message"],
                {
                    "role": "tool",
                    "tool_call_id": calls[0]["id"],
                    "name": "read_project_marker",
                    "content": '{"marker":"ORBIT-7429"}',
                },
            ]
        )
        final = post(
            {
                "model": model,
                "messages": messages,
                "tools": tools,
                "max_tokens": 256,
                "temperature": 0,
                "tool_choice": "none",
            }
        )
        text = final["choices"][0]["message"]["content"]
        assert "ORBIT-7429" in text, final
        stream_calls = []
        with client.stream(
            "POST",
            "chat/completions",
            json={
                "model": model,
                "messages": messages[:2],
                "tools": tools,
                "max_tokens": 256,
                "temperature": 0,
                "stream": True,
                "stream_options": {"include_usage": True},
            },
        ) as response:
            response.raise_for_status()
            frames = []
            for line in response.iter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    frame = json.loads(line[6:])
                    frames.append(frame)
                    if frame.get("error"):
                        raise AssertionError(frame)
                    for choice in frame.get("choices", []):
                        stream_calls.extend(
                            choice.get("delta", {}).get("tool_calls", [])
                        )
        assert stream_calls and any(
            c.get("finish_reason") == "tool_calls"
            for f in frames
            for c in f.get("choices", [])
        ), frames
        stream_usage = next((f["usage"] for f in reversed(frames) if f.get("usage")), {})
        for usage in (first.get("usage", {}), stream_usage):
            assert usage.get("prompt_tokens", 0) > 0 and usage.get("completion_tokens", 0) > 0, usage
            assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"], usage
            assert not usage.get("estimated"), usage
        return {
            "model": model,
            "tool_call_id": calls[0]["id"],
            "tool_name": calls[0]["function"]["name"],
            "answer": text,
            "stream_tool_calls": len(stream_calls),
            "tool_usage": first["usage"],
            "stream_usage": stream_usage,
            "elapsed_sec": round(time.monotonic() - started, 2),
            "capabilities": client.get("models/" + model + "/capabilities").json(),
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765/v1")
    parser.add_argument("--model")
    parser.add_argument("--output")
    args = parser.parse_args()
    result = check(args.base_url, args.model)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    print(rendered)
