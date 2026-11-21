"""Validate and execute task-scoped calls on the Workspace side of a plugin."""

import asyncio
import base64
import json
import os
import sqlite3
import threading
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from hashlib import sha256
from urllib.parse import quote

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from jsonschema.validators import validator_for
from referencing import Registry
from referencing.exceptions import Unresolvable

from workspace.app.chat_documents import for_tool
from workspace.app.plugin_manifest import parse_json
from workspace.app.plugin_mcp import McpError, Session, StreamableMcp
from workspace.app.skill_resources import MAX_OUTPUT_BYTES, MAX_OUTPUT_FILES, read_resources, save_resources
from workspace.app.skills import MAX_FILE, MAX_RESULT, attachment_name
from workspace.app.store import identifier, now


TOTAL_INPUT = 48 * 1024 * 1024
MAX_ARGUMENT_BYTES = 256_000
CALL_STEPS = 12


def timeout_setting(name, default, upper):
    value = float(os.getenv("LOCAL_AI_MCP_" + name + "_SECONDS", str(default)))
    if not 0 < value <= upper:
        raise ValueError(f"LOCAL_AI_MCP_{name}_SECONDS должен быть в диапазоне (0, {upper}].")
    return value


def validator(schema):
    if not isinstance(schema, dict):
        raise McpError("schema", "MCP не объявил корректную схему инструмента.")
    protocol = validator_for(schema, default=None) if "$schema" in schema else Draft202012Validator
    if protocol is None:
        raise McpError("schema", "MCP объявил неподдерживаемую версию JSON Schema.")
    try:
        protocol.check_schema(schema)
    except SchemaError as exc:
        raise McpError("schema", "MCP объявил некорректную JSON Schema.") from exc
    # Registry without retrieval resolves local references and known meta-schemas only.
    return protocol(schema, registry=Registry())


def validate(schema_validator, value, category="schema"):
    try:
        schema_validator.validate(value)
    except ValidationError as exc:
        field = ".".join(map(str, exc.absolute_path)) or "arguments"
        missing = [k for k in exc.validator_value if k not in exc.instance] if exc.validator == "required" else []
        detail = f"не хватает полей {', '.join(missing)}" if missing else f"нарушено условие {exc.validator}"
        raise McpError(category, f"{field} — {detail}.") from exc
    except (Unresolvable, RecursionError) as exc:
        raise McpError(category, "Схема MCP ссылается на недоступное или рекурсивное определение.") from exc


def _field(schema, name, datatype):
    part = schema.get("properties", {}).get(name)
    if not isinstance(part, dict):
        raise McpError("schema", f"MCP не объявил поле «{name}» для передачи файла.")
    allowed = part.get("type")
    if (isinstance(allowed, list) and datatype not in allowed) or (isinstance(allowed, str) and allowed != datatype):
        raise McpError("schema", f"Поле «{name}» несовместимо с файловой привязкой {datatype}.")
    return part


def describe_dependencies(skill, tools):
    selected = {}
    for binding in skill["tools"]:
        name = binding["name"]
        if name not in tools:
            raise McpError("missing_tool", f"MCP не предоставляет инструмент «{name}».")
        tool = tools[name]
        schema = tool.get("inputSchema")
        check_input = validator(schema)
        if schema.get("type") != "object":
            raise McpError("schema", f"Входная схема «{name}» должна описывать объект.")
        hidden = _attachment_fields(binding, schema)
        check_output = validator(tool["outputSchema"]) if "outputSchema" in tool else None
        # The model sees only semantic arguments. Validation always uses the original schema.
        semantics = {"type": "object",
                     "properties": {k: v for k, v in schema.get("properties", {}).items() if k not in hidden},
                     "required": [k for k in schema.get("required", []) if k not in hidden],
                     "additionalProperties": schema.get("additionalProperties", True)}
        for key in ("$defs", "definitions"):
            if key in schema:
                semantics[key] = schema[key]
        description = tool.get("description")
        selected[name] = (binding, check_input, check_output, {
            "name": name,
            "description": description[:3000] if isinstance(description, str) else "",
            "arguments": semantics,
            "attachments": "one" if binding.get("file") else "many" if binding.get("files") else "none",
        })
    return selected


