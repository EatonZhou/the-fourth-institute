const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
// The Menu's scripts in load order; each function comes from the part that declares it.
const source = require('./resident_menu_scripts.cjs').residentMenuSource();
function fn(name, text = source) {
  const start = text.search(new RegExp('    (?:async )?function ' + name + '\\('));
  assert(start >= 0);
  const end = text.indexOf('\n    }', start);
  return text.slice(start, end + 6);
}
function harness(flag) {
  const calls=[], notices=[];
  let nodes={};
  const root = {contains: x => Object.values(nodes).includes(x),querySelector: s => nodes[s],querySelectorAll: () => [],
    set innerHTML(html) {this.html=html;nodes=Object.fromEntries(['[data-family-auto-migrate]','[data-family-settings-error]','[data-family-members]','[data-family-add]'].map(k=>[k,{disabled:false,hidden:true,focus(){},addEventListener(){}}]));}};
  const block={id:'fbi_one',family:{facade:'resident_facade',...(flag === undefined ? {} : {auto_migrate:flag})}};
  const ctx=vm.createContext({nodeMenuContext:{residentId:'resident_facade',lineageId:'lineage_one',family:block},lineage:{function_blocks:[block]},familySettingsView:null,familyPriorityView:null,mountMigrationPriority:()=>({destroy(){}}),
    $id:()=>root,disposeFamilySettings(){},openFamilyMemberPicker(){},changeFamily(){},changed:async x=>notices.push(x),
    window:{PortalI18n:{t:x=>x,html:(p,...v)=>String.raw({raw:p},...v)},PortalChatWorkspace:{mountFamilyStatus:()=>({})},PortalResidentProjection:{invalidate(){}},
      PortalInstructionClient:{execute:async(name,args,options)=>{calls.push({name,args,options});if(name==='GetLineage')return{lineage:{revision:17,function_blocks:[block]}};return{function_block:{...block,family:{...block.family,auto_migrate:args.auto_migrate}}};}}}});
  // The parts read the Menu's shared state from menuState; the context stands for it.
  ctx.menuState=ctx;
  vm.runInContext(fn('renderFamilySettings')+'\n'+fn('saveFamilyAutoMigrate'),ctx);
  ctx.renderFamilySettings(ctx.nodeMenuContext);
  return {ctx,root,calls,notices};
}
test('facade Settings exposes default-on switch before members and saves against a fresh revision',async()=>{
  const h=harness();assert.match(h.root.html,/aria-checked="true"/);
  assert.match(h.root.html,/Family 默认开关仅在成员未显式设置时生效/);
  assert(h.root.html.indexOf('data-family-auto-migrate')<h.root.html.indexOf('data-family-members'));
  await h.ctx.saveFamilyAutoMigrate(h.ctx.nodeMenuContext);
  assert.equal(h.calls[1].name,'UpdateFamily');assert.equal(h.calls[1].args.auto_migrate,false);assert.equal(h.calls[1].options.expectedVersion,17);
  assert.match(h.root.html,/aria-checked="false"/);assert.equal(h.notices.length,1);
});

test('both Family menu entry points explain the same inheritance rule',()=>{
  const path=require('node:path');
  const note='Family 默认开关仅在成员未显式设置时生效；成员显式开启时按站点默认排序切换。';
  for(const name of ['portal_resident_menu_family.js','hatg_function_block_menu.js']){
    const text=fs.readFileSync(path.resolve(__dirname,'../../../management_portal/static',name),'utf8');
    assert(text.includes(note),name);
    assert(!text.includes('Family 默认策略；成员明确设置的开关优先。未自定义排序时'),name);
  }
});

