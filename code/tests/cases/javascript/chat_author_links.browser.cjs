'use strict';
const assert = require('node:assert/strict');
module.exports = async ({ page, errors }) => {
  const own = 'resident_aaaaaaaaaaaa', other = 'resident_bbbbbbbbbbbb';
  const prepare = async () => page.evaluate(({ own, other }) => {
    PortalAPI = { get: async href => {
      const id = new URL(href, location.href).searchParams.get('address');
      return { id, type: 'resident', title: id === own ? '当前 Resident' : '其他 Resident', url: `/lineage?resident=${id}` };
    } };
    PortalUserAvatar = { messageAvatarHtml: id => `<span class="messageAuthorAvatar" data-message-avatar-principal="${id}">头像</span>`, bind() {} };
    workspace.state.contract = { ...workspace.state.contract, target: { kind: 'resident', id: own } };
    workspace.setMessages([
      { id: 'sender', message_id: 'sender', role: 'user', origin: { sender: { kind: 'resident', id: other } }, content: '成员来信' },
      { id: 'own', message_id: 'own', role: 'agent', author_id: own, content: '本 Resident 的回复' },
      { id: 'user', message_id: 'user', role: 'user', origin: { sender: { kind: 'user', id: 'reader' } }, content: '用户消息' },
    ], { chatResidentId: own });
  }, { own, other });
  // Intercept only the target page: the production message bindings still handle clicks.
  await page.route('**/lineage?**', route => route.fulfill({ contentType: 'text/html', body: '<p>Resident destination</p>' }));
  for (const [id, resident, selector, keyboard] of [
    ['sender', other, '.messageAuthorAvatar', false],
    ['sender', other, '.messageTitle', false],
    ['own', own, '.messageTitle', false],
    ['own', own, '.messageAuthorLink', true],
  ]) {
    await prepare();
    assert.equal(await page.locator('[data-message-key="user"] .messageAuthorLink').count(), 0);
    const target = page.locator(`[data-message-key="${id}"] ${selector}`);
    await Promise.all([
      page.waitForURL(url => url.pathname === '/lineage' && url.searchParams.get('resident') === resident && url.searchParams.get('open_chat') === '1'),
      keyboard ? target.press('Enter') : target.click(),
    ]);
    await page.goBack();
    // Back restores the fixture document, but its runtime workspace must be rebuilt.
    await page.evaluate(() => {
      workspace = PortalChatWorkspace.createWorkspace({ root: document.getElementById('chat'), id: 'author-links', refs: false });
    });
  }
  assert.deepEqual(errors, []);
  console.log('PASS Resident sender avatar/name and own reply name/keyboard navigation');
};
