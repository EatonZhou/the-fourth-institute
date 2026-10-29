'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {babelParse} = require('./babel_parse.cjs');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../../management_portal/static');
const source = fs.readFileSync(path.join(root,'lineage_workbench_watchboard.js'),'utf8');
const names = ['closeWatchboardModal','showWatchboardModal','openWatchboardRunOutput'], parts = {};
function visit(node) {
  if (!node || typeof node !== 'object') return;
  if (node.type === 'FunctionDeclaration' && names.includes(node.id?.name)) parts[node.id.name] = source.slice(node.start,node.end);
  for(const child of Object.values(node)) {
    if(Array.isArray(child)) child.forEach(visit);
    else if(child && typeof child==='object') visit(child);
  }
}
visit(babelParse(source,'lineage_workbench_watchboard.js'));
names.forEach(name=>assert.ok(parts[name],name));
(async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:900}});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/*',route=>{
      const name=new URL(route.request().url()).pathname;
      if(name.startsWith('/static/')) return route.fulfill({body:fs.readFileSync(path.join(root,name.slice(8))),contentType:name.endsWith('.css')?'text/css':'text/javascript'});
      return route.fulfill({contentType:'text/html',body:'<!doctype html><html><body></body></html>'});
    });
    await page.goto('http://watchboard-output.fixture.invalid');
    for(const name of ['portal_design_system','portal_themes','lineage_workbench']) await page.addStyleTag({url:'/static/'+name+'.css'});
    for(const name of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','portal_overlay','portal_ui','portal_detail_surface']) await page.addScriptTag({url:'/static/'+name+'.js'});
    await page.evaluate(parts=>{
      const $id=id=>document.getElementById(id),esc=PortalUIPrimitives.escapeHtml,portalUi=PortalUI;
      let watchboardModalDialog=null;
      // closeWatchboardModal also closes the hover edge drawer when one is bound.
      let watchboardEdge=null;
      window.calls=[];
      const residentService={watchboardOutput:async args=>{
        calls.push(args);
        const text=args.offset?'\nnext-page':JSON.stringify({status:'ready',stdout:'long-output-'.repeat(160)});
        return {base64:btoa(text),next_offset:args.offset+text.length,eof:true};
      }};
      eval(parts.join('\n')+'\nwindow.openOutput = () => openWatchboardRunOutput({lineage_id:"fixture"},"run-fixture");');
    },names.map(name=>parts[name]));
    for(const width of [1440,460]) {
      await page.setViewportSize({width,height:900});
      await page.evaluate(()=>openOutput());
      const output=page.locator('[data-watchboard-output="stdout"]'),button=page.locator('[data-watchboard-output-more="stdout"]');
      await page.waitForFunction(()=>document.querySelector('[data-watchboard-output="stdout"]').textContent.includes('long-output-'));
      await button.click();
      await page.waitForFunction(()=>document.querySelector('[data-watchboard-output="stdout"]').textContent.endsWith('next-page'));
      const metrics=await page.evaluate(()=>{
        const card=document.querySelector('.watchboardModalCard'),body=document.querySelector('.watchboardModalBody');
        const rect=card.getBoundingClientRect(),buttons=[...body.querySelectorAll('button')],outputs=[...body.querySelectorAll('pre')];
        return {cardVisible:rect.left>=0&&rect.right<=innerWidth,bodyFits:body.scrollWidth<=body.clientWidth+1,
          buttonsVisible:buttons.every(el=>{const r=el.getBoundingClientRect();return r.width>50&&r.left>=rect.left&&r.right<=rect.right;}),
          outputsFit:outputs.every(el=>el.scrollWidth<=el.clientWidth+1&&parseFloat(getComputedStyle(el).fontSize)>=12),
          calls:calls.slice(-3)};
      });
      assert.ok(metrics.cardVisible&&metrics.bodyFits&&metrics.buttonsVisible&&metrics.outputsFit,JSON.stringify({width,...metrics}));
      assert.ok(metrics.calls.at(-1).offset>0,'next page uses the offset returned by the service');
    }
    assert.deepEqual(errors,[]);
    if(process.env.WATCHBOARD_OUTPUT_SCREENSHOT) await page.screenshot({path:process.env.WATCHBOARD_OUTPUT_SCREENSHOT});
    console.log(JSON.stringify({status:'passed',widths:[1440,460]}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
