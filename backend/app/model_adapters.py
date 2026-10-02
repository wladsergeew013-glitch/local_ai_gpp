"""Model-specific prompt controls; leave unrelated GGUF templates untouched."""
from __future__ import annotations


def qwen_thinking_template(metadata: dict, enable_thinking: bool = False) -> str | None:
    architecture = str(metadata.get('general.architecture', '')).lower()
    template = metadata.get('tokenizer.chat_template')
    if not architecture.startswith('qwen') or not isinstance(template, str) or 'enable_thinking' not in template:
        return None
    if not isinstance(enable_thinking, bool):
        raise ValueError('enable_thinking must be true or false.')
    # The embedded GGUF template contains the model's actual no-thinking branch.
    # Binding its variable avoids a prompt instruction or output-only filtering.
    value = 'true' if enable_thinking else 'false'
    return '{% set enable_thinking = ' + value + ' %}\n' + template


def apply_model_adapter(runtime, cfg: dict) -> dict:
    enabled = cfg.get('enable_thinking', False)
    template = qwen_thinking_template(runtime.metadata, enabled)
    if template is None:
        return {'model_adapter': 'default', 'enable_thinking': None}
    from llama_cpp.llama_chat_format import Jinja2ChatFormatter

    eos_id, bos_id = runtime.token_eos(), runtime.token_bos()

    def token_text(token_id):
        return runtime.detokenize([token_id], special=True).decode('utf-8') if token_id >= 0 else ''

    runtime.chat_handler = Jinja2ChatFormatter(
        template=template, eos_token=token_text(eos_id), bos_token=token_text(bos_id),
        stop_token_ids=[eos_id] if eos_id >= 0 else None,
    ).to_chat_handler()
    return {'model_adapter': 'qwen', 'enable_thinking': enabled}
