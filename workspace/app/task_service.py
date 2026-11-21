"""Task lifecycle, one owned SDK process, durable receipts and no automatic retry."""

from __future__ import annotations
import contextlib
import json
import os
import secrets
import shutil
import sys
import threading
from pathlib import Path
from workspace.app.store import ACTIVE, SCHEMA_VERSION, identifier, now
from workspace.app.runtime_client import RuntimeClient
from workspace.app.owned_process import OwnedProcess
from workspace.app import history, project_diff
from workspace.app.control_access import UI_TOKEN_ENV
from workspace.app.dsh_profile import agent_limits
from workspace.app.plugin_execution import PluginGateway
from workspace.app.agent_plugins import AgentPluginContext


class TaskService:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.processes = {}
        self.runtime_tokens = {}
        self.connector_tokens = {}
        self.results = {}
        self.connector_waiters = {}
        self.connector_discoveries = {}
        self.plugin_gateway = PluginGateway(store)
        self.agent_plugins = {}

    def create(
        self,
        project_id,
        provider_id,
        prompt,
        action_id,
        permission="read-only",
        title="",
        connector_ids=None,
        mode="agent",
        memory=True,
    ):
        if permission not in {"read-only", "workspace-write"}:
            raise ValueError("Unsupported permission preset.")
        if mode not in {"chat", "agent"}:
            raise ValueError("Unknown conversation mode.")
        if mode == "chat" and connector_ids:
            raise ValueError("Application tools require file-agent mode.")
        if not project_id:
            if mode == "agent":
                raise ValueError("Для доступа к файлам сначала подключите проект.")
            project_id = "__direct_chats__"
            with self.store.transaction() as db:
                db.execute(
                    "INSERT OR IGNORE INTO projects VALUES(?,?,?,?)",
                    (
                        project_id,
                        "Диалоги без проекта",
                        str(self.store.root / "chat-files"),
                        now(),
                    ),
                )
        self.store.one("projects", project_id)
        self.store.one("providers", provider_id)
        for connector_id in connector_ids or []:
            self.store.one("connectors", connector_id)
        with self.store.transaction() as db:
            existing = self.store.rows(
                "SELECT id FROM tasks WHERE action_id=?", (action_id,)
            )
            if existing:
                current = self.store.one("tasks", existing[0]["id"])
                if (
                    current["project_id"],
                    current["provider_id"],
                    current["prompt"],
                    current["permission"],
                    current["details"].get("connector_ids", []),
                    current["details"].get("mode", "agent"),
                    current["details"].get("memory", True),
                ) != (
                    project_id,
                    provider_id,
                    prompt,
                    permission,
                    list(dict.fromkeys(connector_ids or [])),
                    mode,
                    memory,
                ):
                    raise ValueError(
                        "ACTION_CONFLICT: this action ID already describes a different task."
                    )
                return current
            task_id = identifier()
            stamp = now()
            db.execute(
                "INSERT INTO tasks(id,project_id,provider_id,title,prompt,status,session_id,created_at,updated_at,permission,action_id,details) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    project_id,
                    provider_id,
                    title or prompt[:80],
                    prompt,
                    "queued",
                    "workspace-" + task_id,
                    stamp,
                    stamp,
                    permission,
                    action_id,
                    json.dumps(
                        {
                            "mode": mode,
                            "memory": memory,
                            "connector_ids": list(dict.fromkeys(connector_ids or [])),
                        }
                    ),
                ),
            )
        self.store.event(
            task_id,
            "task_created",
            {"prompt": prompt, "permission": permission},
            "created",
        )
        return self.store.one("tasks", task_id)

    def start(
        self,
        task_id,
        expected,
        *,
        restore_only=False,
        new_prompt=None,
        workspace_url=None,
        memory=None,
        skills=None,
        attachments=(),
    ):
        with self.lock:
            if self.processes:
                raise ValueError("WORKSPACE_BUSY: one root task can run at a time.")
            task = self.store.one("tasks", task_id)
            if task["revision"] != expected:
                raise ValueError("TASK_CONFLICT: refresh before starting.")
            status = task["status"]
            if status in ACTIVE:
                raise ValueError("TASK_ALREADY_RUNNING")
            initial = status in {"queued", "retryable"}
            if not restore_only and status in {"interrupted", "uncertain"}:
                raise ValueError(
                    "Restore and review canonical history before sending a new instruction."
                )
            if new_prompt is not None and not new_prompt.strip():
                raise ValueError("Введите текст сообщения.")
            if not initial and not restore_only and new_prompt is None:
                raise ValueError(
                    "RECOVERY_REQUIRES_RECONCILIATION: restore history, then explicitly send a new instruction."
                )
            prompt = (
                new_prompt
                if new_prompt is not None
                else task["details"].get("initial_prompt", task["prompt"])
            )
            if task["details"].get("mode") == "chat":
                if restore_only:
                    return self.store.update_task(
                        task_id,
                        "restored",
                        {"partial": "", "error": "", "replayed": False},
                    )
                return self._start_chat(task, prompt, memory, skills, attachments)
            from workspace.app.chat_documents import resolve

            agent_documents = resolve(self.store, task_id, attachments)
            session = history.reconcile(self.store, task)
            resume = session["session_exists"]
            if initial and resume and not restore_only:
                self.store.update_task(task_id, "uncertain")
                raise ValueError("Найдена сохранённая сессия. Сначала восстановите историю.")
            if restore_only and not resume:
                if self._legacy_startup_failure(task):
                    return self.store.update_task(
                        task_id,
                        "retryable",
                        {
                            "initial_prompt": task["prompt"],
                            "session_opened": False,
                            "input_dispatched": False,
                            "error": "Исправьте параметры модели и повторите отправку.",
                        },
                    )
                raise ValueError(
                    "Сохранённая сессия не найдена. Восстановить историю этого запуска нельзя."
                )
            if not initial and not resume:
                raise ValueError("Продолжение требует сохранённой сессии.")
            provider = self.store.one("providers", task["provider_id"])
            client = RuntimeClient(provider)
            caps = client.capabilities(load=True)
            agent_limits(caps)
            project = self.store.one("projects", task["project_id"])
            project_path = Path(project["path"]).resolve()
            workspace_data = self.store.root.resolve()
            if workspace_data.is_relative_to(project_path) or any(
                project_path.is_relative_to(workspace_data / directory)
                for directory in ("tasks", "snapshots", "logs")
            ):
                raise ValueError(
                    "Выберите папку проекта отдельно от служебных данных Workspace."
                )
            task_dir = self.store.root / "tasks" / task_id
            task_dir.mkdir(parents=True, exist_ok=True)
            baseline = task_dir / "baseline"
            if not (baseline / "manifest.json").is_file():
                self.store.update_task(
                    task_id,
                    details={"baseline": project_diff.capture(project_path, baseline)},
                )
            token = secrets.token_urlsafe(32)
            config = {
                "provider": {
                    "base_url": provider["base_url"],
                    "model": provider["model"],
                    "api_key": provider["api_key"],
                },
                "capabilities": caps,
                "owner_id": task_id,
                "owner_token": token,
                "session_id": task["session_id"],
                "dsh_home": str(task_dir / "dsh"),
                "project_path": str(project_path),
                "prompt": prompt,
                "permission": task["permission"],
                "resume": resume,
                "restore_only": restore_only,
            }
            connector_token = None
            installed = self.store.plugins.credentials_snapshot({
                key: item for key, item in self.store.plugins.definitions().items() if item and item["enabled"]
            })
            if task["details"].get("connector_ids") or installed:
                if not workspace_url:
                    raise ValueError(
                        "Workspace HTTP endpoint is required for application tools."
                    )
                config["connector_endpoint"] = (
                    workspace_url.rstrip("/") + "/api/tasks/" + task_id + "/mcp"
                )
                connector_token = secrets.token_urlsafe(32)
                config["connector_token"] = connector_token
            command = (
                [
                    str(
                        Path(sys.executable).parent
                        / "agent"
                        / "LocalAIWorkspaceAgent.exe"
                    )
                ]
                if getattr(sys, "frozen", False)
                else [sys.executable, "-m", "workspace.app.dsh_runner"]
            )
            env = os.environ.copy()
            env.pop(UI_TOKEN_ENV, None)
            if getattr(sys, "frozen", False):
                for key in list(env):
                    if key.startswith("_PYI_") or key.startswith("PYINSTALLER_"):
                        env.pop(key, None)
                env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            root = Path(__file__).resolve().parents[2]
            self.results.pop(task_id, None)
            details = {
                "restoring": restore_only,
                "session_opened": resume,
                "input_dispatched": False,
                "error": "",
            }
            if initial:
                details["initial_prompt"] = prompt
            self.store.update_task(task_id, "starting", details)
            self.runtime_tokens[task_id] = token
            if connector_token:
                self.connector_tokens[task_id] = connector_token
                self.agent_plugins[task_id] = AgentPluginContext(installed, {d["id"] for d in agent_documents})
            try:
                process = OwnedProcess(
                    command,
                    str(root),
                    env,
                    lambda event: self._event(task_id, event),
                    lambda code, error: self._exit(task_id, code, error),
                )
            except Exception as exc:
                self.runtime_tokens.pop(task_id, None)
                self.connector_tokens.pop(task_id, None)
                self.agent_plugins.pop(task_id, None)
                self.store.update_task(
                    task_id,
                    "retryable" if not resume else "uncertain",
                    {"error": str(exc)},
                )
                raise
            self.processes[task_id] = process
            self.store.event(
                task_id,
                "input_admitted",
                {
                    "prompt": prompt if not restore_only else None,
                    "restore_only": restore_only,
                    "replayed": False,
                },
            )
            try:
                process.send(config)
            except Exception as exc:
                self.results[task_id] = {"kind": "startup_failed", "message": str(exc)}
                process.abort()
                raise
        return self.store.one("tasks", task_id)

    def _legacy_startup_failure(self, task):
        if task["status"] not in {"uncertain", "interrupted"}:
            return False
        rows = self.store.rows(
            "SELECT kind,payload FROM events WHERE task_id=? AND kind IN ('started','dsh','diagnostic')",
            (task["id"],),
        )
        # Older adapters reported profile validation failures only on stderr.
        if any(row["kind"] != "diagnostic" for row in rows):
            return False
        return any(
            json.loads(row["payload"])
            .get("message", "")
            .startswith(
                (
                    "ValueError: MODEL_CONTEXT_TOO_SMALL:",
                    "ValueError: MODEL_TOOLS_UNAVAILABLE:",
                )
            )
            for row in rows
        )

    def _start_chat(self, task, prompt, memory, skills=None, attachments=()):
        from workspace.app.chat_run import ChatRun
        from workspace.app.desktop_host import activity
        from workspace.app.skills import SkillUse, all_definitions, prepare_skills
        from workspace.app.chat_documents import active_documents, resolve

        task_id = task["id"]
        remember = task["details"].get("memory", True) if memory is None else memory
        definitions = all_definitions(self.store)
        if skills is None:
            ids = task["details"].get("skill_ids")
            if ids is None:
                old = task["details"].get("skill_id")
                ids = [old] if old else [key for key, item in definitions.items() if item and "product" not in item]
            sections = task["details"].get("skill_disabled_sections", {})
            ids = [key for key in ids if definitions.get(key) is not None and definitions[key].get("enabled", True)]
            ids = list(dict.fromkeys(ids + [key for key, item in definitions.items()
                                          if item and "product" in item and item["enabled"]]))
            skills = [SkillUse(id=key, disabled_sections=sections.get(key, [])) for key in ids]
        automatic = not any(s.attachment or s.attachments for s in skills)
        if not automatic and attachments:
            raise ValueError("Передайте файлы в сообщении или в явном вызове навыка.")
        new_documents = resolve(self.store, task_id, attachments)
        documents = active_documents(self.store, task_id, new_documents, prompt, remember) if automatic else []
        contexts, selection = prepare_skills(self.store, task, skills, automatic=automatic,
                                             reuse=not automatic or (remember and not documents), definitions=definitions)
        token = secrets.token_urlsafe(32)
        self.runtime_tokens[task_id] = token
        self.store.update_task(
            task_id, "running", {"partial": "", "error": "", "memory": remember, **selection}
        )

        def finished(status, error):
            with self.lock:
                self.store.update_task(task_id, status, {"partial": "", "error": error})
                self.processes.pop(task_id, None)
                self.runtime_tokens.pop(task_id, None)
                activity("error" if status == "failed" else "idle")

        run = ChatRun(
            self.store,
            task,
            self.store.one("providers", task["provider_id"]),
            token,
            prompt,
            remember,
            finished,
            skill_contexts=contexts,
            automatic=automatic, documents=documents, new_documents=new_documents,
            plugin_gateway=self.plugin_gateway,
        )
        self.processes[task_id] = run
        activity("running")
        run.start()
        return self.store.one("tasks", task_id)

    def _event(self, task_id, event):
        from workspace.app.desktop_host import activity

        kind = event.get("kind", "unknown")
        key = None
        if kind == "notification" and event.get("method") == "session.event":
            payload = event.get("payload") or {}
            item = payload.get("event") or {}
            if "seq" in item:
                key = f"dsh:{payload.get('sessionId')}:{item['seq']}"
                self.store.event(task_id, "dsh", item, key)
                return
        if kind in {"approval", "question"}:
            activity("awaiting_permission")
            approval_id = identifier()
            with self.store.transaction() as db:
                db.execute(
                    "INSERT INTO approvals VALUES(?,?,?,?,?,?,?)",
                    (
                        approval_id,
                        task_id,
                        kind,
                        json.dumps(event, ensure_ascii=False),
                        "pending",
                        now(),
                        None,
                    ),
                )
            self.store.update_task(task_id, "awaiting_permission")
            event = {**event, "approval_id": approval_id}
        elif kind == "started":
            with self.lock:
                current = self.store.one("tasks", task_id)
                status = "running" if current["status"] == "starting" else None
                self.store.update_task(task_id, status, {"session_opened": True})
                if status:
                    activity(status)
        elif kind == "input_dispatching":
            self.store.update_task(task_id, details={"input_dispatched": True})
        elif kind == "identity":
            self.store.update_task(task_id, details={"adapter": event})
        elif kind in {"result", "uncertain", "restored", "startup_failed"}:
            self.results[task_id] = event
        self.store.event(task_id, kind, event, key)

    def _exit(self, task_id, code, error):
        from workspace.app.desktop_host import activity

        activity("idle")
        with self.lock:
            self.runtime_tokens.pop(task_id, None)
            self.connector_tokens.pop(task_id, None)
            plugin_context = self.agent_plugins.pop(task_id, None)
            if plugin_context:
                plugin_context.stopped.set()
            self.connector_discoveries.pop(task_id, None)
            current = self.store.one("tasks", task_id)
            result = self.results.pop(task_id, None)
            if current["status"] == "stopping":
                status = "cancelled"
            elif (
                code != 0
                or error
                or result is None
                or result.get("kind") in {"uncertain", "startup_failed"}
            ):
                status = "uncertain"
            elif result["kind"] == "restored":
                status = "restored"
            else:
                reason = result.get("finish_reason")
                status = (
                    "completed"
                    if reason in {"completed", "stop", "end_turn"}
                    else "failed"
                )
            details = {
                "exit_code": code,
                "observer_error": error,
                "result": result,
                "automatic_retry": False,
            }
            try:
                details["history"] = history.reconcile(self.store, current)
                session_exists = details["history"]["session_exists"]
                if (status == "restored" and not session_exists) or (
                    status == "completed" and not details["history"]["canonical_events"]
                ):
                    details["history_error"] = "DSH canonical history is missing."
                    status = "uncertain"
                if (
                    status == "uncertain"
                    and not error
                    and result
                    and result["kind"] == "startup_failed"
                    and not session_exists
                    and not current["details"].get("session_opened")
                    and not current["details"].get("input_dispatched")
                ):
                    status = "retryable"
                    details["error"] = result["message"]
            except Exception as exc:
                details["history_error"] = str(exc)
                status = "uncertain"
            self.store.update_task(task_id, status, details)
            self.store.event(
                task_id,
                "process_exited",
                {"status": status, "exit_code": code, "automatic_retry": False},
            )
            self.processes.pop(task_id, None)
            self._cancel_connector_waiters(task_id)
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE approvals SET status='cancelled',decided_at=? WHERE task_id=? AND status IN ('pending','granted')",
                    (now(), task_id),
                )

    def decide(self, approval_id, value):
        with self.lock, self.store.transaction() as db:
            approval = self.store.one("approvals", approval_id)
            if approval["status"] != "pending":
                raise ValueError(
                    "APPROVAL_FINISHED: one-operation decisions cannot be reused."
                )
            process = self.processes.get(approval["task_id"])
            current = self.store.one("tasks", approval["task_id"])
            if process is None or current["status"] == "stopping":
                raise ValueError("TASK_NOT_RUNNING")
            if approval["kind"] in {"approval", "connector"} and value.get(
                "outcome"
            ) not in {"allowed-once", "rejected", "cancelled"}:
                raise ValueError("Invalid approval decision")
            waiter = None
            status = "answered"
            if approval["kind"] == "connector":
                waiter = self.connector_waiters.get(approval_id)
                if waiter is None:
                    raise ValueError(
                        "APPROVAL_FINISHED: connector request is no longer waiting."
                    )
                status = (
                    "granted"
                    if value["outcome"] == "allowed-once"
                    else value["outcome"]
                )
            db.execute(
                "UPDATE approvals SET status=?,decided_at=? WHERE id=?",
                (status, now(), approval_id),
            )
            self.store.event(
                approval["task_id"],
                "permission_decision",
                {"approval_id": approval_id, "value": value},
            )
            if waiter is not None:
                self.store.update_task(approval["task_id"], "running")
                waiter["value"] = value
                waiter["event"].set()
                return {"accepted": True}
            try:
                process.send(
                    {"kind": "answer", "id": approval["payload"]["id"], "value": value}
                )
            except Exception:
                self.store.update_task(
                    approval["task_id"],
                    "uncertain",
                    {
                        "error": "Permission response transport failed; no automatic resend."
                    },
                )
                raise
            self.store.update_task(approval["task_id"], "running")
        return {"accepted": True}

    def stop(self, task_id, expected=None):
        with self.lock:
            task = self.store.one("tasks", task_id)
            if expected is not None and task["revision"] != expected:
                raise ValueError("TASK_CONFLICT: refresh before stopping.")
            process = self.processes.get(task_id)
            token = self.runtime_tokens.get(task_id)
            if process is None:
                return {"confirmed": True, "active": False}
            self.store.update_task(task_id, "stopping")
            self._cancel_connector_waiters(task_id)
            if context := self.agent_plugins.get(task_id):
                context.stopped.set()
        result = process.stop()
        cancellation = {"cancelled": 0}
        if token:
            try:
                cancellation = RuntimeClient(
                    self.store.one("providers", task["provider_id"])
                ).cancel_owner(task_id, token)
            except Exception as exc:
                cancellation = {"error": str(exc), "scope": "only this task owner"}
        self.store.event(
            task_id, "stop_confirmed", {**result, "runtime_request": cancellation}
        )
        return result

    def _cancel_connector_waiters(self, task_id):
        for waiter in self.connector_waiters.values():
            if waiter["task_id"] == task_id:
                waiter["value"] = {"outcome": "cancelled"}
                waiter["event"].set()

    def approve_connector(self, task_id, payload):
        approval_id = identifier()
        waiter = {
            "task_id": task_id,
            "action_id": payload["action_id"],
            "event": threading.Event(),
            "value": None,
        }
        with self.lock, self.store.transaction() as db:
            current = self.store.one("tasks", task_id)
            if task_id not in self.processes or current["status"] not in {
                "running",
                "awaiting_permission",
            }:
                raise ValueError("TASK_NOT_RUNNING")
            if any(
                w["action_id"] == payload["action_id"]
                for w in self.connector_waiters.values()
            ):
                raise ValueError(
                    "CONNECTOR_APPROVAL_PENDING: the operation is already awaiting a decision."
                )
            self.connector_waiters[approval_id] = waiter
            db.execute(
                "INSERT INTO approvals VALUES(?,?,?,?,?,?,?)",
                (
                    approval_id,
                    task_id,
                    "connector",
                    json.dumps({"payload": payload}, ensure_ascii=False),
                    "pending",
                    now(),
                    None,
                ),
            )
            self.store.update_task(task_id, "awaiting_permission")
            self.store.event(
                task_id,
                "connector_write_proposed",
                {"approval_id": approval_id, **payload},
            )
        try:
            if (
                waiter["event"].wait(300)
                and (waiter["value"] or {}).get("outcome") == "allowed-once"
            ):
                return approval_id
            return None
        finally:
            with self.lock, self.store.transaction() as db:
                self.connector_waiters.pop(approval_id, None)
                db.execute(
                    "UPDATE approvals SET status='cancelled',decided_at=? WHERE id=? AND status='pending'",
                    (now(), approval_id),
                )

    def diff(self, task_id):
        task = self.store.one("tasks", task_id)
        project = self.store.one("projects", task["project_id"])
        baseline = self.store.root / "tasks" / task_id / "baseline"
        return (
            project_diff.compare(Path(project["path"]), baseline)
            if (baseline / "manifest.json").is_file()
            else {
                "files": [],
                "diff": "",
                "baseline_available": False,
                "complete": False,
            }
        )

    def close(self):
        errors = []
        for task_id in list(self.processes):
            try:
                self.stop(task_id)
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError(
                "Owned task shutdown could not be confirmed: " + "; ".join(errors)
            )

    def snapshot(self):
        with self.lock:
            if self.processes:
                raise ValueError(
                    "Дождитесь ответа или остановите его перед созданием резервной копии."
                )
            target = self.store.snapshot(self.store.root / "snapshots" / identifier())
            source = self.store.root / "tasks"
            if source.exists():
                shutil.copytree(source, target / "tasks")
            preferences = self.store.root / "mini-helper.json"
            if preferences.is_file():
                shutil.copy2(preferences, target / preferences.name)
            return {
                "path": str(target),
                "schema_version": SCHEMA_VERSION,
                "dsh_version": "0.2.2rc2",
                "includes_sessions": True,
            }
