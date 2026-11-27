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
  vm.runInContext(fn('renderFamilySettings'),ctx);
  ctx.renderFamilySettings(ctx.nodeMenuContext);
  return {ctx,root,calls,notices};
}
test('Family settings keep member management and delegate quota choices to individual Residents', () => {
  const {root, calls} = harness(true);
  assert.match(root.html, /data-family-members/);
  assert.match(root.html, /data-family-add/);
  assert.doesNotMatch(root.html, /data-family-auto-migrate|data-family-priority/);
  assert.deepEqual(calls, []);
});
