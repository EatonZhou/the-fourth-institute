"use strict";
// Isolated multi-user index/ISA inputs; production Store, launcher and alert surface.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { chromium } = require("../../../management_portal/test_framework/tau_runtime/node_modules/playwright");
const code = path.resolve(__dirname, "../../..");
const scripts = ["portal_state", "portal_text", "portal_ui_primitives", "portal_refresh_coordinator",
  "resident_protocol", "portal_conversation_store", "portal_mailbox_todos", "portal_agent_inbox", "portal_mailbox_alerts"];
const styles = ["portal_components", "portal_components_agent_inbox", "portal_design_system"];
const sources = new Map([...scripts.map(n => n + ".js"), ...styles.map(n => n + ".css")]
  .map(name => ["/static/" + name, fs.readFileSync(path.join(code, "management_portal/static", name))]));
const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
${styles.map(n => `<link rel="stylesheet" href="/static/${n}.css">`).join("")}
<style>:root{--text:#172030;--muted:#64748b;--panel:#fff;--border:#cbd5e1;--bg:#f5f7f9;--accent:#0b7972}*{box-sizing:border-box}body{margin:0;background:var(--bg);font:15px sans-serif}header{padding:22px;border-bottom:1px solid var(--border)}main{padding:36px}h1{font-size:24px}</style></head>
<body><header>TFI Portal · 隔离验证</header><main><h1>当前页面</h1><p>无需打开 Mailbox，即可看到聊天室 @ 提醒。</p><input aria-label="原页面输入框" placeholder="继续当前工作"></main>
<script>
window.actor='alice'; window.items=[];window.calls=[];window.opened=[];window.deny=false;
window.PortalIdentity={username:()=>actor,load:async()=>({username:actor}),userId:async()=>actor};
window.PortalEvents={isConnected:()=>false,connect(){}};
window.PortalNav={personalAssistantTarget:async()=>null};
window.PortalAPI={get:async()=>({entries:structuredClone(items),ready:true,content_signature:JSON.stringify(items),read_revision:Date.now()})};
window.PortalInstructionClient={execute:async(name,args)=>{calls.push(name);if(name==='ListMailboxTodos')return {items:[]};if(name==='GetChatroom'){if(window.blockOpen)await new Promise(r=>window.releaseOpen=r);if(deny)throw Error('forbidden');const e=items.find(e=>e.chatroom_id===args.chatroom_id);return {chatroom:e&&{...e}};}throw Error('Unexpected write '+name);}};
window.PortalUI={toast:()=>{}};
window.addEventListener('portal:agent-conversation-open',event=>{window.opened.push(event.detail.entry);event.detail.handled=true});
</script>${scripts.map(n => `<script src="/static/${n}.js"></script>`).join("")}
<script>PortalMailboxAlerts.mount();</script></body></html>`;
const server = http.createServer((req, res) => {
  const source = sources.get(req.url);
  res.writeHead(200, { "Content-Type": source ? (req.url.endsWith(".css") ? "text/css" : "text/javascript") : "text/html" });
  res.end(source || html);
});
const room = (id, mentions, extra = {}) => ({key:`chatroom:${id}`,chatroom_id:id,kind:"chatroom",surface:"lineage",
  title:"AgentLink · 合成聊天室",unread:true,unread_count:3,read_revision:1,updated_at:"2026-11-11T10:00:00Z",
  unread_mentions:mentions.map((message_id,i)=>({message_id,ordinal:i+1})),...extra});
const id="chatroom_111111111111", alert=".portalMailboxAlert";
let browser;
(async()=>{
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
  const origin=`http://127.0.0.1:${server.address().port}`;
  browser=await chromium.launch({headless:true});
  const context=await browser.newContext({viewport:{width:1280,height:800}});
  const page=await context.newPage(); const errors=[];
  page.on("pageerror",e=>errors.push(e.message));
  await page.goto(origin);
  const set=async items=>page.evaluate(async items=>{window.items=items;await PortalConversationStore.refresh({force:true});},items);
  await page.waitForFunction(()=>PortalConversationStore.snapshot().loaded);
  assert.equal(await page.locator(alert).isVisible(),false);
  await set([room(id,[],{muted:true})]);
  assert.equal(await page.locator(alert).isVisible(),false,"ordinary unread is not a mention alert");
  await page.getByRole("textbox").focus();
  await set([room(id,["msg_one"],{muted:true})]);
  assert.equal(await page.locator(alert).isVisible(),true,"direct mention bypasses ordinary mute");
  assert.equal(await page.locator("[data-portal-agent-inbox]").getAttribute("data-expanded"),"false");
  assert.equal(await page.getByRole("textbox").evaluate(e=>e===document.activeElement),true,"does not steal focus");
  const desktopBounds=await page.locator(alert).boundingBox();
  const headerBounds=await page.locator("body > header").boundingBox();
  assert.ok(desktopBounds.x<80 && desktopBounds.y<120,"mention alert appears at the upper left");
  assert.ok(desktopBounds.y>=headerBounds.y+headerBounds.height+8,"site navigation stays unobstructed");
  assert.ok(desktopBounds.height<=56,"alert retains its slim glass surface");
  const dir=process.env.MAILBOX_ALERT_ARTIFACT_DIR;
  if(dir){fs.mkdirSync(dir,{recursive:true});await page.screenshot({path:path.join(dir,"mention-desktop.png")});}
  await page.locator("[data-mailbox-alert-dismiss]").click();
  await set([room(id,["msg_one"],{muted:true})]);
  assert.equal(await page.locator(alert).isVisible(),false,"replayed snapshot does not reopen dismissed alert");
  await page.reload(); await page.waitForFunction(()=>PortalConversationStore.snapshot().loaded);
  await set([room(id,["msg_one"],{muted:true})]);
  assert.equal(await page.locator(alert).isVisible(),false,"dismissal survives reload");
  await set([room(id,["msg_one","msg_two"],{muted:true,read_revision:2})]);
  assert.equal(await page.locator(alert).isVisible(),true,"new mention reappears");
  await page.locator("[data-mailbox-alert-open]").click();
  await page.waitForFunction(()=>opened.length===1);
  assert.equal(await page.evaluate(()=>opened[0].url),`/lineage?chatroom=${id}`);
  assert.equal(await page.evaluate(()=>opened[0].surface),"lineage");
  assert.equal(await page.evaluate(()=>PortalConversationStore.entry('chatroom:'+window.items[0].chatroom_id).unread),true);
  await set([room(id,["msg_three"],{read_revision:3})]);
  await set([room(id,[],{unread:false,unread_count:0,read_revision:4})]);
  assert.equal(await page.locator(alert).isVisible(),false,"real read receipt removes alert");
  await page.evaluate(()=>{actor='bob';items=[];window.dispatchEvent(new Event('portal:identity-changed'));});
  assert.equal(await page.locator(alert).isVisible(),false,"account switch clears prior user content");
  await set([room(id,["msg_one"],{read_revision:5})]);
  assert.equal(await page.locator(alert).isVisible(),true,"dismissals are per user");
  await page.evaluate(()=>{deny=true;items=[];});
  await page.locator("[data-mailbox-alert-open]").click();
  await page.waitForFunction(()=>document.querySelector('.portalMailboxAlert').hidden);
  assert.equal(await page.evaluate(()=>opened.length),1,"revoked membership cannot navigate");
  await page.evaluate(()=>{deny=false;});
  await set([room(id,["msg_mobile"],{read_revision:6})]);
  await page.setViewportSize({width:390,height:844});
  const bounds=await page.locator(alert).boundingBox();
  assert.ok(bounds.x>=0 && bounds.x+bounds.width<=390 && bounds.y+bounds.height<=844);
  assert.ok(bounds.y<120 && bounds.height<=56,"mobile alert also stays near the top and slim");
  assert.ok(bounds.y>=headerBounds.y+headerBounds.height+8,"mobile navigation stays unobstructed");
  await page.locator("[data-portal-agent-inbox]").evaluate(e=>e.dataset.dockSide="left");
  const leftDockBounds=await page.locator(alert).boundingBox();
  assert.ok(leftDockBounds.x>=50 && leftDockBounds.x+leftDockBounds.width<=390,"alert clears the saved left-side Mailbox launcher");
  if(dir)await page.screenshot({path:path.join(dir,"mention-mobile.png")});
  await page.evaluate(()=>{blockOpen=true;});
  await page.locator("[data-mailbox-alert-open]").click();
  await page.waitForFunction(()=>!!window.releaseOpen);
  await page.evaluate(()=>{actor='carol';items=[];window.dispatchEvent(new Event('portal:identity-changed'));window.releaseOpen();});
  await page.waitForFunction(()=>!document.querySelector('[data-mailbox-alert-open]').disabled);
  assert.equal(await page.evaluate(()=>opened.length),1,"late permission reply cannot navigate across users");
  assert.equal(await page.locator(alert).isVisible(),false);
  assert.ok((await page.evaluate(()=>calls)).every(name=>['ListMailboxTodos','GetChatroom'].includes(name)),"no read/write instructions from alerts");
  assert.deepEqual(errors,[]);
  console.log("PASS: isolated mentions, mute, dismissal/reload, new mentions, navigation, read receipts, user switching, permission denial, async identity race, desktop/mobile");
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{await browser?.close();server.close();});
