'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chatWorkspaceScripts } = require('./chat_workspace_modules.cjs');
const { portalWorkbenchScripts } = require('./portal_workbench_scripts.cjs');
const repo = path.resolve(__dirname, '../../..');
const { chromium } = require(path.join(repo, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const { routeScript } = JSON.parse(fs.readFileSync(0, 'utf8'));
const modules = ['portal_state.js', 'portal_page_state.js', 'portal_text.js', 'portal_i18n.js',
  'portal_ui_primitives.js', 'portal_ui.js', 'portal_overlay.js', 'portal_resource_chips.js',
  'portal_image_editor.js', 'portal_editor_open_service.js', 'portal_command_registry.js',
  'portal_workbench_context.js', ...portalWorkbenchScripts(), 'resident_protocol.js', ...chatWorkspaceScripts(),
  'portal_refresh_coordinator.js', 'portal_conversation_store.js', 'portal_mailbox_todos.js', 'portal_agent_inbox.js'];
const styles = ['portal_design_system.css', 'lineage_workbench.css', 'portal_components.css',
  'portal_components_markdown.css', 'portal_components_chat_frame.css', 'portal_components_messages.css',
  'portal_components_agent_inbox.css', 'portal_components_composer.css', 'portal_components_queue.css', 'portal_components_conversation.css'];

const fixture = `
window.actor='alice';window.calls=[];window.reads=[];window.opened=[];window.denied=false;window.blocked=false;
window.roomId='chatroom_111111111111';
window.items=Array.from({length:30},(_,i)=>({key:'chatroom:'+i,chatroom_id:roomId,kind:'chatroom',title:i===12?'产品讨论':'项目讨论 '+i,url:'/lineage?chatroom='+roomId,updated_at:'2026-11-12T06:00:00Z',unread:true}));
items.push({key:'resident:one',kind:'resident',title:'普通Agent',url:'/lineage?resident=resident_111111111111',resident_id:'resident_111111111111',updated_at:'2026-11-11T00:00:00Z'});
window.PortalIdentity={username:()=>actor,userId:async()=>actor,load:async()=>({username:actor})};
window.PortalResponsive={isMobile:()=>innerWidth<=640};
window.PortalEvents={isConnected:()=>false,connect(){}};
window.PortalResidentDiscoveryService={createClient:()=>({resolveNavigationTarget:async()=>({})})};
window.PortalNav={personalAssistantTarget:async()=>null,loadChatWorkspace:async()=>{if(window.blockAssets)await new Promise(r=>window.releaseAssets=r);}};
window.PortalAPI={get:async()=>({entries:structuredClone(items),ready:true,content_signature:JSON.stringify(items),unread_count:30})};
window.messages=Array.from({length:25},(_,i)=>({message_id:'msg_'+i,ordinal:i+1,author_id:i%2?'alice':'bob',role:'user',created_at:'2026-11-12T06:00:00Z',content_parts:[{type:'text',text:i%2?'好的，在这里接着讨论。':'这是合成群聊消息 '+(i+1)}]}));
window.fixtureRoom=()=>({chatroom_id:roomId,title:'产品讨论',members:['alice','bob'],permissions:{can_post:true},unread_mentions:[],unread_count:25,read_revision:1});
window.PortalInstructionClient={execute:async(name,args)=>{
 calls.push({name,args});
 if(name==='ListMailboxTodos')return {items:[]};
 if(name==='ListInputHistory')return {messages:[]};
 if(name==='GetChatroom'){if(denied)throw Error('没有访问这个聊天室的权限');return {chatroom:fixtureRoom()};}
 if(name==='ListChatroomMessages'){if(blocked)await new Promise(r=>window.releaseRoom=r);if(denied)throw Error('forbidden');return {messages:structuredClone(messages),next_cursor:''};}
 if(name==='MarkChatroomRead'){reads.push(args);return {chatroom:{...fixtureRoom(),unread_count:0}};}
 if(name==='CreateChatroomMessage'){const message={message_id:'msg_sent',ordinal:26,author_id:actor,role:'user',content_parts:args.content_parts};messages.push(message);return {message};}
 throw Error('Unexpected instruction '+name);
}};
window.addEventListener('portal:agent-conversation-open',e=>{opened.push(e.detail.entry);e.detail.handled=true});
`;
const server = http.createServer((req,res)=>{
 const url=new URL(req.url,'http://fixture');
 let name=url.pathname.replace(/^\/static\//,'').replace(/^\//,'');
 let body,type='text/javascript';
 if(!name){type='text/html';body=`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">${styles.map(n=>`<link rel="stylesheet" href="/static/${n}">`).join('')}<style>:root{--text:#172030;--muted:#64748b;--panel:#fff;--border:#cbd5e1;--bg:#f5f7f9;--accent:#0b7972}*{box-sizing:border-box}body{margin:0;background:var(--bg);font:15px sans-serif}.portalSiteHeader{height:56px;padding:18px;border-bottom:1px solid var(--border)}main{padding:40px}</style><header class="portalSiteHeader">TFI Portal</header><main><h1>工作台</h1><p>群聊可直接在 Mailbox 中打开。</p></main><script>${routeScript}\n${fixture}</script>${modules.map(n=>`<script src="/static/${n}"></script>`).join('')}</html>`;}
 else if([...modules,...styles,'portal_chatroom_view.js'].includes(name)){body=fs.readFileSync(path.join(repo,'management_portal/static',name));if(name.endsWith('.css'))type='text/css';}
 res.writeHead(body===undefined?404:200,{'Content-Type':type+'; charset=utf-8'});res.end(body||'not found');
});
let browser;
(async()=>{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1280,height:900}});const errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const origin=`http://127.0.0.1:${server.address().port}`;
 await page.route('**/*',route=>route.request().url().startsWith(origin+'/')?route.continue():route.abort());
 await page.goto(origin);await page.waitForFunction(()=>PortalConversationStore.snapshot().loaded);
 const trigger=page.locator('[data-agent-conversation-trigger]'),list=page.locator('[data-agent-conversation-list]');
 const back=page.locator('[data-mailbox-chatroom-back]'),chat=page.locator('[data-mailbox-chatroom-view]');
 const inbox=page.locator('.portalAgentInbox');
 // Mouse clicks do not pin the ordinary list; entering a room must preserve this mode.
 await trigger.click();await page.mouse.move(20,100);
 await page.waitForFunction(()=>document.querySelector('.portalAgentInbox').dataset.expanded==='false');
 await trigger.hover();
 await page.locator('[data-agent-conversation-key="chatroom:12"]').scrollIntoViewIfNeeded();
 const position=await list.evaluate(e=>e.scrollTop);
 const open=async(key='chatroom:12')=>{await trigger.hover();await page.locator(`[data-agent-conversation-key="${key}"]`).click();await back.waitFor({state:'visible'});};
 await open();await chat.locator('.messageList > [data-message-key]').first().waitFor();
 assert.equal(await page.evaluate(()=>opened.length),0,'Chatroom does not navigate the workbench');assert.equal(page.url(),origin+'/');
 // Moving between children is not leaving the panel. A pointer-focused draft is saved on leave.
 await back.hover();await chat.locator('.composerInput').hover();
 assert.equal(await back.isVisible(),true);
 await chat.locator('.composerInput').fill('移出后保留草稿');
 await page.locator('.portalMailboxChatroom > header').click({position:{x:250,y:20}});
 await page.mouse.move(20,100);
 await page.waitForFunction(()=>document.querySelector('.portalAgentInbox').dataset.expanded==='false');
 assert.equal(await chat.evaluate(e=>e.hidden),true);
 assert.equal(await chat.locator('[data-message-key]').count(),0);
 const readsAfterLeave=await page.evaluate(()=>reads.length);
 await page.waitForTimeout(750);
 assert.equal(await page.evaluate(()=>reads.length),readsAfterLeave,'hidden room cannot mark read later');
 await trigger.hover();assert.equal(await list.evaluate(e=>e.scrollTop),position);
 await open();await chat.locator('.composerInput').waitFor();
 assert.equal(await chat.locator('.composerInput').innerText(),'移出后保留草稿');
 await chat.locator('.composerInput').fill('');
 // Keyboard-opened rooms stay expanded without hover, then Escape closes them.
 await back.click();await trigger.hover();await page.locator('[data-agent-conversation-key="chatroom:12"]').press('Enter');
 await back.waitFor({state:'visible'});await page.mouse.move(20,100);
 assert.equal(await inbox.getAttribute('data-expanded'),'true');
 await back.press('Escape');assert.equal(await back.isVisible(),false);
 await trigger.hover();await open();
 await page.waitForFunction(()=>reads.length>0);
 const dir=process.env.MAILBOX_CHATROOM_ARTIFACT_DIR;
 if(dir){fs.mkdirSync(dir,{recursive:true});await page.screenshot({path:path.join(dir,'mailbox-chatroom-desktop.png')});}
 // Actual shared composer and send path, using only synthetic messages.
 const input=chat.locator('.composerInput').first();await input.fill('隔离测试发送');await input.press('Control+Enter');
 await page.waitForFunction(()=>calls.some(c=>c.name==='CreateChatroomMessage'));
 await back.click();assert.equal(await list.evaluate(e=>e.scrollTop),position);
 // Destruction saves the actual shared draft, and reopening restores it.
 await open();await chat.locator('.composerInput').fill('保留这条未发送草稿');await back.click();
 await open();await chat.locator('.composerInput').waitFor();
 assert.equal(await chat.locator('.composerInput').innerText(),'保留这条未发送草稿');
 await chat.locator('.composerInput').fill('');await back.click();
 // The host passes an explicit message link to the shared navigator.
 await page.evaluate(async()=>{items[12].url+='&message=msg_5';await PortalConversationStore.refresh({force:true});});
 await open();await chat.locator('.messageQuoteTarget[data-message-key="msg_5"]').waitFor();
 await page.locator('[data-mailbox-chatroom-close]').click();
 assert.equal(await back.isVisible(),false);await trigger.hover();
 const count=await page.evaluate(()=>reads.length);await page.waitForTimeout(750);assert.equal(await page.evaluate(()=>reads.length),count,'return cancels read timers');
 // Denied entry must not show cached content or acknowledge reading.
 await page.evaluate(()=>denied=true);await open();await chat.getByRole('button',{name:'重试'}).waitFor();
 assert.equal(await chat.locator('[data-message-key]').count(),0);assert.equal(await page.evaluate(()=>reads.length),count);await back.click();
 // Closing while initial data is pending must not mount/reveal or mark read late.
 await page.evaluate(()=>{denied=false;blocked=true;});await open();await page.waitForFunction(()=>!!window.releaseRoom);
 await back.click();await page.evaluate(()=>{blocked=false;releaseRoom();});await page.waitForTimeout(750);
 assert.equal(await page.evaluate(()=>reads.length),count);assert.equal(await chat.locator('[data-message-key]').count(),0);
 // Pointer leave while data is pending also disposes the room; late completion cannot reopen it.
 await page.evaluate(()=>{blocked=true;delete window.releaseRoom;});await open();await page.waitForFunction(()=>!!window.releaseRoom);
 await page.mouse.move(20,100);
 await page.waitForFunction(()=>document.querySelector('.portalAgentInbox').dataset.expanded==='false');
 await page.evaluate(()=>{blocked=false;releaseRoom();});await page.waitForTimeout(750);
 assert.equal(await page.evaluate(()=>reads.length),count);assert.equal(await chat.locator('[data-message-key]').count(),0);
 await trigger.hover();
 // Identity change during lazy loading invalidates the attempt.
 await page.evaluate(()=>{blockAssets=true;});await open();await page.waitForFunction(()=>!!window.releaseAssets);
 await page.evaluate(()=>{actor='carol';items=[];window.dispatchEvent(new Event('portal:identity-changed'));blockAssets=false;releaseAssets();});
 await page.waitForTimeout(100);assert.equal(await chat.locator('[data-message-key]').count(),0);assert.equal(await back.isVisible(),false);
 // A fresh mobile page exercises full-height panel, send area and left-top back.
 const mobile=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});mobile.on('pageerror',e=>errors.push(e.message));
 await mobile.route('**/*',route=>route.request().url().startsWith(origin+'/')?route.continue():route.abort());
 await mobile.goto(origin);await mobile.waitForFunction(()=>PortalConversationStore.snapshot().loaded);
 await mobile.locator('[data-agent-conversation-trigger]').tap();await mobile.locator('[data-agent-conversation-key="chatroom:0"]').tap();
 await mobile.locator('[data-mailbox-chatroom-view] .messageList > [data-message-key]').first().waitFor();
 const bounds=await mobile.locator('.portalMailboxChatroom').boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=390&&bounds.y+bounds.height<=844);
 const bb=await mobile.locator('[data-mailbox-chatroom-back]').boundingBox();assert.ok(bb.x<40&&bb.y<40,'back is top left');
 if(dir)await mobile.screenshot({path:path.join(dir,'mailbox-chatroom-mobile.png')});
 await mobile.locator('[data-mailbox-chatroom-back]').tap();assert.equal(await mobile.locator('[data-agent-conversation-list]').isVisible(),true);
 // Other conversation kinds still use their existing navigation route.
 await mobile.locator('[data-agent-conversation-key="resident:one"]').scrollIntoViewIfNeeded();await mobile.locator('[data-agent-conversation-key="resident:one"]').tap();await mobile.waitForFunction(()=>opened.length===1);
 assert.equal(await mobile.evaluate(()=>opened[0].kind),'resident');
 // Wide touch devices retain the explicit toggle, including outside-click dismissal.
 const touch=await browser.newPage({viewport:{width:1280,height:900},hasTouch:true});touch.on('pageerror',e=>errors.push(e.message));
 await touch.route('**/*',route=>route.request().url().startsWith(origin+'/')?route.continue():route.abort());
 await touch.goto(origin);await touch.waitForFunction(()=>PortalConversationStore.snapshot().loaded);
 await touch.locator('[data-agent-conversation-trigger]').tap();await touch.locator('[data-agent-conversation-key="chatroom:0"]').tap();
 await touch.locator('[data-mailbox-chatroom-view] .composerInput').waitFor();
 await touch.locator('.portalAgentInbox').dispatchEvent('pointerleave',{pointerType:'touch'});
 assert.equal(await touch.locator('[data-mailbox-chatroom-back]').isVisible(),true);
 await touch.locator('main h1').tap();
 await touch.waitForFunction(()=>document.querySelector('.portalAgentInbox').dataset.expanded==='false');
 assert.equal(await touch.locator('[data-mailbox-chatroom-view]').evaluate(e=>e.hidden),true);
 assert.deepEqual(errors,[]);console.log('PASS: inline Chatroom, send, back/scroll, denied access, late load, identity race, mobile layout, other conversation navigation');
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{await browser?.close();server.close()});
