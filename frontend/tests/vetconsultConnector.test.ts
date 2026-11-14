import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import vm from 'node:vm';

test('R52 saving an existing VetConsult connector updates its model and URL with the current revision', async () => {
  const html = fs.readFileSync(new URL('../../examples/vetconsult_connector.html', import.meta.url), 'utf8');
  const script = html.match(/<script>([\s\S]*?)<\/script>/)![1];
  const fields: Record<string, any> = Object.fromEntries(['vet','local','model','prompt','save','test','result'].map(id => [id, { value:'', disabled:false }]));
  fields.vet.value = 'http://127.0.0.1:8000'; fields.local.value = 'http://127.0.0.1:8765/v1'; fields.model.value = 'NEW_MODEL';
  const calls: any[] = [];
  const existing = { id:'local-ai-gpp', revision:7, config:{ base_url:'old', model:'OLD_MODEL', custom:'preserved' } };
  const fetch = async (url: string, options: any) => {
    calls.push({url, ...options});
    return { ok:true, json:async () => options.method === 'GET' ? {items:[existing]} : {ok:true} };
  };
  vm.runInNewContext(script, { document:{getElementById:(id: string) => fields[id]}, fetch, AbortSignal, JSON, Error });
  await fields.save.onclick();
  assert.equal(calls.length,2);
  const body = JSON.parse(calls[1].body);
  assert.equal(calls[1].method,'POST'); assert.equal(body.expected_revision,7);
  assert.equal(body.config.model,'NEW_MODEL'); assert.equal(body.config.base_url,fields.local.value);
  assert.equal(body.config.custom,'preserved'); assert.equal(calls[1].credentials,'include');
});