test('Resident effective switch shows its raw policy source and keeps boolean save semantics',async()=>{
  const {JSDOM}=require('jsdom'),path=require('node:path');
  const text=fs.readFileSync(path.resolve(__dirname,'../../../management_portal/static/portal_resident_menu_runtime.js'),'utf8');
  const start=text.indexOf('onState: view => '),end=text.indexOf('\n          onChanged:',start);
  assert(start>=0&&end>start);
  const callback=text.slice(start+'onState: '.length,end).trim().replace(/,$/,'');
  const dom=new JSDOM('<div id="runtime"></div>',{runScripts:'outside-only'}),w=dom.window,changes=[];
  const context={node:{},runtimeController:{saveSettings:async value=>changes.push(JSON.parse(JSON.stringify(value))),load:async()=>{}}};
  Object.assign(w,{context,menuState:{nodeMenuContext:context},$id:()=>w.document.getElementById('runtime'),esc:String,nodeMenuNotice:()=>{}});
  w.PortalI18n={t:String,html:(parts,...values)=>parts.reduce((s,p,i)=>s+p+(values[i]??''),'')};
  w.PortalChatWorkspace={residentRuntimeSettingRows:()=>[]};
  w.eval('window.paint=('+callback+');');
  for(const row of [
    {raw:true,effective:true,role:'member',label:/Resident 显式开启/},
    {raw:false,effective:false,role:'member',label:/Resident 显式关闭/},
    {effective:true,role:'router',label:/继承 Family 开关/},
    {effective:false,role:'member',label:/继承 Family 开关/},
    {effective:true,label:/沿用既有账号选择策略/},
  ]){
    const settings=Object.hasOwn(row,'raw')?{auto_switch_on_quota:row.raw}:{};
    w.paint({settings,detail:{resident:{family:{role:row.role}}},quotaSwitch:{auto_switch_on_quota:row.effective},canEdit:true});
    const root=w.document.getElementById('runtime'),toggle=root.querySelector('[data-menu-quota-switch]');
    assert.match(root.querySelector('[data-menu-quota-source]').textContent,row.label);
    assert.equal(toggle.getAttribute('aria-checked'),String(row.effective));
    const count=changes.length;toggle.click();await new Promise(resolve=>setImmediate(resolve));
    assert.equal(changes.length,count+1);
    assert.deepEqual(changes.at(-1),{auto_switch_on_quota:!row.effective});
  }
  dom.window.close();
});
test('rejected save retains displayed setting and exposes an error without retry',async()=>{
  const h=harness(false);h.ctx.window.PortalInstructionClient.execute=async()=>{throw Error('revision conflict')};
  await h.ctx.saveFamilyAutoMigrate(h.ctx.nodeMenuContext);
  assert.match(h.root.html,/aria-checked="false"/);assert.equal(h.root.querySelector('[data-family-settings-error]').textContent,'revision conflict');assert.equal(h.root.querySelector('[data-family-auto-migrate]').disabled,false);
});
test('duplicate click is ignored and switching menus during read cannot mutate another family',async()=>{
  const h=harness();let finish;h.ctx.window.PortalInstructionClient.execute=async()=>{h.calls.push({name:'GetLineage'});return new Promise(r=>finish=r)};
  const context=h.ctx.nodeMenuContext;const running=h.ctx.saveFamilyAutoMigrate(context);
  await h.ctx.saveFamilyAutoMigrate(context);assert.equal(h.calls.length,1);
  h.ctx.nodeMenuContext={residentId:'other'};finish({lineage:{revision:17,function_blocks:[context.family]}});await running;assert.equal(h.calls.length,1);
});

test('saved Family policy refreshes the canvas snapshot used to reopen the menu',async()=>{
  const h=harness();
  const state={lineage:{lineage_id:'lineage_one',function_blocks:[h.ctx.nodeMenuContext.family]}};
  Object.assign(h.ctx,{state,removeSelectedNode(){},renderGraph(){},revealUnfamilizedMember(){},
    commitLineage:value=>{state.lineage=value},
    societyService:{loadLineage:async id=>{assert.equal(id,'lineage_one');return h.ctx.lineage;}}});
  const dialogs=fs.readFileSync(require('node:path').join(__dirname,'../../../management_portal/static/hatg_resident_dialogs.js'),'utf8');
  vm.runInContext(fn('residentMenuHandlers',dialogs),h.ctx);
  h.ctx.changed=h.ctx.residentMenuHandlers().onChanged;
  await h.ctx.saveFamilyAutoMigrate(h.ctx.nodeMenuContext);
  // Reopening receives the canvas snapshot, not the previous dialog-local copy.
  h.ctx.lineage=state.lineage;
  h.ctx.nodeMenuContext.family=state.lineage.function_blocks[0];
  h.ctx.renderFamilySettings(h.ctx.nodeMenuContext);
  assert.match(h.root.html,/aria-checked="false"/);
});

