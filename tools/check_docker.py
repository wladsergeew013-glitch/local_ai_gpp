"""Check the Docker web/proxy/API and optionally run CPU inference on one GGUF."""
import argparse
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path


def run(base_url, model_path=None):
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
        runtime = {'n_ctx': 1024, 'n_batch': 64, 'n_threads': 8, 'n_gpu_layers': 0,
                   'gpu_fallback_to_cpu': False, 'warm_policy': 'keep_loaded',
                   'worker_idle_timeout_sec': 60, 'request_timeout_sec': 180}
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
        assert status['worker_pid'] and status['mode'] == 'CPU', status
        assert status['model_adapter'] == 'qwen' and status['enable_thinking'] is False, status
        assert not answer.get('reasoning'), 'No-thinking adapter produced a reasoning block'
        assert status.get('gpu_offloaded_layers', 0) == 0, status
        assert not status.get('fallback_reason'), status
        results['inference'] = {'model': model['id'], 'answer': answer['answer'],
                                'finish_reason': answer['finish_reason'], 'mode': status['mode'],
                                'seconds': round(time.monotonic() - started, 3),
                                'worker_pid': status['worker_pid'], 'usage': answer['usage'],
                                'model_adapter': status['model_adapter'], 'enable_thinking': status['enable_thinking'],
                                'reasoning_chars': len(answer.get('reasoning', ''))}
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8080')
    parser.add_argument('--model-path', help='Path inside container, e.g. /models/Qwen3.5-4B-Q4_K_M.gguf')
    parser.add_argument('--output')
    args = parser.parse_args()
    results = run(args.base_url, args.model_path)
    rendered = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + '\n', encoding='utf-8')
    print(rendered)


if __name__ == '__main__':
    main()
