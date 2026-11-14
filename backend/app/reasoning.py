"""Parse only the reasoning phase declared by the selected model adapter."""
import re


def split_reasoning(content, thinking=False):
    text = content or ''
    if not thinking:
        return dict(answer=text.strip(), reasoning='', answer_state='final_answer' if text.strip() else 'empty', reasoning_truncated=False)
    # Qwen's generation prompt may already contain the opening token.
    text = re.sub(r'^\s*<think>\s*', '', text, count=1, flags=re.I)
    closing = re.search(r'</think\s*>', text, re.I)
    answer = text[closing.end():].strip() if closing else ''
    reasoning = text[:closing.start()].strip() if closing else text.strip()
    return dict(answer=answer, reasoning=reasoning,
                answer_state='final_answer' if answer else 'missing_after_reasoning',
                reasoning_truncated=closing is None)


def uses_reasoning(runtime):
    return runtime.get('model_adapter') == 'qwen' and runtime.get('enable_thinking') is True
