'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
module.exports=async({page})=>{
  const portalRoot=path.resolve(__dirname,'../../../management_portal');
  const frameworkRoot=path.join(portalRoot,'test_framework');
  const prompt=fs.readFileSync(path.join(frameworkRoot,'config/tau_runner_prompt.md'),'utf8');
  const example=prompt.match(/```js\n(\/\/ tau-workspace-root-read\n[\s\S]*?)\n```/);
  assert.ok(example);
  const install=new Function('require','frameworkRoot','portal','origin','workspaceId','directoryPath','record',
    example[1]+'; return directoryObserver;');
  const workspaceId='workspace_777777777777',directoryPath='/fixture/tau-owned-directory';
  const origin=new URL(page.url()).origin,rows=[],ids=new WeakMap();let sequence=0,loads=0;
  const started=request=>ids.set(request,'native-directory-'+(++sequence));
  page.on('request',started);
  await page.route('**/api/agent-workbench/link-file?*',route=>{
    const target=new URL(route.request().url()).searchParams.get('path');
    loads++;
    return route.fulfill({json:{kind:'directory',path:target,children:[{name:'sample.txt',type:'file',size:5}]}});
  });
  for(const [globalName,file] of [['PortalFileTree','portal_file_tree.js'],['PortalPersonalWorkspaces','portal_personal_workspaces.js']])
    if(!await page.evaluate(name=>!!window[name],globalName))await page.addScriptTag({path:path.join(portalRoot,'static',file)});
  await page.evaluate(()=>{
    const root=document.createElement('section');root.id='sample-native-workspace';
    root.style.cssText='position:fixed;top:10px;left:10px;width:550px;height:350px;background:white;z-index:99999';
    document.body.append(root);
    window.sampleNativeWorkspace=PortalPersonalWorkspaces.mount({root,onOpen(){}});
  });
  const observer=install(require,frameworkRoot,{page,requestId:r=>ids.get(r)},origin,workspaceId,directoryPath,r=>rows.push(r));
  const waiting=()=>page.waitForResponse(r=>new URL(r.url()).pathname==='/api/agent-workbench/link-file'
    && new URL(r.url()).searchParams.get('path')===directoryPath);
  try {
    const original=waiting();
    await page.evaluate(args=>sampleNativeWorkspace.openResource(args),{workspaceId,title:'Native shared root',path:directoryPath});
    const response=await original,receipt=await observer.original(response);
    assert.equal(observer.hasOriginalResponse(response),true);
    assert.equal(receipt.request_id,ids.get(response.request()));assert.equal(receipt.body_status,'read');
    assert.equal(receipt.body.kind,'directory');assert.equal(receipt.body.children[0].name,'sample.txt');
    const root=page.locator('#sample-native-workspace');
    await root.getByRole('treeitem',{name:'sample.txt',exact:true}).waitFor({state:'visible'});
    assert.equal(await root.getAttribute('aria-busy'),'false');
    assert.equal(await root.getAttribute('data-workspace-resident-id'),'','resource entry is not a Resident binding');
    const firstLoads=loads;
    await page.evaluate(args=>sampleNativeWorkspace.openResource(args),{workspaceId,title:'Native shared root',path:directoryPath});
    assert.equal(loads,firstLoads,'reopening the loaded directory uses its existing native listing');
    const refresh=waiting();
    await root.locator('[data-workspace-action="refresh"]').click();
    const refreshed=await observer.original(await refresh);
    assert.equal(refreshed.body.kind,'directory');assert.notEqual(refreshed.request_id,receipt.request_id);
    await page.waitForFunction(()=>document.getElementById('sample-native-workspace').getAttribute('aria-busy')==='false');
    assert.equal(loads,firstLoads+1,'native refresh clears the listing cache and reads the actual GET endpoint');
    for(const suffix of ['&meta=1','/child']) {
      const address='/api/agent-workbench/link-file?path='+encodeURIComponent(directoryPath+(suffix==='/child'?suffix:''))+(suffix==='/child'?'':suffix);
      const pending=page.waitForResponse(r=>new URL(r.url()).pathname+new URL(r.url()).search===address);
      await page.evaluate(address=>fetch(address).then(r=>r.json()),address);
      const excluded=await pending;
      assert.equal(observer.hasOriginalResponse(excluded),false);
      assert.equal((await observer.original(excluded)).body_status,'not_recorded');
    }
  }finally{
    await observer.close();page.off('request',started);
    await page.evaluate(()=>{sampleNativeWorkspace.destroy();document.getElementById('sample-native-workspace').remove();});
    await page.unroute('**/api/agent-workbench/link-file?*');
  }
};