def _attachment_fields(binding, schema):
    mapping = binding.get("file") or binding.get("files")
    if not mapping:
        return set()
    if binding.get("files"):
        argument = mapping["argument"]
        elements = _field(schema, argument, "array").get("items")
        if not isinstance(elements, dict) or elements.get("type") not in ("object", None):
            raise McpError("schema", f"Поле «{argument}» должно быть массивом объектов вложений.")
        _field(elements, mapping["filename"], "string")
        _field(elements, mapping["base64"], "string")
        return {argument}
    fields = {mapping["filename"], mapping["base64"]}
    for name in fields:
        _field(schema, name, "string")
    return fields


def task_files(store, task_id):
    return store.rows(
        "SELECT id,name,length(data) AS size FROM skill_artifacts WHERE task_id=? ORDER BY rowid",
        (task_id,),
    )


def _arguments(store, task_id, skill, binding, schema, semantic, file_ids):
    if not isinstance(semantic, dict) or len(json.dumps(semantic, ensure_ascii=False).encode("utf-8")) > MAX_ARGUMENT_BYTES:
        raise McpError("schema", "Смысловые аргументы должны быть объектом JSON до 256 000 байт.")
    hidden = _attachment_fields(binding, schema.schema)
    if hidden.intersection(semantic):
        raise McpError("schema", "Аргументы включают поля для файлов, которые заполняет Workspace.")
    if (not isinstance(file_ids, list) or len(file_ids) > 128
            or any(not isinstance(key, str) for key in file_ids) or len(set(file_ids)) != len(file_ids)):
        raise McpError("attachments", "Укажите уникальные идентификаторы файлов текущей задачи.")
    if ((binding.get("file") and len(file_ids) != 1) or (binding.get("files") and not file_ids)
            or (not hidden and file_ids)):
        raise McpError("attachments", "Укажите ровно один файл." if binding.get("file") else
                       "Укажите непустой набор файлов." if binding.get("files") else "Инструмент не принимает файлы.")
    by_id = {item["id"]: item for item in task_files(store, task_id)}
    if any(key not in by_id for key in file_ids):
        raise McpError("attachments", "Выбранный файл отсутствует в текущей задаче.")
    files = [by_id[key] for key in file_ids]
    extensions = tuple(skill.get("accepts", []))
    for file in files:
        if extensions and not file["name"].casefold().endswith(extensions):
            raise McpError("attachments", f"«{file['name']}» — навык принимает {', '.join(extensions)}.")
        if not 0 < file["size"] <= MAX_FILE:
            raise McpError("attachments", f"«{file['name']}» — размер должен быть от 1 байта до 12 МиБ.")
    if sum(file["size"] for file in files) > TOTAL_INPUT:
        raise McpError("attachments", "Файлы одного вызова должны занимать до 48 МиБ.")
    arguments = dict(semantic)
    if hidden:
        mapping = binding.get("file") or binding["files"]
        inputs = [for_tool(store, task_id, file) for file in files]
        values = [{mapping["filename"]: entry.filename, mapping["base64"]: entry.base64} for entry in inputs]
        if binding.get("file"):
            arguments.update(values[0])
        else:
            arguments[mapping["argument"]] = values
    validate(schema, arguments)
    return arguments, [file["name"] for file in files]


@dataclass(frozen=True)
class PluginCall:
    id: str
    task_id: str
    agent_id: str
    turn_id: str
    skill: dict
    tool: str
    arguments: dict
    file_ids: list


