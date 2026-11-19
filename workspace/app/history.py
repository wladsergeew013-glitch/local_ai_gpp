"""Pinned read-only reader of DSH v4 plaintext JSONL; never repairs/replays it."""

import json
from pathlib import Path
from workspace.app.dsh_profile import SESSION_FORMAT


def read_session(home: Path, session_id=None):
    root = home / "sessions"
    if root.is_symlink() or not root.resolve().is_relative_to(home.resolve()):
        raise ValueError("DSH_HISTORY_PATH_INVALID")
    if not root.exists():
        return [], False
    result = []
    present = False
    for path in sorted(root.rglob("*.jsonl")):
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            raise ValueError("DSH_HISTORY_PATH_INVALID")
        with path.open("rb") as stream:
            first = stream.readline()
            if not first:
                continue
            header = json.loads(first)
            if (
                not isinstance(header, dict)
                or header.get("type") != "session"
                or header.get("version") != SESSION_FORMAT
            ):
                raise ValueError(
                    "DSH_HISTORY_UNSUPPORTED: expected session format "
                    + str(SESSION_FORMAT)
                )
            session = header["id"]
            if session_id is not None and session != session_id:
                continue
            present = True
            for index, line in enumerate(stream):
                if not line.endswith(b"\n"):
                    # A live/crashed writer may leave an uncommitted tail; do not invent an event.
                    break
                event = json.loads(line)
                if (
                    not isinstance(event, dict)
                    or type(event.get("seq")) is not int
                    or event["seq"] != index
                ):
                    raise ValueError(
                        "DSH_HISTORY_CORRUPT: noncontiguous event sequence; no repair or replay was attempted."
                    )
                result.append((f'dsh:{session}:{event["seq"]}', event))
    return result, present


def canonical_events(home: Path):
    return read_session(home)[0]


def reconcile(store, task):
    events, present = read_session(
        store.root / "tasks" / task["id"] / "dsh", task["session_id"]
    )
    for key, event in events:
        store.event(task["id"], "dsh", event, key)
    return {
        "canonical_events": len(events),
        "session_exists": present,
        "format": SESSION_FORMAT,
        "replayed": False,
    }
