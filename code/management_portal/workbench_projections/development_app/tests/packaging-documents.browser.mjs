// One Chromium page exercises deferred index reads without publishing a release.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
const require=createRequire(import.meta.url),{chromium}=require('../../../test_framework/tau_runtime/node_modules/playwright');
const code=await build({stdin:{contents:"export {documentEditor} from './src/packaging/documents.js'",resolveDir:process.cwd()},bundle:true,format:'iife',globalName:'DocumentUI',write:false});
const browser=await chromium.launch({headless:true});
try{
 const page=await browser.newPage();
 await page.setContent('<main></main>');await page.addScriptTag({content:code.outputFiles[0].text});
 await page.evaluate(()=>{
  window.selected='decision_9c258743f91e';window.fail=false;window.indexed=true;window.readable=true;
  window.hold=()=>{window.gate=new Promise(resolve=>{window.releaseRead=resolve;});};hold();
  const sdk={resource:async(operation,args)=>{
   if(operation==='read'){await gate;if(fail)throw Error('模拟读取失败');return {content:`## 规范\n\n| 名称 | ID |\n|---|---|\n| Plan规范 | \`${indexed?selected:'decision_111111111111'}\` |`};}
   return {results:args.addresses.map(address=>({address,ok:readable,result:{readable}}))};
  },hatg:async()=>({resource:{resource_id:selected,resource_type:'decision',attributes:{project_ids:[]}}})};
  window.editor=DocumentUI.documentEditor(document.querySelector('main'),{sdk,signal:new AbortController().signal,
   value:{'SPEC.md':{enabled:true,address:'repo_111111111111/file/SPEC.md',entries:[selected]}},getSource:()=> 'Dev',onChange:()=>{}});
 });
 const spec=page.getByRole('group',{name:'SPEC.md',exact:true});
 const noMissing=async()=>{
  assert.equal(await spec.getByRole('button',{name:/^移除 /}).count(),0);
  assert.doesNotMatch(await spec.innerText(),/不在当前可读索引|未包含此已选条目|资源未能读取/);
  assert.deepEqual(await page.evaluate(()=>editor.read()['SPEC.md'].entries),['decision_9c258743f91e']);
 };
 await noMissing(); // Restored selection before ready() has begun.
 await page.evaluate(()=>{window.ready=editor.ready();});
 await spec.getByText('正在读取索引并核对条目…',{exact:true}).waitFor();await noMissing();
 await page.evaluate(async()=>{releaseRead();await ready;});
 assert(await spec.getByLabel('Plan规范 · decision_9c258743f91e',{exact:true}).isChecked());await noMissing();
 // A failed refresh retains the selection and reports only the read failure.
 await page.evaluate(()=>{hold();fail=true;});await spec.getByRole('button',{name:'读取索引',exact:true}).click();await noMissing();
 await page.evaluate(async()=>{releaseRead();await editor.settle();});
 await spec.getByText('读取失败：模拟读取失败',{exact:true}).waitFor();await noMissing();
 // A completed read can distinguish an absent index entry from an unreadable resource.
 await page.evaluate(()=>{fail=false;indexed=false;});await spec.getByRole('button',{name:'读取索引',exact:true}).click();
 await spec.getByText(/当前索引未包含此已选条目/).waitFor();
 assert.deepEqual(await page.evaluate(()=>editor.read()['SPEC.md'].entries),['decision_9c258743f91e']);
 await page.evaluate(()=>{indexed=true;readable=false;});await spec.getByRole('button',{name:'读取索引',exact:true}).click();
 await spec.getByText(/条目在索引中，但资源未能读取/).waitFor();
 await page.evaluate(()=>{readable=true;});await spec.getByRole('button',{name:'读取索引',exact:true}).click();
 await page.evaluate(()=>editor.settle());await noMissing();
 assert(await spec.getByLabel('Plan规范 · decision_9c258743f91e',{exact:true}).isChecked());
 console.log('Document selection loading, failure, missing, unreadable and retry states passed.');
}finally{await browser.close();}