class PluginGateway:
    def __init__(self, store, *, transport=None):
        self.store, self.transport = store, transport
        self.connect_timeout = timeout_setting("CONNECT", 4, 60)
        self.setup_timeout = timeout_setting("SETUP", 20, 120)
        self.tool_timeout = timeout_setting("TOOL", 900, 1800)
        self.locks, self.sessions, self.statuses = {}, {}, {}
        self.guard = threading.Lock()

    def _lock(self, product):
        with self.guard:
            return self.locks.setdefault(product, threading.Lock())

    @contextmanager
    def _queue(self, product, cancelled):
        lock = self._lock(product)
        while not lock.acquire(timeout=0.15):
            if cancelled and cancelled.is_set():
                raise McpError("cancelled", "Ожидание вызова MCP остановлено.")
        try:
            if cancelled and cancelled.is_set():
                raise McpError("cancelled", "Ожидание вызова MCP остановлено.")
            yield
        finally:
            lock.release()

    def _client(self, skill):
        product, mcp = skill["product"], skill["mcp"]
        key = skill.get("api_key", "")
        address = (mcp["url"], mcp.get("expectedServerName"), key)
        previous, session = self.sessions.get(product, (None, None))
        if address != previous:
            session = Session()
            self.sessions[product] = (address, session)
        return StreamableMcp(mcp["url"], key, session, connect_timeout=self.connect_timeout,
                             setup_timeout=self.setup_timeout, tool_timeout=self.tool_timeout,
                             transport=self.transport)

    def public_status(self, skill):
        status = self.statuses.get((skill["product"], skill["revision"]), {})
        blocked = self.store.rows("SELECT status FROM plugin_calls WHERE product=? AND status IN ('sent','uncertain')",
                                  (skill["product"],))
        return {"connection": status.get("connection", "unknown"), "message": status.get("message", ""),
                "compatibility": status.get("skills", {}).get(skill["name"], {"state": "unchecked"}),
                "paused": any(row["status"] == "uncertain" for row in blocked),
                "running": any(row["status"] == "sent" for row in blocked)}

    def recover(self, product):
        self.store.plugins.package(product)
        lock = self._lock(product)
        if not lock.acquire(blocking=False):
            raise ValueError("PLUGIN_BUSY: дождитесь окончания текущего вызова Workspace.")
        try:
            with self.store.transaction() as db:
                db.execute("UPDATE plugin_calls SET status='resolved',finished_at=? "
                           "WHERE product=? AND status IN ('sent','uncertain')", (now(), product))
            self.sessions.pop(product, None)
        finally:
            lock.release()

    def describe(self, skill, cancelled=None):
        if not skill["tools"]:
            return []
        with self._queue(skill["product"], cancelled):
            selected = self._run(self._discover(skill), cancelled)
        return [data[3] for data in selected.values()]

    async def _discover(self, skill, client=None):
        product, revision = skill["product"], skill["revision"]
        status = self.statuses.setdefault((product, revision), {"skills": {}})
        status.update(connection="connecting", message="")
        own = client is None
        client = client or self._client(skill)
        try:
            tools = await client.tools(skill["mcp"].get("expectedServerName"))
        except McpError as exc:
            status.update(connection="unavailable" if exc.category in {"offline", "timeout"} else "protocol_error",
                          message=str(exc))
            raise
        finally:
            if own:
                await client.close()
        status.update(connection="available", message="")
        try:
            selected = describe_dependencies(skill, tools)
        except McpError as exc:
            status["skills"][skill["name"]] = {"state": exc.category, "message": str(exc)}
            raise
        status["skills"][skill["name"]] = {"state": "compatible"}
        return selected

    @staticmethod
    def _run(coroutine, cancelled):
        async def run():
            action = asyncio.create_task(coroutine)
            try:
                while cancelled is None or not cancelled.is_set():
                    done, _ = await asyncio.wait({action}, timeout=0.15)
                    if done:
                        return action.result()
                if action.done():
                    return action.result()
                raise McpError("cancelled", "Ожидание MCP остановлено.")
            finally:
                if not action.done():
                    action.cancel()
                    with suppress(asyncio.CancelledError):
                        await action
        return asyncio.run(run())

    def invoke(self, call, cancelled=None):
        with self._queue(call.skill["product"], cancelled):
            try:
                return self._run(self._invoke(call), cancelled)
            except McpError as exc:
                if exc.category == "cancelled" and self.store.rows(
                    "SELECT 1 FROM plugin_calls WHERE id=? AND status='uncertain'", (call.id,)
                ):
                    raise McpError("uncertain", "Запрос остановлен в Workspace. Исход операции в приложении неизвестен, очередь приостановлена.") from exc
                raise

    async def _invoke(self, call):
        skill, tool = call.skill, call.tool
        product = skill["product"]
        if not self.store.plugins.enabled(product, skill["name"]):
            raise McpError("disabled", f"Навык «{skill['title']}» выключен.")
        status = self.public_status(skill)
        if status["paused"] or status["running"]:
            raise McpError("paused", "Исход предыдущей операции неизвестен. После проверки приложения возобновите очередь в настройках навыков.")
        if not isinstance(tool, str) or tool not in {definition["name"] for definition in skill["tools"]}:
            raise McpError("missing_tool", "Инструмент не входит в выбранный навык.")
        client = self._client(skill)
        watcher = None
        tracking = False
        try:
            selected = await self._discover(skill, client)
            if client.session.capabilities["tools"].get("listChanged"):
                generation = client.session.generation
                watcher = asyncio.create_task(client.watch_changes())
                await asyncio.sleep(0)
                if client.session.generation != generation:
                    selected = await self._discover(skill, client)
            binding, check_input, check_output, _ = selected[tool]
            arguments, filenames = _arguments(self.store, call.task_id, skill, binding, check_input,
                                              call.arguments, call.file_ids)
            if not self.store.plugins.enabled(product, skill["name"]):
                raise McpError("disabled", f"Навык «{skill['title']}» выключен.")
            with self.store.transaction() as db:
                db.execute("INSERT INTO plugin_calls(id,product,task_id,agent_id,skill_key,revision,tool,started_at,status) "
                           "VALUES(?,?,?,?,?,?,?,?,'sent')",
                           (call.id, product, call.task_id, call.agent_id, skill["key"], skill["revision"], tool, now()))
            tracking = True
            last_progress = -1.0

            def report_progress(params):
                nonlocal last_progress
                value = params.get("progress")
                if (params.get("progressToken") != call.id or type(value) not in (int, float)
                        or not last_progress < value < 10**12):
                    return
                last_progress = value
                total = params.get("total")
                self.store.event(call.task_id, "plugin_progress", {
                    "turn_id": call.turn_id, "call_id": call.id, "progress": value,
                    "total": total if type(total) in (int, float) and value <= total < 10**12 else None,
                })

            client.progress = report_progress
            number = client.session.number + 1
            try:
                reply = await client.rpc("tools/call", {"name": tool, "arguments": arguments,
                                                        "_meta": {"progressToken": call.id}})
            except asyncio.CancelledError:
                self._mark(call.id, "uncertain")
                await client.cancel(number)
                raise
            except McpError as exc:
                self._mark(call.id, "uncertain" if exc.uncertain else exc.category)
                if exc.uncertain:
                    raise McpError("uncertain", "Исход операции неизвестен. Очередь плагина приостановлена.") from exc
                raise
            self._mark(call.id, "received")
            result = await _read_result(client, reply, check_output)
            result.update(tool=tool, filename=", ".join(filenames), call_id=call.id)
            saved = self._save(call, result)
            self._mark(call.id, saved["status"])
            return saved
        except McpError as exc:
            self.store.event(call.task_id, "plugin_diagnostic", {
                "turn_id": call.turn_id, "call_id": call.id, "agent_id": call.agent_id,
                "skill": skill["key"], "revision": skill["revision"], "tool": tool,
                "category": exc.category, "stage": "execution" if tracking else "validation",
            })
            raise
        finally:
            await client.close(watcher)

    def _mark(self, call_id, status):
        with self.store.transaction() as db:
            db.execute("UPDATE plugin_calls SET finished_at=?,status=? WHERE id=?", (now(), status, call_id))

    def _save(self, call, result):
        resources = result.pop("_resources")
        files = []
        for resource in resources:
            try:
                with self.store.transaction() as db:
                    files.extend(save_resources(db, call.task_id, [resource]))
            except sqlite3.Error:
                result["errors"].append(f"«{resource.name}» — не удалось сохранить файл результата.")
        if result["errors"] and result["status"] == "ok":
            result["status"] = "partial"
        result["artifacts"] = files
        self.store.event(call.task_id, "skill_tool", {"turn_id": call.turn_id, "skill_id": call.skill["id"],
                         "skill": call.skill["title"], "tool": call.tool, "result": result,
                         "document_ids": call.file_ids}, key=call.id)
        return result


