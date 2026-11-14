"""Validate persisted settings before replacing the last usable configuration."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from fastapi import HTTPException


class Section(BaseModel):
    model_config = ConfigDict(extra='allow', strict=True)


class Runtime(Section):
    n_ctx: int = Field(default=4096, ge=512, le=131072)
    n_batch: int = Field(default=512, ge=1, le=1048576)
    n_threads: int = Field(default=4, ge=1, le=1024)
    n_threads_batch: int = Field(default=0, ge=0, le=1024)
    n_gpu_layers: int = Field(default=0, ge=-1, le=100000)
    main_gpu: int = Field(default=0, ge=0, le=128)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=1024, ge=1, le=32768)
    top_k: int = Field(default=40, ge=0, le=100000)
    top_p: float = Field(default=0.95, ge=0, le=1)
    min_p: float = Field(default=0.05, ge=0, le=1)
    repeat_penalty: float = Field(default=1.1, gt=0, le=10)
    seed: int = Field(default=-1, ge=-1, le=4294967295)
    context_overflow: Literal['trim', 'error'] = 'trim'
    split_mode: Literal['none', 'layer', 'row'] = 'layer'
    warm_policy: Literal['keep_hot', 'manual', 'unload_after_idle'] = 'unload_after_idle'
    idle_unload_sec: int = Field(default=1800, ge=0, le=604800)
    request_timeout_sec: int = Field(default=180, ge=1, le=86400)
    load_timeout_sec: int = Field(default=120, ge=1, le=86400)
    worker_idle_timeout_sec: int = Field(default=60, ge=1, le=86400)
    tensor_split: str | list[float] = ''
    enable_thinking: bool = False
    offload_kqv: bool = True
    flash_attn: bool = False
    op_offload: bool = True
    swa_full: bool = False
    use_mmap: bool = True
    use_mlock: bool = False
    verbose_runtime: bool = False
    gpu_fallback_to_cpu: bool = True
    preload_on_start: bool = False


class Server(Section):
    host: str = '127.0.0.1'
    port: int = Field(default=8765, ge=1, le=65535)
    public_base_url: str = 'http://127.0.0.1:8765'
    openai_compat_enabled: bool = True
    openai_compat_path: str = '/v1/chat/completions'
    cors_origins: list[str] = Field(default_factory=list)
    api_key: str = ''


class Hub(Section):
    enabled: bool = False
    base_url: str = ''
    models_endpoint: str = '/models'
    pull_endpoint: str = '/models/pull'
    token: str = ''
    timeout_sec: int = Field(default=30, ge=1, le=3600)


class AccessPolicy(Section):
    api_key: str = ''
    openai_compat_enabled: bool = True


def validate_runtime(value, *, complete=False, check_context=True):
    if not isinstance(value, dict):
        raise HTTPException(422, 'INVALID_RUNTIME: runtime must be an object')
    try:
        Runtime.model_validate(value)
        if check_context and (complete or ('n_ctx' in value and 'max_tokens' in value)):
            from backend.app.context_budget import validate_context
            validate_context(value.get('n_ctx',4096),value.get('max_tokens',1024))
    except ValueError as exc:
        # Never include request values, tokens or secrets in schema diagnostics.
        errors = exc.errors(include_input=False) if isinstance(exc,ValidationError) else []
        fields = ', '.join('.'.join(map(str,e['loc'])) for e in errors)
        raise HTTPException(422, 'INVALID_RUNTIME: ' + (fields or str(exc))) from exc
    return value


def validate_section(section, actual, defaults):
    if not isinstance(actual,dict):
        raise HTTPException(422,f'INVALID_SETTINGS: {section} must be an object')
    for key,default in defaults.items():
        item=actual.get(key)
        valid=isinstance(item,type(default))
        if isinstance(default,float):valid=isinstance(item,(int,float)) and not isinstance(item,bool)
        elif isinstance(default,int) and not isinstance(default,bool):valid=isinstance(item,int) and not isinstance(item,bool)
        if section=='runtime' and key=='tensor_split':valid=isinstance(item,(str,list))
        if not valid:raise HTTPException(422,f'INVALID_SETTINGS: {section}.{key}')
    try:
        if section=='runtime':validate_runtime(actual,complete=True)
        elif section=='server':Server.model_validate(actual)
        elif section=='hub':Hub.model_validate(actual)
    except ValidationError as exc:
        raise HTTPException(422,f'INVALID_SETTINGS: {section}') from exc
    return actual


def validate_settings(value, defaults):
    if not isinstance(value,dict):raise HTTPException(422,'INVALID_SETTINGS: object required')
    for section,fields in defaults.items():validate_section(section,value.get(section),fields)
    return value
