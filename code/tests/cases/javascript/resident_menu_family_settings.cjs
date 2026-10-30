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
  assert(h.root.html.indexOf('data-family-auto-migrate')<h.root.html.indexOf('data-family-members'));
  await h.ctx.saveFamilyAutoMigrate(h.ctx.nodeMenuContext);
  assert.equal(h.calls[1].name,'UpdateFamily');assert.equal(h.calls[1].args.auto_migrate,false);assert.equal(h.calls[1].options.expectedVersion,17);
  assert.match(h.root.html,/aria-checked="false"/);assert.equal(h.notices.length,1);
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
