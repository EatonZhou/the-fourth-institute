import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
const require=createRequire(import.meta.url);
const {chromium}=require('../../../test_framework/tau_runtime/node_modules/playwright');
const bundle=await build({stdin:{contents:"export {releasePanel} from './src/packaging/release-panel.js'; export {instancePicker} from './src/packaging/instance-picker.js'",resolveDir:process.cwd()},bundle:true,format:'iife',globalName:'ReleaseUI',write:false});
const server=createServer((req,res)=>{res.setHeader('Content-Type','text/html');res.end('<main><section id="configs"></section><section id="results"></section><p id="error"></p></main>');});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({headless:true});
try {
  const page=await browser.newPage();await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.addScriptTag({content:bundle.outputFiles[0].text});
  const dragText=async locator=>{
    await locator.scrollIntoViewIfNeeded();
    const rect=await locator.evaluate(el=>{const r=document.createRange();r.selectNodeContents(el);const b=r.getBoundingClientRect();return {x:b.x,y:b.y,w:b.width,h:b.height};});
    await page.mouse.move(rect.x+1,rect.y+rect.h/2);await page.mouse.down();
    await page.mouse.move(rect.x+rect.w-1,rect.y+rect.h/2,{steps:12});await page.mouse.up();
    return page.evaluate(()=>getSelection().toString());
  };
  await page.evaluate(()=>{
    window.state={};window.draft={version:'same',notes:'draft'};window.configs=new Map();window.runs=new Map();window.lost=false;window.starts=0;
    const api={configs:async()=>[...configs.values()],config:async id=>structuredClone(configs.get(id)),
      saveConfig:async v=>{const id=v.config_id||'server_config_'+configs.size,previous=configs.get(id);const c={config_id:id,name:v.name,checklist:structuredClone(v.checklist)};configs.set(c.config_id,c);return c;},
      runs:async()=>[...runs.values()],run:async id=>{if(!runs.has(id))throw Error('not found');return runs.get(id);},
      startRun:async v=>{let run=[...runs.values()].find(r=>r.request_key===v.request_key);if(!run){starts++;run={...v,run_id:'server_'+starts,state:'queued',supervisor_resident_id:'resident_e4f55ad12dae'};runs.set(run.run_id,run);}if(lost){lost=false;throw Error('回执丢失');}return run;}};
    window.controller=new AbortController();
    window.panel=ReleaseUI.releasePanel({configRoot:document.querySelector('#configs'),resultRoot:document.querySelector('#results'),api,sdk:{openPage:async()=>{}},signal:controller.signal,
      readState:()=>state,writeState:async value=>{state=structuredClone(value);},collect:()=>draft,requestText:JSON.stringify,loadChecklist:async value=>{draft=value;},prepare:async()=>{},
      act:async fn=>{document.querySelector('main').inert=true;document.querySelector('#error').textContent='';try{await fn();}catch(e){document.querySelector('#error').textContent=e.message;}finally{document.querySelector('main').inert=false;}}});
    return panel.ready();
  });
  await page.getByLabel('配置名称',{exact:true}).fill('版本A');await page.getByRole('button',{name:'保存配置',exact:true}).click();
  await page.waitForFunction(()=>state.config?.name==='版本A');
  await page.getByLabel('配置名称',{exact:true}).fill('版本B');await page.getByRole('button',{name:'另存为新配置',exact:true}).click();
  await page.waitForFunction(()=>configs.size===2);
  await page.getByLabel('完整发布测试中使用 TAU 验证').check();await page.evaluate(()=>{lost=true;});
  await page.getByRole('button',{name:'完整发布测试',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#error').textContent==='回执丢失');
  // Simulate refresh: pending receipt is retained; no new request is sent.
  await page.evaluate(()=>panel.refresh());
  await page.getByRole('button',{name:/核实并重试原运行/}).click();await page.waitForFunction(()=>state.pending_runs.length===0);
  assert.equal(await page.evaluate(()=>starts),1);
  assert.equal(await page.evaluate(()=>[...runs.values()][0].validate_tau),true);
  await page.getByRole('button',{name:'完整发布测试',exact:true}).click();await page.waitForFunction(()=>starts===2);
  assert.equal(await page.getByText('监督 Resident：resident_e4f55ad12dae',{exact:true}).count(),1);
  await page.evaluate(()=>{runs.get(state.run_id).state='installed';return panel.refresh();});
  assert.equal(await page.getByText('实例已安装，等待完整验证',{exact:true}).count(),1);
  await page.evaluate(()=>{const run=runs.get(state.run_id);run.state='needs_configuration';run.stages=[{name:'target_preflight',evidence:{selected_targets:['windows'],support_matrix:[{feature:'portal_core',platform:'windows',capability:'services',implementation:'incomplete',verification:'not_run',reason:'Missing native installer'}],platform_results:[{platform:'linux',scenario:'install',verification:'passed'},{platform:'linux',scenario:'upgrade',verification:'not_run'},{platform:'windows',scenario:'install',verification:'not_run'}]}}];return panel.refresh();});
  assert.equal(await page.getByText('选定目标：["windows"]',{exact:true}).count(),1);
  const stages=page.locator('details').filter({has:page.getByText('阶段记录（1）',{exact:true})});
  assert.equal(await stages.evaluate(node=>node.open),false);
  assert.equal(await stages.locator('pre').count(),0,'Raw stage records should not be rendered until requested');
  assert.equal(await page.getByText(/^target_preflight：/).count(),0);
  await stages.locator('summary').click();
  await stages.locator('pre').waitFor();
  assert.match(await stages.locator('pre').textContent(),/Missing native installer/);
  await stages.locator('summary').click();
  assert.equal(await stages.locator('pre').isVisible(),false);
  await page.getByText('功能 × 平台能力与验证（1 项实现缺口）',{exact:true}).click();
  assert.equal(await page.getByRole('cell',{name:'incomplete',exact:true}).count(),1);
  assert.equal(await page.getByRole('cell',{name:'not_run',exact:true}).count(),1);
  assert.equal(await page.getByText('linux · install · passed',{exact:true}).count(),1);
  assert.equal(await page.getByText('linux · upgrade · not_run',{exact:true}).count(),1);
  assert.equal(await page.getByText('windows · install · not_run',{exact:true}).count(),1);
  await page.evaluate(()=>{
    runs.get(state.run_id).state='generated';
    document.querySelector('main').append(ReleaseUI.instancePicker({sdk:{hatg:async()=>({items:[{resource_id:'concept_111111111111',title:'选择中的对象'}]})},type:'concept',selected:[],signal:controller.signal,onSelect:()=>{window.picked=true;}}));
    return panel.refresh();
  });
  const runID=page.getByText('运行 ID：server_2',{exact:true});
  const copied=await dragText(runID);assert.match(copied,/server_2/);
  await page.waitForTimeout(5500);
  assert.equal(await page.evaluate(()=>getSelection().toString()),copied,'Polling must preserve selected result text');
  await page.evaluate(()=>{state.run_id='server_1';return panel.refresh();});
  await page.getByText('运行 ID：server_1',{exact:true}).waitFor();
  await page.evaluate(()=>{state.run_id='server_2';return panel.refresh();});
  const picker=page.getByRole('combobox',{name:'concept · 名称或 ID'});
  await picker.click();await page.getByRole('option',{name:'选择中的对象 · concept_111111111111'}).waitFor();
  await page.waitForTimeout(5500); // A real background status poll must not blur editing.
  assert.equal(await picker.getAttribute('aria-expanded'),'true');
  await picker.evaluate(el=>el.blur()); // Scrollbar/window focus loss has no outside focus target.
  await page.waitForTimeout(20);assert.equal(await picker.getAttribute('aria-expanded'),'true');
  const candidate=page.getByRole('option',{name:'选择中的对象 · concept_111111111111'});
  assert.match(await dragText(candidate),/选择中的对象/,'Candidate names must support mouse text selection');
  assert.notEqual(await page.evaluate(()=>window.picked),true,'Dragging text must not choose the resource');
  await page.context().grantPermissions(['clipboard-read','clipboard-write']);
  await page.keyboard.press('Control+c');
  assert.match(await page.evaluate(()=>navigator.clipboard.readText()),/选择中的对象.*concept_111111111111/);
  assert.equal(await picker.getAttribute('aria-expanded'),'true');
  await candidate.click();
  assert.equal(await page.evaluate(()=>window.picked),true);
  await picker.click();await page.getByRole('option',{name:'选择中的对象 · concept_111111111111'}).waitFor();
  await page.getByLabel('配置名称',{exact:true}).click();assert.equal(await picker.getAttribute('aria-expanded'),'false');
  await page.evaluate(()=>controller.abort());
  console.log(JSON.stringify({ok:true,scenarios:['save-as','independent-runs','lost-receipt-retry','validation-tau','supervisor','installed-not-success','poll-keeps-picker-open','scrollbar-focus','outside-close','drag-and-copy-candidate-without-choosing','poll-preserves-result-selection']}));
} finally {await browser.close();server.close();}
