'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const dom = new JSDOM('<main id="workspace"></main>', {runScripts:'outside-only'});
const w = dom.window;
w.PortalI18n = {t: String, html: (parts, ...values) => parts.reduce((s,p,i)=>s+p+(values[i]??''),'')};
w.PortalUIPrimitives = {randomUUID:()=> 'fixture', escapeHtml:String, iconHtml:()=>''};
w.PortalIdentity = {listUIAssociations:async()=>({items:[]})};
w.PortalFileTree = {preserveViewport:(_root,render)=>render(),dispose(){},bindRows(){},rowHtml:()=>''};
const reads=[];
w.PortalFileLinks = {load:async target => { reads.push(target); return {kind:'directory',path:target,children:[]}; }};
for (const name of ['portal_resident_service','portal_personal_workspaces','hatg_workspace_projection']) {
 w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static',name+'.js'),'utf8'));
}
(async()=>{
 for (const base of ['/native/project','C:/Users/测试/project','C:\\Users\\测试\\project','\\\\server\\share\\project']) {
  const sep=base.includes('\\')?'\\':'/';
  const lineage=base+sep+'lineage';
  const folder=lineage+sep+'residents'+sep+'resident_native';
  const owned=folder+sep+'workspace';
  const client=w.PortalResidentService.createClient({execute:async()=>({resident:{resident_id:'resident_native',lineage_id:'lineage_native',workspaces:[{workspace_id:'workspace_native',is_owned:true,path:owned}]}})});
  const projection=await client.workspaces('resident_native');
  assert.equal(projection.resident_folder.path,folder.replaceAll('\\','/'));
  assert.equal(projection.lineage_folder.path,lineage.replaceAll('\\','/'));
  assert.equal(w.PortalHatgWorkspaceProjection.workspaces(projection).length,1);
  const root=w.document.getElementById('workspace');
  const panel=w.PortalPersonalWorkspaces.mount({root,onOpen(){}});
  await panel.load();
  panel.setResident({id:'resident_native',roots:projection.roots});
  assert.equal(root.querySelectorAll('[data-workspace-tree]').length,3,'all native directory roots stay visible');
  panel.destroy();
  w.PortalIdentity.listUIAssociations=async()=>({items:[{id:'native-lock',value:{kind:'locked-directory',name:'Native',folders:[owned]}}]});
  const saved=w.PortalPersonalWorkspaces.mount({root,onOpen(){}});
  await saved.load(); await new Promise(resolve=>setImmediate(resolve));
  assert.equal(root.querySelectorAll('[data-workspace-tree]').length,1,'saved native folder survives reload');
  assert.ok(reads.includes(owned),'directory request preserves the original native path');
  saved.destroy();w.PortalIdentity.listUIAssociations=async()=>({items:[]});
 }
 dom.window.close();
 console.log('POSIX, Windows drive/backslash and UNC roots: mapping, graph, personal panel and saved folder passed');
})().catch(error=>{console.error(error);process.exitCode=1;dom.window.close();});
