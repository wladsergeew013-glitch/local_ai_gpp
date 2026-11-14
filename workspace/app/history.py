"""Pinned read-only reader of DSH v4 plaintext JSONL; never repairs/replays it."""
import json
from pathlib import Path
from workspace.app.dsh_profile import SESSION_FORMAT


def canonical_events(home: Path):
    root=(home/'sessions').resolve()
    if not root.exists():return []
    result=[]
    for path in sorted(root.rglob('*.jsonl')):
        if not path.resolve().is_relative_to(root) or path.is_symlink():raise ValueError('DSH_HISTORY_PATH_INVALID')
        lines=path.read_bytes().splitlines(keepends=True)
        if not lines:continue
        header=json.loads(lines[0])
        if header.get('type')!='session' or header.get('version')!=SESSION_FORMAT:
            raise ValueError('DSH_HISTORY_UNSUPPORTED: expected session format '+str(SESSION_FORMAT))
        session=header['id']
        for index,line in enumerate(lines[1:]):
            if not line.endswith(b'\n'):
                # A live/crashed writer may leave an uncommitted tail; do not invent an event.
                break
            event=json.loads(line)
            seq=event.get('seq',index)
            result.append((f'dsh:{session}:{seq}',event))
    return result


def reconcile(store,task):
    events=canonical_events(store.root/'tasks'/task['id']/'dsh')
    for key,event in events:store.event(task['id'],'dsh',event,key)
    return {'canonical_events':len(events),'format':SESSION_FORMAT,'replayed':False}
