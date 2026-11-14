"""One desktop owner per data directory; the OS releases the lock on exit."""
import os
from pathlib import Path
from contextlib import contextmanager
import threading

_process_owners = {}
_owners_lock = threading.RLock()


@contextmanager
def owned_instance(path):
    """Desktop and ASGI lifespan share a reentrant lease in this process."""
    key=str(Path(path).resolve())
    with _owners_lock:
        entry=_process_owners.get(key)
        if entry is None:
            lease=InstanceLock(Path(path));lease.__enter__()
            entry={'lease':lease,'count':0};_process_owners[key]=entry
        entry['count']+=1
    try:yield
    finally:
        with _owners_lock:
            entry['count']-=1
            if not entry['count']:
                _process_owners.pop(key,None)
                entry['lease'].__exit__(None,None,None)


class InstanceAlreadyRunning(RuntimeError):
    pass


class InstanceLock:
    def __init__(self, path: Path):
        self.path, self.handle = path, None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open('a+b')
        try:
            if handle.tell() == 0:
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise InstanceAlreadyRunning('Local AI уже запущен для этого каталога данных.') from exc
        self.handle = handle
        return self

    def __exit__(self, *_):
        if self.handle is not None:
            self.handle.close()
            self.handle = None
