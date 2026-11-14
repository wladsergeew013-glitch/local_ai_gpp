"""First local-app connector: explicit read and one approved, receipted write."""

import json
import httpx
from workspace.app.runtime_client import local_url
from workspace.app.store import now


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


def write_item(store, connector_id, payload):
    connector = store.one("connectors", connector_id)
    if not connector["allow_write"] or payload.get("approved") is not True:
        raise ValueError(
            "CONNECTOR_APPROVAL_REQUIRED: enable writes and approve this exact item."
        )
    action = payload["action_id"]
    body = {"title": payload["title"], "body": payload["body"], "action_id": action}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True)
    with store.transaction() as db:
        rows = store.rows(
            "SELECT * FROM connector_actions WHERE action_id=?", (action,)
        )
        if rows:
            old = rows[0]
            if old["connector_id"] != connector_id or old["payload"] != encoded:
                raise ValueError(
                    "ACTION_CONFLICT: the action ID already describes a different write."
                )
            return {
                "status": old["status"],
                "result": json.loads(old["result"]) if old["result"] else None,
                "resent": False,
            }
        db.execute(
            "INSERT INTO connector_actions VALUES(?,?,?,?,?,?)",
            (action, connector_id, encoded, "sending", None, now()),
        )
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
