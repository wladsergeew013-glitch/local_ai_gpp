"""Markdown skills and their optional, explicitly configured local MCP tools."""

import asyncio
import base64
import binascii
import json
import re
import secrets
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, Field, field_validator, model_validator

from workspace.app.skill_resources import MAX_OUTPUT_BYTES, read_resources, save_resources

from workspace.app.store import identifier, now
from workspace.app.version import VERSION

MAX_FILE = 12 * 1024 * 1024
MAX_BASE64 = (MAX_FILE + 2) // 3 * 4
MAX_RESULT = 32_000
REVISION = "2025-11-25"
MCP_SETUP_READ_SECONDS = 60
MCP_TOOL_READ_SECONDS = 15 * 60
SECTION = re.compile(r"^```workspace-mcp[ \t]*\n(.*?)\n```[ \t]*$", re.M | re.S)


class SkillInput(BaseModel):
    filename: str = Field(min_length=1, max_length=240)
    markdown: str = Field(min_length=1, max_length=32_000)
    api_key: str = Field(default="", max_length=512)


class SkillAttachment(BaseModel):
    filename: str = Field(min_length=1, max_length=240)
    base64: str = Field(min_length=1, max_length=MAX_BASE64)

    @field_validator("filename")
    @classmethod
    def valid_filename(cls, value):
        return attachment_name(value)

    @field_validator("base64")
    @classmethod
    def valid_contents(cls, value):
        try:
            size = len(base64.b64decode(value, validate=True))
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Вложение повреждено.") from exc
        if not 0 < size <= MAX_FILE:
            raise ValueError("Вложение должно занимать не больше 12 МиБ.")
        return value


