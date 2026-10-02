"""Killable persistent inference subprocess, shared by development and EXE."""
from __future__ import annotations
import json
import os
import sys
import threading
import time
import traceback
from typing import Any
from backend.app.core import _filter_supported_kwargs, _float_value, _int_value, _split_mode, _tensor_split
from backend.app.cuda_runtime import configure_cuda_dlls
from backend.app.model_adapters import apply_model_adapter

_output_lock = threading.Lock()


def write_event(payload: dict) -> None:
    with _output_lock:
        sys.stdout.buffer.write((json.dumps(payload, ensure_ascii=False, default=str) + '\n').encode('utf-8'))
        sys.stdout.buffer.flush()


def emit_heartbeats(active: threading.Event, stop: threading.Event) -> None:
    while not stop.wait(2):
        if active.is_set():
            write_event({'type': 'heartbeat'})


def build_llama_kwargs(payload: dict) -> dict:
    cfg = payload.get('runtime') or {}
    kwargs = {
        'model_path': payload['model']['path'], 'n_ctx': _int_value(cfg.get('n_ctx'), 4096),
        'n_batch': _int_value(cfg.get('n_batch'), 512), 'n_threads': _int_value(cfg.get('n_threads'), 4),
        'n_threads_batch': _int_value(cfg.get('n_threads_batch'), 0),
        'n_gpu_layers': _int_value(cfg.get('n_gpu_layers'), 0), 'main_gpu': _int_value(cfg.get('main_gpu'), 0),
        'split_mode': _split_mode(cfg.get('split_mode')), 'offload_kqv': bool(cfg.get('offload_kqv', True)),
        'flash_attn': bool(cfg.get('flash_attn', False)), 'op_offload': bool(cfg.get('op_offload', True)),
        'swa_full': bool(cfg.get('swa_full', False)), 'use_mmap': bool(cfg.get('use_mmap', True)),
        # llama-cpp-python's quiet mode redirects both process descriptors to
        # /dev/null during load. That drops our JSON heartbeats on slow disks.
        # Native diagnostics go to the parent's independently drained stderr.
        'use_mlock': bool(cfg.get('use_mlock', False)), 'verbose': True,
        'seed': _int_value(cfg.get('seed'), -1),
    }
    split = _tensor_split(cfg.get('tensor_split'))
    if split: kwargs['tensor_split'] = split
    if kwargs['n_gpu_layers'] == 0:
        kwargs.update(offload_kqv=False, op_offload=False)
    return kwargs


def llama_backend_details() -> dict:
    configure_cuda_dlls()
    import llama_cpp
    from llama_cpp import llama_cpp as lib
    raw = lib.llama_print_system_info()
    return {'version': llama_cpp.__version__, 'supports_gpu_offload': bool(lib.llama_supports_gpu_offload()),
            'system_info': raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else str(raw)}


def fit_messages(runtime: Any, messages: list[dict], max_tokens: int, n_ctx: int, truncate: bool) -> tuple[list[dict], int]:
    """Conservative budget; llama.cpp also checks its exact chat template."""
    if max_tokens >= n_ctx - 256:
        raise ValueError('max_tokens must be smaller than context size minus 256.')
    kept, dropped = list(messages), 0
    while True:
        text = '\n'.join(str(m.get('content') or '') for m in kept)
        count = len(runtime.tokenize(text.encode('utf-8'), special=True)) + 32 * len(kept) + 128
        if count + max_tokens <= n_ctx: return kept, dropped
        start = next((i for i, m in enumerate(kept) if m.get('role') == 'user'), -1)
        end = next((i for i in range(start + 1, len(kept)) if kept[i].get('role') == 'user'), -1)
        if not truncate or start < 0 or end < 0:
            raise ValueError('Context window exceeded. Shorten the prompt/history or increase n_ctx.')
        dropped += end - start
        del kept[start:end]


