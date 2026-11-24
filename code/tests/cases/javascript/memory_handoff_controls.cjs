'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {JSDOM} = require('jsdom');
const w = new JSDOM('<body><div id="controls"></div></body>', {url:'https://fixture.invalid',runScripts:'outside-only'}).window;
w.CSS = {escape: x=>x}; w.matchMedia = ()=>({matches:false,addEventListener(){},removeEventListener(){}});
for(const name of ['portal_text','portal_state','portal_translations','portal_i18n','portal_ui_primitives','portal_overlay','portal_ui','portal_model_api_settings','chat_workspace_runtime_settings','portal_lineage_catalogs']) w.eval(fs.readFileSync(path.resolve(__dirname,'../../../management_portal/static',name+'.js'),'utf8'));
const config = {source:{provider:'codex'},runtime_settings:{model:'luna',reasoning_effort:'max'},accounts:{codex:[
 {id:'seven',account_id:'codex_seven',context_options:[{id:'native',label:'厂商原生 Fork',enabled:true},{id:'local',label:'本地摘要与 trace',enabled:true}]},
 {id:'eight',account_id:'codex_eight',context_options:[{id:'native',label:'厂商原生 Fork',enabled:false},{id:'local',label:'本地摘要与 trace',enabled:true}]}
]}};
const calls=[];
w.PortalResidentService={sessionRestartOptions:async()=>config};
w.PortalAPI={get:async url=>{calls.push(url);return {models:['luna','sol'],default_model:'sol',efforts_by_model:{luna:['max'],sol:['high']},default_efforts_by_model:{luna:'max',sol:'high'},runtime_capabilities:{settings:['model','reasoning_effort','permission_preset']}};}};
const flush=async()=>{for(let i=0;i<8;i++)await new Promise(r=>setTimeout(r,0));};
(async()=>{
 const host=w.document.getElementById('controls'), c=w.PortalLineageCatalogs.forkControls(host);
 c.open('resident_source','codex','seven'); assert.throws(()=>c.read(),/等待/); await flush();
 assert.equal(c.read().fork_mode,'native'); assert.equal(c.read().model,'luna'); assert.equal(c.read().reasoning_effort,'max');
 assert.ok(calls[0].includes('account_id=codex_seven'));
 assert.deepEqual([...host.querySelector('label.residentFormRow select').options].map(x=>x.value),['native','local']);
 assert.equal(host.querySelector('[data-runtime-setting="permission_preset"]').closest('label').hidden,true);
 c.open('resident_source','codex','eight'); await flush(); assert.equal(c.read().fork_mode,'local'); assert.ok(calls[1].includes('codex_eight'));
 c.reset(); assert.throws(()=>c.read(),/等待/);
 c.open('resident_source','codex','seven'); c.reset(); await flush(); assert.throws(()=>c.read(),/等待/,'closed dialogs ignore late replies');
 console.log('PASS shared Fork model/effort, two transfer methods, stable account ID and stale-response guard');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>w.close());
