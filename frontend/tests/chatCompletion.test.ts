import test from 'node:test';
import assert from 'node:assert/strict';
import { completedAnswer, requestHistory } from '../src/chatCompletion.ts';

test('empty final answer never becomes an unfinished reasoning answer', () => {
  const result = completedAnswer({ answer: '', content: '<think>unfinished', reasoning: 'unfinished',
    answer_state: 'missing_after_reasoning', reasoning_truncated: true, finish_reason: 'length' }, '<think>unfinished');
  assert.equal(result.text, '');
  assert.equal(result.reasoning, 'unfinished');
  assert.equal(result.phase, 'error');
  assert.deepEqual(requestHistory([{ role: 'assistant', ...result }]), []);
});

test('completed final answers and user turns remain in memory', () => {
  const answer = completedAnswer({ answer: 'ORBIT', reasoning: 'private reasoning', finish_reason: 'stop' }, 'raw');
  assert.equal(answer.phase, 'done');
  assert.deepEqual(requestHistory([{ role: 'user', text: 'Project?' }, { role: 'assistant', ...answer }]),
    [{ role: 'user', content: 'Project?' }, { role: 'assistant', content: 'ORBIT' }]);
});

test('old incorrectly completed reasoning and truncated text stay out of memory', () => {
  assert.deepEqual(requestHistory([
    { role: 'assistant', text: 'unfinished reasoning', answer: '', phase: 'done' },
    { role: 'assistant', text: 'partial', phase: 'done', reasoning_truncated: true },
  ]), []);
  assert.equal(completedAnswer({ answer: 'partial', finish_reason: 'length' }, '').phase, 'error');
  assert.equal(completedAnswer({ content: 'legacy final answer' }, '').text, 'legacy final answer');
});
