// Version-pinned bridge. Selection, drawing, toolbars, deletion and undo are PDF.js UI.
export const VIEWER_URL = '/static/vendor/pdfjs-viewer/6.5.438/web/viewer.html';
const HIGHLIGHT = 9;
const copy = value => JSON.parse(JSON.stringify(value, (_, v) => ArrayBuffer.isView(v) ? Array.from(v) : v));
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const api = (url, payload) => window.PortalAPI.request(url, payload ? {method:'POST', body:payload} : {});

export function nativeData(value) {
  return copy(Object.fromEntries(['annotationType','pageIndex','rotation','rect','color',
    'opacity','thickness','quadPoints','outlines'].filter(k=>value[k]!==undefined).map(k=>[k,value[k]])));
}

export function legacyData(item, page) {
  const viewport = page.getViewport({scale:1});
  const points = item.rects.filter(r=>r.page===page.pageNumber).flatMap(r=>{
    const a = viewport.convertToPdfPoint(r.x*viewport.width,r.y*viewport.height);
    const b = viewport.convertToPdfPoint((r.x+r.width)*viewport.width,(r.y+r.height)*viewport.height);
    const l=Math.min(a[0],b[0]), rgt=Math.max(a[0],b[0]), btm=Math.min(a[1],b[1]), t=Math.max(a[1],b[1]);
    return [l,t,rgt,t,l,btm,rgt,btm];
  });
  const xs=points.filter((_,i)=>i%2===0), ys=points.filter((_,i)=>i%2===1);
  return {annotationType:HIGHLIGHT,pageIndex:page.pageNumber-1,rotation:0,
    rect:[Math.min(...xs),Math.min(...ys),Math.max(...xs),Math.max(...ys)],
    color:[255,228,94],opacity:1,quadPoints:points};
}

export function normalizedRects(data,page) {
  const v=page.getViewport({scale:1});
  const quads=data.quadPoints?.length ? data.quadPoints :
    [data.rect[0],data.rect[3],data.rect[2],data.rect[3],data.rect[0],data.rect[1],data.rect[2],data.rect[1]];
  const result=[];
  for(let i=0;i<quads.length;i+=8) {
    const points=[0,2,4,6].map(j=>v.convertToViewportPoint(quads[i+j],quads[i+j+1]));
    const x=Math.max(0,Math.min(...points.map(p=>p[0]))/v.width), y=Math.max(0,Math.min(...points.map(p=>p[1]))/v.height);
    const right=Math.min(1,Math.max(...points.map(p=>p[0]))/v.width), bottom=Math.min(1,Math.max(...points.map(p=>p[1]))/v.height);
    if(right>x && bottom>y) result.push({page:page.pageNumber,x,y,width:right-x,height:bottom-y});
  }
  return result;
}

