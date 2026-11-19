"""First local-app connector: explicit read and one approved, receipted write."""

import json
import httpx
from pydantic import BaseModel, ConfigDict, Field
from workspace.app.runtime_client import local_url
from workspace.app.store import now


class ConnectorWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(max_length=20000)
    action_id: str = Field(min_length=16, max_length=128)


def request(connector, method, body=None):
    headers = (
        {"Authorization": "Bearer " + connector["api_key"]}
        if connector.get("api_key")
        else {}
    )
    with httpx.Client(
        timeout=15, trust_env=False, follow_redirects=False, headers=headers
    ) as client:
        response = client.request(
            method, local_url(connector["base_url"]) + "/items", json=body
        )
        response.raise_for_status()
        return response.json()


def read_items(connector):
    return request(connector, "GET")


def receipt(store, connector_id, write: ConnectorWrite):
    rows = store.rows(
        "SELECT * FROM connector_actions WHERE action_id=?", (write.action_id,)
    )
    if not rows:
        return None
    old = rows[0]
    if (
        old["connector_id"] != connector_id
        or json.loads(old["payload"]) != write.model_dump()
    ):
        raise ValueError(
            "ACTION_CONFLICT: the action ID already describes a different write."
        )
    return {
        "status": old["status"],
        "result": json.loads(old["result"]) if old["result"] else None,
        "resent": False,
    }


def write_item(store, task_id, connector_id, write: ConnectorWrite, approval_id):
    body = write.model_dump()
    action = write.action_id
    with store.transaction() as db:
        task = store.one("tasks", task_id)
        connector = store.one("connectors", connector_id)
        if (
            connector_id not in task["details"].get("connector_ids", [])
            or not connector["allow_write"]
        ):
            raise ValueError("Connector write is unavailable in this conversation.")
        previous = receipt(store, connector_id, write)
        if previous is not None:
            return previous
        row = db.execute(
            "SELECT payload FROM approvals WHERE id=? AND task_id=? "
            "AND kind='connector' AND status='granted'",
            (approval_id, task_id),
        ).fetchone()
        proposal = json.loads(row["payload"]).get("payload", {}) if row else {}
        if (
            task["status"] not in {"running", "awaiting_permission"}
            or proposal.get("connector_id") != connector_id
            or any(proposal.get(key) != value for key, value in body.items())
        ):
            raise ValueError(
                "CONNECTOR_APPROVAL_REQUIRED: approve this exact item in Workspace."
            )
        db.execute(
            "INSERT INTO connector_actions VALUES(?,?,?,?,?,?)",
            (
                action,
                connector_id,
                json.dumps(body, ensure_ascii=False),
                "sending",
                None,
                now(),
            ),
        )
        db.execute("UPDATE approvals SET status='consumed' WHERE id=?", (approval_id,))
    try:
        result = request(connector, "POST", body)
        status = "completed"
    except Exception as exc:
        result = {
            "error": type(exc).__name__,
            "message": "The application may have committed the write. Inspect it before creating a new action.",
        }
        status = "uncertain"
    with store.transaction() as db:
        db.execute(
            "UPDATE connector_actions SET status=?,result=? WHERE action_id=?",
            (status, json.dumps(result, ensure_ascii=False), action),
        )
    return {"status": status, "result": result, "resent": False}
