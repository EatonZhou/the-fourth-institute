import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createServer } from 'node:http';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const app = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const { build } = createRequire(app + '/package.json')('esbuild');
const { chromium } = createRequire(import.meta.url)(resolve(app, '../../test_framework/tau_runtime/node_modules/playwright'));
const out = resolve(process.env.PROGRESS_SYNC_EVIDENCE || '/tmp/progress-live-sync');
await mkdir(out, { recursive: true });
await build({ stdin: { resolveDir: app, contents: `
import {mount} from './src/progress/index.js';
import {REFERENCES} from './src/progress/data.js';
import {fixture,base,stage,report} from './tests/progress-fixtures.mjs';
window.start=async()=>{
 const f=await fixture(); f.db[REFERENCES.index]=f.db[base];
 let active=null, revision=0;
 const history=[];
 const probe=window.probe={history,stage,report,root:REFERENCES.index};
 probe.emit=async ref=>{if(active?.refs.includes(ref)) await active.callback({refs:[ref]});};
 probe.add=(id)=>{
  const ref=report.replace('test.json',id+'.json');
  const doc={schema:'tfi.portal-stage-report.v1',ref,title:id,start_at:'2026-10-20T00:00:00Z',end_at:'2026-10-20T01:00:00Z',headlines:[],modules:[{id:'conversation',title:'对话',improvements:[{id,title:id,effect:'新发布的成果',steps:[]}]}]};
  f.db[ref]=JSON.stringify(doc,null,2);
  const index=JSON.parse(f.db[probe.stage]); index.report_refs.push(ref); f.db[probe.stage]=JSON.stringify(index);
  return ref;
 };
 probe.change=(ref,title)=>{const doc=JSON.parse(f.db[ref]);doc.modules[0].improvements[0].title=title;f.db[ref]=JSON.stringify(doc,null,2);};
 probe.refs=()=>active?.refs||[];
 probe.runtime=await mount(document.querySelector('main'),{resources:f.resources,sdk:{context:async()=>({}),focus:async()=>{}},watch:async(refs,callback)=>{
  if(probe.failRegistration){probe.failRegistration=false;throw Error('registration temporarily unavailable');}
  if(probe.delayRegistration){probe.delayRegistration=false;await new Promise(resolve=>probe.releaseRegistration=resolve);}
  const group={refs:[...refs],callback,revision:++revision};active=group;history.push(group.refs);
  return ()=>{if(active===group)active=null;};
 }});
};` }, outfile: resolve(out, 'reader.js'), bundle: true, format: 'iife', target: 'es2022' });
const server = createServer(async (req, res) => {
  if (req.url === '/') { res.setHeader('Content-Type', 'text/html'); res.end('<!doctype html><html lang="zh"><meta charset="utf-8"><link rel="stylesheet" href="/reader.css"><main></main><script src="/reader.js"></script></html>'); }
  else if (['/reader.js','/reader.css'].includes(req.url)) { res.setHeader('Content-Type', req.url.endsWith('js') ? 'text/javascript' : 'text/css'); res.end(await readFile(resolve(out, req.url.slice(1)))); }
  else { res.statusCode=404;res.end(); }
});
await new Promise(r => server.listen(0, '127.0.0.1', r));
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: {width:1280,height:860} });
page.setDefaultTimeout(10000);
const errors=[], observations=[];
page.on('pageerror', e=>errors.push(e.message));
try {
  await page.goto('http://127.0.0.1:'+server.address().port);
  await page.evaluate(()=>start());
  await page.locator('[data-item-key]').first().getByRole('button',{name:'讨论这项成果'}).click();
  await page.getByLabel('反馈内容').fill('保留本地草稿');
  const first=await page.evaluate(async()=>{const ref=probe.add('自动到达一');await probe.emit(probe.stage);return ref;});
  await page.getByRole('heading',{name:'自动到达一',exact:true}).waitFor();
  const refs=await page.evaluate(()=>probe.refs());
  assert.ok(refs.includes(first),'new reports must join the active watch after index refresh');
  await page.evaluate(async ref=>{probe.change(ref,'新增报告已更新');await probe.emit(ref);},first);
  await page.getByRole('heading',{name:'新增报告已更新',exact:true}).waitFor();
  assert.equal(await page.getByLabel('反馈内容').inputValue(),'保留本地草稿');
  observations.push({step:'index then new-report change',refs});
  assert.ok(await page.evaluate(()=>probe.refs().includes(probe.root)),'the fixed source index remains watched');
  await page.evaluate(async()=>{probe.add('自动到达二');await probe.emit(probe.stage);});
  await page.getByRole('heading',{name:'自动到达二',exact:true}).waitFor();
  const sameCount=await page.evaluate(()=>probe.history.length);
  await page.evaluate(()=>probe.emit(probe.stage));
  assert.equal(await page.evaluate(()=>probe.history.length),sameCount,'unchanged refs must not churn subscriptions');
  const retryRef=await page.evaluate(async()=>{probe.failRegistration=true;const ref=probe.add('订阅失败后重试');await probe.emit(probe.stage);return ref;});
  assert.ok(!await page.evaluate(ref=>probe.refs().includes(ref),retryRef));
  assert.ok(await page.evaluate(()=>probe.refs().includes(probe.stage)),'failed replacement keeps old index subscription');
  await page.evaluate(()=>probe.emit(probe.stage));
  assert.ok(await page.evaluate(ref=>probe.refs().includes(ref),retryRef));
  await page.evaluate(()=>{probe.delayRegistration=true;probe.add('登记期间隐藏');probe.pending=probe.emit(probe.stage);});
  await page.waitForFunction(()=>!!probe.releaseRegistration);
  await page.evaluate(async()=>{probe.runtime.hide();probe.releaseRegistration();await probe.pending;});
  assert.equal(await page.evaluate(()=>probe.refs().length),0,'late watch registration after hide must be disposed');
  await page.evaluate(()=>probe.runtime.show());
  await page.evaluate(()=>{probe.runtime.hide();probe.add('隐藏期间到达');});
  assert.equal(await page.evaluate(()=>probe.refs().length),0);
  await page.evaluate(()=>probe.runtime.show());
  await page.getByRole('heading',{name:'隐藏期间到达',exact:true}).waitFor();
  assert.equal(await page.getByLabel('反馈内容').inputValue(),'保留本地草稿');
  await page.screenshot({path:resolve(out,'continuous-updates.png'),fullPage:true});
  observations.push({step:'same source index, second update, hide/show',subscriptions:await page.evaluate(()=>probe.history)});
  await page.evaluate(()=>probe.runtime.destroy());
  assert.equal(await page.evaluate(()=>probe.refs().length),0);
  assert.deepEqual(errors,[]);
  await writeFile(resolve(out,'result.json'),JSON.stringify({passed:true,scope:'real progress component; in-memory resources and watch boundary, no live business writes',observations,errors},null,2));
  console.log('PASS: fixed source index, continuous report watch, no churn, hide/show, drafts, disposal');
} catch(e) {
  await page.screenshot({path:resolve(out,'failure.png'),fullPage:true});
  await writeFile(resolve(out,'failure.json'),JSON.stringify({error:e.message,observations,errors},null,2));
  throw e;
} finally {await browser.close();await new Promise(r=>server.close(r));}
