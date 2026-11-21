"""Store portable package snapshots separately from profile settings and credentials."""

import hashlib
import json
import sqlite3
from contextlib import closing, contextmanager

from workspace.app.skill_storage import check_directory


TDMS_PRODUCT = "TDMS-Project-Manager"
TDMS_GROUP = "tdms"
TDMS_ENDPOINT = "http://127.0.0.1:8788/mcp"
TDMS_TOOLS = {
    "tdms-stamps": "stamp_people",
    "tdms-pdf-bookmarks": "pdf_bookmarks",
    "tdms-project": "tdms_project_prepare",
    "tdms-dwg": "tdms_match_drawings",
    "tdms-validate": "tdms_validate",
}


def legacy_tdms_name(item):
    connection = item.get("connection") or {}
    group = item.get("group") or {}
    name = item["name"]
    if (name in TDMS_TOOLS and TDMS_TOOLS[name] == connection.get("tool")
            and group.get("id") == TDMS_GROUP
            and item.get("bundle_source") == connection.get("url") == TDMS_ENDPOINT):
        return name
    return None


class PluginCatalog:
    def __init__(self, store):
        self.store = store
        self.folder = store.root / "Skills"
        self.path = self.folder / "packages.sqlite3"

    @contextmanager
    def _database(self, *, create=False):
        check_directory(self.folder)
        if self.path.is_symlink():
            raise ValueError("База пакетов в Skills должна быть обычным файлом.")
        if create:
            self.folder.mkdir(parents=True, exist_ok=True)
        target = self.path if create else self.path.as_uri() + "?mode=ro"
        with closing(sqlite3.connect(target, uri=not create, timeout=10)) as db, db:
            if create:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                db.execute("CREATE TABLE IF NOT EXISTS packages("
                           "product TEXT PRIMARY KEY COLLATE NOCASE, snapshot TEXT NOT NULL)")
            yield db

    def snapshots(self):
        if not self.path.exists():
            return []
        with self._database() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='packages'").fetchone():
                return []
            return [json.loads(row[0]) for row in db.execute("SELECT snapshot FROM packages ORDER BY rowid")]

    def backup(self, root):
        if not self.path.exists():
            return
        destination = root / "Skills"
        destination.mkdir()
        with self._database() as source, closing(sqlite3.connect(destination / self.path.name)) as target:
            source.backup(target)

    def install(self, package):
        config = package.manifest
        product, group = config["product"], config["skillGroup"]
        entries = {entry["name"]: entry for entry in config["skills"]}
        legacy = self.store.rows("SELECT id,definition FROM markdown_skills")
        legacy_ids = {row["id"] for row in legacy}
        candidates = {}
        if (product, group["id"]) == (TDMS_PRODUCT, TDMS_GROUP):
            for row in legacy:
                name = legacy_tdms_name(json.loads(row["definition"]))
                if name in entries and TDMS_TOOLS[name] in {tool["name"] for tool in entries[name]["tools"]}:
                    candidates.setdefault(name, []).append(row["id"])
        with self._database(create=True) as db:
            rows = db.execute("SELECT product,snapshot FROM packages").fetchall()
            installed = {key.casefold(): json.loads(raw) for key, raw in rows}
            old = installed.get(product.casefold())
            if old and old["manifest"]["product"] != product:
                raise ValueError(f"product — конфликт регистра с «{old['manifest']['product']}».")
            if old and old["manifest"]["skillGroup"]["id"] != group["id"]:
                raise ValueError("skillGroup.id — смена идентификатора установленной группы требует миграции.")
            if old and old["revision"] == package.revision:
                return {"product": product, "revision": old["revision"], "retained": len(entries), "migrated": 0}

            ids = dict(old["ids"]) if old else {}
            folded = {name.casefold(): name for name in ids}
            occupied = {key for snapshot in installed.values() for key in snapshot["ids"].values()}
            migrated = 0
            for name in entries:
                if folded.get(name.casefold(), name) != name:
                    raise ValueError(f"skills.name — конфликт регистра с ранее установленным «{folded[name.casefold()]}».")
                if name in ids:
                    continue
                aliases = candidates.get(name, [])
                if len(aliases) > 1:
                    raise ValueError(f"Миграция TDMS неоднозначна для «{name}».")
                stable = hashlib.sha256(f"workspace-package\0{product}\0{group['id']}\0{name}".encode()).hexdigest()[:32]
                key = aliases[0] if aliases else stable
                if key in occupied or (key in legacy_ids and not aliases):
                    raise ValueError(f"Конфликт идентификатора навыка «{name}».")
                occupied.add(key)
                ids[name] = key
                migrated += bool(aliases)
            snapshot = {"manifest": config, "raw_manifest": package.raw_manifest, "markdown": package.markdown,
                        "instructions": package.instructions, "ids": ids, "revision": package.revision}
            db.execute("INSERT INTO packages(product,snapshot) VALUES(?,?) ON CONFLICT(product) "
                       "DO UPDATE SET snapshot=excluded.snapshot",
                       (product, json.dumps(snapshot, ensure_ascii=False)))
        return {"product": product, "revision": package.revision, "installed": len(entries), "migrated": migrated}

    def package(self, product):
        for package in self.snapshots():
            if package["manifest"]["product"] == product:
                return package
        raise ValueError("Пакет не установлен.")

    def enabled(self, product, name):
        return not self.store.rows(
            "SELECT 1 FROM plugin_preferences WHERE product=? AND skill IN ('',?) AND enabled=0",
            (product, name),
        )

    def set_enabled(self, product, name, enabled):
        package = self.package(product)
        if name and name not in package["ids"]:
            raise ValueError("В пакете нет такого навыка.")
        with self.store.transaction() as db:
            db.execute("INSERT INTO plugin_preferences VALUES(?,?,?) ON CONFLICT(product,skill) "
                       "DO UPDATE SET enabled=excluded.enabled", (product, name, int(enabled)))

    def credential(self, product, endpoint):
        rows = self.store.rows("SELECT api_key FROM plugin_credentials WHERE product=? AND endpoint=?",
                               (product, endpoint))
        return rows[0]["api_key"] if rows else ""

    def set_credential(self, product, key):
        endpoint = self.package(product)["manifest"]["mcp"]["url"]
        if any(not 33 <= ord(char) <= 126 for char in key) or len(key) > 512:
            raise ValueError("Требуется ключ MCP без пробелов длиной до 512 символов.")
        with self.store.transaction() as db:
            db.execute("INSERT INTO plugin_credentials VALUES(?,?,?) ON CONFLICT(product,endpoint) "
                       "DO UPDATE SET api_key=excluded.api_key", (product, endpoint, key))

    def definitions(self, *, snapshots=None):
        from workspace.app.skills import instruction_sections

        definitions = {}
        disabled = {(row["product"], row["skill"]) for row in self.store.rows(
            "SELECT product,skill FROM plugin_preferences WHERE enabled=0")}
        for pkg in snapshots if snapshots is not None else self.snapshots():
            manifest, ids = pkg["manifest"], pkg["ids"]
            product, group = manifest["product"], manifest["skillGroup"]
            active = {entry["name"] for entry in manifest["skills"]}
            group_enabled = (product, "") not in disabled
            for entry in manifest["skills"]:
                name = entry["name"]
                key, instructions = ids[name], pkg["instructions"][name]
                sections, _ = instruction_sections(instructions)
                definitions[key] = {
                    "id": key, "name": name, "title": entry["title"], "description": entry["description"],
                    "connection": None, "tools": entry["tools"], "accepts": entry["accepts"],
                    "group": {**group, "key": product, "enabled": group_enabled},
                    "key": f"{product}/{group['id']}/{name}", "product": product, "mcp": manifest["mcp"],
                    "revision": pkg["revision"], "package_version": manifest.get("packageVersion"),
                    "instructions": instructions, "sections": sections,
                    "enabled": group_enabled and (product, name) not in disabled,
                    "skill_enabled": (product, name) not in disabled,
                }
            for name in ids.keys() - active:
                definitions[ids[name]] = None
        return definitions

    def credentials_snapshot(self, definitions):
        keys = {(row["product"], row["endpoint"]): row["api_key"] for row in self.store.rows(
            "SELECT product,endpoint,api_key FROM plugin_credentials"
        )}
        return {
            key: {**skill, "api_key": keys.get((skill["product"], skill["mcp"]["url"]), "")}
            for key, skill in definitions.items() if skill is not None
        }