def serve(payloads) -> int:
    runtime, config, summary = None, None, {}
    active, stop = threading.Event(), threading.Event()
    threading.Thread(target=emit_heartbeats, args=(active, stop), daemon=True).start()
    try:
        for payload in payloads:
            started = time.perf_counter()
            active.set()
            try:
                configure_cuda_dlls()
                from llama_cpp import Llama
                requested_kwargs = _filter_supported_kwargs(Llama, build_llama_kwargs(payload))
                kwargs = dict(requested_kwargs)
                if requested_kwargs != config or runtime is None:
                    if runtime is not None:
                        runtime.close()
                        runtime = None
                    write_event({'type': 'worker_status', 'message': 'Загружаю модель.'})
                    details = llama_backend_details()
                    fallback = ''
                    if kwargs.get('n_gpu_layers') and not details['supports_gpu_offload']:
                        if not payload.get('runtime', {}).get('gpu_fallback_to_cpu', True):
                            raise RuntimeError('CUDA offload is unavailable in this llama.cpp build.')
                        fallback = 'Installed llama.cpp runtime does not support GPU offload.'
                        kwargs.update(n_gpu_layers=0, offload_kqv=False, op_offload=False)
                    try:
                        runtime = Llama(**kwargs)
                    except Exception as exc:
                        if not kwargs.get('n_gpu_layers') or not payload.get('runtime', {}).get('gpu_fallback_to_cpu', True): raise
                        fallback = str(exc)
                        kwargs.update(n_gpu_layers=0, offload_kqv=False, op_offload=False)
                        runtime = Llama(**kwargs)
                    config = requested_kwargs
                    summary = {'mode': 'CPU fallback' if fallback else ('CUDA' if kwargs.get('n_gpu_layers') else 'CPU'),
                               'n_gpu_layers': kwargs.get('n_gpu_layers', 0), 'fallback_reason': fallback, 'backend': details,
                               'worker_pid': os.getpid(), 'load_elapsed_ms': round((time.perf_counter()-started)*1000)}
                cfg = payload.get('runtime') or {}
                summary.update(apply_model_adapter(runtime, cfg))
                write_event({'type': 'runtime', 'mode': summary['mode'], 'runtime': summary})
                if payload.get('operation') == 'load':
                    write_event({'type': 'ready', 'runtime': summary})
                    continue
                max_tokens = int(payload.get('max_tokens', 512))
                messages, dropped = fit_messages(runtime, payload['messages'], max_tokens, int(cfg.get('n_ctx', 4096)), bool(payload.get('truncate_history')))
                chat_kwargs = {'messages': messages, 'max_tokens': max_tokens, 'temperature': float(payload.get('temperature', 0.2)),
                               'top_k': _int_value(cfg.get('top_k'), 40), 'top_p': _float_value(cfg.get('top_p'), 0.95),
                               'min_p': _float_value(cfg.get('min_p'), 0.05), 'repeat_penalty': _float_value(cfg.get('repeat_penalty'), 1.1),
                               'seed': _int_value(cfg.get('seed'), -1), 'stream': bool(payload.get('stream'))}
                generated = time.perf_counter()
                result = runtime.create_chat_completion(**_filter_supported_kwargs(runtime.create_chat_completion, chat_kwargs))
                if not payload.get('stream'):
                    result['_local_ai_gpp'] = {'runtime': summary, 'history_dropped': dropped,
                                               'generation_elapsed_ms': round((time.perf_counter()-generated)*1000)}
                    write_event({'type': 'result', 'result': result})
                    continue
                content, finish = '', 'stop'
                for chunk in result:
                    choice = (chunk.get('choices') or [{}])[0]
                    finish = choice.get('finish_reason') or finish
                    text = str((choice.get('delta') or {}).get('content') or '')
                    if text:
                        content += text
                        write_event({'type': 'delta', 'text': text})
                prompt_tokens = len(runtime.tokenize('\n'.join(m['content'] for m in messages).encode('utf-8')))
                completion_tokens = len(runtime.tokenize(content.encode('utf-8'), add_bos=False))
                write_event({'type': 'done', 'content': content, 'finish_reason': finish, 'runtime': summary, 'history_dropped': dropped,
                             'elapsed_ms': round((time.perf_counter()-started)*1000),
                             'usage': {'prompt_tokens': prompt_tokens, 'completion_tokens': completion_tokens,
                                       'total_tokens': prompt_tokens+completion_tokens, 'estimated': True}})
            except Exception as exc:
                traceback.print_exc(file=sys.stderr)
                write_event({'type': 'error', 'message': str(exc), 'status_code': 400 if isinstance(exc, ValueError) else 500})
            finally:
                active.clear()
    finally:
        stop.set()
        if runtime is not None: runtime.close()
    return 0


def main() -> int:
    if '--serve' in sys.argv:
        return serve(json.loads(line) for line in sys.stdin.buffer if line.strip())
    return serve([json.loads(sys.stdin.buffer.read())])


if __name__ == '__main__':
    raise SystemExit(main())
