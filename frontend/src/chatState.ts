import type { ChatMessage } from './types';
import { messageError } from './chatCompletion.ts';

export type Conversation = { id: string; title: string; createdAt: string; messages: ChatMessage[]; clearedVersion?: number };
export type ChatState = { activeConversationId: string; conversations: Conversation[]; updatedAt?: number; version?: number; storeId?: string; resetVersion?: number };
export const STORE = 'local-ai-gpp-chats-v1';
export const fresh = (): Conversation => ({ id: crypto.randomUUID(), title: 'Новый диалог', createdAt: new Date().toISOString(), messages: [] });

export function normalizeState(value: unknown): ChatState {
  if (!value || typeof value !== 'object') throw new Error('Некорректная история');
  const state = value as Record<string, unknown>;
  if (!Array.isArray(state.conversations) || !state.conversations.length) throw new Error('Некорректный список диалогов');
  const conversations = state.conversations.map(raw => {
    if (!raw || typeof raw !== 'object' || typeof raw.id !== 'string' || !Array.isArray(raw.messages)) throw new Error('Некорректный диалог');
    const messages = raw.messages.map((message: unknown) => {
      if (!message || typeof message !== 'object') throw new Error('Некорректное сообщение');
      const m = message as Record<string, unknown>;
      if (!['user', 'assistant'].includes(String(m.role)) || typeof m.text !== 'string') throw new Error('Некорректный текст сообщения');
      for (const key of ['answer', 'reasoning', 'error', 'status', 'createdAt']) {
        if (m[key] != null && typeof m[key] !== 'string') throw new Error(`Некорректное поле ${key}`);
      }
      return { ...m, id: typeof m.id === 'string' && m.id ? m.id : crypto.randomUUID() } as ChatMessage;
    });
    return { ...raw, title: typeof raw.title === 'string' ? raw.title : 'Новый диалог',
      createdAt: typeof raw.createdAt === 'string' ? raw.createdAt : '', messages } as Conversation;
  });
  const active = typeof state.activeConversationId === 'string' && conversations.some(c => c.id === state.activeConversationId)
    ? state.activeConversationId : conversations[0].id;
  return { ...state, activeConversationId: active, conversations } as ChatState;
}

export function initialState(storage: Pick<Storage, 'getItem' | 'setItem'> = localStorage): ChatState {
  let raw: string | null = null;
  try {
    raw = storage.getItem(STORE);
    if (raw) return normalizeState(JSON.parse(raw));
  } catch {
    // The original key is never overwritten. Preserve even structurally invalid JSON.
    try { storage.setItem(`${STORE}-corrupt-${crypto.randomUUID()}`, raw || ''); } catch { /* Keep the original. */ }
  }
  const conversation = fresh();
  return { activeConversationId: conversation.id, conversations: [conversation] };
}

export function acceptsRemote(previous: ChatState | null, incoming: ChatState, authoritative = false): boolean {
  if (!previous) return true;
  if ((previous.storeId || 'legacy') !== (incoming.storeId || 'legacy')) return authoritative;
  return (incoming.version ?? incoming.updatedAt ?? 0) > (previous.version ?? previous.updatedAt ?? 0);
}

export function stateStatus(state: ChatState): string {
  const active = state.conversations.find(c => c.id === state.activeConversationId) || state.conversations[0];
  const last = active?.messages.filter(m => m.role === 'assistant').slice(-1)[0];
  if (!last) return 'Готово';
  return last.pending ? 'Модель отвечает…' : messageError(last) || 'Готово';
}
