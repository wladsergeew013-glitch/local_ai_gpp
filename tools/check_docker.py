"""Check Docker web/proxy/API and optionally verify CPU, mixed or CUDA inference."""
import argparse
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path


def run(base_url, model_path=None, gpu_layers=0):
    base = base_url.rstrip('/')

    def request(path, payload=None, form=False, timeout=10):
        data = None
        headers = {}
        if payload is not None:
            data = (urllib.parse.urlencode(payload) if form else json.dumps(payload)).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
        with urllib.request.urlopen(urllib.request.Request(base + path, data=data, headers=headers), timeout=timeout) as response:
            return response.read(), response.headers.get('Content-Type', '')

    results = {'base_url': base, 'checks': [], 'inference': None}
    body, content_type = request('/')
    assert 'text/html' in content_type and b'id="root"' in body, 'React page missing'
    results['checks'].append('React page')
    assets = re.findall(r'(?:src|href)="(/ui-assets/[^"]+)"', body.decode())
    assert assets, 'No frontend bundle references'
    for asset in assets:
        data, mime = request(asset)
        assert data and 'text/html' not in mime, f'Bundle replaced with HTML: {asset}'
    results['checks'].append('JS/CSS bundles')
    for path in ('/api/health', '/api/bootstrap', '/v1/models', '/openapi.json'):
        data, mime = request(path)
        assert 'application/json' in mime, f'Invalid proxy route: {path}'
        json.loads(data)
        results['checks'].append(path)
    data, mime = request('/docs')
    assert 'text/html' in mime and b'swagger' in data.lower()
    results['checks'].append('Swagger UI')
    data, mime = request('/assets/branding/icons/mini_agent_head_v2.png')
    assert data.startswith(b'\x89PNG'), 'Model/branding asset proxy failed'
    results['checks'].append('Branding asset proxy')

    if model_path:
        runtime = {'n_ctx': 1024, 'n_batch': 64, 'n_threads': 8, 'n_gpu_layers': gpu_layers,
                   'gpu_fallback_to_cpu': False, 'warm_policy': 'keep_loaded',
                   'worker_idle_timeout_sec': 60, 'request_timeout_sec': 180,
                   'enable_thinking': False}
        raw, _ = request('/api/models/register-path', {
            'model_name': 'Docker Qwen3.5 4B', 'model_type': 'LLM',
            'model_path': model_path, 'runtime_json': json.dumps(runtime)}, form=True)
        model = json.loads(raw)
        started = time.monotonic()
        raw, _ = request('/api/chat', {
            'model_id': model['id'], 'message': 'What is two plus two? Answer with one word.',
            'max_tokens': 512, 'temperature': 0, 'memory': False, 'runtime': runtime}, timeout=200)
        answer = json.loads(raw)
        assert answer['finish_reason'] == 'stop', answer['finish_reason']
        assert re.search(r'\b(four|4)\b', answer['answer'], re.IGNORECASE), answer['answer']
        raw, _ = request('/api/runtime/status')
        status = next(item for item in json.loads(raw) if item['model_id'] == model['id'])
        assert status['worker_pid'], status
        assert status['model_adapter'] == 'qwen' and status['enable_thinking'] is False, status
        assert not answer.get('reasoning'), 'No-thinking adapter produced a reasoning block'
        offloaded, total = status.get('gpu_offloaded_layers', 0), status.get('model_layers', 0)
        if gpu_layers == 0:
            assert status['mode'] == 'CPU' and offloaded == 0, status
        elif gpu_layers == -1:
            assert status['mode'] == 'CUDA' and total > 0 and offloaded == total, status
        else:
            assert total > gpu_layers > 0 and offloaded == gpu_layers, status
            assert status['mode'] == 'CPU + CUDA', status
        if gpu_layers:
            assert status['backend']['supports_gpu_offload'], status
        assert not status.get('fallback_reason'), status
        results['inference'] = {'model': model['id'], 'answer': answer['answer'],
                                'finish_reason': answer['finish_reason'], 'mode': status['mode'],
                                'seconds': round(time.monotonic() - started, 3),
                                'worker_pid': status['worker_pid'], 'usage': answer['usage'],
                                'requested_gpu_layers': gpu_layers,
                                'gpu_offloaded_layers': offloaded, 'model_layers': total,
                                'fallback_reason': status.get('fallback_reason', ''),
                                'model_adapter': status['model_adapter'], 'enable_thinking': status['enable_thinking'],
                                'reasoning_chars': len(answer.get('reasoning', ''))}
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8080')
    parser.add_argument('--model-path', help='Path inside container, e.g. /models/Qwen3.5-4B-Q4_K_M.gguf')
    parser.add_argument('--gpu-layers', type=int, default=0,
                        help='0: CPU, positive: mixed (less than total layers), -1: all layers on CUDA')
    parser.add_argument('--output')
    args = parser.parse_args()
    if args.gpu_layers < -1:
        parser.error('--gpu-layers must be -1 or nonnegative')
    if args.gpu_layers and not args.model_path:
        parser.error('--gpu-layers requires --model-path to verify actual inference')
    results = run(args.base_url, args.model_path, args.gpu_layers)
    rendered = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + '\n', encoding='utf-8')
    print(rendered)


if __name__ == '__main__':
    main()
