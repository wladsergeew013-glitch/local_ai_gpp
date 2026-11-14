"""Task-relative text diffs preserve edits that already existed at task start."""

import difflib
import hashlib
import json
import os
import subprocess
from pathlib import Path

MAX_FILE = 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024


def git_files(project: Path):
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

    def git(*args):
        result = subprocess.run(
            ["git", "-c", "safe.directory=" + str(project), "-C", str(project), *args],
            capture_output=True,
            timeout=15,
            **flags
        )
        if result.returncode:
            raise ValueError(
                "Select an existing Git working tree: "
                + result.stderr.decode("utf-8", "replace")[-1000:]
            )
        return result.stdout

    try:
        inside = git("rev-parse", "--is-inside-work-tree").strip() == b"true"
    except (ValueError, FileNotFoundError):
        inside = False
    if not inside:
        paths = []
        skipped = {
            ".git",
            ".venv",
            "venv",
            "node_modules",
            "__pycache__",
            "dist",
            "build",
        }
        for directory, folders, files in os.walk(project, followlinks=False):
            folders[:] = [
                name
                for name in folders
                if name not in skipped
                and not (Path(directory) / name).is_symlink()
                and not (
                    getattr((Path(directory) / name).lstat(), "st_file_attributes", 0)
                    & 0x400
                )
            ]
            paths.extend(
                (Path(directory) / name).relative_to(project).as_posix()
                for name in files
            )
            if len(paths) > 20000:
                raise ValueError(
                    "Папка слишком велика для сравнения. Подключите более узкую папку или Git-репозиторий."
                )
        return paths, ""
    paths = (
        git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
        .decode("utf-8", "replace")
        .split("\0")
    )
    try:
        head = git("rev-parse", "HEAD").decode().strip()
    except ValueError:
        head = ""
    return [p for p in dict.fromkeys(paths) if p], head


def capture(project: Path, destination: Path):
    files, head = git_files(project)
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {"head": head, "files": {}, "skipped": []}
    total = 0
    for name in files:
        source = project / name
        try:
            resolved = source.resolve()
            if (
                not resolved.is_relative_to(project.resolve())
                or source.is_symlink()
                or source.stat().st_size > MAX_FILE
            ):
                raise ValueError()
            content = source.read_bytes()
            if b"\0" in content or total + len(content) > MAX_TOTAL:
                raise ValueError()
            content.decode("utf-8")
        except (OSError, ValueError, UnicodeError):
            manifest["skipped"].append(name)
            continue
        key = hashlib.sha256(name.encode()).hexdigest()
        (destination / key).write_bytes(content)
        manifest["files"][name] = {
            "key": key,
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        total += len(content)
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    return {
        "head": head,
        "captured_files": len(manifest["files"]),
        "skipped_files": len(manifest["skipped"]),
    }


def compare(project: Path, baseline: Path):
    manifest = json.loads((baseline / "manifest.json").read_text(encoding="utf-8"))
    files, head = git_files(project)
    changes = []
    parts = []
    size = 0
    for name in sorted(set(files) | set(manifest["files"])):
        if name in manifest["skipped"]:
            continue
        record = manifest["files"].get(name)
        before = (baseline / record["key"]).read_bytes() if record else b""
        target = project / name
        if (
            not target.resolve().is_relative_to(project.resolve())
            or target.is_symlink()
        ):
            continue
        try:
            after = target.read_bytes() if target.exists() else b""
        except OSError:
            continue
        if before == after:
            continue
        if len(after) > MAX_FILE or b"\0" in after:
            changes.append({"path": name, "binary_or_large": True})
            continue
        try:
            diff = "".join(
                difflib.unified_diff(
                    before.decode("utf-8").splitlines(True),
                    after.decode("utf-8").splitlines(True),
                    fromfile="before/" + name,
                    tofile="after/" + name,
                )
            )
        except UnicodeError:
            continue
        changes.append(
            {"path": name, "added": not bool(record), "deleted": not target.exists()}
        )
        if size + len(diff) <= 4 * MAX_FILE:
            parts.append(diff)
            size += len(diff)
    return {
        "files": changes,
        "diff": "".join(parts),
        "baseline_head": manifest["head"],
        "current_head": head,
        "skipped_files": manifest["skipped"],
        "comparison": "task-start working tree; pre-existing edits are preserved",
    }
