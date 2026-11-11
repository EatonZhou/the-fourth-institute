"use strict";
// Real shared launcher, styles, state and input events in an isolated browser.
// Only external identity/data and the assistant's child chat are fixture inputs.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { chromium } = require("../../../management_portal/test_framework/tau_runtime/node_modules/playwright");
const code = path.resolve(__dirname, "../../..");
const scripts = ["portal_state", "portal_text", "portal_ui_primitives", "portal_refresh_coordinator",
  "resident_protocol", "portal_conversation_store", "portal_mailbox_todos", "portal_agent_inbox"];
const styles = ["portal_components", "portal_components_agent_inbox", "wall"];
const sources = new Map([...scripts.map(n => n + ".js"), ...styles.map(n => n + ".css")]
  .map(name => ["/static/" + name, fs.readFileSync(path.join(code, "management_portal/static", name))]));
const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
${styles.map(n => `<link rel="stylesheet" href="/static/${n}.css">`).join("")}
<style>:root{--text:#172030;--muted:#64748b;--panel:#fff;--border:#cbd5e1;--bg:#f5f7f9;--accent:#0b7972}
*{box-sizing:border-box}body{margin:0;background:var(--bg);font:14px sans-serif}header.portalSiteHeader{height:56px;padding:18px;border-bottom:1px solid var(--border)}
.wallWorkspaceView{height:calc(100vh - 56px)}h1{margin:28px 60px;font-size:20px}.composer{position:fixed;bottom:0;left:0;width:220px;height:80px}</style></head>
<body><header class="portalSiteHeader">TFI Portal · 共享入口隔离验证</header><main class="wallWorkspaceView"><h1>Mailbox / To Do / My Lineage</h1></main>
<script>
window.fixtureTarget={resident_id:'resident_111111111111',lineage_id:'lineage_222222222222',title:'个人助手'};
window.PortalIdentity={username:()=> 'docking-fixture',load:async()=>({username:'docking-fixture'}),userId:async()=> 'docking-fixture'};
window.PortalEvents={isConnected:()=>false,connect(){}};
window.PortalResponsive={isMobile:()=>innerWidth<=640};
window.PortalNav={personalAssistantTarget:async()=>window.fixtureTarget,loadChatWorkspace:async()=>{}};
window.PortalChatWorkspace={panelPackage:{create({root}){root.textContent='个人助手 · 隔离测试会话';return {presented:false,setPresented(value){this.presented=value},destroy(){}}}}};
window.PortalAPI={get:async(url)=>{if(url!='/api/agent-conversations')throw Error('Unexpected API '+url);return {entries:[],ready:true,content_signature:'empty',unread_count:0}}};
window.fixtureCalls=[];
window.fixtureTodos=[{todo_id:'todo_fixture',text:'整理本周工作计划',date:'2026-11-12',completed:false,position:0,indent:0}];
window.PortalInstructionClient={execute:async(name,args)=>{
  window.fixtureCalls.push({name,args});
  if(name==='ListMailboxTodos'){
    if(window.fixtureTodoFailure)throw Error('隔离测试：待办读取失败');
    return {items:window.fixtureTodos.map(item=>({...item}))};
  }
  if(name==='CreateMailboxTodo'){
    const item={...args,todo_id:'todo_created',completed:false,position:1,indent:0};
    window.fixtureTodos.push(item);return {item:{...item}};
  }
  if(name==='UpdateMailboxTodo'){
    const item=window.fixtureTodos.find(item=>item.todo_id===args.todo_id);
    Object.assign(item,args.changes);return {item:{...item}};
  }
  if(name==='DeleteMailboxTodo'){
    window.fixtureTodos=window.fixtureTodos.filter(item=>item.todo_id!==args.todo_id);return {};
  }
  throw Error('Unexpected ISA '+name);
}};
</script>${scripts.map(n => `<script src="/static/${n}.js"></script>`).join("")}</body></html>`;
const server = http.createServer((req, res) => {
  const source = sources.get(req.url);
  if (source) { res.writeHead(200, { "Content-Type": req.url.endsWith(".css") ? "text/css; charset=utf-8" : "text/javascript; charset=utf-8" }); res.end(source); }
  else { res.writeHead(200, { "Content-Type": "text/html" }); res.end(html); }
});
const rootSelector = "[data-portal-agent-inbox]";
const mailbox = "[data-agent-conversation-trigger]";
const todo = "[data-agent-todo-trigger]";
const assistant = "[data-personal-assistant-trigger]";
const preference = page => page.evaluate(() => PortalState.read("agent-inbox", "device", {}));
const box = (page, selector = rootSelector) => page.locator(selector).boundingBox();
const saved = [], errors = [];
let browser;
async function settled(page, side) {
  await page.waitForFunction(({ rootSelector, side }) => {
    const root = document.querySelector(rootSelector);
    return root?.dataset.dockSide === side && root.dataset.dragging !== "true";
  }, { rootSelector, side });
  const b = await box(page);
  assert.ok(Math.abs(b.x - (side === "left" ? 0 : page.viewportSize().width - b.width)) < 1, JSON.stringify(b));
  assert.ok(b.y >= 64 && b.y + b.height <= page.viewportSize().height - 7, JSON.stringify(b));
}
async function drag(page, selector, x, y) {
  // Leave first so a previous hover does not hide the touch trigger.
  await page.mouse.move(page.viewportSize().width / 2, 20);
  const b = await box(page, selector);
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
  await page.mouse.down();
  await page.mouse.move(x, y, { steps: 12 });
  await page.mouse.up();
  assert.equal(await page.locator(rootSelector).getAttribute("data-expanded"), "false", "drag does not open either panel");
}
async function screenshot(page, name) {
  if (!process.env.DOCKING_ARTIFACT_DIR) return;
  fs.mkdirSync(process.env.DOCKING_ARTIFACT_DIR, { recursive: true });
  const target = path.join(process.env.DOCKING_ARTIFACT_DIR, name + ".png");
  await page.screenshot({ path: target }); saved.push(target);
}
async function visiblePanel(page, selector) {
  await page.waitForFunction(selector => {
    const panel = document.querySelector(selector);
    return panel && !panel.inert && getComputedStyle(panel).visibility === "visible";
  }, selector);
  await page.waitForTimeout(180);
  const b = await box(page, selector), v = page.viewportSize();
  assert.ok(b.x >= 0 && b.x + b.width <= v.width && b.y >= 0 && b.y + b.height <= v.height, JSON.stringify(b));
}
async function touch(page, cdp, selector, destination) {
  if (!destination) { await page.locator(selector).tap(); return; }
  const b = await box(page, selector);
  const start = { x: b.x + b.width / 2, y: b.y + b.height / 2 };
  await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [start] });
  // Deliver a continuous gesture over frames, not an instantaneous viewport jump.
  for (let step = 1; step <= 12; step++) {
    await page.waitForTimeout(20);
    await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{
      x: start.x + (destination.x - start.x) * step / 12, y: start.y + (destination.y - start.y) * step / 12,
    }] });
  }
  await page.waitForTimeout(80);
  await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
}
(async () => {
  try {
    await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ viewport: { width: 1100, height: 760 }, deviceScaleFactor: 2 });
    context.on("page", page => page.on("pageerror", error => { errors.push(error.message); console.error(error.message); }));
    let page = await context.newPage();
    page.setDefaultTimeout(8000);
    const origin = `http://127.0.0.1:${server.address().port}`;
    await page.goto(origin);
    await page.waitForSelector(assistant);
    // A legacy record contains no side, and unrelated fields must survive.
    await page.evaluate(() => {
      PortalAgentInbox.destroy();
      PortalState.replace("agent-inbox", "device", { verticalRatio: 0.4, section: "todo", retained: "legacy" });
      PortalAgentInbox.mount();
    });
    await settled(page, "right");
    assert.equal((await box(page)).height, 132, "three desktop entries share the dock");
    assert.equal(await page.locator('[data-inbox-trigger]:visible').count(), 3);
    await drag(page, mailbox, 16, 300);
    await settled(page, "left");
    assert.equal((await preference(page)).retained, "legacy");
    assert.equal((await preference(page)).section, "todo");
    await screenshot(page, "left-docked");
    await page.waitForTimeout(420);
    await page.locator(mailbox).hover();
    await visiblePanel(page, '[data-inbox-panel="mailbox"]');
    assert.equal(await page.locator('[data-inbox-panel="mailbox"] [data-mailbox-todos]').count(), 0);
    assert.equal(await page.locator('[data-agent-inbox-filter]').count(), 0);
    await screenshot(page, "left-mailbox-open");
    await page.locator(todo).hover();
    await visiblePanel(page, '[data-inbox-panel="todo"]');
    assert.equal(await page.locator(mailbox).getAttribute("aria-expanded"), "false");
    await page.waitForSelector('[data-mailbox-todo-id="todo_fixture"]');
    const mailboxCount = await page.locator(`${mailbox} [data-agent-conversation-count]`).textContent();
    const todoCount = `${todo} [data-agent-todo-count]`;
    assert.equal(await page.locator(todoCount).textContent(), "1");
    await page.locator('[data-mailbox-todo-text]').fill('验证独立 To Do 入口');
    await page.locator('[data-mailbox-todo-save]').click();
    const created = page.locator('[data-mailbox-todo-id="todo_created"]');
    await created.waitFor();
    await created.locator('[data-mailbox-todo-content]').click();
    await created.locator('[data-mailbox-todo-editor]').fill('独立入口已验证');
    // Switching entries saves the edit through the original shared component.
    await page.locator(mailbox).hover();
    await page.waitForFunction(() => fixtureTodos.find(item => item.todo_id === 'todo_created')?.text === '独立入口已验证');
    await page.locator(todo).hover();
    await visiblePanel(page, '[data-inbox-panel="todo"]');
    await created.locator('[data-mailbox-todo-check]').check();
    await page.waitForFunction(() => fixtureTodos.find(item => item.todo_id === 'todo_created')?.completed);
    assert.equal(await page.locator(todoCount).textContent(), "1");
    assert.equal(await page.locator(`${mailbox} [data-agent-conversation-count]`).textContent(), mailboxCount, "todo does not change Mailbox unread count");
    await screenshot(page, "left-todo-open");
    await created.locator('[data-mailbox-todo-action="delete"]').click();
    await created.waitFor({ state: 'detached' });
    await page.evaluate(() => { fixtureTodoFailure = true; });
    await page.locator('[data-agent-todo-refresh]').click();
    await page.waitForSelector('[data-mailbox-todo-error]:visible');
    await page.locator(mailbox).hover();
    await visiblePanel(page, '[data-inbox-panel="mailbox"]');
    assert.equal(await page.locator('[data-mailbox-todo-error]').isVisible(), false);
    await page.evaluate(() => { fixtureTodoFailure = false; });
    await page.locator(todo).hover();
    await visiblePanel(page, '[data-inbox-panel="todo"]');
    await page.locator('[data-agent-todo-refresh]').click();
    await page.waitForSelector('[data-mailbox-todo-error]', { state: 'hidden' });
    assert.ok((await page.evaluate(() => fixtureCalls.filter(call => ['CreateMailboxTodo', 'UpdateMailboxTodo'].includes(call.name))))
      .every(call => call.args.user_id === 'docking-fixture'));
    const padding = await page.locator(".wallWorkspaceView").evaluate(el => [getComputedStyle(el).paddingLeft, getComputedStyle(el).paddingRight]);
    assert.deepEqual(padding, ["50px", "0px"]);
    await page.locator(assistant).hover();
    await visiblePanel(page, ".portalPersonalAssistantPanel");
    assert.equal(await page.locator(mailbox).getAttribute("aria-expanded"), "false");
    await drag(page, assistant, 1095, 320);
    await settled(page, "right");
    await screenshot(page, "right-docked");
    const beforeCancel = await preference(page);
    const b = await box(page, mailbox);
    await page.mouse.move(b.x + 15, b.y + 15); await page.mouse.down();
    await page.mouse.move(200, 500);
    await page.locator(mailbox).dispatchEvent("pointercancel", { pointerId: 1, pointerType: "mouse" });
    await page.mouse.up();
    await settled(page, "right");
    assert.deepEqual(await preference(page), beforeCancel, "cancelled drag does not persist partial coordinates");
    await drag(page, todo, 10, 300);
    const leftPreference = await preference(page);
    await page.reload(); await settled(page, "left");
    assert.deepEqual(await preference(page), leftPreference, "same storage contract restores after reload");
    await page.setViewportSize({ width: 780, height: 400 }); await settled(page, "left");
    await drag(page, mailbox, -80, 900);
    await settled(page, "left");
    await page.evaluate(() => { const obstacle = document.createElement("div"); obstacle.className = "composer"; document.body.append(obstacle); });
    await page.waitForFunction(() => document.querySelector('[data-portal-agent-inbox]').getBoundingClientRect().bottom <= document.querySelector('.composer').getBoundingClientRect().top - 7);
    await page.locator(".composer").evaluate(el => el.remove());
    const phone = await browser.newContext({ viewport: { width: 320, height: 568 }, isMobile: true, hasTouch: true,
      storageState: await context.storageState() });
    phone.on("page", page => page.on("pageerror", error => errors.push(error.message)));
    page = await phone.newPage(); page.setDefaultTimeout(8000);
    await page.goto(origin); await settled(page, "left");
    await page.waitForTimeout(420);
    const cdp = await phone.newCDPSession(page);
    await touch(page, cdp, mailbox);
    await visiblePanel(page, '[data-inbox-panel="mailbox"]');
    await screenshot(page, "narrow-mailbox-open");
    await touch(page, cdp, "[data-agent-inbox-mobile-close]");
    assert.equal(await page.locator(rootSelector).getAttribute("data-expanded"), "false");
    assert.equal((await box(page)).height, 120, "three narrow-screen entries");
    await touch(page, cdp, todo);
    await visiblePanel(page, '[data-inbox-panel="todo"]');
    await page.locator('[data-mailbox-todo-text]').fill('触屏待办');
    await page.locator('[data-mailbox-todo-save]').tap();
    await page.waitForSelector('[data-mailbox-todo-id="todo_created"]');
    await screenshot(page, "narrow-todo-open");
    await touch(page, cdp, '[data-agent-inbox-mobile-close]');
    assert.equal(await page.locator(rootSelector).getAttribute('data-expanded'), 'false');
    await touch(page, cdp, assistant, { x: 310, y: 240 });
    await settled(page, "right");
    assert.equal(await page.locator(rootSelector).getAttribute("data-expanded"), "false");
    await page.waitForTimeout(420);
    await touch(page, cdp, assistant);
    await visiblePanel(page, ".portalPersonalAssistantPanel");
    await touch(page, cdp, "[data-agent-inbox-mobile-close]");
    await page.evaluate(() => { window.fixtureTarget = null; dispatchEvent(new Event("focus")); });
    await page.waitForFunction(() => document.querySelector('[data-portal-agent-inbox]').dataset.hasAssistant === "false");
    assert.equal((await box(page)).height, 80, "Mailbox and To Do remain without an assistant");
    await touch(page, cdp, todo, { x: 0, y: 200 }); await settled(page, "left");
    await page.evaluate(() => { PortalAgentInbox.destroy(); PortalAgentInbox.mount(); });
    await settled(page, "left");
    assert.equal(await page.locator(rootSelector).count(), 1);
    assert.deepEqual(errors, [], "no browser errors");
    console.log(JSON.stringify({ status: "passed", browser: "Chromium", isolated: true, formal_tau: false,
      checks: ["three triggers drag as a group to either edge", "legacy preferences and reload", "cancel rollback", "drag/click separation", "inward mutually exclusive panels", "independent To Do create/edit/complete/delete and unread isolation", "Todo failure and recovery", "responsive clamp and obstacle avoidance", "touch todo creation and mobile close", "optional assistant and remount", "Wall clearance", "2x pixel density"], screenshots: saved }));
  } finally {
    await browser?.close();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
