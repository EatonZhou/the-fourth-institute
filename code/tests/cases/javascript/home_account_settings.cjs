const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {JSDOM}=require('jsdom');const {babelParse}=require('./babel_parse.cjs');
const dom=new JSDOM('<section id="panel"></section>',{url:'http://portal.invalid/'}),document=dom.window.document;
const tag=(parts,...args)=>parts.reduce((s,p,i)=>s+p+(args[i]??''),'');
const account=id=>({account_id:id,capabilities:{scheduler_settings:true,resident_details:false},max_concurrency:10,dispatch_interval_min_seconds:2,dispatch_interval_max_seconds:6,close_threshold:{supported:true,value:0,unit:'%',metric:'percent'}});
const calls=[];let failure=false,release,normalized=false;
const context=vm.createContext({document,window:{PortalI18n:{t:x=>x,html:tag,text:tag,markup:x=>x},PortalUI:{toast:()=>{},errorMessage:(_,e)=>e.message},PortalInstructionClient:{execute:async(name,args,opts)=>{calls.push({name,args,opts});if(release!==undefined)await new Promise(r=>release=r);if(failure)throw Error('save failed');return {settings:{...args,...(normalized?{max_concurrency:24}:{})}};}}},PortalUIPrimitives:{randomUUID:()=>String(calls.length)},
 esc:x=>String(x??'').replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;'),canSetScheduler:true,accountSchedulerPending:new Set(),accountIntakePending:new Map(),agentStatusData:{accounts:[account('a'),account('b')]},executionAccounts:d=>d.accounts,
 accountBalanceHtml:()=>'',accountQuotaWindows:()=>[],accountCreditsHtml:()=>'',syncAccountDetailsButtons:()=>{},loadAgentStatus:()=>{},loadAccountDetails:()=>{},renderAgentStatus:()=>{},formatDateTime:()=>'',
 accountDetails:{accountId:'a',panel:document.querySelector('#panel'),popover:{isOpen:()=>true,reposition:()=>{},close:()=>{}},snapshot:null}});
const source=fs.readFileSync(path.resolve(__dirname,'../../../management_portal/static/home.js'),'utf8');
const names=new Set(['accountSchedulerSettings','renderAccountDetails','saveAccountSchedulerSettings']);
for(const n of babelParse(source,'home.js').program.body)if(n.type==='FunctionDeclaration'&&names.delete(n.id.name))vm.runInContext(source.slice(n.start,n.end),context);
assert.equal(names.size,0);
const input=n=>document.querySelector(`[name="${n}"]`),form=()=>document.querySelector('form');
const open=id=>{context.accountDetails.accountId=id;document.querySelector('#panel').replaceChildren();context.renderAccountDetails();};
(async()=>{
 open('a');assert.equal(document.querySelectorAll('button[type=submit]').length,1);input('concurrency').value='23';input('concurrency').focus();context.renderAccountDetails();assert.equal(input('concurrency').value,'23');assert.equal(document.activeElement,input('concurrency'));
 input('intervalMin').value='9';await context.saveAccountSchedulerSettings(form());assert.equal(calls.length,0);
 input('intervalMax').value='10';input('threshold').value='101';await context.saveAccountSchedulerSettings(form());assert.equal(calls.length,0);
 input('threshold').value='4';failure=true;await context.saveAccountSchedulerSettings(form());assert.equal(input('threshold').value,'4');assert.equal(document.querySelector('fieldset').disabled,false);
 failure=false;release=null;const saving=context.saveAccountSchedulerSettings(form());await context.saveAccountSchedulerSettings(form());assert.equal(calls.length,2);assert(document.querySelector('fieldset').disabled);
 open('b');input('concurrency').value='19';context.agentStatusData={accounts:[account('a'),account('b')]};release();await saving;release=undefined;assert.equal(input('concurrency').value,'19');assert.equal(context.agentStatusData.accounts[0].max_concurrency,23);assert.equal(context.agentStatusData.accounts[0].close_threshold.value,4);
 assert.equal(calls[1].name,'UpdateProviderAccount');assert.match(calls[1].opts.idempotencyKey,/scheduler:a:/);assert.deepEqual(JSON.parse(JSON.stringify(calls[1].args)),{account_id:'a',max_concurrency:23,dispatch_interval_min_seconds:9,dispatch_interval_max_seconds:10,close_threshold_percent:4});
 open('a');input('concurrency').value='15';normalized=true;release=null;const redrawSave=context.saveAccountSchedulerSettings(form());context.renderAccountDetails();release();await redrawSave;release=undefined;normalized=false;assert.equal(input('concurrency').value,'24','Refreshed pending form accepts authoritative save result');
 open('b');
 context.canSetScheduler=false;context.renderAccountDetails();assert(input('concurrency').disabled);await context.saveAccountSchedulerSettings(form());assert.equal(calls.length,3);
 context.canSetScheduler=true;context.agentStatusData.accounts[1].close_threshold={supported:false};open('b');assert(input('threshold').disabled);await context.saveAccountSchedulerSettings(form());assert.equal(calls[3].args.close_threshold_percent,undefined);
 context.agentStatusData.accounts[1].capabilities.scheduler_settings=false;open('b');assert.equal(form(),null);
 console.log('Home details: draft/focus, validation, pending isolation, current snapshot update, permissions and unknown capability passed');
})().finally(()=>dom.window.close()).catch(e=>{console.error(e);process.exitCode=1;});
