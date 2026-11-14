"""A Workspace conversation uses Runtime's public API without filesystem tools."""

import json
import threading
import time
import httpx
from workspace.app.runtime_client import RuntimeClient


def completed_messages(events):
    turns = {}
    for event in events:
        value = json.loads(event["payload"])
        turns.setdefault(value["turn_id"], {})[value["role"]] = value
    messages = []
    for turn in turns.values():
        if turn.get("assistant", {}).get("phase") == "done" and "user" in turn:
            messages.extend(
                {"role": role, "content": turn[role]["text"]}
                for role in ("user", "assistant")
            )
    return messages


class ChatRun:
    def __init__(self, store, chat, provider, token, prompt, memory, on_exit):
        self.store, self.chat, self.provider = store, chat, provider
        self.token, self.prompt, self.memory, self.on_exit = (
            token,
            prompt,
            memory,
            on_exit,
        )
        self.cancelled, self.finished = threading.Event(), threading.Event()
        self.thread = threading.Thread(
            target=self._run, daemon=False, name="workspace-chat"
        )

    def start(self):
        self.thread.start()

    def stop(self):
        self.cancelled.set()
        for _ in range(3):
            try:
                RuntimeClient(self.provider).cancel_owner(self.chat["id"], self.token)
            except Exception:
                pass
            if self.finished.wait(1):
                return {"confirmed": True, "survivors": []}
        if not self.finished.wait(5):
            raise RuntimeError("Conversation cancellation has not been confirmed.")
        return {"confirmed": True, "survivors": []}

    def _run(self):
        from workspace.app.store import identifier, now

        chat_id, turn_id = self.chat["id"], identifier()
        text, reason, error, done = "", None, "", False
        try:
            history = self.store.rows(
                "SELECT payload FROM events WHERE task_id=? AND kind='chat_message' ORDER BY seq",
                (chat_id,),
            )
            messages = completed_messages(history) if self.memory else []
            messages.append({"role": "user", "content": self.prompt})
            self.store.event(
                chat_id,
                "chat_message",
                {
                    "turn_id": turn_id,
                    "role": "user",
                    "text": self.prompt,
                    "phase": "done",
                },
            )
            client = RuntimeClient(self.provider)
            # Loading belongs to the owned inference request, so stop can cancel
            # both a cold load and token generation through the same owner token.
            caps = client.capabilities()
            if self.cancelled.is_set():
                return
            headers = {
                "X-Local-AI-Owner-ID": chat_id,
                "X-Local-AI-Owner-Token": self.token,
            }
            if self.provider.get("api_key"):
                headers["Authorization"] = "Bearer " + self.provider["api_key"]
            payload = {
                "model": self.provider["model"],
                "messages": messages,
                "max_tokens": min(2048, int(caps["max_output_tokens"])),
                "stream": True,
                "memory": self.memory,
                "runtime": {"enable_thinking": False, "context_overflow": "trim"},
            }
            last_flush = 0
            with httpx.Client(
                timeout=httpx.Timeout(300, connect=10), trust_env=False
            ) as http:
                with http.stream(
                    "POST",
                    client.base_url + "/chat/completions",
                    headers=headers,
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if self.cancelled.is_set():
                            break
                        if not line.startswith("data: "):
                            continue
                        if line == "data: [DONE]":
                            done = True
                            break
                        value = json.loads(line[6:])
                        if value.get("error"):
                            raise RuntimeError(
                                str(value["error"].get("message", value["error"]))
                            )
                        for choice in value.get("choices", []):
                            text += choice.get("delta", {}).get("content") or ""
                            reason = choice.get("finish_reason") or reason
                        if time.monotonic() - last_flush >= 0.15:
                            with self.store.transaction() as db:
                                db.execute(
                                    "UPDATE tasks SET details=json_set(details,'$.partial',?),updated_at=? WHERE id=?",
                                    (text, now(), chat_id),
                                )
                            last_flush = time.monotonic()
            if not done or reason != "stop":
                error = (
                    "Ответ остановлен."
                    if self.cancelled.is_set()
                    else (
                        "Ответ достиг лимита токенов."
                        if reason == "length"
                        else "Соединение оборвалось до завершения ответа."
                    )
                )
        except Exception as exc:
            error = str(exc)
        finally:
            successful = done and reason == "stop" and not self.cancelled.is_set()
            phase = (
                "done"
                if successful
                else "cancelled" if self.cancelled.is_set() else "error"
            )
            self.store.event(
                chat_id,
                "chat_message",
                {
                    "turn_id": turn_id,
                    "role": "assistant",
                    "text": text,
                    "phase": phase,
                    "error": error,
                    "finish_reason": reason,
                },
            )
            try:
                self.on_exit(
                    (
                        "completed"
                        if successful
                        else "cancelled" if self.cancelled.is_set() else "failed"
                    ),
                    error,
                )
            finally:
                self.finished.set()
