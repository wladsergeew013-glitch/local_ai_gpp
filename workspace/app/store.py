"""Transactional Workspace metadata. DSH JSONL remains the canonical agent log."""

from __future__ import annotations
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
ACTIVE = {"starting", "running", "awaiting_permission", "stopping"}


def now():
    return datetime.now(timezone.utc).isoformat()


def identifier():
    return uuid.uuid4().hex


def data_root():
    return Path(
        os.environ.get("LOCAL_AI_WORKSPACE_DATA_DIR")
        or (
            Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share"))
            / "LocalAIWorkspace"
        )
    ).resolve()


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(
            self.root / "workspace.sqlite3", check_same_thread=False
        )
        self.connection.row_factory = sqlite3.Row
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            self.connection.close()
            raise RuntimeError(
                "WORKSPACE_DATA_NEWER: use the newer application; the database was not reset."
            )
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        if version == 0:
            with self.connection:
                self.connection.executescript(
                    """
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,path TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS providers(id TEXT PRIMARY KEY,name TEXT NOT NULL,base_url TEXT NOT NULL,model TEXT NOT NULL,api_key TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),provider_id TEXT NOT NULL REFERENCES providers(id),title TEXT NOT NULL,prompt TEXT NOT NULL,status TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,session_id TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,permission TEXT NOT NULL,details TEXT NOT NULL DEFAULT '{}',action_id TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT NOT NULL REFERENCES tasks(id),source_key TEXT NOT NULL,kind TEXT NOT NULL,created_at TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(task_id,source_key));
CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),kind TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,decided_at TEXT);
CREATE TABLE IF NOT EXISTS connectors(id TEXT PRIMARY KEY,name TEXT NOT NULL,base_url TEXT NOT NULL,api_key TEXT NOT NULL DEFAULT '',allow_write INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS connector_actions(action_id TEXT PRIMARY KEY,connector_id TEXT NOT NULL REFERENCES connectors(id),payload TEXT NOT NULL,status TEXT NOT NULL,result TEXT,created_at TEXT NOT NULL);
PRAGMA user_version=1;
"""
                )

    @contextmanager
    def transaction(self):
        with self.lock, self.connection:
            yield self.connection

    def rows(self, query, args=()):
        with self.lock:
            return [
                dict(row) for row in self.connection.execute(query, args).fetchall()
            ]

    def one(self, table, value):
        if table not in {"projects", "tasks", "providers", "connectors", "approvals"}:
            raise ValueError("Unknown entity")
        rows = self.rows("SELECT * FROM " + table + " WHERE id=?", (value,))
        if not rows:
            raise KeyError(value)
        row = rows[0]
        for field in ("details", "payload"):
            if field in row:
                row[field] = json.loads(row[field])
        return row

    def event(self, task_id, kind, payload, key=None):
        with self.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO events(task_id,source_key,kind,created_at,payload) VALUES(?,?,?,?,?)",
                (
                    task_id,
                    key or identifier(),
                    kind,
                    now(),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )

    def update_task(self, task_id, status=None, details=None, expected=None):
        with self.transaction() as db:
            current = self.one("tasks", task_id)
            if expected is not None and current["revision"] != expected:
                raise ValueError("TASK_CONFLICT: refresh the task before acting.")
            merged = {**current["details"], **(details or {})}
            db.execute(
                "UPDATE tasks SET status=?,details=?,revision=revision+1,updated_at=? WHERE id=?",
                (
                    status or current["status"],
                    json.dumps(merged, ensure_ascii=False),
                    now(),
                    task_id,
                ),
            )
        return self.one("tasks", task_id)

    def recover(self):
        for row in self.rows("SELECT id,status FROM tasks"):
            if row["status"] in ACTIVE:
                self.update_task(
                    row["id"],
                    "interrupted",
                    {
                        "recovery": "The owning Workspace stopped. No input or action was replayed."
                    },
                )
                self.event(
                    row["id"],
                    "recovery",
                    {"previous_status": row["status"], "replayed": False},
                )
        with self.transaction() as db:
            db.execute(
                "UPDATE approvals SET status='cancelled',decided_at=? WHERE status='pending'",
                (now(),),
            )
            db.execute(
                "UPDATE connector_actions SET status='uncertain' WHERE status='sending'"
            )

    def snapshot(self, destination: Path):
        with self.lock:
            destination.mkdir(parents=True, exist_ok=False)
            target = sqlite3.connect(destination / "workspace.sqlite3")
            try:
                self.connection.backup(target)
            finally:
                target.close()
            (destination / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "created_at": now(),
                        "dsh_version": "0.2.2rc2",
                        "dsh_format": 4,
                    }
                ),
                encoding="utf-8",
            )
        return destination

    def close(self):
        with self.lock:
            self.connection.close()
