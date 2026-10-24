'use strict';
const assert = require('node:assert/strict');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const { observeMailboxFrames, mailboxResponseMetadata } = require('../../../management_portal/test_framework/tau_mailbox_evidence');
const target = { residentId: 'resident_a123', entryKey: 'resident:resident_a123', documentId: 'fixture-document-a' };

(async () => {
  const root = { paged: true, next_page_token: 'private-cursor', read_revision: 7, unread_count: 1,
    sections: { conversation: { count: 2, unread_count: 1 }, notification: { count: 0, unread_count: 0 } },
    entries: [{ key: target.entryKey, resident_id: target.residentId, unread: true, title: 'private content' },
      { key: 'resident:resident_bb', resident_id: 'resident_bb', title: 'other private content' }],
    mailbox_summary: { unread_count: 8, sections: { conversation: { count: 20, unread_count: 5 }, notification: { count: 4, unread_count: 3 } },
      entries: [{ key: 'resident:resident_bb', resident_id: 'resident_bb', unread: true }] } };
  const metadata = mailboxResponseMetadata(root, target);
  assert.equal(metadata.page.entry_count, 2);
  assert.equal(metadata.page.sections.conversation.count, 2);
  assert.equal(metadata.global_summary.sections.conversation.count, 20);
  assert.equal(metadata.global_summary.unread_count, 8, 'global count is the actual field, not unread membership length');
  assert.equal(metadata.global_summary.unread_membership_count, 1);
  assert.equal(metadata.page.target_entries.length, 1);
  assert.equal(metadata.page.target_entries[0].read_revision, null);
  const absent = mailboxResponseMetadata({}, target);
  assert.equal(absent.global_summary, null);
  assert.equal(absent.page.unread_count, null);
  assert.equal(absent.page.target_entries, null);
  assert.equal(absent.page.next_page_present, null);
  assert.ok(!JSON.stringify(metadata).includes('private'));
  assert.ok(!JSON.stringify(metadata).includes('resident_bb'));
  assert.throws(() => mailboxResponseMetadata(root, {}), /Explicit target/);

  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent(`<style>.portalAgentInboxPanel {display:none;width:300px}.unread{background:rgb(255,240,240)}
      .portalAgentInboxUnreadDot{display:inline-block;width:8px;height:8px;background:red}</style>
      <aside data-portal-agent-inbox><button data-agent-conversation-trigger aria-expanded="false"><span data-agent-conversation-count>1</span></button>
      <section class="portalAgentInboxPanel"><div data-agent-conversation-list></div></section>
      <section class="portalAgentInboxPanel portalPersonalAssistantPanel" style="display:block">Other panel</section></aside>`);
    await page.evaluate(({ residentId, entryKey }) => {
      window.mutationsByProbe = 0;
      window.snapshot = { ready: true, loading: false, refreshing: false, syncing: false, paged: true,
        entries: [{ key: entryKey, resident_id: residentId, unread: true, title: 'secret target title' }],
        unreadCount: 8, sections: { conversation: { count: 1, unreadCount: 5 }, notification: { count: 0, unreadCount: 3 } }, revision: 7 };
      window.PortalConversationStore = { snapshot: () => window.snapshot,
        refresh: () => { throw new Error('probe must not refresh'); }, subscribe: () => { throw new Error('probe must not acquire'); } };
      new MutationObserver(() => window.mutationsByProbe++).observe(document.body, {subtree:true,attributes:true,childList:true});
    }, target);
    const probe = await observeMailboxFrames(page, target);
    const before = await probe.arm('unread');
    assert.equal(before.panel.visible, false, 'visible Personal Assistant is not Mailbox');
    assert.equal(before.store.unreadCount, 8);
    assert.equal(await page.evaluate(() => mutationsByProbe), 0, 'observer only reads');
    await page.evaluate(() => {
      document.querySelector('.portalAgentInboxPanel').style.display = 'block';
      document.querySelector('[data-agent-conversation-trigger]').setAttribute('aria-expanded', 'true');
    });
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    let captures = await probe.take();
    assert.deepEqual(captures[0].first_visible_animation_frame.target_dom, [], 'missing original row is retained');
    assert.ok(captures[0].first_visible_observation.browser_ms <= captures[0].first_visible_animation_frame.browser_ms);
    await page.evaluate(({ entryKey }) => {
      const list = document.querySelector('[data-agent-conversation-list]');
      list.innerHTML = `<button data-resident-id="resident_a123" data-agent-conversation-key="other">decoy secret</button>
        <button data-agent-conversation-key="${entryKey}" class="portalAgentInboxItem unread" data-status="idle">
        <span>secret title</span><span class="portalAgentInboxStatus idle">Idle</span><span class="portalAgentInboxUnreadDot"></span></button>`;
    }, target);
    assert.equal((await probe.sample()).target_dom.length, 1, 'exact key matches the real row without href or resident attribute');
    assert.deepEqual((await probe.take())[0].first_visible_animation_frame.target_dom, [], 'later correction never fills an original gap');
    await assert.rejects(probe.arm('unread'), /cannot be rearmed/);
    await assert.rejects(probe.arm('already-open'), /before opening/);
    await page.evaluate(() => document.querySelector('.portalAgentInboxPanel').style.display = 'none');
    await probe.arm('read');
    await page.evaluate(() => {
      const row = document.querySelector('.portalAgentInboxItem');
      row.classList.remove('unread'); row.querySelector('.portalAgentInboxUnreadDot').remove();
      window.snapshot.entries[0].unread = false; window.snapshot.entries[0].read_revision = 8;
      window.snapshot.refreshing = true;
      document.querySelector('.portalAgentInboxPanel').style.display = 'block';
    });
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    captures = await probe.stop();
    const firstRead = captures[1].first_visible_animation_frame;
    assert.equal(firstRead.document_id, target.documentId);
    assert.equal(firstRead.store.refreshing, true, 'first frame is not replaced with eventual ready');
    assert.equal(firstRead.target_dom[0].unread_class, false);
    assert.equal(firstRead.target_dom[0].unread_dot, null);
    assert.equal(firstRead.target_dom[0].status, 'idle');
    assert.ok(firstRead.target_dom[0].rect[2] > 0);
    assert.ok(firstRead.target_dom[0].font_weight);
    assert.ok(!JSON.stringify(captures).includes('secret'));
    const second = await browser.newPage();
    await second.setContent('<aside data-portal-agent-inbox><section class="portalAgentInboxPanel" style="display:none"></section></aside>');
    const other = await observeMailboxFrames(second, { ...target, documentId: 'fixture-document-b' });
    const sampleB = await other.arm('unopened');
    assert.equal(sampleB.store.available, false, 'never reads the default page Store');
    assert.equal(sampleB.document_id, 'fixture-document-b');
    assert.equal((await other.stop())[0].first_visible_animation_frame, null, 'no open means no invented frame');
    console.log('Mailbox original-frame and response metadata contracts passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
