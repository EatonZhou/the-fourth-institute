const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {execFileSync} = require('node:child_process');
const app = path.resolve(__dirname, '../../../management_portal/paper_reader_app');
const {chromium, firefox} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const esbuild = require(path.join(app, 'node_modules/esbuild'));

(async () => {
  execFileSync('python3', [path.join(app, 'generate_translation_contract.py')]);
  const {translationPollDelay} = await import(pathToFileURL(path.join(app, 'translation_client.js')));
  assert.deepEqual([0, 1999, 2000, 9999, 10000, 3600000].map(translationPollDelay), [250,250,700,700,2000,2000]);
  // Optional immutable pre-fix artifact for demonstrating the reported regression.
  const before = process.env.TRANSLATION_BEFORE_SOURCE ? fs.readFileSync(process.env.TRANSLATION_BEFORE_SOURCE, 'utf8') : null;
  const script = esbuild.buildSync({stdin: {contents: `
    import React from 'react'; import {createRoot} from 'react-dom/client';
    import {TranslationPanel} from './translation_panel.jsx';
    import {translateSelection} from './translation_client.js';
    const selection = {text:'Synthetic mathematical text', document:{paper_id:'fixture',attachment_id:'fixture',attachment_version:'v1'}};
    window.start = mode => createRoot(document.getElementById('root')).render(<TranslationPanel selection={selection} mode={mode} isCurrent={()=>true}
      request={(input,mode,operationId,signal)=>translateSelection({selection:input,mode,operationId,principal:'fixture',signal})}/>);
  `,resolveDir:app,loader:'jsx'}, bundle:true,write:false,format:'iife'}).outputFiles[0].text;
  for (const engine of [chromium, firefox]) {
    const browser = await engine.launch({headless:true});
    try {
      const page = await browser.newPage(); let scenario = 'success', calls = [], observed = [];
      page.on('pageerror', error => console.error(error.message));
      await page.route('http://localhost:18579/**', async route => {
        if (!route.request().url().includes('/paper/api/reader/translation')) return route.fulfill({contentType:'text/html',body:'<meta charset="utf-8"><div id="root"></div><script>'+script+'</script>'});
        const method = route.request().method(); calls.push(method); observed.push(Date.now());
        const running = scenario === 'poll' && method === 'POST';
        await new Promise(resolve=>setTimeout(resolve,100));
        return route.fulfill({json:{total_ms:2842.137625091709,task_total_ms:1873.413,google_request_ms:1228.4772149287164,task_id:'fixed',status:running?'running':'succeeded',text:running?null:'这是 Kimi 译文',provider:scenario==='invalid'?'unregistered':scenario==='google'?'google':'kimi',model:scenario==='google'?'translate_a/single:gtx':'fixture-kimi',cache:scenario==='google'?'inflight':'miss'}});
      });
      await page.goto('http://localhost:18579/');
      if (before) {
      const oldError = await page.evaluate(async source => {
        const {translateSelection} = await import(URL.createObjectURL(new Blob([source], {type:'text/javascript'})));
        try {await translateSelection({selection:{text:'same',document:{}},mode:'kimi',operationId:'before',principal:'fixture',signal:new AbortController().signal});return null;}
        catch(error){return error.message;}
      },before);
      assert.match(oldError,/无效结果/);
      }
      calls=[]; observed=[];
      await page.evaluate(()=>start('kimi'));
      await page.locator('.paperTranslationText').filter({hasText:'这是 Kimi 译文'}).waitFor();
      assert.match(await page.locator('.paperTranslation small').innerText(),/Moonshot Kimi API/);
      assert.deepEqual(calls,['POST']);
      scenario='poll'; calls=[]; observed=[];
      await page.getByRole('button',{name:'重试翻译'}).click();
      await page.locator('.paperTranslationText').waitFor();
      assert.deepEqual(calls,['POST','GET']); assert(observed[1]-observed[0]>=230 && observed[1]-observed[0]<850);
      const line=page.locator('.paperTranslation small');
      assert.match(await line.innerText(),/来源 Moonshot Kimi API · 用时 [0-9]+\.[0-9] 秒/);
      const diagnostic=await line.getAttribute('title');
      assert.match(diagnostic,/task_id: fixed/);assert.match(diagnostic,/任务总耗时: 1.9 秒/);assert.match(diagnostic,/最后一次接口处理耗时: 2.8 秒/);assert(!diagnostic.includes('上游请求开始'));
      scenario='google';await page.getByRole('button',{name:'重试翻译'}).click();await page.locator('.paperTranslationText').waitFor();
      assert.match(await line.innerText(),/来源 Google · 用时/);assert(!(await line.innerText()).includes('gtx'));
      assert.match(await line.getAttribute('title'),/从原任务开始计算/);
      scenario='invalid';
      await page.getByRole('button',{name:'重试翻译'}).click();
      await page.getByRole('alert').filter({hasText:'无效结果'}).waitFor();
      console.log(engine.name()+': React panel Kimi/Google compact source+local wait; diagnostic task/HTTP durations and reuse note; hidden gtx; 250ms polling; invalid source rejected');
    } finally {await browser.close();}
  }
  console.log('PASS server-derived enum generation and bounded 250/700/2000ms backoff; no external API calls');
})().catch(error=>{console.error(error);process.exitCode=1;});
