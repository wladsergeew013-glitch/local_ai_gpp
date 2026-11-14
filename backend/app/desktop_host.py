"""Optional desktop callbacks. The service never imports a windowing toolkit."""

from fastapi import HTTPException

_callbacks = {}


def register(**callbacks):
    _callbacks.clear()
    _callbacks.update(callbacks)


def active():
    return bool(_callbacks)


def invoke(name):
    callback = _callbacks.get(name)
    if callback is None:
        raise HTTPException(
            404, "DESKTOP_UNAVAILABLE: this Runtime has no desktop host"
        )
    return callback()
