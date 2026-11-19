"""User-facing messages, separate from the agent's tool/event log."""

import json

MESSAGE_FILTER = "(kind IN ('chat_message','task_created','input_admitted','result') OR (kind='dsh' AND json_extract(payload,'$.type') IN ('user/message','assistant/message')))"


def text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            str(b.get("text") or "")
            for b in value
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def project_messages(rows):
    parsed = [{**r, "payload": json.loads(r["payload"])} for r in rows]
    plain = any(r["kind"] == "chat_message" for r in parsed)
    canonical_user = any(
        r["kind"] == "dsh" and r["payload"].get("type") == "user/message"
        for r in parsed
    )
    admitted = any(
        r["kind"] == "input_admitted" and r["payload"].get("prompt") for r in parsed
    )
    out = []
    for row in parsed:
        p = row["payload"]
        role = value = error = ""
        if row["kind"] == "chat_message":
            role = p["role"]
            value = p.get("text", "")
            error = p.get("error") or (
                "Ответ остановлен." if p.get("phase") == "cancelled" else ""
            )
        elif row["kind"] == "input_admitted" and not plain and not canonical_user:
            role = "user"
            value = p.get("prompt") or ""
        elif row["kind"] == "task_created" and not (
            plain or canonical_user or admitted
        ):
            role = "user"
            value = p.get("prompt", "")
        elif row["kind"] == "dsh":
            data = p.get("data") or {}
            m = data.get("message") or data
            role = "user" if p.get("type") == "user/message" else "assistant"
            value = text(m.get("content"))
        elif row["kind"] == "result":
            role = "assistant"
            value = p.get("final_response", "")
        if not (value or error):
            continue
        if not plain and out and out[-1]["role"] == role and out[-1]["text"] == value:
            continue
        out.append(
            {
                "id": row["seq"],
                "role": role,
                "text": value,
                "error": error,
                "time": row["created_at"],
            }
        )
    return out
