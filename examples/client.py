"""Dependency-free Local AI client. Python 3.10+."""
import argparse
import json
import os
import urllib.request
import re


class LocalAI:
    def __init__(self, base_url='http://127.0.0.1:8765/v1', model=None, memory=True, max_tokens=512):
        self.base_url = base_url.rstrip('/')
        self.headers = {'Content-Type': 'application/json'}
        if os.getenv('LOCAL_AI_API_KEY'):
            self.headers['Authorization'] = 'Bearer ' + os.environ['LOCAL_AI_API_KEY']
        self.history, self.memory = [], memory
        self.max_tokens = max_tokens
        if model is None:
            request = urllib.request.Request(self.base_url + '/models', headers=self.headers)
            with urllib.request.urlopen(request, timeout=10) as response:
                models = json.load(response)['data']
            if not models: raise RuntimeError('Register a GGUF model in Local AI first.')
            model = models[0]['id']
        self.model = model

    def ask(self, text, *, stream=False):
        messages = (self.history if self.memory else []) + [{'role':'user','content':text}]
        payload = {'model':self.model,'messages':messages,'memory':self.memory,
                   'temperature':0.2,'max_tokens':self.max_tokens,'stream':stream}
        request = urllib.request.Request(self.base_url + '/chat/completions',
                  data=json.dumps(payload).encode(), headers=self.headers, method='POST')
        with urllib.request.urlopen(request, timeout=200) as response:
            if stream:
                pieces, done, finish_reason = [], False, None
                for line in response:
                    if not line.startswith(b'data: '): continue
                    frame = line[6:].decode().strip()
                    if frame == '[DONE]':
                        done = True
                        break
                    chunk = json.loads(frame)
                    if 'error' in chunk: raise RuntimeError(chunk['error']['message'])
                    for choice in chunk.get('choices', []):
                        finish_reason = choice.get('finish_reason') or finish_reason
                        piece = choice.get('delta', {}).get('content', '')
                        pieces.append(piece)
                        print(piece, end='', flush=True)
                if not done: raise RuntimeError('Stream ended before [DONE].')
                answer = ''.join(pieces)
                print()
            else:
                choice = json.load(response)['choices'][0]
                answer = choice['message']['content']
                finish_reason = choice.get('finish_reason')
        if finish_reason == 'length':
            raise RuntimeError('Token limit reached: increase --max-tokens and model context; answer may be incomplete.')
        if self.memory:
            # Qwen templates can prefill <think>, leaving only </think> in output.
            # Previous turns should contain final answers, without reasoning.
            final = re.sub(r'<think>.*?</think>', '', answer, flags=re.DOTALL | re.IGNORECASE)
            if re.search(r'</think\s*>', final, re.IGNORECASE):
                final = re.split(r'</think\s*>', final, maxsplit=1, flags=re.IGNORECASE)[1]
            self.history.extend([{'role':'user','content':text},{'role':'assistant','content':final.strip()}])
        return answer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8765/v1')
    parser.add_argument('--model')
    parser.add_argument('--no-memory', action='store_true')
    parser.add_argument('--stream', action='store_true')
    parser.add_argument('--max-tokens', type=int, default=512)
    parser.add_argument('--prompt', help='One request, then exit; otherwise interactive.')
    args = parser.parse_args()
    client = LocalAI(args.base_url, args.model, not args.no_memory, args.max_tokens)
    while True:
        try:
            text = args.prompt or input('You: ')
            if not text.strip(): break
            answer = client.ask(text, stream=args.stream)
            if not args.stream: print('Local AI:', answer)
            if args.prompt: break
        except (EOFError, KeyboardInterrupt): break


if __name__ == '__main__': main()
