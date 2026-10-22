const assert = require('node:assert/strict');
const { test } = require('node:test');
const { randomUUID } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static/portal_society_service.js'), 'utf8');

function fixture(code = 'instruction.version_conflict', refreshFails = false) {
  const context = { PortalUIPrimitives: { randomUUID }, PortalI18n: { t: String } };
  context.window = context;
  vm.runInNewContext(source, context);
  const calls = [], conflicts = [];
  let reject = true;
  const original = Object.assign(new Error('expected 1695, current 1697'), { code, commit_state: 'not_committed' });
  const client = context.PortalSocietyService.createClient({
    execute: async (name, args, controls) => {
      calls.push({ name, args, controls });
      if (name === 'GetLineage') {
        if (refreshFails) throw new Error('refresh unavailable');
        return { lineage: { lineage_id: 'lineage_test', revision: 1697 } };
      }
      assert.equal(name, 'ForkResident');
      if (reject) throw original;
      return { resident_id: 'resident_child' };
    },
    onConflict: value => conflicts.push(value),
  });
  return { client, calls, conflicts, original, accept: () => { reject = false; } };
}

test('Fork refreshes a version conflict without replay and uses the new revision for a new action', async () => {
  const f = fixture();
  const payload = { lineage_id: 'lineage_test', source_resident_id: 'resident_parent' };
  await assert.rejects(f.client.branchNode({ ...payload, expected_version: 1695 }), error => error === f.original);
  assert.deepEqual(f.calls.map(c => c.name), ['ForkResident', 'GetLineage']);
  assert.equal(f.calls[0].controls.expectedVersion, 1695);
  assert.equal(f.conflicts[0].lineage.revision, 1697);
  assert.equal(f.original.commit_state, 'not_committed');
  assert.match(f.original.message, /本次修改未保存/);
  f.accept();
  const result = await f.client.branchNode(payload);
  assert.equal(result.resident_id, 'resident_child');
  assert.equal(f.calls[2].controls.expectedVersion, 1697);
  assert.notEqual(f.calls[2].controls.idempotencyKey, f.calls[0].controls.idempotencyKey);
  assert.equal(f.calls.filter(c => c.name === 'ForkResident').length, 2);
});

test('failed conflict refresh preserves the original failure and reports a visible read failure', async () => {
  const f = fixture('instruction.version_conflict', true);
  await assert.rejects(f.client.branchNode({ lineage_id: 'lineage_test', source_resident_id: 'resident_parent', expected_version: 1695 }), error => error === f.original);
  assert.deepEqual(f.calls.map(c => c.name), ['ForkResident', 'GetLineage']);
  assert.equal(f.conflicts[0].lineage, undefined);
  assert.match(f.original.refreshError.message, /refresh unavailable/);
  assert.match(f.original.message, /重读失败/);
});

test('authorization rejection does not refresh or replay the write', async () => {
  const f = fixture('instruction.forbidden');
  await assert.rejects(f.client.branchNode({ lineage_id: 'lineage_test', source_resident_id: 'resident_parent' }), error => error === f.original);
  assert.deepEqual(f.calls.map(c => c.name), ['ForkResident']);
  assert.equal(f.conflicts.length, 0);
});
