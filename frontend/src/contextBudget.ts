export function estimateContext(messages: { content: string }[], maxTokens: number): number {
  const encoder = new TextEncoder();
  return messages.reduce((sum, message) => sum + Math.ceil(encoder.encode(message.content).length / 3) + 32, 128 + maxTokens);
}

export function contextSettingsError(nCtx: number, maxTokens: number): string {
  if (!Number.isInteger(nCtx) || nCtx < 512 || nCtx > 131072) return 'Контекст должен быть целым числом от 512 до 131072 токенов.';
  if (!Number.isInteger(maxTokens) || maxTokens < 1 || maxTokens > Math.min(32768, nCtx - 257)) return `Для контекста ${nCtx} максимум на ответ — ${Math.min(32768, nCtx - 257)} токенов. Уменьшите длину ответа.`;
  return '';
}
