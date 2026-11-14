"""Independent Workspace ASGI application. Runtime is a remote HTTP provider."""

from __future__ import annotations
import json
import sys
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from workspace.app.store import Store, data_root, identifier, now, SCHEMA_VERSION
from workspace.app.task_service import TaskService
from workspace.app.runtime_client import RuntimeClient, local_url
from workspace.app.history import reconcile
from workspace.app.instance import Instance

from workspace.app.version import VERSION


def create_app(root=None):
    root = Path(root or data_root())

    @asynccontextmanager
    async def lifespan(application):
        with Instance(root / ".instance.lock"):
            store = Store(root)
            service = TaskService(store)
            application.state.store = store
            application.state.tasks = service
            store.recover()
            try:
                yield
            finally:
                import asyncio

                await asyncio.to_thread(service.close)
                store.close()

    app = FastAPI(title="Local AI Workspace", version=VERSION, lifespan=lifespan)

    @app.middleware("http")
    async def boundary(request, call_next):
        host = request.headers.get("host", "").split(":")[0].lower()
        if host not in {"localhost", "127.0.0.1", "[::1]"}:
            return JSONResponse({"detail": "Loopback Host required"}, 400)
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            if origin != os.environ.get("LOCAL_AI_WORKSPACE_DEV_ORIGIN") and (
                parsed.scheme != "http"
                or parsed.hostname not in {"localhost", "127.0.0.1"}
                or parsed.netloc != request.headers.get("host")
            ):
                return JSONResponse({"detail": "Foreign browser origin denied"}, 403)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def bad_request(request, exc):
        return JSONResponse(
            {"detail": str(exc)},
            (
                409
                if any(
                    code in str(exc)
                    for code in ("CONFLICT", "BUSY", "RUNNING", "FINISHED")
                )
                else 400
            ),
        )

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "Entity not found"}, 404)

    def store(request):
        return request.app.state.store

    def tasks(request):
        return request.app.state.tasks

    @app.get("/api/host/status")
    def host_status():
        from workspace.app import desktop_host

        return desktop_host.status()

    @app.post("/api/host/{action}")
    def host_action(action: str, request: Request, payload: dict | None = None):
        from workspace.app import desktop_host

        names = {
            "open-main": "show_main",
            "show-avatar": "show_avatar",
            "choose-folder": "choose_folder",
            "show-mini": "show_mini",
            "hide-mini": "hide_mini",
            "exit": "exit",
        }
        if action not in names:
            raise HTTPException(404, "Unknown desktop action")
        return desktop_host.invoke(names[action], payload)

    @app.get("/api/health")
    def health():
        return {
            "ok": True,
            "product": "Local AI Workspace",
            "version": VERSION,
            "schema_version": SCHEMA_VERSION,
            "sdk_version": "0.2.2rc2",
        }

    @app.get("/api/bootstrap")
    def bootstrap(request: Request):
        db = store(request)
        return {
            "version": VERSION,
            "projects": db.rows(
                "SELECT p.*, COALESCE(s.source, 'folder') AS source, COALESCE(s.url, '') AS github_url FROM projects p LEFT JOIN project_sources s ON p.id=s.project_id WHERE p.id!='__direct_chats__' ORDER BY p.created_at DESC"
            ),
            "providers": db.rows(
                "SELECT id,name,base_url,model,(api_key!='') AS api_key_configured FROM providers"
            ),
            "tasks": db.rows(
                "SELECT id,CASE WHEN project_id='__direct_chats__' THEN NULL ELSE project_id END AS project_id,provider_id,title,status,revision,updated_at,COALESCE(json_extract(details,'$.mode'),'agent') AS mode FROM tasks ORDER BY created_at DESC"
            ),
            "connectors": db.rows(
                "SELECT id,name,base_url,allow_write,(api_key!='') AS api_key_configured FROM connectors"
            ),
        }

    @app.post("/api/projects")
    def project(payload: ProjectInput, request: Request):
        from workspace.app.projects import create_project

        return create_project(
            store(request), payload.name, payload.path, payload.github_url
        )

    @app.post("/api/providers")
    def provider(payload: ProviderInput, request: Request):
        base = local_url(payload.base_url)
        if not urlsplit(base).path.rstrip("/").endswith("/v1"):
            raise ValueError("Runtime provider URL must end with /v1.")
        db = store(request)
        value = identifier()
        with db.transaction() as connection:
            connection.execute(
                "INSERT INTO providers VALUES(?,?,?,?,?)",
                (value, payload.name, base, payload.model, payload.api_key),
            )
        return {
            "id": value,
            "name": payload.name,
            "base_url": base,
            "model": payload.model,
            "api_key_configured": bool(payload.api_key),
        }

    @app.post("/api/providers/probe")
    def probe(payload: ProviderInput):
        client = RuntimeClient(payload.model_dump())
        models = client.request("GET", "models")
        result = {
            "models": models.get("data", []),
            "ready": client.request("GET", "runtime/readiness"),
        }
        if payload.model:
            result["capabilities"] = client.capabilities()
        return result

    @app.post("/api/providers/{provider_id}")
    def update_provider(provider_id: str, payload: ProviderInput, request: Request):
        old = store(request).one("providers", provider_id)
        base = local_url(payload.base_url)
        if not urlsplit(base).path.rstrip("/").endswith("/v1"):
            raise ValueError("Адрес Runtime должен заканчиваться на /v1.")
        with store(request).transaction() as db:
            db.execute(
                "UPDATE providers SET name=?,base_url=?,model=?,api_key=? WHERE id=?",
                (
                    payload.name,
                    base,
                    payload.model,
                    payload.api_key or old["api_key"],
                    provider_id,
                ),
            )
        return {
            "id": provider_id,
            "name": payload.name,
            "base_url": base,
            "model": payload.model,
        }

    @app.post("/api/providers/{provider_id}/probe")
    def probe_saved_provider(
        provider_id: str, payload: ProviderInput, request: Request
    ):
        old = store(request).one("providers", provider_id)
        client = RuntimeClient(
            {**payload.model_dump(), "api_key": payload.api_key or old["api_key"]}
        )
        return {
            "models": client.request("GET", "models").get("data", []),
            "ready": client.request("GET", "runtime/readiness"),
        }

    @app.post("/api/chats")
    @app.post("/api/tasks")
    def create_task(payload: TaskInput, request: Request):
        result = tasks(request).create(
            payload.project_id,
            payload.provider_id,
            payload.prompt,
            payload.action_id,
            payload.permission,
            payload.title,
            payload.connector_ids,
            mode=(
                payload.mode
                if "mode" in payload.model_fields_set
                else ("chat" if request.url.path == "/api/chats" else "agent")
            ),
            memory=payload.memory,
        )

        if result["project_id"] == "__direct_chats__":
            result["project_id"] = None
        return result

    @app.get("/api/chats/{task_id}")
    @app.get("/api/tasks/{task_id}")
    def task(task_id: str, request: Request):
        db = store(request)
        value = db.one("tasks", task_id)
        if value["project_id"] == "__direct_chats__":
            value["project_id"] = None
        approvals = db.rows(
            "SELECT * FROM approvals WHERE task_id=? AND status='pending' ORDER BY created_at",
            (task_id,),
        )
        value["approvals"] = [
            {**row, "payload": json.loads(row["payload"])} for row in approvals
        ]
        return value

    @app.get("/api/chats/{task_id}/events")
    @app.get("/api/tasks/{task_id}/events")
    def events(task_id: str, request: Request, after: int = 0):
        db = store(request)
        db.one("tasks", task_id)
        rows = db.rows(
            "SELECT seq,source_key,kind,created_at,payload FROM events WHERE task_id=? AND seq>? ORDER BY seq LIMIT 500",
            (task_id, max(0, after)),
        )
        return {
            "events": [{**row, "payload": json.loads(row["payload"])} for row in rows],
            "cursor": rows[-1]["seq"] if rows else after,
        }

    @app.get("/api/chats/{task_id}/messages")
    def chat_messages(task_id: str, request: Request, version: int = -1):
        from workspace.app.conversation_messages import MESSAGE_FILTER, project_messages

        db = store(request)
        db.one("tasks", task_id)
        current = db.rows(
            "SELECT COALESCE(MAX(seq),0) AS version FROM events WHERE task_id=? AND "
            + MESSAGE_FILTER,
            (task_id,),
        )[0]["version"]
        if version == current:
            return {"version": current, "messages": None}
        rows = db.rows(
            "SELECT seq,kind,created_at,payload FROM events WHERE task_id=? AND "
            + MESSAGE_FILTER
            + " ORDER BY seq",
            (task_id,),
        )
        return {"version": current, "messages": project_messages(rows)}

    @app.post("/api/chats/{task_id}/start")
    @app.post("/api/tasks/{task_id}/start")
    def start(task_id: str, payload: TaskAction, request: Request):
        return tasks(request).start(
            task_id, payload.revision, workspace_url=str(request.base_url)
        )

    @app.post("/api/chats/{task_id}/stop")
    @app.post("/api/tasks/{task_id}/stop")
    def stop(task_id: str, payload: TaskAction, request: Request):
        return tasks(request).stop(task_id, payload.revision)

    @app.post("/api/chats/{task_id}/restore")
    @app.post("/api/tasks/{task_id}/restore")
    def restore(task_id: str, payload: TaskAction, request: Request):
        return tasks(request).start(
            task_id,
            payload.revision,
            restore_only=True,
            workspace_url=str(request.base_url),
        )

    @app.post("/api/chats/{task_id}/continue")
    @app.post("/api/tasks/{task_id}/continue")
    def followup(task_id: str, payload: Followup, request: Request):
        current = store(request).one("tasks", task_id)
        if current["status"] in {"interrupted", "uncertain"}:
            raise ValueError(
                "Restore and review canonical history before sending a new instruction."
            )
        return tasks(request).start(
            task_id,
            payload.revision,
            new_prompt=payload.prompt,
            memory=payload.memory,
            workspace_url=str(request.base_url),
        )

    @app.post("/api/chats/{task_id}/reconcile")
    @app.post("/api/tasks/{task_id}/reconcile")
    def reconcile_task(task_id: str, request: Request):
        current = store(request).one("tasks", task_id)
        if current["details"].get("mode") == "chat":
            return {"source": "workspace", "replayed": False}
        return reconcile(store(request), current)

    @app.get("/api/chats/{task_id}/diff")
    @app.get("/api/tasks/{task_id}/diff")
    def diff(task_id: str, request: Request):
        return tasks(request).diff(task_id)

    @app.post("/api/approvals/{approval_id}")
    def decide(approval_id: str, payload: dict, request: Request):
        return tasks(request).decide(approval_id, payload)

    @app.post("/api/snapshots")
    def snapshot(request: Request):
        return tasks(request).snapshot()

    @app.post("/api/connectors")
    def add_connector(payload: ConnectorInput, request: Request):
        db = store(request)
        key = identifier()
        url = local_url(payload.base_url)
        with db.transaction() as connection:
            connection.execute(
                "INSERT INTO connectors VALUES(?,?,?,?,?)",
                (key, payload.name, url, payload.api_key, int(payload.allow_write)),
            )
        return {
            "id": key,
            "name": payload.name,
            "base_url": url,
            "allow_write": payload.allow_write,
        }

    @app.get("/api/connectors/{connector_id}/items")
    def connector_read(connector_id: str, request: Request):
        from workspace.app.connectors import read_items

        return read_items(store(request).one("connectors", connector_id))

    @app.post("/api/connectors/{connector_id}/items")
    def connector_write(connector_id: str, payload: ConnectorWrite, request: Request):
        from workspace.app.connectors import write_item

        return write_item(store(request), connector_id, payload.model_dump())

    @app.post("/api/chats/{task_id}/mcp")
    @app.post("/api/tasks/{task_id}/mcp")
    def connector_mcp(task_id: str, payload: dict, request: Request):
        from workspace.app.connector_mcp import handle

        result = handle(
            tasks(request),
            task_id,
            request.headers.get("X-Workspace-Task-Token", ""),
            payload,
        )
        return (
            JSONResponse(result, status_code=200)
            if result is not None
            else JSONResponse(None, status_code=202)
        )

    frontend = (
        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        / "workspace_frontend"
    )
    if not frontend.is_dir():
        frontend = Path(__file__).resolve().parents[1] / "frontend" / "dist"

    @app.get("/{path:path}", include_in_schema=False)
    def ui(path: str):
        if path.startswith(("api/", "v1/")):
            raise HTTPException(404, "Unknown API route")
        file = (frontend / path).resolve()
        if not file.is_relative_to(frontend.resolve()):
            raise HTTPException(404, "Not found")
        target = file if file.is_file() else frontend / "index.html"
        if not target.is_file():
            return JSONResponse(
                {"detail": "Build workspace/frontend to open the Workspace UI."}, 503
            )
        return FileResponse(target, headers={"Cache-Control": "no-cache"})

    return app


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    path: str = Field(default="", max_length=4096)
    github_url: str = Field(default="", max_length=500)


class ProviderInput(BaseModel):
    name: str = Field(default="Local AI Runtime", max_length=120)
    base_url: str = "http://127.0.0.1:8765/v1"
    model: str = ""
    api_key: str = ""


class TaskInput(BaseModel):
    project_id: str | None = None
    provider_id: str
    prompt: str = Field(min_length=1, max_length=100000)
    title: str = ""
    mode: str = "agent"
    memory: bool = True
    action_id: str = Field(min_length=16, max_length=128)
    permission: str = "read-only"
    connector_ids: list[str] = Field(default_factory=list, max_length=16)


class TaskAction(BaseModel):
    revision: int = Field(ge=1)


class Followup(TaskAction):
    memory: bool | None = None
    prompt: str = Field(min_length=1, max_length=100000)


class ConnectorInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: str
    api_key: str = ""
    allow_write: bool = False


class ConnectorWrite(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(max_length=20000)
    action_id: str = Field(min_length=16, max_length=128)
    approved: bool = False


app = create_app()
