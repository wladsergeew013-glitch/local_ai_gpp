"""Projects organize conversations. Folder/GitHub connections are optional."""

import os
import re
from pathlib import Path
from workspace.app.store import identifier, now
from workspace.app.owned_process import OwnedProcess


def create_project(store, name, path="", github_url=""):
    if path.strip() and not Path(path.strip().strip('"')).expanduser().is_absolute():
        raise ValueError("Укажите абсолютный путь к папке.")
    key = identifier()
    source = "github" if github_url else "folder" if path.strip() else "virtual"
    if source == "github":
        match = re.fullmatch(
            r"https://github\.com/([A-Za-z0-9_-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?",
            github_url.strip(),
        )
        if not match or match[2] in {".", ".."}:
            raise ValueError(
                "Укажите адрес https://github.com/owner/repository без пароля или параметров."
            )
        url = f"https://github.com/{match[1]}/{match[2]}.git"
        target = (
            Path(path).expanduser().resolve() if path else store.root / "projects" / key
        )
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            raise ValueError("Для клонирования выберите новую или пустую папку.")
        target.parent.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
        process = OwnedProcess(
            [
                "git",
                "-c",
                "core.longpaths=true",
                "clone",
                "--config",
                "core.longpaths=true",
                "--depth=1",
                "--quiet",
                "--",
                url,
                str(target),
            ],
            str(target.parent),
            env,
            lambda e: None,
            lambda *_: None,
        )
        if not process.finished.wait(180):
            process.stop()
            raise ValueError(
                "Клонирование не завершилось за 180 секунд. Папка сохранена для проверки."
            )
        if process.process.returncode:
            raise ValueError(
                "Не удалось подключить GitHub. Проверьте URL и вход в Git на этом компьютере."
            )
    elif source == "folder":
        try:
            target = Path(path.strip().strip('"')).expanduser().resolve(strict=True)
        except OSError as exc:
            raise ValueError("Папка не найдена. Укажите существующую папку.") from exc
        if not target.is_dir():
            raise ValueError("Выберите существующую папку.")
        existing = store.rows("SELECT * FROM projects WHERE path=?", (str(target),))
        if existing:
            return existing[0]
        url = ""
    else:
        target = store.root / "projects" / key
        target.mkdir(parents=True)
        url = ""
    value = {"id": key, "name": name, "path": str(target), "created_at": now()}
    with store.transaction() as db:
        db.execute("INSERT INTO projects VALUES(?,?,?,?)", tuple(value.values()))
        db.execute("INSERT INTO project_sources VALUES(?,?,?)", (key, source, url))
    return {**value, "source": source, "github_url": url}
