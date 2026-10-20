import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
const require=createRequire(import.meta.url),{chromium}=require('../../../test_framework/tau_runtime/node_modules/playwright');
const bundle=await build({stdin:{contents:"export {permissionEditor} from './src/packaging/permissions.js'",resolveDir:process.cwd()},bundle:true,format:'iife',globalName:'UI',write:false});
const browser=await chromium.launch({headless:true});
try{
 const page=await browser.newPage();await page.setContent('<main></main>');await page.addScriptTag({content:bundle.outputFiles[0].text});
 await page.evaluate(()=>{
  window.mode='waiting';window.controller=new AbortController();window.changes=0;
  window.old={schemes:[{id:'default',template:true,instance:true}],description:'旧范围原文',configured:true};
  window.sdk={hatg:async(op,a)=>{
   if(op==='ListPermissionSchemes'){
    if(mode==='waiting')await new Promise(resolve=>window.releaseList=resolve);
    if(mode==='failed')throw Error('服务暂不可用');
    return {schemes:mode==='empty'?[]:[{scheme_id:'default',title:'内置方案',builtin:true},{scheme_id:'custom',title:'自定义方案'}]};
   }
   if(op==='GetPermissionScheme'){if(mode==='templateFailed'&&a.scheme_id==='default')throw Error('规则内容不可读');return {scheme_id:a.scheme_id,rules:{}};}
   if(op==='ListPermissionEntries'){if(mode==='entriesFailed'&&a.scheme_id==='default')throw Error('实例查询失败');return {scheme_id:a.scheme_id,entries:[],total:a.scheme_id==='custom'?12:0};}
   throw Error(op);
  }};
  window.editor=UI.permissionEditor(document.querySelector('main'),{sdk,signal:controller.signal,value:old,onChange:()=>changes++});
  window.loading=editor.ready();
 });
 const status=page.getByRole('status'),builtin=page.getByRole('group',{name:'内置方案 · default',exact:true});
 assert.match(await status.textContent(),/正在读取/);
 await page.evaluate(async()=>{mode='ready';releaseList();await loading;});
 assert.match(await status.textContent(),/读取完成/);assert.match(await builtin.textContent(),/内置模板／方案已读取/);assert.match(await builtin.textContent(),/当前为空或尚未生成实例/);
 assert.match(await page.getByRole('group',{name:'自定义方案 · custom',exact:true}).textContent(),/12 条/);
 await page.evaluate(async()=>{mode='failed';await editor.ready();});assert.match(await status.textContent(),/读取失败.*已有选择保留/);assert.doesNotMatch(await status.textContent(),/权限不足/);assert(await builtin.getByLabel('带出此方案实例').isDisabled());
 assert.deepEqual(await page.evaluate(()=>editor.read()),{schemes:[{id:'default',template:true,instance:true}],description:'旧范围原文',configured:true});
 await page.evaluate(async()=>{mode='templateFailed';await editor.ready();});assert.match(await builtin.textContent(),/模板／方案读取失败：规则内容不可读/);assert.match(await builtin.textContent(),/当前为空或尚未生成实例/);
 await page.evaluate(async()=>{mode='entriesFailed';await editor.ready();});assert.match(await builtin.textContent(),/模板／方案已读取/);assert.match(await builtin.textContent(),/权限实例读取失败：实例查询失败/);
 await page.evaluate(async()=>{mode='empty';await editor.ready();});assert.match(await status.textContent(),/读取成功，列表为空/);assert.equal(await page.getByRole('checkbox',{name:'带出此方案实例',exact:true}).isChecked(),true);
 await page.evaluate(()=>{controller.abort();controller=new AbortController();document.querySelector('main').replaceChildren();editor=UI.permissionEditor(document.querySelector('main'),{sdk,signal:controller.signal,value:{},onChange:()=>{}});});assert.match(await status.textContent(),/尚未读取.*尚未选择/);
 await page.getByRole('button',{name:'不附带现有权限内容'}).click();assert.match(await status.textContent(),/已选择不附带/);
 console.log(JSON.stringify({ok:true,checks:['loading','empty success','read failure preserves prior selection','unselected versus explicit none','builtin/custom IDs','separate template and entries results','native total']}));
}finally{await browser.close();}
