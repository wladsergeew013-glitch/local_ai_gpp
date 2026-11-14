"""Task-capability MCP bridge. Every external write also needs a concrete UI grant."""

import hashlib
import json
import secrets
from fastapi import HTTPException
from workspace.app.connectors import read_items, write_item
from workspace.app.version import VERSION


def handle(service, task_id, token, payload):
    expected = service.tokens.get(task_id, "")
    if not expected or not token or not secrets.compare_digest(token, expected):
        raise HTTPException(403, "Task capability required")
    task = service.store.one("tasks", task_id)
    allowed = task["details"].get("connector_ids") or []
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
                "instructions": "Read only explicitly selected local applications. Creating an item requires a separate one-operation user approval in Workspace.",
            }
        )
    if method == "ping":
        return response({})
    if method == "tools/list":
        common = {
            "connector_id": {
                "type": "string",
                "enum": allowed,
                "description": "ID of a connector explicitly attached to this task",
            }
        }
        return response(
            {
                "tools": [
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
                ]
            }
        )
    if method != "tools/call":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "Method not found"},
        }
    params = payload.get("params") or {}
    arguments = params.get("arguments") or {}
    connector_id = arguments.get("connector_id")
    try:
        if connector_id not in allowed:
            raise ValueError("Connector was not attached to this task.")
        connector = service.store.one("connectors", connector_id)
        if params.get("name") == "read_items":
            result = read_items(connector)
        elif params.get("name") == "create_item":
            if not connector["allow_write"]:
                raise ValueError("Connector writes are disabled.")
            from workspace.app.main import ConnectorWrite

            action_id = hashlib.sha256(
                (token + ":" + str(request_id)).encode()
            ).hexdigest()
            write = ConnectorWrite.model_validate(
                {**arguments, "action_id": action_id, "approved": True}
            )
            receipt = service.store.rows(
                "SELECT status,result FROM connector_actions WHERE action_id=?",
                (action_id,),
            )
            if receipt:
                result = {
                    "status": receipt[0]["status"],
                    "result": (
                        json.loads(receipt[0]["result"])
                        if receipt[0]["result"]
                        else None
                    ),
                    "resent": False,
                }
            elif not service.approve_connector(
                task_id,
                {
                    "connector": connector["name"],
                    "connector_id": connector_id,
                    "title": write.title,
                    "body": write.body,
                    "action_id": action_id,
                },
            ):
                result = {"status": "rejected", "written": False}
            else:
                result = write_item(service.store, connector_id, write.model_dump())
                service.store.event(
                    task_id,
                    "connector_write_result",
                    {"connector_id": connector_id, "action_id": action_id, **result},
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