export function connectViewer({frame,paper,anchor,status}) {
  let app,win,fingerprint,ready=false,disposed=false,busy=false,failed=false,timer,selectedText='';
  let saved=new Map(), sources=new Map();
  const known=new Map(), baseline=new Map(), pages=new Map(), imported=new Set(), importing=new Map();
  let saving=Promise.resolve(true);
  frame.inert=true;
  function captureAnchor() {
    const container=app?.pdfViewer.container;
    if(!container) return anchor;
    const b=container.getBoundingClientRect();
    const element=win.document.elementFromPoint(b.left+b.width/2,b.top+b.height/2)?.closest('.page') || app.pdfViewer.getPageView(app.page-1)?.div;
    if(!element) return anchor;
    const p=element.getBoundingClientRect();
    return {kind:'pdf-page',pageNumber:Number(element.dataset.pageNumber),pageXRatio:.5,
      pageYRatio:Math.max(0,Math.min(1,(b.top+b.height/2-p.top)/p.height)),viewportX:b.width/2,viewportY:b.height/2};
  }
  function restoreAnchor() {
    if(anchor?.kind!=='pdf-page') return;
    const view=app.pdfViewer.getPageView(anchor.pageNumber-1);
    if(!view) return;
    const v=view.viewport, [x,y]=v.convertToPdfPoint((anchor.pageXRatio||0)*v.width,(anchor.pageYRatio||0)*v.height);
    app.pdfViewer.scrollPageIntoView({pageNumber:anchor.pageNumber,destArray:[null,{name:'XYZ'},x,y,null],allowNegativeOffset:true});
  }
  function importPage(pageNumber) {
    if(!ready || disposed || imported.has(pageNumber)) return Promise.resolve();
    if(importing.has(pageNumber)) return importing.get(pageNumber);
    const view=app.pdfViewer.getPageView(pageNumber-1), layer=view?.annotationEditorLayer?.annotationEditorLayer;
    if(!layer) return Promise.resolve();
    const request=(async()=>{
      for(const item of sources.values()) for(const data of item.native.filter(d=>d.pageIndex===pageNumber-1)) {
        const editor=await layer.deserialize(data);
        if(disposed) return;
        // Loading an existing mark is not a user edit or an undoable creation.
        editor.onceAdded=()=>{};
        layer.add(editor);
        delete editor.onceAdded;
        layer.div.hidden=false;
        editor.show(true);
        known.set(editor.id,{id:item.id,text:item.text});
        baseline.set(editor.id,nativeData(editor.serialize()));
      }
      imported.add(pageNumber);
    })();
    importing.set(pageNumber,request);
    return request.finally(()=>importing.delete(pageNumber));
  }
  function snapshot() {
    const result=new Map(saved), groups=new Map(), current=new Map();
    for(const [key,value] of app.pdfDocument.annotationStorage.serializable.map || []) {
      if(value.annotationType!==HIGHLIGHT || value.id || value.deleted) continue;
      let meta=known.get(key);
      if(!meta) { meta={id:crypto.randomUUID(),text:selectedText.trim().slice(0,20000)||'手绘高亮'}; known.set(key,meta); }
      const data=nativeData(value);
      current.set(key,data);
      if(!groups.has(meta.id)) groups.set(meta.id,[]);
      groups.get(meta.id).push({data,text:meta.text});
    }
    for(const id of new Set([...known.values()].map(m=>m.id))) {
      const previous=saved.get(id), parts=groups.get(id)||[];
      const keys=[...known].filter(([,m])=>m.id===id).map(([k])=>k);
      if(previous && keys.every(k=>same(current.get(k),baseline.get(k)))) continue;
      const rects=previous?.rects.filter(r=>!imported.has(r.page))||[];
      const native=sources.get(id)?.native.filter(d=>!imported.has(d.pageIndex+1))||[];
      for(const part of parts) {
        const page=pages.get(part.data.pageIndex+1)||app.pdfViewer.getPageView(part.data.pageIndex).pdfPage;
        rects.push(...normalizedRects(part.data,page)); native.push(part.data);
      }
      if(!rects.length) result.delete(id);
      else result.set(id,{id,text:previous?.text||parts[0].text,rects,native});
    }
    return {result,current};
  }
  async function flush() {
    if(!ready || disposed) return true;
    if(busy) { if(!await saving) return false; return flush(); }
    try { await Promise.all([...importing.values()]); }
    catch(error) {status(`高亮载入失败，未保存：${error.message}`,true);return false;}
    busy=true;
    saving=(async()=>{
      try {
        const {result,current}=snapshot();
        for(const id of new Set([...saved.keys(),...result.keys()])) {
          const item=result.get(id);
          if(same(item,saved.get(id))) continue;
          status('正在保存高亮…');
          await api('/paper/api/highlights',{paper_id:paper.id,document:fingerprint,
            ...(item?{operation:'add',highlight:item}:{operation:'delete',id})});
          if(item) {saved.set(id,item);sources.set(id,item);} else {saved.delete(id);sources.delete(id);}
          for(const [key,meta] of known) if(meta.id===id) {
            if(current.has(key)) baseline.set(key,current.get(key)); else baseline.delete(key);
          }
          if(disposed) return true;
        }
        failed=false;
        app.pdfDocument.annotationStorage.resetModified();
        status('高亮已同步到当前账号 · 选中文字可高亮，点击标记可删除');
        return true;
      } catch(error) {
        failed=true;
        status(`高亮尚未保存，请重试；关闭前会再次保存。${error.message}`,true);
        return false;
      } finally {busy=false;}
    })();
    return saving;
  }
  const beforeUnload=event=>{
    if(ready && (failed||busy||!same([...snapshot().result],[...saved]))) {event.preventDefault();event.returnValue='';}
  };
  async function load(event) {
    if(event.detail.source!==frame.contentWindow) return;
    win=event.detail.source;app=win.PDFViewerApplication;
    win.PDFViewerApplicationOptions.setAll({disablePreferences:true,enableHighlightFloatingButton:true,
      enableComment:false,enableScripting:false,enablePermissions:true,enableMerge:false,
      enableSignatureEditor:false,defaultZoomValue:'page-width',disableHistory:true});
    const style=win.document.createElement('link');style.rel='stylesheet';style.href='/static/paper_pdfjs_viewer.css';win.document.head.append(style);
    // A reader is bound to one authorized Paper; local file replacement must not
    // save another PDF's edits under this Paper's identity.
    win.document.addEventListener('drop',event=>{event.preventDefault();event.stopImmediatePropagation();},true);
    win.document.addEventListener('change',event=>{
      if(event.target.id==='fileInput') {event.preventDefault();event.stopImmediatePropagation();}
    },true);
    win.document.addEventListener('keydown',event=>{
      if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='o') {event.preventDefault();event.stopImmediatePropagation();}
    },true);
    win.document.addEventListener('selectionchange',()=>{const text=win.getSelection()?.toString();if(text?.trim()) selectedText=text;});
    await app.initializedPromise;
    if(disposed) return;
    app.eventBus.on('annotationeditorlayerrendered',({pageNumber})=>{
      importPage(pageNumber).catch(error=>{failed=true;status(`高亮载入失败：${error.message}`,true);});
    });
    app.eventBus.on('documentloaded',async()=>{
      try {
        fingerprint=app.pdfDocument.fingerprints[0];
        const response=await api(`/paper/api/highlights?id=${encodeURIComponent(paper.id)}&document=${fingerprint}`);
        if(disposed) return;
        saved=new Map(response.highlights.map(item=>[item.id,item]));
        // Resolve geometry only for pages with old marks; never rewrite them merely on opening.
        for(const item of saved.values()) {
          const native=item.native||await Promise.all([...new Set(item.rects.map(r=>r.page))].map(async n=>{
            if(!pages.has(n)) pages.set(n,await app.pdfDocument.getPage(n));
            return legacyData(item,pages.get(n));
          }));
          sources.set(item.id,{...item,native});
        }
        ready=true;
        for(let i=0;i<app.pagesCount;i++) await importPage(i+1);
        restoreAnchor();
        app.pdfDocument.annotationStorage.resetModified();
        frame.inert=false;
        status('选中文字即可高亮；点击高亮可删除。修改保存到当前账号。');
        timer=setInterval(()=>{if(!busy&&!failed&&!importing.size) void flush();},500);
      } catch(error) {ready=false;failed=true;status(`个人高亮载入失败：${error.message}`,true);}
    });
  }
  document.addEventListener('webviewerloaded',load);
  window.addEventListener('beforeunload',beforeUnload);
  return {captureAnchor,flush,retry(){
    if(ready) return flush();
    failed=false;known.clear();baseline.clear();pages.clear();imported.clear();sources.clear();
    frame.src=frame.src;
  },dispose(){disposed=true;clearInterval(timer);
    document.removeEventListener('webviewerloaded',load);window.removeEventListener('beforeunload',beforeUnload);app?.close();}};
}
