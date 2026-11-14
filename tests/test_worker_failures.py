import sys
import threading
import time
import unittest
from fastapi import HTTPException
from backend.app.worker_client import WorkerClient


class WorkerFailureTests(unittest.TestCase):
    def test_R22_timeout_includes_a_blocked_stdin_write(self):
        worker = WorkerClient([sys.executable, '-u', '-c', 'import time; time.sleep(30)'])
        errors = []
        def run():
            try: list(worker.events({'content': 'x' * 1024**2}, timeout=.15, idle_timeout=.15))
            except BaseException as error: errors.append(error)
        started = time.monotonic()
        thread = threading.Thread(target=run, daemon=True); thread.start(); thread.join(.8)
        blocked = thread.is_alive()
        if blocked:
            worker.process.kill(); thread.join(2)
        worker.close()
        self.assertFalse(blocked, 'The request deadline did not interrupt stdin writing')
        self.assertLess(time.monotonic() - started, .8)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], HTTPException)
        self.assertEqual(errors[0].status_code, 504)
        self.assertIsNotNone(worker.process.poll())
