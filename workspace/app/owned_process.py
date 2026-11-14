"""Bounded shutdown of the adapter and precisely its descendants."""
import contextlib
import json
import os
import signal
import subprocess
import threading
import time
import psutil


class OwnedProcess:
    def __init__(self,command,cwd,env,on_event,on_exit):
        self.write_lock=threading.Lock();self.stop_lock=threading.Lock();self.known={};self.job=None
        self.finished=threading.Event();self.on_event=on_event;self.on_exit=on_exit
        kwargs={'start_new_session':True} if os.name!='nt' else {'creationflags':subprocess.CREATE_NO_WINDOW}
        if os.name=='nt':
            from workspace.app.windows_job import OwnedJob
            self.job=OwnedJob()
        try:
            self.process=subprocess.Popen(command,cwd=cwd,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                          text=True,encoding='utf-8',errors='replace',bufsize=1,**kwargs)
            if self.job:self.job.assign(self.process)
        except BaseException:
            if hasattr(self,'process'):self.process.kill();self.process.wait()
            if self.job:self.job.close()
            raise
        self.remember()
        threading.Thread(target=self._stderr,daemon=True).start()
        threading.Thread(target=self._read,daemon=True).start()
        threading.Thread(target=self._track,daemon=True).start()

    def remember(self):
        with contextlib.suppress(psutil.Error):
            root=psutil.Process(self.process.pid)
            for process in [root,*root.children(recursive=True)]:
                self.known[process.pid]=process.create_time()

    def _track(self):
        while not self.finished.wait(.1):self.remember()

    def send(self,payload):
        with self.write_lock:
            if self.process.poll() is not None:raise RuntimeError('Adapter process has exited.')
            self.process.stdin.write(json.dumps(payload,ensure_ascii=False)+'\n');self.process.stdin.flush()

    def _stderr(self):
        for line in self.process.stderr:
            self.on_event({'kind':'diagnostic','message':line[-8000:].rstrip()})

    def _read(self):
        error=None
        try:
            for line in self.process.stdout:
                try:
                    value=json.loads(line)
                    if not isinstance(value,dict):raise ValueError('Expected an event object')
                except ValueError as exc:
                    error=str(exc);break
                self.on_event(value)
        except Exception as exc:error=str(exc)
        finally:
            self._kill_tree()
            self.finished.set()
            self.on_exit(self.process.returncode,error)

    def _kill_tree(self):
        with self.stop_lock:
            self.remember()
            if self.job:self.job.close()
            elif os.name!='nt':
                with contextlib.suppress(ProcessLookupError,PermissionError):os.killpg(self.process.pid,signal.SIGKILL)
            survivors=[]
            for pid,created in list(self.known.items()):
                try:
                    process=psutil.Process(pid)
                    if process.create_time()==created and process.is_running():process.kill();survivors.append(process)
                except psutil.Error:pass
            _,alive=psutil.wait_procs(survivors,timeout=3)
            with contextlib.suppress(subprocess.TimeoutExpired):self.process.wait(timeout=3)
            return [p.pid for p in alive if p.is_running()]

    def stop(self,grace=2):
        with contextlib.suppress(Exception):self.send({'kind':'stop'})
        if not self.finished.wait(grace):
            alive=self._kill_tree()
            if not self.finished.wait(4) or alive:raise RuntimeError('PROCESS_STOP_UNCONFIRMED: owned descendants remain.')
        return {'confirmed':True,'owned_pid':self.process.pid,'descendants':len(self.known)-1}
