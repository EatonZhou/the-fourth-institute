const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const tick = async () => { for (let i=0;i<4;i++) await new Promise(setImmediate); };
const deferred = () => { let resolve; const promise=new Promise(r=>resolve=r); return {promise,resolve}; };
const code = path.resolve(__dirname, '../../../management_portal/static');

async function check(name) {
  const dom = new JSDOM('<main id="root"></main><div id="paperModal"><form id="paperForm"><input name="title"><input name="registration_upload_id"></form><p id="paperRegisterUploadStatus"></p><button id="paperRegisterUploadBtn"></button><button id="submitPaperBtn"></button></div>',
    {url:'http://fixture/',runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window, timers=new Map(); let serial=0;
  w.setTimeout=(fn,delay)=>{timers.set(++serial,{fn,delay});return serial;};
  w.clearTimeout=id=>timers.delete(id);
  const run=async delay=>{
    const item=[...timers].find(([,v])=>v.delay===delay); assert.ok(item,`missing ${delay} timer`);
    timers.delete(item[0]);item[1].fn();await tick();
  };
  const load=file=>w.eval(fs.readFileSync(path.join(code,file),'utf8'));
  try {
    if (name.startsWith('paper-')) {
      load('paper_registration.js');
      const state={registrationEditedFields:new Set()};const modal=w.document.getElementById('paperModal');
      const uploaded=deferred(), query=deferred(); let reads=0;
      const api={postForm:()=>uploaded.promise, get:async()=>{
        reads++;
        if(name==='paper-query-close')return query.promise;
        if(name==='paper-terminal')return {status:'done',metadata:{title:'Finished'}};
        throw Object.assign(new Error('query rejected'),{status:name==='paper-transient'?503:Number(name.split('-')[1])});
      }};
      const registration=w.PortalPaperRegistration.createRegistration({
        $:id=>w.document.getElementById(id),state,paperApi:api,
        paperDialog:{open(){modal.hidden=false;},close(){modal.hidden=true;}},
        loadLibrary:async()=>{},mergeDetailPayload(){},setMode(){},syncPaperLocation(){},
      });
      registration.openPaperModal();
      const upload=registration.uploadPaperRegistrationPdf(new w.File(['fixture'],'fixture.pdf'));
      if(name==='paper-close') {
        registration.closePaperModal(); registration.openPaperModal();
        uploaded.resolve({upload_id:'old',status:'queued'});await upload;
        assert.equal(state.registrationUploadId,'','late upload must not bind the reopened form');
        assert.equal(timers.size,0,'late upload must not restart polling');return;
      }
      uploaded.resolve({upload_id:'upload',status:'queued'});await upload;
      await run(1500);assert.equal(reads,1);
      if(name==='paper-query-close') {
        modal.hidden=true; // Escape/backdrop closes the shared dialog directly.
        query.resolve({status:'extracting',metadata:{title:'Old query'}});await tick();
        assert.equal(timers.size,0,'late query cannot restart a closed dialog');
        assert.equal(w.document.querySelector('[name="title"]').value,'');return;
      }
      if(name==='paper-transient') {
        assert.equal(timers.size,1,'temporary error keeps bounded retry timer');
        registration.closePaperModal(); assert.equal(timers.size,0);return;
      }
      assert.equal(timers.size,0,'terminal response or permanent rejection stops polling');
      assert.equal(state.registrationDraftInFlight,false);
      assert.equal(w.document.getElementById('paperRegisterUploadBtn').disabled,false);
      return;
    }
    w.PortalUIPrimitives={radioGroup:()=>({root:w.document.createElement('span')})};
    const mediaRead=deferred();let requests=0, validationReads=0;
    const reply=(body,status=200)=>({ok:status<400,status,json:async()=>body});
    const snapshot={title:'Fixture',report_id:'report_123456789abc',revision:1,latest_revision:1,
      target:{kind:'project',id:'fixture'},version:'v1',quality:'low',warnings:[],sources:[],rows:[],counts:{},reasoning:{},
      media:{status:'pending',quality:'medium'}};
    w.fetch=async url=>{
      requests++;
      if(url.includes('/media/'))return mediaRead.promise;
      if(url.endsWith('/validate')){validationReads++;return name==='report-validation-hidden'?mediaRead.promise:reply({});}
      return reply(snapshot);
    };
    load('home_report.js');
    const view=w.PortalHomeReportView.create({root:w.document.getElementById('root')});
    await view.show({kind:'report',id:snapshot.report_id,revision:1});
    await run(name==='report-validation-hidden'?20000:1000);
    if(name==='report-destroy')view.destroy();
    if(['report-hidden','report-resume','report-validation-hidden'].includes(name)){
      Object.defineProperty(w.document,'hidden',{configurable:true,value:true});
      w.document.dispatchEvent(new w.Event('visibilitychange'));
    }
    const status=Number(name.split('-')[1]);
    mediaRead.resolve(Number.isFinite(status)?reply({error:'query denied'},status):reply({status:name==='report-terminal'?'cancelled':'pending'}));
    await tick();
    if(name==='report-terminal') {
      assert.equal([...timers.values()].some(t=>t.delay<20000),false,'terminal media stops task polling');
      assert.equal([...timers.values()].filter(t=>t.delay===20000).length,1,'visible report retains its access recheck');
    } else {
      assert.equal(timers.size,0,'withdrawn, hidden or destroyed view must not restart timers');
      const before=requests;await tick();assert.equal(requests,before);
    }
    assert.equal(validationReads,name==='report-validation-hidden'?1:0);
    if(name==='report-resume') {
      Object.defineProperty(w.document,'hidden',{configurable:true,value:false});
      w.document.dispatchEvent(new w.Event('visibilitychange'));await tick();
      assert.equal(validationReads,1,'visible restoration rechecks access');
      await run(1000);
      assert.equal([...timers.values()].filter(t=>t.delay===3000).length,1,'media polling resumes once');
    }
    view.destroy();
  } finally {dom.window.close();}
}
check(process.argv[2]).then(()=>console.log(process.argv[2]+': passed')).catch(error=>{console.error(error);process.exitCode=1;});
