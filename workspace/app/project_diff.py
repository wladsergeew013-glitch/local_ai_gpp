"""Bounded comparisons against the working tree at the start of a run."""

import difflib
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

MAX_FILE = 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
MAX_DIFF = 4 * MAX_FILE


def is_link(metadata):
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


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
        omissions = []
        skipped = {
            ".git",
            ".venv",
            "venv",
            "node_modules",
            "__pycache__",
            "dist",
            "build",
        }

        def scan_error(exc):
            name = (
                Path(exc.filename).relative_to(project).as_posix()
                if exc.filename
                else "."
            )
            omissions.append({"path": name, "reason": "scan_error"})

        for directory, folders, files in os.walk(
            project, followlinks=False, onerror=scan_error
        ):
            for name in folders[:]:
                entry = Path(directory) / name
                if name in skipped:
                    folders.remove(name)
                    continue
                try:
                    if is_link(entry.lstat()):
                        paths.append(entry.relative_to(project).as_posix())
                        folders.remove(name)
                except OSError as exc:
                    scan_error(exc)
                    folders.remove(name)
            paths.extend(
                (Path(directory) / name).relative_to(project).as_posix()
                for name in files
            )
            if len(paths) > 20000:
                raise ValueError(
                    "Папка слишком велика для сравнения. Подключите более узкую папку или Git-репозиторий."
                )
        return paths, "", omissions
    paths = (
        git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
        .decode("utf-8", "replace")
        .split("\0")
    )
    try:
        head = git("rev-parse", "HEAD").decode().strip()
    except ValueError:
        head = ""
    return [p for p in dict.fromkeys(paths) if p], head, []


def inspect_entry(project, name, remaining):
    relative = Path(name)
    if relative.anchor or not relative.parts or ".." in relative.parts:
        return {"kind": "unknown", "reason": "invalid_path"}, None
    path = project
    try:
        for index, part in enumerate(relative.parts):
            path /= part
            metadata = path.lstat()
            if is_link(metadata):
                if index != len(relative.parts) - 1:
                    return {"kind": "unknown", "reason": "linked_parent"}, None
                return {
                    "kind": "link",
                    "sha256": hashlib.sha256(
                        os.fsencode(os.readlink(path))
                    ).hexdigest(),
                }, None
    except (FileNotFoundError, NotADirectoryError):
        return {"kind": "missing"}, None
    except OSError:
        return {"kind": "unknown", "reason": "read_error"}, None
    if not stat.S_ISREG(metadata.st_mode):
        kind = "directory" if stat.S_ISDIR(metadata.st_mode) else "special"
        return {"kind": kind, "reason": kind}, None
    record = {"kind": "file", "size": metadata.st_size}
    limit = min(MAX_FILE, remaining)
    if metadata.st_size > limit:
        return {
            **record,
            "reason": "large" if metadata.st_size > MAX_FILE else "size_limit",
        }, None
    try:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not os.path.samestat(metadata, opened) or not stat.S_ISREG(
                opened.st_mode
            ):
                return {**record, "reason": "read_error"}, None
            content = stream.read(limit + 1)
            current = os.fstat(stream.fileno())
            if (
                len(content) != metadata.st_size
                or current.st_mtime_ns != metadata.st_mtime_ns
            ):
                return {**record, "reason": "changed_during_read"}, None
    except OSError:
        return {**record, "reason": "read_error"}, None
    record["sha256"] = hashlib.sha256(content).hexdigest()
    if b"\0" in content:
        record["text_omitted"] = "binary"
    else:
        try:
            content.decode("utf-8")
        except UnicodeError:
            record["text_omitted"] = "non_utf8"
    return record, content


def capture(project: Path, destination: Path):
    files, head, omissions = git_files(project)
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {"head": head, "files": {}, "omissions": omissions}
    remaining = MAX_TOTAL
    for name in sorted(files):
        record, content = inspect_entry(project, name, remaining)
        if record["kind"] == "missing":
            continue
        if content is not None:
            remaining -= len(content)
            if not record.get("text_omitted"):
                key = hashlib.sha256(os.fsencode(name)).hexdigest()
                (destination / key).write_bytes(content)
                record["key"] = key
        manifest["files"][name] = record
    stage = destination / "manifest.tmp"
    stage.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    stage.replace(destination / "manifest.json")
    return {
        "head": head,
        "captured_files": sum("key" in row for row in manifest["files"].values()),
        "skipped_files": len(omissions)
        + sum(
            "reason" in row or "text_omitted" in row
            for row in manifest["files"].values()
        ),
    }


