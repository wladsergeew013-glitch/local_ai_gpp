"""Keep imported Markdown and its library metadata with the Workspace package."""

import json
import os
from pathlib import Path
import re
import tempfile


INDEX = ".workspace-skills.json"
SKILL_ID = re.compile(r"[0-9a-f]{32}")


def skill_slug(item):
    slug = re.sub(r"[^\w.-]+", "-", item["name"]).strip("-._")
    return slug[:48] or "skill"


def markdown_copy(item):
    if "source_markdown" in item:
        return item["source_markdown"]
    # Profiles from earlier versions stored the instructions and metadata separately.
    meta = {"name": item["name"], "description": item.get("description")}
    if item.get("accepts"):
        meta["workspace-accepts"] = ", ".join(item["accepts"])
    if item.get("activity"):
        meta["workspace-activity"] = item["activity"]
    header = "\n".join(f"{name}: {value}" for name, value in meta.items() if value)
    source = f"---\n{header}\n---\n\n{item['instructions']}\n"
    if item.get("connection"):
        source += "\n```workspace-mcp\n" + json.dumps(item["connection"], ensure_ascii=False) + "\n```\n"
    return source


def check_directory(path):
    if path.resolve() != path:
        raise ValueError("Папка Skills должна находиться внутри установки или профиля Workspace.")


def replace_file(path, data):
    if path.is_symlink():
        raise ValueError("В папке Skills вместо файла обнаружена ссылка.")
    if path.is_file() and path.stat().st_size == len(data) and path.read_bytes() == data:
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".skill-", delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def restore_skill_copies(store, db):
    # The same installation can fall back to the profile folder when installed read-only.
    directories = dict.fromkeys([store.root / "Skills", store.skills_directory])
    for folder in directories:
        path = folder / INDEX
        if not path.exists():
            continue
        check_directory(folder)
        if path.is_symlink():
            raise ValueError("Файл библиотеки Skills не должен быть ссылкой.")
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(snapshot, dict) or snapshot.get("version") != 1 or not isinstance(snapshot.get("skills"), list):
            raise ValueError("Не удалось прочитать библиотеку в папке Skills.")
        for item in snapshot["skills"]:
            if (not isinstance(item, dict) or not SKILL_ID.fullmatch(str(item.get("id", "")))
                    or not isinstance(item.get("name"), str) or not isinstance(item.get("instructions"), str)
                    or not isinstance(item.get("description"), str) or not isinstance(item.get("api_key"), str)
                    or "connection" not in item or not isinstance(item["connection"], (dict, type(None)))):
                raise ValueError("В библиотеке Skills обнаружена повреждённая запись.")
            db.execute("INSERT OR IGNORE INTO markdown_skills(id,definition) VALUES(?,?)",
                       (item["id"], json.dumps(item, ensure_ascii=False)))


def save_skill_copies(store, db):
    items = [json.loads(row[0]) for row in db.execute("SELECT definition FROM markdown_skills ORDER BY rowid")]
    if not items:
        return

    def save(folder):
        check_directory(folder)
        folder.mkdir(parents=True, exist_ok=True)
        for item in items:
            if not SKILL_ID.fullmatch(item["id"]):
                raise ValueError("В библиотеке Skills обнаружен неверный идентификатор.")
            filename = f"{skill_slug(item)}--{item['id']}.md"
            replace_file(folder / filename, markdown_copy(item).encode("utf-8"))
        # The manifest carries IDs, groups and local connection settings. Keys stay out of Markdown.
        snapshot = json.dumps({"version": 1, "skills": items}, ensure_ascii=False, indent=2)
        replace_file(folder / INDEX, snapshot.encode("utf-8"))

    try:
        try:
            save(store.skills_directory)
        except PermissionError:
            if store.skills_directory == store.root / "Skills":
                raise
            store.skills_directory = store.root / "Skills"
            save(store.skills_directory)
    except OSError as exc:
        raise ValueError("Не удалось сохранить копии навыков в папку Skills.") from exc
