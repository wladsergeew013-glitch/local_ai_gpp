"""Narrow local control surface, with its own credential."""

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from workspace.app.runtime_client import RuntimeClient
from workspace.local_ai_control import check_payload, check_summary

PREFIX = "/api/control"
CONTROL_ROUTES = {
    ("GET", PREFIX + "/status"),
    ("POST", PREFIX + "/check"),
    ("POST", PREFIX + "/select-model"),
    ("POST", PREFIX + "/backup"),
}

router = APIRouter(prefix=PREFIX, tags=["local control"])


class ControlProvider(BaseModel):
    provider_id: str | None = Field(default=None, max_length=128)


class ControlModel(ControlProvider):
    model: str = Field(min_length=1, max_length=1024)


def _provider(store, provider_id):
    if provider_id:
        return store.one("providers", provider_id)
    rows = store.rows("SELECT id FROM providers")
    if len(rows) != 1:
        raise ValueError("Choose a Workspace provider with --provider after running status.")
    return store.one("providers", rows[0]["id"])


def _model_state(probe):
    if probe["model_available"] is None:
        return "choose_model"
    if not probe["model_available"]:
        return "model_missing"
    return "model_ready" if probe["model_loaded"] else "model_unloaded"


@router.get("/status")
def status(request: Request):
    store = request.app.state.store
    providers = store.rows("SELECT * FROM providers ORDER BY name,id")
    connections = []
    for provider in providers:
        connection = {
            key: provider[key] for key in ("id", "name", "base_url", "model")
        }
        latest = store.rows(
            "SELECT id,status,updated_at FROM tasks WHERE provider_id=? ORDER BY updated_at DESC LIMIT 1",
            (provider["id"],),
        )
        connection["latest_dialog"] = latest[0] if latest else None
        try:
            probe = RuntimeClient(provider).probe()
            connection["state"] = _model_state(probe)
            connection["available_models"] = [row["id"] for row in probe["models"]]
        except httpx.HTTPStatusError as exc:
            connection["state"] = "runtime_http_error"
            connection["http_status"] = exc.response.status_code
        except httpx.RequestError:
            connection["state"] = "runtime_unavailable"
        except ValueError:
            connection["state"] = "runtime_invalid_response"
        connections.append(connection)
    projects = store.rows(
        "SELECT p.id,p.name,COALESCE(s.source,'folder') AS source,"
        " count(t.id) AS dialog_count FROM projects p"
        " LEFT JOIN project_sources s ON s.project_id=p.id"
        " LEFT JOIN tasks t ON t.project_id=p.id"
        " WHERE p.id!='__direct_chats__' GROUP BY p.id ORDER BY p.created_at DESC"
    )
    return {"providers": connections, "projects": projects}


@router.post("/check")
def check(payload: ControlProvider, request: Request):
    provider = _provider(request.app.state.store, payload.provider_id)
    client = RuntimeClient(provider)
    try:
        client.capabilities()
        result = client.request("POST", "chat/completions", json=check_payload(client.model))
    except httpx.HTTPStatusError as exc:
        raise ValueError(f"Runtime returned HTTP {exc.response.status_code} during the check.") from exc
    except httpx.RequestError as exc:
        raise ValueError("Workspace could not complete a request to Runtime.") from exc
    return check_summary(client.model, result)


@router.post("/select-model")
def select_model(payload: ControlModel, request: Request):
    store = request.app.state.store
    current = _provider(store, payload.provider_id)
    try:
        probe = RuntimeClient({**current, "model": payload.model}).probe()
    except httpx.HTTPError as exc:
        raise ValueError("Could not verify the model in the saved Runtime connection.") from exc
    if not probe["model_available"]:
        raise ValueError("This exact model ID is absent from the saved Runtime connection.")
    with store.transaction() as db:
        rows = db.execute(
            "UPDATE providers SET model=? WHERE id=? AND base_url=? AND api_key=? AND model=?",
            (payload.model, current["id"], current["base_url"], current["api_key"], current["model"]),
        )
        if rows.rowcount != 1:
            raise ValueError("PROVIDER_CONFLICT: the connection changed during verification.")
    return {"provider_id": current["id"], "model": payload.model, "state": _model_state(probe)}


@router.post("/backup")
def backup(request: Request):
    return request.app.state.tasks.snapshot()
