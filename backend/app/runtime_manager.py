"""One admitted inference and capability-scoped cancellation, independent of UI."""

from __future__ import annotations

import secrets
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field

from fastapi import HTTPException

API_VERSION = "1.1"


def failure(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status,
        {"code": code, "message": message},
        headers={"Retry-After": "1"} if status == 409 else None,
    )


@dataclass
class InferenceLease:
    manager: "RuntimeManager"
    request_id: str
    owner_id: str
    token: str = field(repr=False)
    cancelled: threading.Event = field(default_factory=threading.Event, repr=False)
    worker: object | None = field(default=None, repr=False)
    closed: bool = False

    def attach(self, worker):
        with self.manager.lock:
            self.worker = worker
            if self.cancelled.is_set():
                worker.close()
                raise failure(
                    499,
                    "REQUEST_CANCELLED",
                    "The owning client cancelled this request.",
                )

    def check(self):
        if self.cancelled.is_set():
            raise failure(
                499, "REQUEST_CANCELLED", "The owning client cancelled this request."
            )

    def close(self):
        with self.manager.lock:
            if not self.closed:
                self.closed = True
                if self.manager.active is self:
                    self.manager.active = None
                    from backend.app import core

                    core.RUNTIME_LOCK.release()


class RuntimeManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.active: InferenceLease | None = None

    def admit(self, owner_id: str = "", token: str = "") -> InferenceLease:
        from backend.app import core

        with self.lock:
            if self.active is not None or not core.RUNTIME_LOCK.acquire(blocking=False):
                raise failure(
                    409,
                    "RUNTIME_BUSY",
                    "One model operation is active; retry after it finishes.",
                )
            lease = InferenceLease(
                self,
                uuid.uuid4().hex,
                owner_id or uuid.uuid4().hex,
                token or secrets.token_urlsafe(32),
            )
            self.active = lease
            return lease

    @contextmanager
    def operation(self, lease: InferenceLease | None = None):
        own = lease is None
        lease = lease or self.admit()
        try:
            with self.lock:
                if lease is not self.active or lease.closed:
                    raise failure(
                        409,
                        "REQUEST_FINISHED",
                        "The request lease is no longer active.",
                    )
            lease.check()
            yield lease
        finally:
            if own:
                lease.close()

    def cancel(self, identifier: str, token: str, *, owner=False) -> dict:
        with self.lock:
            lease = self.active
            if (
                lease is None
                or (lease.owner_id if owner else lease.request_id) != identifier
            ):
                return {"cancelled": 0, "active": False}
            if not token or not secrets.compare_digest(lease.token, token):
                raise failure(
                    403, "CANCEL_FORBIDDEN", "A request ownership token is required."
                )
            lease.cancelled.set()
            # Keep admission until the generator has observed termination.
            if lease.worker is not None:
                lease.worker.close()
            return {"cancelled": 1, "active": True, "request_id": lease.request_id}

    def snapshot(self):
        with self.lock:
            lease = self.active
            return {
                "capacity": 1,
                "queue_capacity": 0,
                "busy": lease is not None,
                "active_request_id": lease.request_id if lease else None,
            }


runtime_manager = RuntimeManager()
