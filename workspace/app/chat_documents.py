"""Uploaded documents belong to a chat. A message refers to immutable local copies."""

import base64
import hashlib
import io
import json
import zipfile
from pathlib import PurePath
from xml.etree import ElementTree

from workspace.app.skill_resources import Resource, save_resources
from workspace.app.skills import MAX_FILE, SkillAttachment, attachment_name


async def upload(store, chat_id, filename, request):
    task = store.one("tasks", chat_id)
    if task["details"].get("mode") not in {"chat", "agent"}:
        raise ValueError("Вложения доступны в диалогах и задачах агента.")
    name = attachment_name(filename)
    if not name or len(name) > 240:
        raise ValueError("Укажите имя файла длиной до 240 символов.")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_FILE:
            raise ValueError("Один файл может занимать до 12 МиБ.")
        data.extend(chunk)
    if not data:
        raise ValueError("Файл пуст.")
    digest = hashlib.sha256(data).hexdigest()
    with store.transaction() as db:
        row = db.execute("SELECT id FROM chat_uploads WHERE task_id=? AND name=? AND sha256=?",
                         (chat_id, name, digest)).fetchone()
        if row:
            return {"id": row["id"], "name": name, "size": len(data)}
        doc, = save_resources(db, chat_id, [Resource(name, "application/octet-stream", data)])
        db.execute("INSERT INTO chat_uploads(id,task_id,name,sha256) VALUES(?,?,?,?)",
                   (doc["id"], chat_id, name, digest))
        return doc


def resolve(store, chat_id, ids):
    docs = []
    for key in dict.fromkeys(ids):
        rows = store.rows("SELECT f.id,f.name,length(f.data) AS size FROM skill_artifacts f "
                          "JOIN chat_uploads u ON u.id=f.id WHERE f.task_id=? AND f.id=?", (chat_id, key))
        if not rows:
            raise ValueError("Вложение отсутствует в этом диалоге.")
        docs.append(rows[0])
    return docs


def active_documents(store, chat_id, new, prompt, memory):
    if not memory:
        return new
    rows = store.rows("SELECT payload FROM events WHERE task_id=? AND kind='chat_message' "
                      "AND json_extract(payload,'$.role')='user' AND json_array_length(payload,'$.documents')>0 "
                      "ORDER BY seq DESC", (chat_id,))
    groups = [json.loads(row["payload"])["documents"] for row in rows]
    active = list(new or (groups[0] if groups else []))
    seen_names = {doc["name"].casefold() for doc in active}
    # Prior files stay in history. An explicit filename can bring an older
    # document into the current comparison; newest revision wins for a name.
    for group in groups:
        for doc in group:
            name = doc["name"].casefold()
            if name not in seen_names and name in prompt.casefold():
                active.append(doc)
                seen_names.add(name)
    for doc in store.rows(
        "SELECT id,name,length(data) AS size FROM skill_artifacts f WHERE task_id=? AND NOT EXISTS"
        "(SELECT 1 FROM chat_uploads u WHERE u.id=f.id) ORDER BY f.rowid DESC", (chat_id,),
    ):
        name = doc["name"].casefold()
        if name not in seen_names and name in prompt.casefold():
            active.append(doc)
            seen_names.add(name)
    return active


def read_bytes(store, chat_id, doc):
    rows = store.rows("SELECT data FROM skill_artifacts WHERE task_id=? AND id=?", (chat_id, doc["id"]))
    if not rows:
        raise ValueError("Копия документа отсутствует в диалоге.")
    return rows[0]["data"]


def for_tool(store, chat_id, doc):
    encoded = base64.b64encode(read_bytes(store, chat_id, doc)).decode("ascii")
    return SkillAttachment.model_construct(filename=doc["name"], base64=encoded)


def excerpt(name, data, limit):
    ext = PurePath(name).suffix.lower()
    try:
        if ext == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            text = ""
            for index, page in enumerate(reader.pages):
                page_text = page.extract_text() or ""
                if page_text.strip():
                    text += f"\n[{index + 1}] {page_text}"
                if len(text) > limit:
                    break
        elif ext == ".docx":
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                info = archive.getinfo("word/document.xml")
                if info.file_size > 24 * 1024 * 1024:
                    return "Для этого документа требуется навык чтения."
                text = " ".join(e.text for e in ElementTree.fromstring(archive.read(info)).iter()
                                if e.tag.endswith("}t") and e.text)
        else:
            sample = data[:max(4 * limit, 512)]
            encoding = "utf-16" if sample.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
            if encoding != "utf-16" and b"\x00" in sample:
                return "Доступно имя файла. Для чтения содержимого нужен подходящий навык."
            import codecs
            text = codecs.getincrementaldecoder(encoding)().decode(sample, final=len(data) == len(sample))
            if any(ord(c) < 32 and c not in "\n\r\t\f" for c in text):
                return "Для чтения этого формата нужен подходящий навык."
        text = text.strip()
        if not text:
            return "Текстовый слой пуст. Для чтения нужен подходящий навык."
        return text[:limit] + (" … [фрагмент]" if len(text) > limit else "")
    except Exception:
        return "Текст извлечь не удалось. Для чтения нужен подходящий навык."
