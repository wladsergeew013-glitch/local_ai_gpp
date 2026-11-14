"""Task lifecycle, one owned SDK process, durable receipts and no automatic retry."""
from __future__ import annotations
import contextlib
import json
import os
import secrets
import shutil
import sys
import threading
from pathlib import Path
from workspace.app.store import ACTIVE, identifier, now
from workspace.app.runtime_client import RuntimeClient
from workspace.app.owned_process import OwnedProcess
from workspace.app import history, project_diff


class TaskService:
    def __init__(self,store):
        self.store=store;self.lock=threading.RLock();self.processes={};self.tokens={};self.results={}

    def create(self,project_id,provider_id,prompt,action_id,permission='read-only',title=''):
        if permission not in {'read-only','workspace-write'}:raise ValueError('Unsupported permission preset.')
        self.store.one('projects',project_id);self.store.one('providers',provider_id)
        with self.store.transaction() as db:
            existing=self.store.rows('SELECT id FROM tasks WHERE action_id=?',(action_id,))
            if existing:return self.store.one('tasks',existing[0]['id'])
            task_id=identifier();stamp=now()
            db.execute('INSERT INTO tasks(id,project_id,provider_id,title,prompt,status,session_id,created_at,updated_at,permission,action_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (task_id,project_id,provider_id,title or prompt[:80],prompt,'queued','workspace-'+task_id,stamp,stamp,permission,action_id))
        self.store.event(task_id,'task_created',{'prompt':prompt,'permission':permission},'created')
        return self.store.one('tasks',task_id)

    def start(self,task_id,expected,*,restore_only=False,new_prompt=None):
        with self.lock:
            if self.processes:raise ValueError('WORKSPACE_BUSY: one root task can run at a time.')
            task=self.store.one('tasks',task_id)
            if task['revision']!=expected:raise ValueError('TASK_CONFLICT: refresh before starting.')
            resume=task['status']!='queued'
            if task['status'] in ACTIVE:raise ValueError('TASK_ALREADY_RUNNING')
            if resume and not restore_only and not (new_prompt and new_prompt.strip()):
                raise ValueError('RECOVERY_REQUIRES_RECONCILIATION: restore history, then explicitly send a new instruction.')
            if resume:history.reconcile(self.store,task)
            provider=self.store.one('providers',task['provider_id']);client=RuntimeClient(provider)
            caps=client.capabilities(load=True)
            project=self.store.one('projects',task['project_id']);task_dir=self.store.root/'tasks'/task_id
            task_dir.mkdir(parents=True,exist_ok=True)
            baseline=task_dir/'baseline'
            if not baseline.exists():
                self.store.update_task(task_id,details={'baseline':project_diff.capture(Path(project['path']),baseline)})
            token=secrets.token_urlsafe(32);self.tokens[task_id]=token;self.results.pop(task_id,None)
            self.store.update_task(task_id,'starting',{'restoring':restore_only})
            config={'provider':provider,'capabilities':caps,'owner_id':task_id,'owner_token':token,
                    'session_id':task['session_id'],'dsh_home':str(task_dir/'dsh'),'project_path':project['path'],
                    'prompt':new_prompt or task['prompt'],'permission':task['permission'],'resume':resume,'restore_only':restore_only}
            command=[sys.executable,'--adapter-runner'] if getattr(sys,'frozen',False) else [sys.executable,'-m','workspace.app.dsh_runner']
            env=os.environ.copy();env['PYTHONUTF8']='1';env['PYTHONIOENCODING']='utf-8'
            root=Path(__file__).resolve().parents[2]
            try:
                process=OwnedProcess(command,str(root),env,lambda event:self._event(task_id,event),lambda code,error:self._exit(task_id,code,error))
                self.processes[task_id]=process
                self.store.event(task_id,'input_admitted',{'prompt':config['prompt'] if not restore_only else None,'restore_only':restore_only,'replayed':False})
                process.send(config)
            except Exception as exc:
                self.processes.pop(task_id,None)
                self.store.update_task(task_id,'failed',{'error':str(exc)});raise
        return self.store.one('tasks',task_id)

    def _event(self,task_id,event):
        kind=event.get('kind','unknown');key=None
        if kind=='notification' and event.get('method')=='session.event':
            payload=event.get('payload') or {};item=payload.get('event') or {}
            if 'seq' in item:
                key=f"dsh:{payload.get('sessionId')}:{item['seq']}"
                self.store.event(task_id,'dsh',item,key);return
        if kind in {'approval','question'}:
            approval_id=identifier()
            with self.store.transaction() as db:
                db.execute('INSERT INTO approvals VALUES(?,?,?,?,?,?,?)',(approval_id,task_id,kind,json.dumps(event,ensure_ascii=False),'pending',now(),None))
            self.store.update_task(task_id,'awaiting_permission')
            event={**event,'approval_id':approval_id}
        elif kind=='started':self.store.update_task(task_id,'running')
        elif kind=='identity':self.store.update_task(task_id,details={'adapter':event})
        elif kind in {'result','uncertain','restored'}:self.results[task_id]=event
        self.store.event(task_id,kind,event,key)

    def _exit(self,task_id,code,error):
        with self.lock:
            current=self.store.one('tasks',task_id);result=self.results.pop(task_id,None)
            if current['status']=='stopping':status='cancelled'
            elif error or result is None or result.get('kind')=='uncertain':status='uncertain'
            elif result['kind']=='restored':status='restored'
            else:
                reason=result.get('finish_reason')
                status='completed' if reason in {'completed','stop','end_turn'} else 'failed'
            details={'exit_code':code,'observer_error':error,'result':result,'automatic_retry':False}
            try:details['history']=history.reconcile(self.store,current)
            except Exception as exc:details['history_error']=str(exc);status='uncertain'
            self.store.update_task(task_id,status,details)
            self.store.event(task_id,'process_exited',{'status':status,'exit_code':code,'automatic_retry':False})
            self.processes.pop(task_id,None);self.tokens.pop(task_id,None)
            with self.store.transaction() as db:
                db.execute("UPDATE approvals SET status='cancelled',decided_at=? WHERE task_id=? AND status='pending'",(now(),task_id))

    def decide(self,approval_id,value):
        with self.lock,self.store.transaction() as db:
            approval=self.store.one('approvals',approval_id)
            if approval['status']!='pending':raise ValueError('APPROVAL_FINISHED: one-operation decisions cannot be reused.')
            process=self.processes.get(approval['task_id'])
            if process is None:raise ValueError('TASK_NOT_RUNNING')
            if approval['kind']=='approval' and value.get('outcome') not in {'allowed-once','rejected','cancelled'}:raise ValueError('Invalid approval decision')
            db.execute('UPDATE approvals SET status=?,decided_at=? WHERE id=?',('answered',now(),approval_id))
            self.store.event(approval['task_id'],'permission_decision',{'approval_id':approval_id,'value':value})
            try:process.send({'kind':'answer','id':approval['payload']['id'],'value':value})
            except Exception:
                self.store.update_task(approval['task_id'],'uncertain',{'error':'Permission response transport failed; no automatic resend.'});raise
            self.store.update_task(approval['task_id'],'running')
        return {'accepted':True}

    def stop(self,task_id,expected=None):
        with self.lock:
            task=self.store.one('tasks',task_id)
            if expected is not None and task['revision']!=expected:raise ValueError('TASK_CONFLICT: refresh before stopping.')
            process=self.processes.get(task_id);token=self.tokens.get(task_id)
            if process is None:return {'confirmed':True,'active':False}
            self.store.update_task(task_id,'stopping')
        result=process.stop()
        cancellation={'cancelled':0}
        if token:
            try:cancellation=RuntimeClient(self.store.one('providers',task['provider_id'])).cancel_owner(task_id,token)
            except Exception as exc:cancellation={'error':str(exc),'scope':'only this task owner'}
        self.store.event(task_id,'stop_confirmed',{**result,'runtime_request':cancellation})
        return result

    def diff(self,task_id):
        task=self.store.one('tasks',task_id);project=self.store.one('projects',task['project_id'])
        baseline=self.store.root/'tasks'/task_id/'baseline'
        return project_diff.compare(Path(project['path']),baseline) if baseline.exists() else {'files':[],'diff':''}

    def close(self):
        for task_id in list(self.processes):
            with contextlib.suppress(Exception):self.stop(task_id)

    def snapshot(self):
        with self.lock:
            if self.processes:raise ValueError('Stop tasks before taking a consistent application snapshot.')
            target=self.store.snapshot(self.store.root/'snapshots'/identifier())
            source=self.store.root/'tasks'
            if source.exists():shutil.copytree(source,target/'tasks')
            return {'path':str(target),'schema_version':1,'dsh_version':'0.2.2rc2','includes_sessions':True}
