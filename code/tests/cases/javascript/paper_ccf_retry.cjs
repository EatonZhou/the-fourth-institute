const assert=require('node:assert/strict'),{test}=require('node:test');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
test('the native CCF client marks explicit retry separately from projection reads',async()=>{
 const window={};vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../../management_portal/static/paper_ccf_catalog.js'),'utf8'),{window,console,setTimeout,clearTimeout});
 const module=window.PortalPaperCcfCatalog,requests=[];let renders=0;
 const state={ccfEnrichmentByKey:{},ccfEnrichmentInFlight:{}};
 const deps=Object.fromEntries(module.dependencies.map(name=>[name,()=>{}]));
 Object.assign(deps,{state,hashString:x=>x,renderWorkspace:()=>{renders++;},paperApi:{request:async(url,options)=>{const body=JSON.parse(options.body);requests.push(body);return {status:body.retry?'pending':'error',error:body.retry?'':'quota unavailable'};}}});
 const catalog=module.createCcfCatalog(deps),paper={title:'Failed paper',dblp_key:'conf/test/failed'};
 state.ccfSelectedPaper=paper;
 await catalog.ensureCcfPaperEnrichment(paper,{refresh:true});
 assert.equal(requests[0].retry,false);
 await catalog.ensureCcfPaperEnrichment(paper);
 assert.equal(requests.length,1);
 await catalog.ensureCcfPaperEnrichment(paper,{retry:true});
 assert.equal(requests.length,2);assert.equal(requests[1].retry,true);
 assert.equal(paper.enrichment.status,'pending');assert.equal(renders,2);
});
test('the native CCF failure preview exposes an explicit retry control',()=>{
 const window={PortalI18n:{t:x=>x,html:(parts,...values)=>parts.reduce((s,p,i)=>s+p+(values[i]??''),'')}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../../management_portal/static/paper_ccf_view.js'),'utf8'),{window,queueMicrotask:()=>{}});
 const module=window.PortalPaperCcfView,main={innerHTML:''},paper={title:'Failed paper'};
 const deps=Object.fromEntries(module.dependencies.map(name=>[name,()=>{}]));
 Object.assign(deps,{$:()=>main,state:{ccfSelectedVenue:'fixture',ccfSelectedYear:'2026',ccfSelectedPaper:paper},
  currentCcfPayload:()=>({papers:[paper]}),currentCcfVenue:()=>({title:'Fixture'}),ccfPaperEnrichment:()=>({status:'error',error:'quota unavailable'}),
  ccfPaperEnrichmentKey:p=>p.title,esc:x=>String(x??'')});
 module.createCcfView(deps).renderCcfWorkspace();
 assert.match(main.innerHTML,/quota unavailable/);assert.match(main.innerHTML,/data-ccf-retry-enrich/);
});