class SkillUse(BaseModel):
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    attachment: SkillAttachment | None = None
    attachments: list[SkillAttachment] | None = Field(default=None, min_length=1, max_length=128)
    disabled_sections: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_files(self):
        if self.attachment and self.attachments:
            raise ValueError("Передайте attachment или attachments.")
        if self.attachments:
            size = sum(len(f.base64) // 4 * 3 - f.base64[-2:].count("=") for f in self.attachments)
            if size > 48 * 1024 * 1024:
                raise ValueError("Суммарный размер входных файлов превышает 48 МиБ.")
        return self


class FileArguments(BaseModel, extra="forbid"):
    filename: str = Field(pattern=r"^[\w-]{1,80}$")
    base64: str = Field(pattern=r"^[\w-]{1,80}$")


class FileListArguments(FileArguments):
    argument: str = Field(pattern=r"^[\w-]{1,80}$")


class McpConnection(BaseModel, extra="forbid"):
    url: str = Field(min_length=1, max_length=500)
    tool: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    file: FileArguments | None = None
    files: FileListArguments | None = None
    arguments: dict = Field(default_factory=dict)


def local_mcp_url(value):
    url = urlsplit(value)
    if (url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1", "::1"}
            or url.username is not None or url.password is not None or url.query or url.fragment
            or not url.path.startswith("/") or url.port is None):
        raise ValueError("Укажите локальный HTTP-адрес MCP с портом и путём.")
    host = "[::1]" if url.hostname == "::1" else "127.0.0.1"
    return urlunsplit(("http", f"{host}:{url.port}", url.path, "", ""))


def attachment_name(value):
    if value in {".", ".."} or any(c in value for c in "/\\:") or any(ord(c) < 32 for c in value):
        raise ValueError("Передайте имя вложения без пути.")
    return value


OPTION_HEADING = re.compile(r"^ {0,3}##[ \t]+(.+?)[ \t]+<!-- option: ([a-zA-Z0-9_-]+) -->[ \t]*$")
HEADING = re.compile(r"^ {0,3}#{1,2}(?:[ \t]+|$)")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def instruction_sections(text, disabled=()):
    options, output, known = [], [], set()
    omitted = set(disabled)
    include = True
    fence = ""
    for line in text.splitlines(keepends=True):
        raw = line.rstrip("\r\n")
        marker = FENCE.match(raw)
        if fence:
            if marker and marker[1].startswith(fence) and not marker[2].strip():
                fence = ""
        elif marker:
            fence = marker[1]
        elif HEADING.match(raw):
            include = True
            option = OPTION_HEADING.match(raw)
            if option:
                title, key = option.groups()
                if key in known:
                    raise ValueError(f"Повторяется идентификатор пункта «{key}».")
                known.add(key)
                options.append({"id": key, "title": title.strip()})
                include = key not in omitted
                line = "## " + title.strip() + ("\n" if line.endswith("\n") else "")
        if include:
            output.append(line)
    if omitted - known:
        raise ValueError("В навыке нет одного из указанных необязательных пунктов.")
    return options, "".join(output).strip()


def skill_definition(payload):
    filename = attachment_name(payload.filename)
    if not filename.lower().endswith(".md"):
        raise ValueError("Выберите файл навыка Markdown (.md).")
    text = payload.markdown.lstrip("\ufeff").replace("\r\n", "\n")
    meta = re.match(r"\A---\n(.*?)\n---(?:\n|$)", text, re.S)
    name, description, appearance = Path(filename).stem, "", {}
    if meta:
        for key, value in re.findall(r"^(name|description|workspace-activity|workspace-accepts):[ \t]*(.+)$", meta[1], re.M):
            if key == "name":
                name = value.strip(" \"'")
            elif key == "description":
                description = value.strip(" \"'")
            elif key == "workspace-accepts":
                extensions = [part.strip().lower() for part in value.strip(" \"'").split(",")]
                if any(not re.fullmatch(r"\.[\w-]+(?:\.[\w-]+)*", ext) for ext in extensions):
                    raise ValueError("В workspace-accepts перечислите расширения через запятую, например .pdf, .docx.")
                appearance["accepts"] = list(dict.fromkeys(extensions))
            else:
                if value.strip(" \"'") != "documents":
                    raise ValueError("Допустимое значение workspace-activity — documents.")
                appearance["activity"] = "documents"
        text = text[meta.end():]
    blocks = list(SECTION.finditer(text))
    if len(blocks) > 1:
        raise ValueError("Навык может объявить одно подключение workspace-mcp.")
    connection = None
    if blocks:
        try:
            connection = McpConnection.model_validate_json(blocks[0][1])
            connection.url = local_mcp_url(connection.url)
        except ValueError as exc:
            raise ValueError("Проверьте JSON и локальный адрес в блоке workspace-mcp.") from exc
        if connection.file and connection.files:
            raise ValueError("Выберите один формат вложений: file или files.")
        mapping = connection.file or connection.files
        if mapping and mapping.filename == mapping.base64:
            raise ValueError("Для имени и содержимого файла укажите разные аргументы MCP.")
    instructions = SECTION.sub("", text).strip()
    if not instructions or not name or len(name) > 120:
        raise ValueError("Навыку нужны название и текст инструкции.")
    options, _ = instruction_sections(instructions)
    key = payload.api_key.strip()
    if any(not 33 <= ord(c) <= 126 for c in key):
        raise ValueError("Ключ MCP должен быть одной строкой без пробелов.")
    return {"id": secrets.token_hex(16), "name": name, "description": description[:400],
            "instructions": instructions, "sections": options, "connection": connection.model_dump() if connection else None,
            "api_key": key, **appearance}


def register_many(store, payloads):
    if not payloads:
        raise ValueError("Выберите хотя бы один файл навыка.")
    items = []
    for payload in payloads:
        try:
            items.append(skill_definition(payload))
        except ValueError as exc:
            raise ValueError(f"{payload.filename} — {exc}") from exc
    with store.transaction() as db:
        db.executemany(
            "INSERT INTO markdown_skills(id,definition) VALUES(?,?)",
            [(item["id"], json.dumps(item, ensure_ascii=False)) for item in items],
        )
    return [public_skill(item) for item in items]


def register(store, payload):
    return register_many(store, [payload])[0]


def public_skill(item):
    return {"id": item["id"], "name": item["name"], "description": item["description"],
            "sections": item.get("sections", []), "has_mcp": item["connection"] is not None,
            "formats": item.get("accepts", []), "group": item.get("group"),
            "accepts_attachment": bool(item["connection"] and (item["connection"].get("file") or item["connection"].get("files"))),
            "accepts_multiple": bool(item["connection"] and item["connection"].get("files"))}


def list_skills(store):
    return [public_skill(json.loads(row["definition"]))
            for row in store.rows("SELECT definition FROM markdown_skills ORDER BY rowid")]


def selected_skill(store, selection):
    if not selection:
        return None
    rows = store.rows("SELECT definition FROM markdown_skills WHERE id=?", (selection.id,))
    if not rows:
        raise ValueError("Этот навык больше не зарегистрирован.")
    item = json.loads(rows[0]["definition"])
    attached = selection.attachments or ([selection.attachment] if selection.attachment else [])
    connection = item.get("connection") or {}
    if connection.get("files"):
        return item, attached or None
    if attached and not connection.get("file"):
        raise ValueError("У выбранного навыка нет аргументов для вложения.")
    if len(attached) > 1:
        raise ValueError("Этот навык принимает один файл.")
    return item, attached[0] if attached else None



@dataclass
class ChatSkill:
    definition: dict
    instructions: str
    attachment: SkillAttachment | list[SkillAttachment] | None
    cached: dict | None


def _report_ref(seq, result):
    return {"event": seq, "filename": result.get("filename", "")}


def _insert_report(db, task_id, payload):
    return db.execute(
        "INSERT INTO events(task_id,source_key,kind,created_at,payload) VALUES(?,?,'skill_tool',?,?)",
        (task_id, identifier(), now(), json.dumps(payload, ensure_ascii=False)),
    ).lastrowid


def _previous_reports(store, task):
    details = task["details"]
    if "skill_reports" in details:
        return details["skill_reports"]
    skill_id, result = details.get("skill_id"), details.get("skill_result")
    if not skill_id or result is None:
        return {}
    # Older versions kept the report only in task details. Preserve it in the
    # history before a new selection replaces those fields.
    with store.transaction() as db:
        event = db.execute(
            "SELECT seq,payload FROM events WHERE task_id=? AND kind='skill_tool' ORDER BY seq DESC LIMIT 1",
            (task["id"],),
        ).fetchone()
        if event:
            seq = event["seq"]
            payload = json.loads(event["payload"])
            payload.update(skill_id=skill_id, result=result)
            db.execute("UPDATE events SET payload=? WHERE seq=?", (json.dumps(payload, ensure_ascii=False), seq))
        else:
            seq = _insert_report(db, task["id"], {"skill_id": skill_id, "tool": result["tool"], "result": result})
        refs = {skill_id: _report_ref(seq, result)}
        db.execute("UPDATE tasks SET details=json_set(details,'$.skill_reports',json(?)) WHERE id=?",
                   (json.dumps(refs, ensure_ascii=False), task["id"]))
    return refs


def prepare_skills(store, task, uses, *, automatic=False, reuse=True):
    previous = _previous_reports(store, task) if reuse else {}
    contexts, retained, disabled_sections = [], {}, {}
    seen = set()
    for use in uses or ():
        if use.id in seen:
            raise ValueError("Один навык выбран несколько раз.")
        seen.add(use.id)
        item, attachment = selected_skill(store, use)
        cached = None
        ref = previous.get(use.id)
        if ref and attachment is None:
            rows = store.rows(
                "SELECT payload FROM events WHERE task_id=? AND seq=? AND kind='skill_tool'",
                (task["id"], ref["event"]),
            )
            if rows:
                payload = json.loads(rows[0]["payload"])
                if payload.get("skill_id") == use.id and payload.get("result") is not None:
                    retained[use.id] = ref
                    cached = payload["result"]
        if not automatic and public_skill(item)["accepts_attachment"] and attachment is None and cached is None:
            raise ValueError(f"Прикрепите файл для навыка «{item['name']}».")
        try:
            _, instructions = instruction_sections(item["instructions"], use.disabled_sections)
        except ValueError as exc:
            raise ValueError(f"Навык «{item['name']}» — {exc}") from exc
        if use.disabled_sections:
            disabled_sections[use.id] = list(dict.fromkeys(use.disabled_sections))
        contexts.append(ChatSkill(item, instructions, attachment, cached))
    ids = [context.definition["id"] for context in contexts]
    single = contexts[0] if len(contexts) == 1 else None
    return contexts, {
        "skill_ids": ids, "skill_reports": retained, "skill_disabled_sections": disabled_sections,
        "skill_id": ids[0] if single else None, "skill_result": single.cached if single else None,
    }


def record_report(store, task_id, turn_id, item, result, *, single, document_ids=()):
    public = {k: v for k, v in result.items() if k != "_resources"}
    with store.transaction() as db:
        if result.get("_resources"):
            public["artifacts"] = save_resources(db, task_id, result["_resources"])
        result = public
        payload = {"turn_id": turn_id, "skill_id": item["id"], "skill": item["name"], "tool": result["tool"], "result": result}
        if document_ids:
            payload["document_ids"] = list(document_ids)
        seq = _insert_report(db, task_id, payload)
        db.execute(
            "UPDATE tasks SET details=json_set(details,?,json(?),'$.skill_result',json(?)) WHERE id=?",
            ('$.skill_reports."' + item["id"] + '"', json.dumps(_report_ref(seq, result), ensure_ascii=False),
             json.dumps(result if single else None, ensure_ascii=False), task_id),
        )
    return result


def invoke(item, attachment, *, transport=None, cancelled=None):
    """Called by the chat worker; Stop also interrupts a silent HTTP response."""
    async def run():
        if cancelled is None:
            return await _invoke(item, attachment, transport)
        request = asyncio.create_task(_invoke(item, attachment, transport))
        try:
            while not cancelled.is_set():
                done, _ = await asyncio.wait({request}, timeout=0.2)
                if done:
                    return request.result()
            raise ValueError("Выполнение навыка остановлено.")
        finally:
            if not request.done():
                request.cancel()
                with suppress(asyncio.CancelledError):
                    await request

    return asyncio.run(run())


async def _invoke(item, attachment, transport):
    connection = McpConnection.model_validate(item["connection"])
    url = local_mcp_url(connection.url)
    headers = {"Accept": "application/json, text/event-stream"}
    if item["api_key"]:
        headers["Authorization"] = "Bearer " + item["api_key"]
    arguments = dict(connection.arguments)
    if connection.file:
        if attachment is None:
            raise ValueError("Прикрепите документ для выбранного навыка.")
        arguments.update({connection.file.filename: attachment.filename, connection.file.base64: attachment.base64})

    elif connection.files:
        if not isinstance(attachment, list) or not attachment:
            raise ValueError("Прикрепите файлы для навыка.")
        fields = connection.files
        arguments[fields.argument] = [{fields.filename: f.filename, fields.base64: f.base64} for f in attachment]

    async with httpx.AsyncClient(follow_redirects=False, trust_env=False, transport=transport) as client:
        async def rpc(number, method, params=None):
            payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
            if number is not None:
                payload["id"] = number
            read_seconds = MCP_TOOL_READ_SECONDS if method == "tools/call" else MCP_SETUP_READ_SECONDS
            timeout = httpx.Timeout(connect=4, read=read_seconds, write=60, pool=4)
            try:
                cap = (MAX_OUTPUT_BYTES * 4 // 3 + 1_000_000) if method == "tools/call" else 256_000
                raw = bytearray()
                async with client.stream("POST", url, json=payload, headers=headers, timeout=timeout) as response:
                    response.raise_for_status()
                    if number is None:
                        return {}
                    async for chunk in response.aiter_bytes(64 * 1024):
                        if len(raw) + len(chunk) > cap:
                            raise ValueError("Ответ MCP превышает допустимый размер.")
                        raw.extend(chunk)
            except httpx.ReadTimeout as exc:
                raise ValueError(f"MCP не прислал данные за {read_seconds} с при запросе {method}.") from exc
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                raise ValueError("Не удалось подключиться к локальному MCP.") from exc
            except httpx.HTTPStatusError as exc:
                raise ValueError(f"MCP вернул HTTP {exc.response.status_code}.") from exc
            except httpx.HTTPError as exc:
                raise ValueError(f"Ошибка обмена с локальным MCP при запросе {method}.") from exc
            if response.headers.get("content-type", "").startswith("text/event-stream"):
                entries = [json.loads(line[6:]) for line in raw.decode("utf-8").splitlines() if line.startswith("data: {")]
                data = next((entry for entry in entries if entry.get("id") == number), {})
            else:
                data = json.loads(raw)
            if data.get("id") != number or not isinstance(data.get("result"), dict):
                raise ValueError("MCP не вернул результат запроса " + method + ".")
            if session := response.headers.get("Mcp-Session-Id"):
                headers["Mcp-Session-Id"] = session
            return data["result"]

        setup = await rpc(1, "initialize", {"protocolVersion": REVISION, "capabilities": {},
                                            "clientInfo": {"name": "Local AI Workspace", "version": VERSION}})
        revision = setup.get("protocolVersion")
        if revision not in {REVISION, "2025-06-18", "2025-03-26"}:
            raise ValueError("Версия протокола MCP не поддерживается.")
        headers["MCP-Protocol-Version"] = revision
        await rpc(None, "notifications/initialized")
        cursor = None
        tool = None
        for number in range(2, 22):
            page = await rpc(number, "tools/list", {"cursor": cursor} if cursor else None)
            tool = next((t for t in page.get("tools", []) if t.get("name") == connection.tool), None)
            cursor = page.get("nextCursor")
            if tool is not None or not cursor:
                break
        if not tool or (tool.get("annotations") or {}).get("readOnlyHint") is not True:
            raise ValueError("Заявленный инструмент MCP должен быть доступен и помечен как читающий.")
        reply = await rpc(23, "tools/call", {"name": connection.tool, "arguments": arguments})
        plain = "\n".join(c.get("text", "") for c in reply.get("content", []) if c.get("type") == "text")
        if reply.get("isError"):
            raise ValueError((plain or "Инструмент MCP вернул ошибку.")[:500])
        resources = read_resources(reply.get("content", []))
        report = reply.get("structuredContent", {} if resources else plain)
        if len(json.dumps(report, ensure_ascii=False)) > MAX_RESULT:
            raise ValueError("Результат инструмента слишком велик для передачи модели.")
        names = [f.filename for f in attachment] if isinstance(attachment, list) else ([attachment.filename] if attachment else [])
        result = {"tool": connection.tool, "filename": ", ".join(names), "report": report}
        if resources:
            result["_resources"] = resources
        return result