def _public_data(value):
    if isinstance(value, dict):
        return {k: _public_data(v) for k, v in value.items() if k.casefold() not in
                {"blob", "base64", "contentbase64", "encoding", "api_key", "token", "authorization"}}
    if isinstance(value, list):
        return [_public_data(item) for item in value]
    if isinstance(value, str) and len(value) > MAX_RESULT:
        return value[:MAX_RESULT] + " …"
    return value


def _is_profile(value):
    return isinstance(value, dict) and type(value.get("workspaceResultVersion")) is int and value["workspaceResultVersion"] == 1


async def _read_result(client, reply, check_output):
    structured = reply.get("structuredContent")
    content = reply.get("content", [])
    errors, resources, warnings, seen = [], [], [], set()
    if not isinstance(content, list):
        errors.append("MCP вернул некорректное содержимое результата.")
        content = []
    if check_output is not None:
        try:
            validate(check_output, structured, "output_schema")
        except McpError as exc:
            errors.append(str(exc))
    plain, profile = [], structured if _is_profile(structured) else None
    if "isError" in reply and type(reply["isError"]) is not bool:
        errors.append("MCP вернул неверный статус операции.")

    def add_resource(block):
        try:
            resource, = read_resources([block])
            marker = (resource.name, sha256(resource.data).digest())
            if marker in seen:
                return
            if len(resources) >= MAX_OUTPUT_FILES or sum(len(item.data) for item in resources) + len(resource.data) > MAX_OUTPUT_BYTES:
                raise ValueError("Превышен предел выходных файлов MCP.")
            seen.add(marker)
            resources.append(resource)
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))

    for block in content:
        if not isinstance(block, dict):
            errors.append("Не удалось прочитать блок результата MCP.")
            continue
        kind = block.get("type")
        if kind == "text" and isinstance(block.get("text"), str):
            text = block["text"]
            try:
                decoded = parse_json(text)
            except (ValueError, RecursionError):
                decoded = None
            if _is_profile(decoded):
                if profile is None:
                    profile = decoded
            elif structured is None or decoded is None:
                plain.append(_public_data(decoded) if decoded is not None else text)
        elif kind == "resource" and isinstance(record := block.get("resource"), dict):
            if "blob" in record:
                add_resource(block)
            elif isinstance(record.get("text"), str):
                plain.append(record["text"])
        elif kind == "resource_link":
            uri = block.get("uri")
            if not isinstance(uri, str) or not uri or "resources" not in client.session.capabilities:
                errors.append("MCP вернул ресурс, который этот сервер не предоставляет через resources/read.")
                continue
            try:
                returned = await client.rpc("resources/read", {"uri": uri})
                contents = returned.get("contents")
                matched = [item for item in contents if isinstance(item, dict) and item.get("uri") == uri] if isinstance(contents, list) else []
                if not matched:
                    raise ValueError("MCP не вернул запрошенный ресурс.")
                for item in matched:
                    if "blob" not in item and isinstance(item.get("text"), str):
                        item = {**item, "blob": base64.b64encode(item["text"].encode()).decode("ascii")}
                    add_resource({"type": "resource", "resource": item})
            except (McpError, ValueError) as exc:
                errors.append(f"Не удалось получить ресурс MCP. {exc}")
    if profile is not None:
        payload = profile.get("data")
        warnings = profile.get("warnings", [])
        if not isinstance(warnings, list) or any(not isinstance(w, dict) or not isinstance(w.get("message"), str) for w in warnings):
            errors.append("Профиль результата MCP содержит неверные предупреждения.")
            warnings = [w for w in warnings if isinstance(w, dict) and isinstance(w.get("message"), str)] if isinstance(warnings, list) else []
        artifacts = profile.get("artifacts", [])
        if not isinstance(artifacts, list):
            errors.append("Профиль результата MCP содержит неверный список файлов.")
            artifacts = []
        if len(artifacts) > MAX_OUTPUT_FILES:
            errors.append("Профиль результата содержит больше 32 файлов.")
        for artifact in artifacts[:MAX_OUTPUT_FILES]:
            try:
                name = attachment_name(artifact["filename"])
                mime = artifact["mimeType"]
                if not name.strip(". ") or len(name) > 200 or not isinstance(mime, str):
                    raise ValueError("Некорректное имя или тип файла результата.")
                add_resource({"type": "resource", "resource": {
                    "uri": "workspace://artifact/" + quote(name, safe=""), "mimeType": mime,
                    "blob": artifact["contentBase64"],
                }})
            except (KeyError, TypeError, ValueError):
                errors.append("Профиль результата MCP содержит некорректное описание файла.")
    else:
        payload = structured
        warnings = structured.get("warnings", []) if isinstance(structured, dict) else []
    if isinstance(warnings, str):
        warnings = [warnings]
    elif not isinstance(warnings, list):
        warnings = []
    warnings = [row for row in warnings if isinstance(row, str) or
                isinstance(row, dict) and isinstance(row.get("message"), str)]
    report = _public_data(payload if payload is not None else plain)
    if len(json.dumps(report, ensure_ascii=False)) > MAX_RESULT:
        report = {"excerpt": json.dumps(report, ensure_ascii=False)[:MAX_RESULT],
                  "truncated": True}
    return {"report": report, "text": [x[:1800] for x in plain if isinstance(x, str) and len(x) <= MAX_RESULT][:12],
            "warnings": _public_data(warnings), "errors": errors,
            "status": "error" if reply.get("isError") is True else "partial" if errors else "ok",
            "_resources": resources}
