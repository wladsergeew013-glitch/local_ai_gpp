"""Function calling with an explicit, bounded local tool; no shell execution.

Run Runtime first, then: python examples/tools_client.py
Uses only Python's standard library. Qwen3.5 4B with its native tools template.
"""

import json
import os
import urllib.request

try:
    from examples.client import LocalAI
except ModuleNotFoundError:
    from client import LocalAI


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765/v1")
    parser.add_argument("--model")
    args = parser.parse_args()
    ai = LocalAI(
        base_url=args.base_url, model=args.model, enable_thinking=False, max_tokens=512
    )

    def post(messages, tools):
        payload = {
            "model": ai.model,
            "messages": messages,
            "tools": tools,
            "max_tokens": 512,
            "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        request = urllib.request.Request(
            ai.base_url + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers=ai.headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=240) as response:
            return json.load(response)

    tools = [
        {
            "type": "function",
            "function": {
                "name": "project_name",
                "description": "Return the name of this example project.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        }
    ]
    messages = [
        {"role": "user", "content": "Use project_name to find the name of my project."}
    ]
    for _ in range(4):
        choice = post(messages, tools)["choices"][0]
        message = choice["message"]
        messages.append(message)
        if choice.get("finish_reason") == "length":
            raise RuntimeError("Incomplete response; no action was executed.")
        calls = message.get("tool_calls") or []
        if not calls:
            print(message.get("content") or "")
            return
        for call in calls:
            # Never eval a function name or execute generated shell text.
            if (
                call["function"]["name"] != "project_name"
                or json.loads(call["function"]["arguments"]) != {}
            ):
                raise RuntimeError("Unknown or invalid tool call")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": "project_name",
                    "content": json.dumps({"name": "ORBIT"}),
                }
            )
    raise RuntimeError("Tool-turn limit reached; no automatic retry.")


if __name__ == "__main__":
    main()
