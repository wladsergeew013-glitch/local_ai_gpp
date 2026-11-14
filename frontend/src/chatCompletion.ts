import type { ChatMessage } from './types';

export function messageError(message: ChatMessage): string {
  if (message.phase === 'error' || message.finish_reason === 'length' || message.reasoning_truncated || message.answer_state === 'missing_after_reasoning')
    return message.error || message.status || 'Ответ не завершён; он не включён в память.';
  return '';
}

type Completion = {
  answer?: string | null; content?: string; reasoning?: string;
  answer_state?: string; reasoning_truncated?: boolean; finish_reason?: string | null;
};

export function completedAnswer(event: Completion, streamed: string) {
  const answer = 'answer' in event ? (event.answer ?? '') : (event.content ?? streamed);
  const incomplete = event.finish_reason === 'length' || !!event.reasoning_truncated
    || event.answer_state === 'missing_after_reasoning' || !answer.trim();
  return { text: answer, answer, reasoning: event.reasoning, pending: false,
    phase: incomplete ? 'error' as const : 'done' as const,
    answer_state: event.answer_state, reasoning_truncated: event.reasoning_truncated,
    finish_reason: event.finish_reason,
    error: incomplete ? 'Ответ не завершён. Увеличьте лимит ответа и повторите запрос.' : undefined,
    status: incomplete ? 'Ответ не завершён. Увеличьте лимит ответа и повторите запрос.' : 'Готово' };
}

export function requestHistory(messages: ChatMessage[]) {
  return messages.filter(m => !m.pending && m.phase !== 'error'
    && m.finish_reason !== 'length' && !m.reasoning_truncated && m.answer_state !== 'missing_after_reasoning')
    .map(m => ({ role: m.role, content: m.answer ?? m.text }))
    .filter(m => m.content.trim());
}
