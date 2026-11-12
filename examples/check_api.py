"""Standalone Local AI HTTP check: model selection, answer, memory and SSE."""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request


def check(base_url='http://127.0.0.1:8765/v1', model=None):
    base = base_url.rstrip('/')
    headers = {'Content-Type': 'application/json'}
    if os.getenv('LOCAL_AI_API_KEY'):
        headers['Authorization'] = 'Bearer ' + os.environ['LOCAL_AI_API_KEY']

    def request(path, body=None):
        data = None if body is None else json.dumps(body).encode('utf-8')
        return urllib.request.urlopen(urllib.request.Request(base + path, data=data, headers=headers), timeout=200)

    with request('/models') as response:
        models = json.load(response)['data']
    if not models:
        raise RuntimeError('Register a GGUF model in Local AI first.')
    model = model or models[0]['id']
    if model not in {item['id'] for item in models}:
        raise RuntimeError('Model ID is absent from /v1/models: ' + model)
    print('Checking model:', model, flush=True)
    history = []

    def ask(text, *, stream=False, memory=True):
        messages = (history if memory else []) + [{'role':'user', 'content':text}]
        body = {'model':model, 'messages':messages, 'memory':memory, 'stream':stream,
                'max_tokens':256, 'temperature':0,
                'chat_template_kwargs':{'enable_thinking':False}}
        with request('/chat/completions', body) as response:
            if stream:
                done = False
                finish_reason = None
                pieces = []
                for line in response:
                    if not line.startswith(b'data:'):
                        continue
                    data = line[5:].decode('utf-8').strip()
                    if data == '[DONE]':
                        done = True
                        break
                    event = json.loads(data)
                    if 'error' in event:
                        raise RuntimeError(str(event['error']))
                    for choice in event.get('choices', []):
                        finish_reason = choice.get('finish_reason') or finish_reason
                        if choice.get('finish_reason') == 'length':
                            raise RuntimeError('The streamed answer hit its token limit.')
                        pieces.append(choice.get('delta', {}).get('content', ''))
                if not done:
                    raise RuntimeError('SSE ended without [DONE].')
                if finish_reason != 'stop':
                    raise RuntimeError('SSE did not report a completed answer.')
                answer = ''.join(pieces)
            else:
                choice = json.load(response)['choices'][0]
                if choice.get('finish_reason') != 'stop':
                    raise RuntimeError('Incomplete answer: ' + str(choice.get('finish_reason')))
                answer = choice['message']['content']
        if '<think>' in answer or '</think>' in answer:
            raise RuntimeError('The model returned reasoning despite enable_thinking=False.')
        if memory:
            history.extend([{'role':'user','content':text},{'role':'assistant','content':answer}])
        return answer

    first = ask('Remember: my project is named ORBIT. Reply with only OK.')
    second = ask('What is my project named? Reply with only the name.', stream=True)
    if 'ORBIT' not in second.upper():
        raise RuntimeError('Memory check failed: ' + second)
    math = ask('What is two plus two? Reply with one word.', memory=False)
    if not re.search(r'\b(four|4)\b', math, re.IGNORECASE):
        raise RuntimeError('Independent request failed: ' + math)
    return {'ok':True,'model':model,'first':first,'memory_answer':second,'independent_answer':math,
            'history_messages':len(history),'sse_done_verified':True,'enable_thinking':False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8765/v1')
    parser.add_argument('--model', help='Exact ID from /v1/models; omit to check the first listed model.')
    args = parser.parse_args()
    try:
        result = check(args.base_url, args.model)
    except Exception as error:
        print(json.dumps({'ok':False,'error':str(error)}, ensure_ascii=False), file=sys.stderr)
        print('Start LocalAIGPP.exe and check the base URL/model ID.', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
