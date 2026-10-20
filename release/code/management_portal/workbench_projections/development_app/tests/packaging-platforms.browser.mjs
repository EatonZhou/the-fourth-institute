import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
const require=createRequire(import.meta.url),{chromium}=require('../../../test_framework/tau_runtime/node_modules/playwright');
const bundle=await build({stdin:{contents:"export {platformEditor} from './src/packaging/platforms.js'",resolveDir:process.cwd()},bundle:true,format:'iife',globalName:'PlatformUI',write:false});
const browser=await chromium.launch({headless:true});
try{
 const page=await browser.newPage();await page.setContent('<main></main>');await page.addScriptTag({content:bundle.outputFiles[0].text});
 await page.evaluate(()=>{window.queue=[];window.features=[];window.changes=0;window.editor=PlatformUI.platformEditor(document.querySelector('main'),{sdk:{release:(op,args)=>new Promise(resolve=>queue.push({op,args,resolve}))},signal:new AbortController().signal,value:{version:'legacy'},getFeatures:()=>features,onChange:()=>{changes++;editor.refresh();}});editor.ready();});
 assert.match(await page.locator('main').innerText(),/旧版/);
 await page.evaluate(()=>queue[0].resolve({targets:[{id:'linux',label:'Linux',implementation:'implemented',verification:'not_run'},{id:'windows',label:'Windows',implementation:'incomplete',verification:'not_run'}],resolution:{support_matrix:[]}}));
 await page.getByLabel('Windows',{exact:true}).check();await page.getByLabel('Linux',{exact:true}).uncheck();
 await page.evaluate(()=>{queue[2].resolve({targets:[{id:'linux',label:'Linux'},{id:'windows',label:'Windows',implementation:'incomplete',verification:'not_run'}],resolution:{support_matrix:[{feature:'current',platform:'windows',capability:'service',implementation:'incomplete',verification:'not_run',reason:'Current gap'}]}});});
 await page.getByText('功能 × 平台能力与验证（1 项实现缺口）',{exact:true}).click();
 await page.getByRole('cell',{name:'Current gap',exact:true}).waitFor();
 await page.evaluate(()=>queue[1].resolve({targets:[{id:'stale',label:'STALE'}],resolution:{support_matrix:[]}}));
 assert.equal(await page.getByLabel('STALE',{exact:true}).count(),0);
 assert.deepEqual(await page.evaluate(()=>editor.read().target_platforms),['windows']);
 await page.evaluate(()=>{features=['new feature'];editor.refresh();});
 assert.equal(await page.getByRole('cell',{name:'Current gap',exact:true}).count(),0);
 assert.deepEqual(await page.evaluate(()=>queue.at(-1).args.features),['new feature']);
 console.log('Platform editor: legacy origin, shared catalogue, multi-select, stale-query isolation, and feature-dependent refresh passed.');
}finally{await browser.close();}
