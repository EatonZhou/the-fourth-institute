'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
module.exports = async ({ page, output, errors }) => {
  await page.evaluate(() => {
    const user = (id, text) => ({ message_id: id, role: 'user', author_id: 'weihao',
      content_parts: [{ type: 'text', text }], created_at: '2026-11-05T09:18:00Z' });
    const reply = (id, request, text, resident = 'resident_111111111111') => ({ message_id: id,
      role: 'agent', author_id: 'resident_222222222222', content_parts: [{ type: 'text', text }],
      family_reply: { request_message_id: request, request_message_ids: [request], resident_id: resident, title: '处理成员' },
      created_at: '2026-11-05T09:35:00Z' });
    window.blockSnapshot = { family_facade: { title: 'Portal 开发' }, messages: [
      user('video', '修改视频页面，移除页头说明。'),
      user('improve', '优化展示规则。'), reply('improve-before', 'improve', '已检查原有展示规则。'),
      user('rollback', '恢复原版。'), reply('rollback-after', 'rollback', '已按补充要求恢复原版。'),
      reply('video-late', 'video', '视频页面已整理完成。此条回复虽然晚到，仍在视频请求下方。', 'resident_333333333333'),
    ] };
    window.drawBlocks = () => {
      const projected = PortalChatWorkspace.projectResidentConversation({}, blockSnapshot);
      workspace.setMessages(projected.messages);
    };
    const position = ordinal => ({ resident_id: 'resident_111111111111', ordinal });
    blockSnapshot.messages.find(m => m.message_id === 'improve-before').conversation_position = position(2);
    const context = (id, ordinal, role, text) => ({ message_id: id, role,
      author_id: role === 'user' ? 'resident_444444444444' : 'portal',
      origin: { sender: { kind: role === 'user' ? 'resident' : 'service', id: role === 'user' ? 'resident_444444444444' : 'portal' } },
      content_parts: [{ type: 'text', text }], conversation_position: position(ordinal),
      conversation_context: { request_message_id: 'improve' },
      family_context: { request_message_id: 'improve', resident_id: 'resident_111111111111', title: '处理成员' } });
    // History/event arrival order is intentionally different from trace order.
    blockSnapshot.messages.push(
      { ...reply('improve-after', 'improve', '收到协作后继续工作。'), conversation_position: position(6) },
      context('receipt', 5, 'system', 'Fast 已开启'),
      { ...context('fast', 4, 'user', '/fast'), author_id: 'weihao', origin: { sender: { kind: 'user', id: 'weihao' } } },
      context('collaboration', 3, 'user', '来自同事的补充。'),
    );
    drawBlocks();
  });
  const ids = () => page.locator('.messageList > article').evaluateAll(nodes => nodes.map(n => n.dataset.messageKey));
  const expected = ['video', 'video-late', 'improve', 'improve-before', 'collaboration', 'fast', 'receipt', 'improve-after', 'rollback', 'rollback-after'];
  assert.deepEqual(await ids(), expected);
  await page.evaluate(() => drawBlocks());
  assert.deepEqual(await ids(), expected);
  assert.ok((await page.locator('[data-message-key="collaboration"]').innerText()).includes('处理成员 的工作期间'));
  assert.equal(await page.evaluate(() => PortalChatWorkspace.projectResidentConversation({}, blockSnapshot).messages
    .find(m => m.id === 'improve-after').display_reply_to_message_id), 'improve');
  if (output) await page.screenshot({ path: path.join(output, 'request-blocks-desktop.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.deepEqual(await ids(), expected);
  if (output) await page.screenshot({ path: path.join(output, 'request-blocks-mobile.png') });
  // A late update while reading another block retains that block's position.
  await page.setViewportSize({ width: 1000, height: 500 });
  await page.evaluate(() => {
    blockSnapshot.messages.find(m => m.message_id === 'video-late').content_parts[0].text += '\n\n' + '视频说明。'.repeat(900);
    drawBlocks();
  });
  await page.locator('[data-message-key="rollback"]').scrollIntoViewIfNeeded();
  await page.mouse.move(700, 300); await page.mouse.wheel(0, -50);
  await page.waitForTimeout(100);
  const before = await page.locator('[data-message-key="rollback"]').boundingBox();
  await page.evaluate(() => {
    blockSnapshot.messages.push({ message_id: 'video-more', role: 'agent', author_id: 'resident_222222222222',
      content_parts: [{ type: 'text', text: '补充视频结果。'.repeat(40) }], family_reply: { request_message_id: 'video', resident_id: 'resident_333333333333' } });
    drawBlocks();
  });
  await page.waitForTimeout(100);
  const after = await page.locator('[data-message-key="rollback"]').boundingBox();
  assert.ok(Math.abs(after.y - before.y) < 3, `reading anchor changed: ${before.y} -> ${after.y}`);
  assert.deepEqual(errors, []);
  console.log('PASS request blocks: late parallel reply, redraw, narrow viewport, reading anchor');
};
