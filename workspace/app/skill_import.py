"""Import a trusted local MCP skill bundle from disk."""
import hashlib
import json
import re
from pathlib import Path

from workspace.app.skills import SkillInput, local_mcp_url, skill_definition
from workspace.app.store import Store


def manifest_group(config, endpoint):
    if "skillGroup" not in config:
        return None
    group = config["skillGroup"]
    if not isinstance(group, dict):
        raise ValueError("В skillGroup укажите id и name.")
    key, name = group.get("id"), group.get("name")
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", key):
        raise ValueError("В skillGroup.id укажите короткий идентификатор комплекта.")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120 or not name.strip().isprintable():
        raise ValueError("В skillGroup.name укажите название комплекта.")
    # Two local applications may use the same group id.
    scope = hashlib.sha256(f"{endpoint}#{key}".encode()).hexdigest()
    return {"id": key, "name": name.strip(), "key": scope}


def import_manifest(profile, manifest_path, bundle_root=None):
    manifest = Path(manifest_path).resolve(strict=True)
    root = Path(bundle_root).resolve(strict=True) if bundle_root else manifest.parent
    if not manifest.is_relative_to(root) or manifest.stat().st_size > 256_000:
        raise ValueError("Проверьте корневую папку и размер манифеста.")
    config = json.loads(manifest.read_text(encoding="utf-8-sig"))
    endpoint = local_mcp_url(config["url"])
    group = manifest_group(config, endpoint)
    def local_file(relative, limit):
        if not isinstance(relative, str) or not relative or Path(relative).anchor or ".." in Path(relative).parts:
            raise ValueError("В манифесте нужны относительные пути внутри комплекта.")
        file = (root / relative).resolve(strict=True)
        if not file.is_relative_to(root) or not file.is_file() or file.stat().st_size > limit:
            raise ValueError("Проверьте путь и размер файла в комплекте.")
        return file
    token = local_file(config["tokenFile"], 1024).read_text(encoding="utf-8-sig").strip() if config.get("tokenFile") else ""
    if len(token) > 512 or any(not 33 <= ord(c) <= 126 for c in token):
        raise ValueError("Проверьте формат ключа в комплекте.")
    entries = config.get("skills")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 128:
        raise ValueError("Манифест должен содержать список skills.")
    items = []
    for entry in entries:
        file = local_file(entry["path"], 128000)
        item = skill_definition(SkillInput(filename=file.name, markdown=file.read_text(encoding="utf-8-sig")))
        conn = item.get("connection")
        if conn:
            if conn["url"] != endpoint:
                raise ValueError(f"Адрес MCP в навыке «{item['name']}» отличается от адреса пакета.")
            item["api_key"] = token
        items.append(item)

    def identity(item):
        conn = item.get("connection") or {}
        return item["name"], conn.get("url"), conn.get("tool")

    if len({identity(item) for item in items}) != len(items):
        raise ValueError("В манифесте повторяются навыки.")
    db_store = Store(Path(profile))
    try:
        counts = {"added": 0, "updated": 0, "retained": 0}
        with db_store.transaction() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = {}
            for row in db.execute("SELECT definition FROM markdown_skills"):
                original = json.loads(row[0])
                existing.setdefault(identity(original), []).append(original)
            for item in items:
                old = existing.get(identity(item), [])
                if len(old) > 1:
                    raise ValueError(f"В библиотеке несколько навыков с именем «{item['name']}» для одного инструмента.")
                item["bundle_source"] = endpoint
                if old:
                    previous = old[0]
                    item["id"] = previous["id"]
                    if previous.get("bundle_source") != endpoint:
                        # Preserve authored instructions and history links on first adoption.
                        item = {**previous, "api_key": item["api_key"]}
                if group:
                    item["group"] = group
                else:
                    item.pop("group", None)
                if old:
                    if previous == item:
                        counts["retained"] += 1
                        continue
                    counts["updated"] += 1
                else:
                    counts["added"] += 1
                db.execute("INSERT INTO markdown_skills(id,definition) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET definition=excluded.definition",
                           (item["id"], json.dumps(item, ensure_ascii=False)))
        return counts
    finally:
        db_store.close()
