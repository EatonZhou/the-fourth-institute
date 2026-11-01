'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
module.exports=async({page,requests,errors})=>{
  const nav=page.getByRole('region',{name:'Lineage navigation',exact:true});
  await nav.getByRole('button',{name:/^Add Lineage in /}).click();
  const form=page.getByRole('dialog',{name:'New Lineage',exact:true});
  await form.getByRole('textbox',{name:'Name (optional)',exact:true}).fill('New Sample Lineage');
  await form.getByRole('checkbox').uncheck();
  await form.getByRole('button',{name:'Create Lineage',exact:true}).click();
  const node=page.getByRole('dialog',{name:'New node',exact:true});
  await node.getByRole('textbox',{name:'Agent name',exact:true}).waitFor({state:'visible'});
  assert.equal(requests.filter(r=>r.instruction==='CreateLineage').length,1);
  assert.equal(requests.filter(r=>r.instruction==='CreateResident').length,0);
  const hatg=page.getByRole('region',{name:'HATG',exact:true});
  // Old preparation clicked the toolbar behind the automatically opened modal.
  await assert.rejects(hatg.getByRole('button',{name:/^New node:/}).click({timeout:200}),/Timeout/);
  assert.equal(await node.isVisible(),true);
  const prompt=fs.readFileSync(path.join(__dirname,'../../../management_portal/test_framework/config/tau_runner_prompt.md'),'utf8');
  assert.ok(prompt.includes('CreateLineage成功后原生页面会自动打开首个New node表单'),
    'public preparation must account for the native automatically opened form');
  await node.getByRole('button',{name:'Cancel',exact:true}).click();
  await node.waitFor({state:'hidden'});
  assert.deepEqual(errors,[]);
};
