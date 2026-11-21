"""The file agent's task-scoped entry point into the same plugin gateway as chat."""

import threading
from dataclasses import dataclass, field

from workspace.app.plugin_execution import PluginCall, task_files
from workspace.app.skills import instruction_sections
from workspace.app.store import identifier


TOOLS = [
    {
        "name": "list_workspace_skills",
        "description": "List installed active skills and document handles for this task. Call this before opening a relevant skill.",
        "inputSchema": {"type": "object", "additionalProperties": False},
    },
    {
        "name": "open_workspace_skill",
        "description": "Read the full Workspace skill and its tool schemas. Use only a skill matching the user's request.",
        "inputSchema": {
            "type": "object", "properties": {"skill": {"type": "string"}},
            "required": ["skill"], "additionalProperties": False,
        },
    },
    {
        "name": "call_workspace_skill",
        "description": "Invoke one tool from an opened Workspace skill. Supply semantic arguments and file handles, never bytes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "skill": {"type": "string"}, "tool": {"type": "string"},
                "arguments": {"type": "object"},
                "files": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["skill", "tool", "arguments", "files"], "additionalProperties": False,
        },
    },
    {
        "name": "delegate_workspace_skill",
        "description": "Delegate a bounded part of the user's task to a child agent using an opened Workspace skill and selected task files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "skill": {"type": "string"},
                "prompt": {"type": "string", "minLength": 1, "maxLength": 4000},
                "files": {"type": "array", "items": {"type": "string"}, "maxItems": 128},
            },
            "required": ["skill", "prompt", "files"], "additionalProperties": False,
        },
    },
]
TOOL_NAMES = {tool["name"] for tool in TOOLS}


@dataclass
class AgentPluginContext:
    skills: dict
    files: set[str]
    turn_id: str = field(default_factory=identifier)
    stopped: threading.Event = field(default_factory=threading.Event)
    opened: set[str] = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)


def handle(service, task, context, name, arguments):
    store, task_id = service.store, task["id"]
    if context.stopped.is_set():
        raise ValueError("Выполнение задачи остановлено.")
    active = {item["key"]: item for item in context.skills.values() if item["enabled"]
              and store.plugins.enabled(item["product"], item["name"])}
    if name == "list_workspace_skills":
        with context.lock:
            visible = set(context.files)
        return {"skills": [{"key": key, "title": item["title"], "description": item["description"]}
                           for key, item in active.items()],
                "files": [{k: item[k] for k in ("id", "name")} for item in task_files(store, task_id)
                          if item["id"] in visible]}
    key = arguments.get("skill")
    if not isinstance(key, str) or key not in active:
        raise ValueError("Навык вне каталога задачи или выключен.")
    skill = active[key]
    gateway = service.plugin_gateway
    if name == "open_workspace_skill":
        _, instructions = instruction_sections(skill["instructions"])
        tools = gateway.describe(skill, context.stopped)
        with context.lock:
            context.opened.add(key)
        return {"key": key, "instructions": instructions, "tools": tools}
    file_ids = arguments.get("files")
    with context.lock:
        opened = key in context.opened
        visible = set(context.files)
    if not opened:
        raise ValueError("Сначала прочитайте инструкцию выбранного навыка.")
    if (not isinstance(file_ids, list) or len(file_ids) > 128
            or any(not isinstance(k, str) or k not in visible for k in file_ids)):
        raise ValueError("Выберите файлы, предоставленные в этой задаче.")
    if name == "delegate_workspace_skill":
        result = delegate(service, task, context, skill, arguments.get("prompt"), file_ids)
        with context.lock:
            context.files.update(item["id"] for result_item in result["operations"] for item in result_item["artifacts"])
        return result
    call = PluginCall(identifier(), task_id, task["session_id"], context.turn_id, skill,
                      arguments.get("tool"), arguments.get("arguments"), file_ids)
    result = gateway.invoke(call, context.stopped)
    with context.lock:
        context.files.update(item["id"] for item in result["artifacts"])
    return result


def delegate(service, task, context, skill, prompt, file_ids):
    from workspace.app.plugin_workflow import run_workflow
    from workspace.app.runtime_client import RuntimeClient
    from workspace.app.skills import ChatSkill

    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 4000:
        raise ValueError("Укажите подзадачу для навыка длиной до 4000 символов.")
    task_id = task["id"]
    docs = {item["id"]: item for item in task_files(service.store, task_id)}
    client = RuntimeClient(service.store.one("providers", task["provider_id"]))
    caps = client.capabilities()
    owner = service.runtime_tokens.get(task_id)
    headers = {"X-Local-AI-Owner-ID": task_id, "X-Local-AI-Owner-Token": owner} if owner and not client.remote else {}
    child_id = "child-" + identifier()
    child = ChatSkill(skill, skill["instructions"], None, None)
    results, note = run_workflow(
        service.plugin_gateway, child, task_id, context.turn_id, [docs[key] for key in file_ids],
        client, headers, [{"role": "user", "content": prompt}], int(caps["max_output_tokens"]),
        context.stopped, native=(caps.get("tools") or {}).get("supported") is True, agent_id=child_id,
    )
    status = "error" if any(result["status"] == "error" for result in results) else "partial" if any(
        result["status"] == "partial" for result in results) else "ok"
    return {"agent_id": child_id, "operations": results, "note": note, "status": status}
