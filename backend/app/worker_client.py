"""Persistent, killable llama.cpp subprocess with independently drained pipes."""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import contextlib
import signal
import threading
import time
from collections import deque
from typing import Any, Iterator

from fastapi import HTTPException


class WorkerClient:
    def __init__(self, command: list[str], **kwargs: Any):
        self.job = None
        self.command_lock = threading.Lock()
        self.writer_done: threading.Event | None = None
        self.owns_process_group = os.name != 'nt' and kwargs.setdefault('start_new_session', True)
        if os.name == 'nt':
            from backend.app.windows_job import WorkerJob
            self.job = WorkerJob()
        try:
            self.process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, **kwargs,
            )
        except BaseException:
            if self.job:
                self.job.close()
            raise
        if self.job:
            try:
                self.job.assign(self.process)
            except BaseException:
                self.process.kill()
                self.job.close()
                raise
        self.output: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=128)
        self.stderr: deque[str] = deque(maxlen=80)
        self.stopped = threading.Event()
        self.close_lock = threading.Lock()
        self.runtime: dict[str, Any] = {}
        self.offload_counts: dict[str, int] = {}
        threading.Thread(target=self._stdout, daemon=True).start()
        threading.Thread(target=self._stderr, daemon=True).start()

    def _put(self, event: dict[str, Any]) -> None:
        while not self.stopped.is_set():
            try:
                self.output.put(event, timeout=0.2)
                return
            except queue.Full:
                continue

    def _stdout(self) -> None:
        try:
            assert self.process.stdout is not None
            for line in self.process.stdout:
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise ValueError('Expected a worker event object')
                    self._put(event)
                except (ValueError, TypeError):
                    self.stderr.append(line.decode('utf-8', 'replace')[-2000:])
        except (OSError, ValueError):
            pass  # Pipes can close while the native worker is being cancelled.
        finally:
            self._put({'type': 'eof'})

    def _stderr(self) -> None:
        assert self.process.stderr is not None
        try:
            for line in self.process.stderr:
                text = line.decode('utf-8', 'replace')[-2000:]
                self.stderr.append(text)
                match = re.search(r'offloaded (\d+)/(\d+) layers to GPU', text)
                if match:
                    self.offload_counts = {'gpu_offloaded_layers': int(match[1]), 'model_layers': int(match[2])}
        except (OSError, ValueError):
            pass

    def events(self, payload: dict[str, Any], *, timeout: float = 180,
               idle_timeout: float = 60) -> Iterator[dict[str, Any]]:
        if not self.command_lock.acquire(blocking=False):
            raise HTTPException(409, 'Worker is busy.')
        complete = False
        started = activity = heartbeat = time.monotonic()
        written = threading.Event()
        self.writer_done = written
        errors: list[BaseException] = []
        def write_request():
            try:
                assert self.process.stdin is not None
                self.process.stdin.write((json.dumps(payload, ensure_ascii=False) + '\n').encode('utf-8'))
                self.process.stdin.flush()
            except BaseException as error:
                errors.append(error)
            finally:
                written.set()
        try:
            threading.Thread(target=write_request, daemon=True).start()
            while True:
                now = time.monotonic()
                if now - started >= timeout or now - activity >= idle_timeout:
                    raise HTTPException(504, 'Inference timeout; worker stopped. Retry the request.')
                if written.is_set() and errors:
                    raise HTTPException(502, f'Inference worker pipe failed: {errors[0]}')
                try:
                    event = self.output.get(timeout=min(0.2, max(0.01, timeout - (now - started))))
                except queue.Empty:
                    if self.process.poll() is not None:
                        raise HTTPException(502, 'Inference worker exited: ' + ''.join(self.stderr)[-4000:])
                    # Let HTTP streams observe a disconnected client during model loading/prefill.
                    if now - heartbeat >= .1:
                        heartbeat = now
                        yield {'type': 'heartbeat'}
                    continue
                activity = time.monotonic()
                kind = event.get('type')
                if kind == 'eof':
                    raise HTTPException(502, 'Inference worker exited: ' + ''.join(self.stderr)[-4000:])
                if kind == 'runtime':
                    self.runtime = event.get('runtime') or {}
                if kind in {'ready', 'done', 'result', 'error'}:
                    complete = True
                yield event
                if complete:
                    return
        except (BrokenPipeError, OSError) as exc:
            raise HTTPException(502, f'Inference worker pipe failed: {exc}') from exc
        finally:
            # A timeout, disconnect or abandoned iterator must release native RAM/VRAM.
            try:
                if not complete:
                    self.close()
            finally:
                self.command_lock.release()

    def close(self) -> None:
        with self.close_lock:
            if self.stopped.is_set(): return
            self.stopped.set()
            if self.job:
                self.job.close()
            with contextlib.suppress(OSError):
                if self.owns_process_group:
                    os.killpg(self.process.pid, signal.SIGKILL)
                elif self.process.poll() is None:
                    self.process.kill()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            if self.writer_done is not None:
                self.writer_done.wait(.2)
            for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
                if pipe:
                    if pipe is self.process.stdin and self.writer_done is not None and not self.writer_done.is_set():
                        continue
                    with contextlib.suppress(OSError):
                        pipe.close()
