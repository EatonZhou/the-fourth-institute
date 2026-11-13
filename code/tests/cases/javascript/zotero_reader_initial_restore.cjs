// Source-contract probe: real PDF.js parses a generated PDF; native methods run
// against a controlled viewer/event bridge. Not an end-to-end mouse/UI test.
const fs=require('node:fs'),http=require('node:http'),path=require('node:path'),cp=require('node:child_process'),assert=require('node:assert/strict');
const checkout=process.argv[2]||'/tmp/portal-zotero-reader-a';
assert.equal(cp.execFileSync('git',['-C',checkout,'rev-parse','HEAD']).toString().trim(),'04795bfbafd55600e042d62e05f9a4147458ae53');
const source=cp.execFileSync('git',['-C',checkout,'show','HEAD:src/pdf/pdf-view.js']).toString();
const init=source.slice(source.indexOf('\tasync _handleDocumentInit()'),source.indexOf('\n\tasync _setState('));
const set=source.slice(source.indexOf('\tasync _setState('),source.indexOf('\n\tasync _initThumbnails('));
const area=source.slice(source.indexOf('\t_handleViewAreaUpdate ='),source.indexOf('\n\t_updateViewStats()'));
function pdf(){const objs=['<< /Type /Catalog /Pages 2 0 R >>','<< /Type /Pages /Kids [4 0 R 6 0 R 8 0 R] /Count 3 >>','<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>'];for(let n=1;n<=3;n++){const text=`BT /F1 14 Tf 50 720 Td (SYNTHETIC PAGE ${n}) Tj ET`;objs.push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents ${objs.length+2} 0 R >>`,`<< /Length ${text.length} >>\nstream\n${text}\nendstream`);}let out='%PDF-1.4\n',offsets=[];objs.forEach((o,i)=>{offsets.push(Buffer.byteLength(out));out+=`${i+1} 0 obj\n${o}\nendobj\n`;});const start=Buffer.byteLength(out);return out+`xref\n0 ${objs.length+1}\n0000000000 65535 f \n`+offsets.map(n=>String(n).padStart(10,'0')+' 00000 n \n').join('')+`trailer\n<< /Size ${objs.length+1} /Root 1 0 R >>\nstartxref\n${start}\n%%EOF`;}
const root=path.resolve(__dirname,'../../../management_portal/paper_reader_app/zotero_prototype/upstream');
const server=http.createServer((req,res)=>{if(req.url==='/'){res.setHeader('Content-Type','text/html');return res.end('<title>isolated restore probe</title>');}if(req.url==='/fixture.pdf'){res.setHeader('Content-Type','application/pdf');return res.end(pdf());}const file=path.resolve(root,'.'+req.url);if(!file.startsWith(root+path.sep)||!fs.existsSync(file)){res.writeHead(404);return res.end();}res.setHeader('Content-Type','text/javascript');res.end(fs.readFileSync(file));});
(async()=>{await new Promise(r=>server.listen(0,'127.0.0.1',r));const browser=await require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright').chromium.launch({headless:true});try{const page=await browser.newPage();await page.goto(`http://127.0.0.1:${server.address().port}`);const result=await page.evaluate(async({init,set,area})=>{
 const pdfjs=await import('/pdf/build/pdf.mjs');pdfjs.GlobalWorkerOptions.workerSrc='/pdf/build/pdf.worker.mjs';
 const doc=await pdfjs.getDocument('/fixture.pdf').promise;
 const timers=[];
 const Native=new Function('setTimeout',`return class {${init}\n${set}\n${area}}`)((fn,ms)=>{timers.push({fn,ms});return timers.length;});
 const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {resolve,promise};};
 const traces=[];
 async function run(name,{user=false,destroy=false,timeout=false,skip=false}={}){
  const gate=deferred(),trace=[];const owner=new Native();
  let initialized=false;
  const viewer={pagesPromise:gate.promise,scrollMode:0,spreadMode:0,scrollPageIntoView({pageNumber,destArray}){
    trace.push({event:'view',page:pageNumber});owner._handleViewAreaUpdate({location:{pageNumber,scale:'page-width',top:destArray?.[3]||0,left:destArray?.[2]||0}});
  }};
  Object.assign(owner,{_viewState:{pageIndex:0,scale:'page-width',top:700,left:0},_location:skip?{pageNumber:3}:null,
   _iframeWindow:{PDFViewerApplication:{pdfDocument:doc,pdfViewer:viewer}},_documentData:{setDocument(){}},_searchController:{setDocument(){}},
   setTool(){},_cancelPendingPageTurn(){},_onChangeViewState(state){trace.push({event:'callback',page:state.pageIndex+1,initialized});},
   _history:{save(){}},_updateViewStats(){},_resolveInitializedPromise(value){initialized=value;trace.push({event:'initialized',value});},navigate(location){viewer.scrollPageIntoView({pageNumber:location.pageNumber});}});
  const promise=owner._handleDocumentInit();
  if(user)viewer.scrollPageIntoView({pageNumber:3}); // actual scroll bridge, not outer Reader.navigate which waits for init.
  if(destroy){owner._destroyed=true;owner._resolveInitializedPromise(false);}
  const parsed=await doc.getPage(3);if(parsed.pageNumber!==3)throw Error('fixture parse failed');
  if(timeout)timers.at(-1).fn();else gate.resolve();
  await promise;traces.push({name,trace});
 }
 await run('normal');await run('user navigation while pages pending',{user:true});
 await run('destroy while pending',{destroy:true});await run('timeout branch',{timeout:true,user:true});await run('explicit initial location',{skip:true});
 await doc.destroy();return {pages:3,traces};
},{init,set,area});
const views=name=>result.traces.find(t=>t.name===name).trace.filter(t=>t.event==='view').map(t=>t.page);
assert.deepEqual(views('normal'),[1,1]);assert.deepEqual(views('user navigation while pages pending'),[1,3,1]);assert.deepEqual(views('destroy while pending'),[1]);assert.deepEqual(views('timeout branch'),[1,3,1]);assert.deepEqual(views('explicit initial location'),[3]);
assert(result.traces[0].trace.filter(t=>t.event==='callback').every(t=>t.initialized===false));
console.log(JSON.stringify(result,null,2));console.log('PASS source-contract: callbacks precede settled, late restore overwrites user scroll, timeout/destroy/initial-location paths');
}finally{await browser.close();server.close();}})().catch(e=>{console.error(e);server.close();process.exitCode=1;});
