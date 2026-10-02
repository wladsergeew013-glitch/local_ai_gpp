"""Verify CPU, mixed CPU/CUDA, and complete GPU layer offload on one GGUF."""
import argparse
import json
import re
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-path', type=Path, required=True)
    parser.add_argument('--model-name', required=True)
    parser.add_argument('--base-url', default='http://127.0.0.1:8765')
    parser.add_argument('--output', type=Path, default=Path('tools/out/inference_modes_result.json'))
    parser.add_argument('--mixed-layers', type=int, default=8)
    parser.add_argument('--thinking', action='store_true', help='Enable thinking for capable Qwen templates')
    args = parser.parse_args()
    checks = []
    with httpx.Client(base_url=args.base_url, timeout=200, trust_env=False) as client:
        for label, layers in [('CPU', 0), ('CPU + CUDA', args.mixed_layers), ('CUDA', -1)]:
            runtime = {'n_gpu_layers': layers, 'n_ctx': 1024, 'n_batch': 64,
                       'flash_attn': True, 'gpu_fallback_to_cpu': False, 'verbose_runtime': True,
                       'load_timeout_sec': 120, 'request_timeout_sec': 180, 'enable_thinking': args.thinking}
            registered = client.post('/api/models/register-path', data={
                'model_name': args.model_name, 'model_path': str(args.model_path.resolve()),
                'runtime_json': json.dumps(runtime)})
            registered.raise_for_status()
            model_id = registered.json()['id']
            started = time.monotonic()
            answer = ''
            done = None
            with client.stream('POST', '/api/chat/stream', json={
                'model_id': model_id, 'message': 'What is two plus two? Answer briefly.',
                'temperature': 0, 'max_tokens': 512, 'runtime': runtime}) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith('data: '):
                        continue
                    event = json.loads(line[6:])
                    if event.get('type') == 'error':
                        raise RuntimeError(event)
                    if event.get('type') == 'delta':
                        answer += event.get('text', '')
                    if event.get('type') == 'done':
                        done = event
            status = next(s for s in client.get('/api/runtime/status').json() if s['model_id'] == model_id)
            assert done, answer
            assert done.get('finish_reason') == 'stop', done
            if status.get('model_adapter') == 'qwen' and not args.thinking:
                assert not done.get('reasoning'), done
            final = answer.split('</think>')[-1].strip()
            assert re.search(r'\b(?:4|four)\b', final, re.IGNORECASE), answer
            assert status['runtime_mode'] == label and not status['fallback_reason'], status
            offloaded = status.get('gpu_offloaded_layers', 0)
            total = status.get('model_layers', 0)
            if layers == 0:
                assert offloaded == 0, status
            elif layers > 0:
                assert 0 < offloaded < total and offloaded == layers, status
            else:
                assert offloaded == total and total > 0, status
            saved = {'passed': True, 'mode': label, 'elapsed_sec': round(time.monotonic() - started, 3),
                     'answer': answer, 'final_answer': final, 'usage': done.get('usage'), 'runtime': status}
            checks.append(saved)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps({'model': args.model_name, 'checks': checks}, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(saved, ensure_ascii=False), flush=True)
        print('All three inference modes passed.', flush=True)


if __name__ == '__main__':
    main()
