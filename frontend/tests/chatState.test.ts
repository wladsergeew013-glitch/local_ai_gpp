import test from 'node:test';
import assert from 'node:assert/strict';
import { acceptsRemote, initialState, normalizeState, stateStatus, STORE } from '../src/chatState.ts';
import { requestHistory } from '../src/chatCompletion.ts';

const state = (version: number, pending = false) => ({ version, storeId: 's', activeConversationId: 'd', conversations: [
  { id: 'd', title: 'Dialog', createdAt: '', messages: [{ id: 'a', role: 'assistant' as const, text: 'partial', pending }] }] });

test('R04 delayed send response cannot replace a newer completed poll', () => {
  assert.equal(acceptsRemote(state(3), state(2, true)), false);
  assert.equal(acceptsRemote(state(3), state(3)), false);
  assert.equal(acceptsRemote(state(2, true), state(3)), true);
  assert.equal(acceptsRemote(state(3), { ...state(1), storeId: 'new-store' }), false);
  assert.equal(acceptsRemote(state(3), { ...state(1), storeId: 'new-store' }, true), true);
});

test('R06 errors remain visible after parsing saved/remote state', () => {
  const saved = state(3);
  Object.assign(saved.conversations[0].messages[0], { phase: 'error', error: 'Inference interrupted' });
  assert.equal(stateStatus(normalizeState(saved)), 'Inference interrupted');
});

test('R11 malformed structural JSON is backed up without overwriting the original', () => {
  for (const raw of [JSON.stringify({ conversations: [{ id: 'd', messages: [{ role: 'user', text: {} }] }] }), '{}', '[]', '{broken']) {
    const records = new Map([[STORE, raw]]);
    const storage = { getItem: (key: string) => records.get(key) ?? null, setItem: (key: string, value: string) => { records.set(key, value); } };
    assert.doesNotThrow(() => initialState(storage));
    assert.equal(records.get(STORE), raw);
    assert.ok([...records].some(([key, value]) => key.startsWith(STORE + '-corrupt-') && value === raw));
  }
});

test('R17 strict history has no implicit 64-message limit', () => {
  const messages = Array.from({ length: 130 }, (_, i) => ({ role: (i % 2 ? 'assistant' : 'user') as 'user' | 'assistant', text: String(i), phase: 'done' as const }));
  assert.equal(requestHistory(messages).length, 130);
  assert.equal(requestHistory(messages)[0].content, '0');
});