test('Family editor distinguishes site inheritance and draft order, and explicitly allows other kinds',async()=>{
  const {JSDOM}=require('jsdom');
  const menu=fs.readFileSync(require('node:path').resolve(__dirname,'../../../management_portal/static/portal_resident_menu.js'),'utf8');
  const start=menu.indexOf('  function mountMigrationPriority(');
  const body=menu.slice(start,menu.indexOf('\n  }\n',start)+4);
  const dom=new JSDOM('<div id="root"></div>',{runScripts:'outside-only'});
  const w=dom.window,calls=[],kinds=[
    {id:'codex:api_profile:openai',label:'Codex · API（openai）',default_priority:201},
    {id:'codex:subscription_login',label:'Codex · 订阅',default_priority:1},
    {id:'claude:subscription_login',label:'Claude Code · 订阅',default_priority:0},
    {id:'kimi:service',label:'Kimi Code · 服务账号',default_priority:102}];
  let saved=[];
  w.PortalUIPrimitives={escapeHtml:value=>String(value).replace(/[&<>"]/g,c=>`&#${c.charCodeAt(0)};`)};
  w.PortalI18n={t:value=>value};
  w.PortalInstructionClient={execute:async(name,args,options)=>{calls.push({name,args,options});
    if(name==='ListProviderAccounts')return{accounts:kinds.map(migration_type=>({migration_type}))};
    if(name==='GetLineage')return{lineage:{revision:3,function_blocks:[{id:'family',family:{auto_migrate_priority:saved}}]}};
    saved=[...args.auto_migrate_priority];return{};}};
  w.eval(`window.mount=(()=>{const esc=value=>window.PortalUIPrimitives.escapeHtml(value);${body}\nreturn mountMigrationPriority;})()`);
  const root=w.document.getElementById('root');
  w.mount(root,{lineageId:'lineage_one',familyId:'family'});
  const details=root.querySelector('details');details.open=true;details.dispatchEvent(new w.Event('toggle'));
  for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve));
  const listed=()=>[...root.querySelectorAll('ol li span')].map(node=>node.textContent);
  assert.deepEqual(listed(),['Claude Code · 订阅','Codex · 订阅']);
  assert.match(root.textContent,/当前继承站点默认帐号选择排序/);
  assert.match(root.textContent,/不代表站点当前生效顺序/);
  assert.match(root.textContent,/成员显式开启额度自动切换时使用站点默认排序/);
  assert.match(root.textContent,/跨种类使用 API 或服务账号须在此明确允许/);
  assert.match(root.textContent,/排序仅表示候选优先级，不保证切换成功/);
  assert.match(root.textContent,/目标仍需有可用额度和兼容模型/);
  assert.match(root.textContent,/自动切换优先完整保留上下文/);
  assert.match(root.textContent,/无法直接保留时自动整理交接/);
  assert.match(root.textContent,/待目标就绪后切换并继续原输入，旧会话历史保留/);
  assert.doesNotMatch(root.textContent,/可能需手动选择交接方式/);
  assert.doesNotMatch(root.textContent,/自动切换只在订阅账号之间进行/);
  assert.equal(calls.filter(call=>call.name==='UpdateFamily').length,0,'opening a draft must not persist or replace the inherited site policy');
  assert.deepEqual([...root.querySelectorAll('[data-priority-allow]')].map(node=>node.dataset.priorityAllow),['kimi:service','codex:api_profile:openai']);
  root.querySelector('[data-priority-save]').click();
  for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(saved,['claude:subscription_login','codex:subscription_login'],'saving the preview explicitly customizes the Family order');
  assert.match(root.textContent,/已自定义 Family 排序；未排序的订阅种类按默认顺序追加/);
  details.open=true;
  root.querySelector('[data-priority-default]').click();
  assert.match(root.textContent,/保存后恢复站点默认帐号选择排序/);
  assert.match(root.textContent,/不代表站点当前生效顺序/);
  assert.deepEqual(saved,['claude:subscription_login','codex:subscription_login'],'restore-default is still an unsaved draft');
  root.querySelector('[data-priority-save]').click();
  for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(saved,[],'restore default follows the site order instead of persisting the preview');
  assert.match(root.textContent,/当前继承站点默认帐号选择排序/);
  details.open=true;
  root.querySelector('[data-priority-allow="codex:api_profile:openai"]').click();
  assert.deepEqual(listed(),['Claude Code · 订阅','Codex · 订阅','Codex · API（openai）']);
  root.querySelector('[data-priority-save]').click();
  for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(saved,['claude:subscription_login','codex:subscription_login','codex:api_profile:openai']);
  details.open=true;
  root.querySelector('[data-priority-revoke="codex:api_profile:openai"]').click();
  assert.deepEqual(listed(),['Claude Code · 订阅','Codex · 订阅']);
  assert(calls.filter(call=>call.name==='UpdateFamily').every(call=>call.options?.expectedVersion===3),'priority writes keep the fresh graph CAS');
});