def compare(project: Path, baseline: Path):
    manifest = json.loads((baseline / "manifest.json").read_text(encoding="utf-8"))
    files, head, scan_omissions = git_files(project)
    previous = {
        name: {"kind": "file", **record} for name, record in manifest["files"].items()
    }
    for name in manifest.get("skipped", []):  # Baselines from Workspace 0.2.0.
        previous[name] = {"kind": "unknown", "reason": "legacy_baseline"}
    omissions = [
        {**row, "stage": stage}
        for stage, rows in (
            ("before", manifest.get("omissions", [])),
            ("after", scan_omissions),
        )
        for row in rows
    ]
    changes = []
    parts = []
    size, remaining = 0, MAX_TOTAL
    truncated = False
    for name in sorted(set(files) | set(previous)):
        before = previous.get(name, {"kind": "missing"})
        after, content = inspect_entry(project, name, remaining)
        if content is not None:
            remaining -= len(content)
        for stage, entry in (("before", before), ("after", after)):
            if entry.get("reason"):
                omissions.append(
                    {"path": name, "stage": stage, "reason": entry["reason"]}
                )
        first, last = before["kind"], after["kind"]
        if "unknown" in {first, last}:
            continue
        state = None
        if first != last:
            state = (
                "added"
                if first == "missing"
                else "deleted" if last == "missing" else "type_changed"
            )
        elif before.get("sha256") and after.get("sha256"):
            if before["sha256"] != after["sha256"]:
                state = "modified"
        elif (
            first == "file"
            and before.get("size") is not None
            and after.get("size") is not None
        ):
            if before["size"] != after["size"]:
                state = "modified"
        if state is None:
            continue
        change = {
            "path": name,
            "status": state,
            "before_type": first,
            "after_type": last,
            "added": state == "added",
            "deleted": state == "deleted",
        }
        changes.append(change)
        if ({first, last} - {"file", "missing"}) or any(
            entry.get("reason") or entry.get("text_omitted")
            for entry in (before, after)
        ):
            change["text_omitted"] = (
                after.get("text_omitted")
                or before.get("text_omitted")
                or after.get("reason")
                or before.get("reason")
                or "type"
            )
            change["binary_or_large"] = change["text_omitted"] in {
                "binary",
                "large",
                "non_utf8",
            }
            continue
        try:
            key = before.get("key", "")
            if first == "file" and not (
                len(key) == 64 and all(c in "0123456789abcdef" for c in key)
            ):
                raise ValueError("Invalid snapshot entry")
            snapshot = baseline / key
            if first == "file" and (
                is_link(snapshot.lstat()) or snapshot.stat().st_size > MAX_FILE
            ):
                raise ValueError("Invalid snapshot file")
            old = snapshot.read_bytes().decode("utf-8") if first == "file" else ""
            new = content.decode("utf-8") if last == "file" else ""
        except (OSError, ValueError, UnicodeError):
            omissions.append({"path": name, "stage": "before", "reason": "read_error"})
            continue
        diff = "".join(
            difflib.unified_diff(
                old.splitlines(True),
                new.splitlines(True),
                fromfile="before/" + name,
                tofile="after/" + name,
            )
        )
        length = len(diff.encode("utf-8"))
        if size + length <= MAX_DIFF:
            parts.append(diff)
            size += length
        else:
            truncated = True
            change["text_omitted"] = "diff_limit"
    return {
        "files": changes,
        "diff": "".join(parts),
        "baseline_head": manifest["head"],
        "current_head": head,
        "skipped_files": sorted({row["path"] for row in omissions}),
        "omissions": omissions,
        "baseline_available": True,
        "complete": not omissions and not truncated,
        "truncated": truncated,
        "comparison": "task-start working tree; pre-existing edits are preserved",
    }
