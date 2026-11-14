"""Optional Workspace shell callbacks. Headless API never imports GUI packages."""

from fastapi import HTTPException

_callbacks = {}


def register(**callbacks):
    _callbacks.clear()
    _callbacks.update(callbacks)


def status():
    return {
        "desktop": bool(_callbacks),
        "mini_helper_owner": "workspace",
        "mini_available": bool(_callbacks),
    }


def invoke(name, payload=None):
    callback = _callbacks.get(name)
    if callback is None:
        raise HTTPException(404, "Workspace desktop shell is not running.")
    return callback(payload or {}) or {"ok": True}


def activity(state):
    callback = _callbacks.get("activity")
    if callback:
        callback(state)
