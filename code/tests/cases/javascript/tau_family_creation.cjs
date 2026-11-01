'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {chromium} = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const {LINEAGE_TEMPLATES_SCRIPTS} = require('./lineage_templates_scripts.cjs');
const root = path.resolve(__dirname, '../../../management_portal/static');
const template = JSON.parse(fs.readFileSync(0, 'utf8'));
(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => route.abort());
    await page.setContent('<!doctype html><div id="shelf"></div><div id="canvas"></div>');
    for (const name of ['portal_text','portal_state','portal_translations','portal_i18n',
      'portal_ui_primitives','portal_overlay','portal_ui','portal_detail_surface',...LINEAGE_TEMPLATES_SCRIPTS]) {
      await page.addScriptTag({path:path.join(root, name+'.js')});
    }
    await page.evaluate(async template => {
      // Account groups have their own fixed sample. Exercise the production
      // parameter renderer/reader here with the actual ISA template schema.
      template.behavior.roles = [];
      window.applied = [];
      window.controller = LineageTemplates.mount({
        shelf:document.querySelector('#shelf'), canvas:document.querySelector('#canvas'),
        getContext:()=>({lineage_id:'lineage_fixture',residents:[]}), resolveTarget:()=>null,
        toast:()=>{}, request:async (name,args)=>{
          if (name==='GetTemplate') return {template};
          if (name==='ApplyTemplate') { applied.push(args); return {summary:'Done'}; }
          throw new Error('Unexpected instruction '+name);
        },
      });
      await controller.apply(template.template_id,{kind:'selection',resident_ids:['resident_fixture']});
    }, template);
    const checkbox = page.locator('[data-apply-parameter="auto_migrate"]');
    assert.equal(await checkbox.count(), 1);
    assert.equal(await checkbox.isChecked(), template.body.auto_migrate);
    await checkbox.uncheck();
    await page.locator('[data-template-dialog="apply"] button[type="submit"]').click();
    await page.waitForFunction(()=>applied.length===1);
    assert.equal(await page.evaluate(()=>applied[0].parameters.auto_migrate), false);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify(await page.evaluate(()=>applied[0].parameters)));
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
