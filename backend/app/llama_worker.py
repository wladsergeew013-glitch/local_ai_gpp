from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.app.core import (
    _filter_supported_kwargs,
    _float_value,
    _int_value,
    _split_mode,
    _tensor_split,
)


def write_event(payload: dict[str, Any]) -> None:
    sys.stdout.buffer.write((json.dumps(payload, ensure_ascii=False, default=str) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def trace_stage(stage: str, payload: dict[str, Any] | None = None) -> None:
    trace_path = os.environ.get("LOCAL_AI_GPP_WORKER_TRACE", "")
    if not trace_path:
        return
    try:
        with open(trace_path, "a", encoding="utf-8") as handle:
            handle.write(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] worker_{stage}\n")
            if payload:
                handle.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
                handle.write("\n")
    except Exception:
        pass


def build_llama_kwargs(payload: dict[str, Any]) -> dict[str, Any]:
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    model = payload.get("model") if isinstance(payload.get("model"), dict) else {}
    model_path = Path(str(model.get("path") or ""))
    kwargs: dict[str, Any] = {
        "model_path": str(model_path),
        "n_ctx": _int_value(runtime.get("n_ctx"), 4096),
        "n_batch": _int_value(runtime.get("n_batch"), 512),
        "n_threads": _int_value(runtime.get("n_threads"), 4),
        "n_threads_batch": _int_value(runtime.get("n_threads_batch"), 0),
        "n_gpu_layers": _int_value(runtime.get("n_gpu_layers"), 0),
        "main_gpu": _int_value(runtime.get("main_gpu"), 0),
        "split_mode": _split_mode(runtime.get("split_mode")),
        "offload_kqv": bool(runtime.get("offload_kqv", True)),
        "flash_attn": bool(runtime.get("flash_attn", False)),
        "op_offload": bool(runtime.get("op_offload", True)),
        "swa_full": bool(runtime.get("swa_full", False)),
        "use_mmap": bool(runtime.get("use_mmap", True)),
        "use_mlock": bool(runtime.get("use_mlock", False)),
        "verbose": bool(runtime.get("verbose_runtime", False)),
        "seed": _int_value(runtime.get("seed"), -1),
    }
    tensor_split = _tensor_split(runtime.get("tensor_split"))
    if tensor_split:
        kwargs["tensor_split"] = tensor_split
    return kwargs


def llama_backend_details() -> dict[str, Any]:
    details: dict[str, Any] = {}
    try:
        import llama_cpp as package
        from llama_cpp import llama_cpp as llama_cpp_lib

        details["version"] = getattr(package, "__version__", "")
        try:
            raw_info = llama_cpp_lib.llama_print_system_info()
            details["system_info"] = raw_info.decode("utf-8", "replace") if isinstance(raw_info, bytes) else str(raw_info)
        except Exception as exc:
            details["system_info_error"] = str(exc)
        try:
            details["supports_gpu_offload"] = bool(llama_cpp_lib.llama_supports_gpu_offload())
        except Exception as exc:
            details["supports_gpu_offload_error"] = str(exc)
    except Exception as exc:
        details["error"] = str(exc)
    return details


def count_tokens(runtime: Any, text: str, *, add_bos: bool = False) -> int | None:
    encoded = text.encode("utf-8", "ignore")
    attempts = (
        {"add_bos": add_bos, "special": True},
        {"add_bos": add_bos},
        {},
    )
    for kwargs in attempts:
        try:
            return len(runtime.tokenize(encoded, **kwargs))
        except TypeError:
            continue
        except Exception:
            return None
    return None


def estimate_usage(runtime: Any, messages: list[dict[str, Any]], completion_text: str) -> dict[str, int]:
    prompt_text = "\n".join(f"{item.get('role', '')}: {item.get('content', '')}" for item in messages)
    prompt_tokens = count_tokens(runtime, prompt_text, add_bos=True)
    completion_tokens = count_tokens(runtime, completion_text, add_bos=False)
    usage: dict[str, int] = {}
    if prompt_tokens is not None:
        usage["prompt_tokens"] = prompt_tokens
    if completion_tokens is not None:
        usage["completion_tokens"] = completion_tokens
    if prompt_tokens is not None and completion_tokens is not None:
        usage["total_tokens"] = prompt_tokens + completion_tokens
    return usage


def main() -> int:
    try:
        trace_stage("process_started", {"python": sys.executable, "cwd": os.getcwd(), "argv": sys.argv})
        raw_payload = sys.stdin.buffer.read()
        trace_stage("stdin_read", {"bytes": len(raw_payload)})
        payload = json.loads(raw_payload.decode("utf-8") or "{}")
        trace_stage(
            "payload_loaded",
            {
                "model_path": ((payload.get("model") or {}) if isinstance(payload.get("model"), dict) else {}).get("path"),
                "stream": bool(payload.get("stream")),
                "message_count": len(payload.get("messages") or []),
            },
        )
        write_event({"type": "worker_status", "message": "Worker запущен, импортирую llama-cpp-python."})
        trace_stage("import_llama_start")
        from llama_cpp import Llama

        trace_stage("import_llama_done")
        llama_kwargs = _filter_supported_kwargs(Llama, build_llama_kwargs(payload))
        trace_stage("llama_kwargs_ready", llama_kwargs)
        mode = runtime_mode(llama_kwargs)
        write_event({"type": "runtime", "mode": mode, "llama_kwargs": llama_kwargs})
        trace_stage("runtime_event_written", {"mode": mode})
        trace_stage("llama_load_start")
        runtime = Llama(**llama_kwargs)
        trace_stage("llama_load_done")
        chat_kwargs: dict[str, Any] = {
            "messages": payload.get("messages") or [],
            "max_tokens": _int_value(payload.get("max_tokens"), 1024),
            "temperature": _float_value(payload.get("temperature"), 0.2),
            "top_k": _int_value(payload.get("runtime", {}).get("top_k"), 40),
            "top_p": _float_value(payload.get("runtime", {}).get("top_p"), 0.95),
            "min_p": _float_value(payload.get("runtime", {}).get("min_p"), 0.05),
            "repeat_penalty": _float_value(payload.get("runtime", {}).get("repeat_penalty"), 1.1),
            "seed": _int_value(payload.get("runtime", {}).get("seed"), -1),
        }
        stream = bool(payload.get("stream"))
        if stream:
            full_text = ""
            finish_reason = None
            started = time.perf_counter()
            trace_stage("stream_generation_start", {"max_tokens": chat_kwargs["max_tokens"]})
            for chunk in runtime.create_chat_completion(
                **_filter_supported_kwargs(runtime.create_chat_completion, {**chat_kwargs, "stream": True})
            ):
                choice = (chunk.get("choices") or [{}])[0]
                finish_reason = choice.get("finish_reason") or finish_reason
                delta = choice.get("delta") or {}
                text = str(delta.get("content") or "")
                if text:
                    full_text += text
                    write_event({"type": "delta", "text": text})
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            trace_stage("stream_generation_done", {"elapsed_ms": elapsed_ms, "chars": len(full_text), "finish_reason": finish_reason})
            write_event({
                "type": "done",
                "content": full_text,
                "finish_reason": finish_reason,
                "usage": estimate_usage(runtime, payload.get("messages") or [], full_text),
                "elapsed_ms": elapsed_ms,
                "runtime": {"mode": mode, "n_gpu_layers": llama_kwargs.get("n_gpu_layers")},
            })
            return 0

        started = time.perf_counter()
        trace_stage("generation_start", {"max_tokens": chat_kwargs["max_tokens"]})
        result = runtime.create_chat_completion(**_filter_supported_kwargs(runtime.create_chat_completion, chat_kwargs))
        trace_stage("generation_done", {"elapsed_ms": round((time.perf_counter() - started) * 1000)})
        if isinstance(result, dict):
            if not result.get("usage"):
                result["usage"] = estimate_usage(
                    runtime,
                    payload.get("messages") or [],
                    str((result.get("choices") or [{}])[0].get("message", {}).get("content") or ""),
                )
            result["_local_ai_gpp_worker"] = {
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "runtime": {"mode": mode, "n_gpu_layers": llama_kwargs.get("n_gpu_layers")},
            }
        write_event({"type": "result", "result": result})
        return 0
    except Exception as exc:
        trace_stage("error", {"message": str(exc), "traceback": traceback.format_exc()})
        write_event({"type": "error", "message": str(exc), "traceback": traceback.format_exc()})
        return 1


def runtime_mode(kwargs: dict[str, Any]) -> str:
    n_gpu_layers = _int_value(kwargs.get("n_gpu_layers"), 0)
    if n_gpu_layers == 0:
        return "CPU only"
    if n_gpu_layers < 0:
        return "GPU only: all layers"
    return f"CPU/GPU: {n_gpu_layers} GPU layers"


if __name__ == "__main__":
    raise SystemExit(main())