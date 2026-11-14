import assert from 'node:assert/strict';
import test from 'node:test';
import { changedRuntime, disjointRuntimeChange, clearSubmittedDraft } from '../src/settingsPatch.ts';
import type { RuntimeSettings } from '../src/types.ts';

test('F06 only edited runtime fields are sent, without seed or server secrets', () => {
  const baseline={temperature:.2,seed:3,n_ctx:4096} as RuntimeSettings;
  const patch=changedRuntime(baseline,{temperature:.4,n_ctx:4096});
  assert.deepEqual(patch,{temperature:.4});
  assert(disjointRuntimeChange(baseline,{...baseline,seed:8},patch));
  assert(!disjointRuntimeChange(baseline,{...baseline,temperature:.6},patch));
});

test('F10 accepted whitespace draft clears; a newly typed draft remains', () => {
  assert.equal(clearSubmittedDraft('  hello\n','  hello\n'),'');
  assert.equal(clearSubmittedDraft('next question','  hello\n'),'next question');
});
