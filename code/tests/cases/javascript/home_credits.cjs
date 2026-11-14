const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const {JSDOM}=require('jsdom');const {homeStatements}=require('./home_modules.cjs');
const {babelParse}=require('./babel_parse.cjs');
const dom=new JSDOM('<section id="panel"></section>'),document=dom.window.document;
const tag=(p,...a)=>p.reduce((s,v,i)=>s+v+(a[i]??''),'');
const account={account_id:'codex',provider:'codex',kind:'subscription_login',credits_supported:true,allow_credits:false,
 max_concurrency:2,dispatch_interval_min_seconds:2,dispatch_interval_max_seconds:6,
 close_threshold:{supported:true,metric:'percent',unit:'%',value:0}};
let canWrite=true,fail=false,held;const calls=[];
const context=vm.createContext({document,URL,AbortController,console,
 window:{PortalI18n:{t:x=>x,html:tag,text:tag,markup:x=>x},PortalUI:{errorMessage:(_,e)=>e.message},
 PortalInstructionClient:{execute:async(name,args)=>{calls.push({name,args});if(name==='ListProviderAccounts')return {accounts:[{...account,max_concurrency:7}]};if(fail)throw Error('save denied');return {settings:{...account,...args}};}}},
 PortalUIPrimitives:{randomUUID:()=> 'credits-fixture'},esc:x=>String(x??''),formatDateTime:()=>'',
 currentCanSetScheduler:()=>canWrite,currentAgentStatusData:()=>({accounts:[account]}),
 accountIntervalEdits:new Map(),accountThresholdEdits:new Map(),accountConcurrencyEdits:new Map(),accountIntakePending:new Map(),
 syncAccountDetailsButtons:()=>{},loadAgentStatus:()=>{},changeAccountResidentsIntake:()=>{},
 homeApi:{get:()=>new Promise(resolve=>{held=resolve;})},
 accountDetails:{accountId:'codex',requestVersion:0,panel:document.querySelector('#panel'),snapshot:{total:3,open_resident_ids:['r'],closed_resident_ids:['c']},popover:{isOpen:()=>true,reposition(){},close(){}}}});
let statements=homeStatements();
if(process.env.HOME_ACCOUNTS_SOURCE){const text=fs.readFileSync(process.env.HOME_ACCOUNTS_SOURCE,'utf8');const ast=babelParse(text,'home_accounts.js');
 const factory=ast.program.body[0].expression.callee.body.body.find(n=>n.type==='FunctionDeclaration'&&n.id.name.startsWith('create'));
 statements=statements.filter(s=>s.file!=='home_accounts.js').concat(factory.body.body.map(node=>({node,source:text.slice(node.start,node.end)})));}
const names=['executionAccounts','accountConcurrencyRows','accountConcurrencyHtml','accountThresholdHtml','accountIntervalHtml','accountSettingsPending','accountQuotaWindowIsStale','accountQuotaWindows','accountSummaryQuotaWindows','accountQuotaWarning','accountSubscriptionHtml','accountCreditsHtml','renderAccountDetails','loadAccountThresholds','loadAccountDetails','saveAccountThreshold'];
for(const name of names)vm.runInContext(statements.find(s=>s.node.type==='FunctionDeclaration'&&s.node.id.name===name).source,context);
const form=()=>document.querySelector('[data-account-threshold]');
const toggle=()=>form().elements.allow_credits;
(async()=>{try{
 context.renderAccountDetails();assert(toggle(),'supported Codex offers credits');assert.equal(toggle().checked,false);
 assert(document.querySelector('[data-account-interval]'),'interval editor retained');
 toggle().checked=true;toggle().focus();toggle().dispatchEvent(new dom.window.Event('input',{bubbles:true}));
 context.renderAccountDetails();assert.equal(toggle().checked,true);assert.equal(document.activeElement,toggle());
 await context.saveAccountThreshold(form());
 assert.equal(calls[0].name,'ListProviderAccounts');assert.equal(calls[1].args.allow_credits,true);assert.equal(calls[1].args.max_concurrency,7);
 assert.equal(calls[1].args.close_threshold_percent,0);assert.equal(account.allow_credits,true);
 assert(!calls.some(c=>/Intake|OpenResident/.test(c.name)),'saving never opens manually closed residents');
 toggle().checked=false;toggle().dispatchEvent(new dom.window.Event('input',{bubbles:true}));fail=true;
 await context.saveAccountThreshold(form());assert.equal(toggle().checked,false);assert.match(document.querySelector('[role=alert]').textContent,/save denied/);fail=false;
 await context.saveAccountThreshold(form());assert.equal(account.allow_credits,false,'can persist opt-out');
 assert.equal(calls.at(-1).args.allow_credits,false);
 canWrite=false;context.renderAccountDetails();assert(toggle().disabled);let count=calls.length;await context.saveAccountThreshold(form());assert.equal(calls.length,count);canWrite=true;
 context.accountThresholdEdits.clear();
 const refreshing=context.loadAccountDetails(context.accountDetails);
 assert.equal(form().elements.close_threshold.value,'0','pending read keeps threshold');
 assert.match(form().textContent,/%/);assert.doesNotMatch(document.body.textContent,/正在读取/);
 assert.equal(document.querySelector('[data-account-metric=total]').textContent,'3','node refresh keeps old count');
 held({account_id:'codex',total:4});await refreshing;
 assert.equal(document.querySelector('[data-account-metric=total]').textContent,'4');
 account.credits_supported=false;context.accountDetails.thresholds=new Map();context.renderAccountDetails();assert.equal(toggle(),undefined);
 form().elements.close_threshold.value='5';await context.saveAccountThreshold(form());
 assert.equal(calls.at(-1).args.close_threshold_percent,5,'positive thresholds retained');
 assert.equal(Object.hasOwn(calls.at(-1).args,'allow_credits'),false,'unsupported routes omit field');
 account.credits_supported=true;account.provider='kimi';context.accountDetails.thresholds=new Map();context.renderAccountDetails();
 assert.equal(toggle(),undefined,'only Codex subscription may offer credits');
 console.log('credits permissions, fresh read, omission, zero threshold, error/draft/focus and stable refresh pass');
}finally{dom.window.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
