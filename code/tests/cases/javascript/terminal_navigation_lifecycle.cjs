const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {JSDOM} = require('jsdom');
const {babelParse} = require('./babel_parse.cjs');
const staticRoot = path.resolve(__dirname, '../../../management_portal/static');
const dom = new JSDOM('<main></main>');
const tag = (parts,...values) => parts.reduce((s,p,i)=>s+p+(values[i]??''),'');
const calls=[], timers=new Map(); let timer=0, saved;
class Terminal {
  constructor() { this.cols=80;this.rows=24; }
  loadAddon() {} open() {} write() {} focus() {} dispose() {} reset() {}
  onData() {} onResize() {}
}
class FitAddon { fit() {} }
const window=dom.window;
Object.assign(window,{Terminal,FitAddon:{FitAddon},PortalI18n:{t:x=>x,text:tag,html:tag},
  setTimeout:fn=>{timers.set(++timer,fn);return timer;},clearTimeout:id=>timers.delete(id)});
const context=vm.createContext({window,document:window.document,navigator:window.navigator,
  requestAnimationFrame:fn=>fn(),resolveRoot:x=>x,escapeHtml:x=>String(x),console});
vm.runInContext(fs.readFileSync(path.join(staticRoot,'portal_terminal.js'),'utf8'),context);
const source=fs.readFileSync(path.join(staticRoot,'portal_workspace_surface.js'),'utf8');
const ast=babelParse(source,'portal_workspace_surface.js');
const fn=ast.program.body[0].expression.callee.body.body.find(n=>n.type==='FunctionDeclaration'&&n.id.name==='mountWorkbenchTerminalPanel');
vm.runInContext(source.slice(fn.start,fn.end),context);
const flush=async()=>{for(let i=0;i<6;i++)await new Promise(resolve=>setImmediate(resolve));};
const options=()=>({root:window.document.createElement('section'),target:{key:'host',cwd:'/tmp'},
  apiGet:async url=>{calls.push(['read',url]);return {session_id:'shell-1',alive:true,cursor:0};},
  apiPost:async(url,payload)=>{calls.push([url,payload]);return {session_id:'shell-1',alive:true,cursor:0};},
  emit:()=>{saved=panel.snapshot();}});
let panel;
(async()=>{
  panel=context.mountWorkbenchTerminalPanel(options()); panel.show(); await flush();
  assert.equal(saved.sessionId,'shell-1','new session is persisted immediately');
  await panel.setContext({workspace:{key:'different',cwd:'/other'}});
  assert.equal(panel.snapshot().target.cwd,'/tmp','selection must not kill or retarget a running command');
  panel.hide(); panel.show(); await flush();
  assert.equal(calls.filter(([url])=>url.endsWith('/start')).length,1);
  const snapshot=panel.snapshot(); await panel.destroy();
  assert.equal(calls.filter(([,p])=>p?.action==='close').length,0,'navigation only detaches');
  panel=context.mountWorkbenchTerminalPanel(options()); panel.restore(snapshot); panel.show(); await flush();
  assert.equal(calls.filter(([url])=>url.endsWith('/start')).length,1,'returning must reconnect to the original shell');
  for(const [id,read] of [...timers]){timers.delete(id);await read();} await flush();
  assert(calls.some(([kind,url])=>kind==='read'&&url.includes('session_id=shell-1')));
  await panel.canClose(); await panel.destroy();
  assert.equal(calls.filter(([,p])=>p?.action==='close').length,1,'explicit close ends the shell exactly once');
  panel=context.mountWorkbenchTerminalPanel(options());panel.restore(snapshot);
  await panel.canClose(); await panel.destroy();
  assert.equal(calls.filter(([,p])=>p?.action==='close').length,2,'closing a restored inactive panel also ends its shell');
  const boot=fs.readFileSync(path.join(staticRoot,'lineage_workbench.js'),'utf8');
  assert(boot.indexOf('await window.PortalIdentity.load();')<boot.indexOf('const bootstrappedIdentity'));
  console.log('Terminal navigation preserves session, polling and target; explicit close releases shell');
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>dom.window.close());
