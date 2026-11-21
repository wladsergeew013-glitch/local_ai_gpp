"""Binary MCP results are stored separately from chat events and model messages."""
import base64
import binascii
import re
from typing import NamedTuple
from urllib.parse import quote, unquote, urlsplit

from fastapi import HTTPException, Response

from workspace.app.store import identifier

MAX_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_OUTPUT_FILES = 32


class Resource(NamedTuple):
    name: str
    mime: str
    data: bytes


def read_resources(content):
    resources, size = [], 0
    for block in content:
        if block.get("type") != "resource":
            continue
        source = block.get("resource") or {}
        if "blob" not in source:
            continue
        blob = source["blob"]
        if (not isinstance(blob, str) or len(resources) >= MAX_OUTPUT_FILES or
                len(blob) > ((MAX_OUTPUT_BYTES - size + 2) // 3) * 4):
            raise ValueError("Слишком большой набор файлов от MCP (до 32 файлов и 64 МиБ).")
        try:
            data = base64.b64decode(blob, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("MCP вернул повреждённый файл.") from exc
        size += len(data)
        if size > MAX_OUTPUT_BYTES:
            raise ValueError("Файлы MCP превышают 64 МиБ.")
        # The URI is a label. Never open it or use it as a filesystem path.
        uri = source.get("uri")
        if not isinstance(uri, str):
            raise ValueError("В ресурсе MCP отсутствует URI.")
        name = unquote(urlsplit(uri).path).replace("\\", "/").rsplit("/", 1)[-1]
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", name).strip(". ")[:200] or "result"
        mime = source.get("mimeType") or "application/octet-stream"
        if not isinstance(mime, str) or not re.fullmatch(r"[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+", mime):
            mime = "application/octet-stream"
        resources.append(Resource(name, mime, data))
    return resources


def save_resources(db, task_id, resources):
    files = []
    for resource in resources:
        key = identifier()
        db.execute("INSERT INTO skill_artifacts(id,task_id,name,mime,data) VALUES(?,?,?,?,?)",
                   (key, task_id, resource.name, resource.mime, resource.data))
        files.append({"id": key, "name": resource.name, "size": len(resource.data), "mime_type": resource.mime})
    return files


def download(store, task_id, artifact_id):
    rows = store.rows("SELECT name,mime,data FROM skill_artifacts WHERE id=? AND task_id=?", (artifact_id, task_id))
    if not rows:
        raise HTTPException(404, "Файл отсутствует в этом диалоге.")
    item = rows[0]
    return Response(item["data"], media_type=item["mime"], headers={
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(item["name"], safe=""),
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
    })
