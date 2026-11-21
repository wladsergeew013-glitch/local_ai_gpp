"""Task-capability MCP bridge. Every external write also needs a concrete UI grant."""

import hashlib
import json
import secrets
from fastapi import HTTPException
from workspace.app.connectors import ConnectorWrite, read_items, receipt, write_item
from workspace.app.version import VERSION
from workspace.app import agent_plugins


def handle(service, task_id, token, payload):
    expected = service.connector_tokens.get(task_id, "")
    if not expected or not secrets.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(403, "Task capability required")
    task = service.store.one("tasks", task_id)
    allowed = task["details"].get("connector_ids") or []
    plugin_context = service.agent_plugins.get(task_id)
    if (not allowed and not plugin_context) or task["details"].get("mode", "agent") != "agent":
        raise HTTPException(
            403, "No applications are attached to this file-agent conversation"
        )
    request_id = payload.get("id")
    method = payload.get("method", "")
    if request_id is None:
        return None

    def response(result):
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    if method == "initialize":
        return response(
            {
                "protocolVersion": (payload.get("params") or {}).get(
                    "protocolVersion", "2025-03-26"
                ),
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "Local AI Workspace applications",
                    "version": VERSION,
                },
                "instructions": "Use Workspace skills only for the user's task. Read a matching skill before calling its tools. Separate connector writes require user approval.",
            }
        )
    if method == "ping":
        return response({})
    if method == "workspace/readiness":
        return response({"ready": service.connector_discoveries.get(task_id) == token})
    if method == "tools/list":
        service.connector_discoveries[task_id] = token
        service.store.event(task_id, "connector_tools_discovered", {"tool_count": 2})
        common = {
            "connector_id": {
                "type": "string",
                "enum": allowed,
                "description": "ID of a connector explicitly attached to this task",
            }
        }
        tools = [
                    {
                        "name": "read_items",
                        "description": "Read items from a selected local application.",
                        "inputSchema": {
                            "type": "object",
                            "properties": common,
                            "required": ["connector_id"],
                            "additionalProperties": False,
                        },
                        "annotations": {
                            "readOnlyHint": True,
                            "idempotentHint": True,
                            "openWorldHint": False,
                        },
                    },
                    {
                        "name": "create_item",
                        "description": "Propose one new item in a selected local application. Workspace shows exact title/body and waits for user approval; no write happens on rejection.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                **common,
                                "title": {"type": "string", "maxLength": 500},
                                "body": {"type": "string", "maxLength": 20000},
                            },
                            "required": ["connector_id", "title", "body"],
                            "additionalProperties": False,
                        },
                        "annotations": {
                            "readOnlyHint": False,
                            "destructiveHint": True,
                            "idempotentHint": False,
                            "openWorldHint": False,
                        },
                    },
                ] if allowed else []
        return response({"tools": tools + (agent_plugins.TOOLS if plugin_context and plugin_context.skills else [])})
    if method != "tools/call":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "Method not found"},
        }
    params = payload.get("params") or {}
    arguments = params.get("arguments") or {}
    try:
        name = params.get("name")
        if name in agent_plugins.TOOL_NAMES and plugin_context:
            result = agent_plugins.handle(service, task, plugin_context, name, arguments)
            return response({"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                             "isError": result.get("status") in {"error", "partial"}})
        connector_id = arguments.get("connector_id")
        if connector_id not in allowed:
            raise ValueError("Connector was not attached to this task.")
        connector = service.store.one("connectors", connector_id)
        if name == "read_items":
            result = read_items(connector)
        elif name == "create_item":
            if not connector["allow_write"]:
                raise ValueError("Connector writes are disabled.")
            action_id = hashlib.sha256(
                (token + ":" + str(request_id)).encode()
            ).hexdigest()
            write = ConnectorWrite.model_validate(
                {k: v for k, v in arguments.items() if k != "connector_id"}
                | {"action_id": action_id}
            )
            result = receipt(service.store, connector_id, write)
            if result is None:
                proposal = {
                    "connector": connector["name"],
                    "connector_id": connector_id,
                    **write.model_dump(),
                }
                approval_id = service.approve_connector(task_id, proposal)
                if approval_id is None:
                    result = {"status": "rejected", "written": False}
                else:
                    result = write_item(
                        service.store, task_id, connector_id, write, approval_id
                    )
                    service.store.event(
                        task_id,
                        "connector_write_result",
                        {
                            "connector_id": connector_id,
                            "action_id": action_id,
                            **result,
                        },
                    )
        else:
            raise ValueError("Unknown connector tool.")
        return response(
            {
                "content": [
                    {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
                ],
                "isError": False,
            }
        )
    except Exception as exc:
        return response(
            {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        )
