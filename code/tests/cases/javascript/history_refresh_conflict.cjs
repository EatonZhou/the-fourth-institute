'use strict';
const { JSDOM } = require('jsdom');
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../../..');
async function check(mode) {
  const dom = new JSDOM('', { url: 'https://history.fixture.invalid', runScripts: 'outside-only' });
  const w = dom.window;
  let data = Array.from({ length: 10 }, (_, i) => ({ message_id: 'm' + (i + 1) }));
  let refresh = false, heads = 0, probes = 0;
  const errors = [];
  const page = (before, limit) => {
    const messages = data.filter(m => Number(m.message_id.slice(1)) < before).slice(-limit);
    const min = Number(messages[0]?.message_id.slice(1));
    return { resident_id: 'member', messages, input_states: [], next_page_token: min > 1 ? String(min) : '' };
  };
  w.PortalIdentity = { load: async () => {}, username: () => 'fixture' };
  w.PortalEvents = { watchResident: () => () => {} };
  w.PortalResidentService = {
    snapshot: async (_, { historyLimit }) => {
      if (refresh) heads++;
      return { ...page(Infinity, historyLimit), resident: { resident_id: 'member' }, cursor: refresh ? 2 : 1,
        state: { work_state: 'idle' }, controls: { can_send: true }, queue: { items: [] }, activities: [] };
    },
    conversationMessages: async (_, { pageToken, limit }) => {
      if (refresh && mode === 'denied') throw Object.assign(new Error('forbidden'), { status: 403 });
      const response = page(Number(pageToken), limit);
      if (refresh && limit === 1) {
        probes++;
        if (mode === 'mismatch') response.resident_id = 'other';
        else if (mode === 'persistent' || (mode === 'once' && probes === 1)) {
          const id = response.messages.at(-1)?.message_id;
          data = data.filter(m => m.message_id !== id);
        }
      }
      return response;
    },
    events: async () => ({ resident_id: 'member', cursor: 2, events: [] })
  };
  w.eval(fs.readFileSync(path.join(root, 'management_portal/static/resident_protocol.js'), 'utf8'));
  const sub = w.PortalResidentProtocol.acquire('member', u => { if (u.type === 'error') errors.push(u); }, { load: false });
  try {
    await sub.load({ historyLimit: 2 });
    await sub.loadOlder({ limit: 2 });
    const before = JSON.stringify(sub.snapshot());
    refresh = true;
    if (mode === 'once') {
      await sub.load({ force: true, historyLimit: 2 });
      assert.deepEqual(Array.from(sub.snapshot().messages, m => m.message_id), ['m7', 'm8', 'm9', 'm10']);
      assert.equal(sub.snapshot().next_page_token, '7');
      assert.equal(heads, 2, 'retry must restart at authoritative head');
      assert.equal(errors.length, 0, 'recovered conflict must not emit a conversation error');
    } else {
      await assert.rejects(sub.load({ force: true, historyLimit: 2 }), e => {
        if (mode === 'persistent') return e.code === 'conversation_history_changed';
        return e.message === (mode === 'denied' ? 'forbidden' : 'Resident message page target mismatch');
      });
      if (mode === 'denied') assert.equal(sub.snapshot(), null, 'permission denial must revoke cached history');
      else assert.equal(JSON.stringify(sub.snapshot()), before, 'failed read cannot commit partial history or cursor');
      assert.equal(heads, mode === 'persistent' ? 3 : 1, 'only history conflicts retry and attempts are bounded');
    }
  } finally { sub.destroy(); w.close(); }
}
(async () => { for (const mode of ['once', 'persistent', 'denied', 'mismatch']) await check(mode); console.log('History conflict recovery checks passed'); })().catch(e => { console.error(e); process.exitCode = 1; });
