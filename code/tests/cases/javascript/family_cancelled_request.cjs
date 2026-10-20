'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { mount } = require('./message_state_family.cjs');

const staticRoot = path.resolve(__dirname, '../../../management_portal/static');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const request = (id, state) => ({
  message_id: id, role: 'user', author_id: 'playwright',
  created_at: '2026-10-19T06:02:07Z', activated_at: '2026-10-19T06:02:07Z',
  content_parts: [{ type: 'text', text: 'Keep this original request.' }],
  // The facade's own input is completed on relay; the actual member was cancelled.
  relay: { resident_id: 'resident_111111111111', message_id: 'relayed-input',
    state, title: 'Worker', via: [{ resident_id: 'resident_333333333333', title: 'Nested team' }] },
});

(async () => {
  for (const [locale, label] of [['zh-CN', '已撤销'], ['en', 'Cancelled']]) {
    const app = mount(input.routeScript, [request('previous', 'completed'), request('cancelled', 'queued')]);
    try {
      app.window.__PORTAL_LANGUAGE__ = locale;
      for (const file of ['portal_translations.js', 'portal_i18n.js']) {
        app.window.eval(fs.readFileSync(path.join(staticRoot, file), 'utf8'));
      }
      app.snapshot.input_states = [{ message_id: 'cancelled', state: 'completed' }];
      app.render();
      assert.equal(app.root.querySelectorAll('.messageCancelledBadge').length, 0);
      app.snapshot.messages[1].relay.state = 'cancelled';
      app.render();
      const row = app.output('cancelled');
      assert.equal(row.querySelector('.messageCancelledBadge')?.textContent, label,
        'retained cancelled request must visibly carry its status');
      assert.match(row.querySelector('.messageBubble').textContent, /Keep this original request\./);
      assert.equal(app.output('previous').querySelector('.messageCancelledBadge'), null);
      assert.equal(row.querySelector('.familyRequestAction'), null);
      // The repeated human header is compacted; cancellation must still be visible.
      assert.equal(row.querySelector('.messageHead'), null);
      app.render();
      assert.equal(app.root.querySelectorAll('.messageCancelledBadge').length, 1);

      for (const state of ['running', 'completed', 'failed', 'queued']) {
        app.snapshot.messages[1].relay.state = state;
        app.render();
        assert.equal(app.output('cancelled').querySelector('.messageCancelledBadge'), null, state);
      }
      // Reopened/history snapshots carry relay state without live input_states.
      delete app.snapshot.input_states;
      app.snapshot.messages = [request('history', 'cancelled')];
      app.render();
      assert.equal(app.output('history').querySelector('.messageCancelledBadge')?.textContent, label);
      // Undelivered inputs still use the server's body-free withdrawal annotation.
      app.snapshot.messages = [{ ...request('withdrawn', 'cancelled'), withdrawn: true, content_parts: [] }];
      app.render();
      assert.ok(app.output('withdrawn').classList.contains('messageWithdrawal'));
      assert.equal(app.output('withdrawn').querySelector('.messageBubble'), null);
      assert.doesNotMatch(app.output('withdrawn').textContent, /Keep this original request/);
    } finally { await app.close(); }
  }
  console.log('PASS retained cancellation: live update, compact header, history, languages, withdrawal distinction');
})();
