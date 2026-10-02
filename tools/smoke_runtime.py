"""Live checks against a running Local AI. Uses the registered test GGUF."""
import argparse
import json
import time
from pathlib import Path

import httpx

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--base-url', default='http://127.0.0.1:8765')
parser.add_argument('--output', type=Path, default=Path('tools/out/live_results.json'))
parser.add_argument('--desktop', action='store_true')
args = parser.parse_args()
client = httpx.Client(base_url=args.base_url, timeout=200, trust_env=False)
model = client.get('/v1/models').json()['data'][0]['id']
checks = []


def record(name, **details):
    checks.append({'check':name, 'passed':True, **details})
    print(json.dumps(checks[-1]), flush=True)


def chat(text, **kwargs):
    payload = {'model_id':model,'message':text,'temperature':0,'max_tokens':64,
               'runtime':{'n_ctx':2048,'n_gpu_layers':-1}, **kwargs}
    started = time.monotonic()
    response = client.post('/api/chat', json=payload)
    return response, round(time.monotonic()-started, 3)


def status(): return client.get('/api/runtime/status').json()


def recover():
    response, _ = chat('What is two plus two? Answer briefly.')
    assert response.status_code == 200, response.text
    assert '4' in response.json()['answer'], response.text
    return status()[0]['worker_pid']


try:
    first, cold = chat('What is two plus two? Answer briefly.')
    assert first.status_code == 200, first.text
    pid = status()[0]['worker_pid']
    second, warm = chat('What is two plus two? Answer briefly.')
    assert second.status_code == 200 and status()[0]['worker_pid'] == pid
    assert status()[0]['runtime_mode'] == 'CUDA', status()
    record('CUDA and persistent worker', cold_seconds=cold, warm_seconds=warm, pid=pid, answer=second.json()['answer'])

    initial, _ = chat('My code word is ORBIT. Say OK.', system_prompt='Ты полезный помощник. Отвечай кратко и по делу.')
    assert initial.status_code == 200, initial.text
    history = [{'role':'user','content':'My code word is ORBIT. Say OK.'}, {'role':'assistant','content':initial.json()['answer']}]
    with_memory, _ = chat('What was my code word?', history=history, memory=True, system_prompt='Ты полезный помощник. Отвечай кратко и по делу.')
    assert with_memory.status_code == 200 and 'ORBIT' in with_memory.json()['answer']
    without, _ = chat('What is two plus two?', history=history, memory=False)
    assert without.status_code == 200 and 'ORBIT' not in without.json()['answer']
    record('memory on and off; separate message collections', with_memory=with_memory.json()['answer'], without_memory=without.json()['answer'])

    stream_body = {'model_id':model,'message':'Write a very long numbered list from 1 to 1000. Do not stop early.',
                   'max_tokens':1600,'temperature':0,'runtime':{'n_ctx':2048,'n_gpu_layers':-1}}
    with client.stream('POST','/api/chat/stream',json=stream_body) as response:
        lines = response.iter_lines()
        for line in lines:
            if line.startswith('data: ') and json.loads(line[6:]).get('type') == 'delta': break
        started = time.monotonic()
        health = client.get('/api/health')
        health_seconds = time.monotonic()-started
        busy = client.post('/api/chat',json={'model_id':model,'message':'hello','max_tokens':16})
        assert health.status_code == 200 and health_seconds < 1
        assert busy.status_code == 409, busy.text
        cancelled = client.post('/api/runtime/cancel')
        assert cancelled.status_code == 200 and cancelled.json()['cancelled'] == 1
        list(lines)
    new_pid = recover()
    assert new_pid != pid
    record('busy 409, responsive health, cancel and recovery', health_seconds=round(health_seconds,3), recovered_pid=new_pid)

    with client.stream('POST','/api/chat/stream',json=stream_body) as response:
        for line in response.iter_lines():
            if line.startswith('data: ') and json.loads(line[6:]).get('type') == 'delta': break
    deadline = time.monotonic()+5
    while status() and time.monotonic() < deadline: time.sleep(.1)
    assert not status(), status()
    record('disconnected SSE releases worker')
    recover()

    timeout_body = dict(stream_body, runtime={'n_ctx':2048,'n_gpu_layers':-1,'request_timeout_sec':1})
    started = time.monotonic()
    timeout = client.post('/api/chat',json=timeout_body)
    elapsed = time.monotonic()-started
    assert timeout.status_code == 504 and elapsed < 5, timeout.text
    recover()
    record('bounded real generation timeout and recovery', seconds=round(elapsed,3))

    cpu, _ = chat('What is two plus two? Answer briefly.', runtime={'n_ctx':2048,'n_gpu_layers':0,'n_threads':4})
    assert cpu.status_code == 200 and status()[0]['runtime_mode'] == 'CPU', cpu.text
    record('CPU inference', answer=cpu.json()['answer'])
    recover()

    if args.desktop:
        diagnostics = client.get('/api/desktop/diagnostics').json()
        assert diagnostics['worker_python_exists'] and diagnostics['frontend_dist_exists']
        conversation = 'local-ai-smoke-' + str(time.time_ns())
        def desktop_ask(text):
            admitted = client.post('/api/desktop/chat-send',json={'model_id':model,'message':text,
                         'conversationId':conversation,'conversationTitle':'Local AI connectivity test',
                         'memory':True,'max_tokens':64,'temperature':0})
            assert admitted.status_code == 200, admitted.text
            assistant_id = admitted.json()['assistant_id']
            deadline = time.monotonic()+30
            while time.monotonic() < deadline:
                state = client.get('/api/desktop/chat-sync').json()
                for conv in state['conversations']:
                    for message in conv['messages']:
                        if message['id'] == assistant_id and not message.get('pending'):
                            assert message.get('phase') != 'error', message
                            return message.get('answer') or message['text']
                time.sleep(.15)
            raise AssertionError('Desktop request did not finish')
        desktop_ask('My code word is ORBIT. Say OK.')
        answer = desktop_ask('What was my code word?')
        assert 'ORBIT' in answer, answer
        record('EXE canonical mini assistant API, two-turn memory', answer=answer, diagnostics=diagnostics)
finally:
    client.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(checks,ensure_ascii=False,indent=2), encoding='utf-8')
