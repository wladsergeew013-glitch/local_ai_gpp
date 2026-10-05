"""Cheap UI estimates and validation shared by the desktop and inference paths."""
from __future__ import annotations
import math


def validate_context(n_ctx: int, max_tokens: int) -> None:
    if not 512 <= n_ctx <= 131072:
        raise ValueError('Контекст должен быть от 512 до 131072 токенов.')
    if not 1 <= max_tokens <= min(32768, n_ctx - 257):
        raise ValueError(f'Для контекста {n_ctx} длина ответа должна быть от 1 до {min(32768, n_ctx - 257)} токенов. Уменьшите длину ответа или увеличьте контекст.')


def estimate_context(messages: list[dict], max_tokens: int) -> int:
    # An estimate only: the worker verifies the model's tokenizer before inference.
    return sum(math.ceil(len(str(m.get('content') or '').encode('utf-8')) / 3) + 32 for m in messages) + 128 + max_tokens


def context_limit_message(n_ctx: int, prompt_tokens: int, max_tokens: int, *, history: bool) -> str:
    advice = 'Включите автоматическое сокращение истории или начните новый диалог.' if history else 'Сократите сообщение или инструкцию модели.'
    return f'Не хватает контекста: запрос и история занимают около {prompt_tokens} токенов, под ответ выделено {max_tokens}, лимит — {n_ctx}. {advice} Также можно уменьшить длину ответа или увеличить контекст в настройках.'
