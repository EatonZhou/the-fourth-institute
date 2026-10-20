// Research views own data epochs; the Portal host owns identity, confirmation and Chat.
export const absent = error => ['permission_denied', 'file_not_found', 'resource_not_found', 'projection.reference_missing'].includes(error?.code);
export const addressOf = ref => String(ref).replace(/#L\d+(?:-L\d+)?$/, '');
export function decode(payload) {
  const value = payload.content ?? payload.text ?? '';
  return payload.encoding === 'base64'
    ? new TextDecoder().decode(Uint8Array.from(atob(value), x => x.charCodeAt(0))) : value;
}
export function createResearchSession(raw, {workbenchId, onInvalidate, onError}) {
  let epoch = 0, active = true, disposed = false, watchQueued = false, stopWatch, selection, selectionId=0;
  let chain = Promise.resolve();
  const roots = new Set();
  // Failed releases remain owned here, including subscriptions arriving after hide/destroy.
  const releases = new Map();
  function release(stop) {
    if (releases.get(stop)) return releases.get(stop);
    const pending = Promise.resolve().then(stop).then(() => {
      releases.delete(stop);
    }, error => {
      releases.set(stop, null);
      console.error('Research projection watch cleanup failed', error);
      throw error;
    });
    releases.set(stop, pending);
    return pending;
  }
  function releaseWatches() {
    const stops = new Set(releases.keys());
    if (stopWatch) stops.add(stopWatch);
    stopWatch = null;
    const pending = Promise.all([...stops].map(release)).then(() => {});
    // Visibility/pagehide callers can discard the result; awaiters still receive failures.
    pending.catch(() => {});
    return pending;
  }
  const stale = () => Object.assign(new Error('数据观察已过期，请重新读取'), {code:'projection.stale'});
  const check = token => { if (disposed || !active || token !== epoch) throw stale(); };
  function watch() {
    if (watchQueued || !active || disposed) return;
    watchQueued = true;
    queueMicrotask(() => {
      watchQueued = false;
      const token = epoch;
      chain = chain.catch(() => {}).then(async () => {
        if (disposed || !active || token !== epoch) return;
        const stop = await raw.watch([...roots], () => invalidate('sources'));
        if (disposed || !active || token !== epoch) { await release(stop); return; }
        stopWatch = stop;
      }).catch(error => { if (active && !disposed && token === epoch) onError?.(error); });
    });
  }
  function observed(ref) { const root=addressOf(ref).split('/')[0]; if(!roots.has(root)){roots.add(root);watch();} }
  async function resolve(ref) {
    const token = epoch; check(token);
    const result = await raw.resource('resolve', {address:addressOf(ref)}); check(token);
    observed(ref); return result;
  }
  async function read(ref) {
    const token = epoch;
    if (!(await resolve(ref)).readable) throw Object.assign(new Error('来源不存在或不可读取'), {code:'permission_denied'});
    const result = await raw.resource('read', {address:addressOf(ref)}); check(token);
    return {...result, content:decode(result), encoding:'utf-8'};
  }
  async function readable(refs) {
    const result = new Set();
    const all = [...new Set(refs.filter(Boolean))];
    for (let i = 0; i < all.length; i += 8) {
      await Promise.all(all.slice(i,i+8).map(async ref => { if ((await resolve(ref)).readable) result.add(ref); }));
    }
    return result;
  }
  function invalidate(reason) {
    if (disposed) return releaseWatches();
    ++epoch; ++selectionId; selection = null;
    raw.cancelFeedback().catch(() => {});
    const released = releaseWatches();
    onInvalidate?.(reason);
    return released;
  }
  async function select({ref, source=ref, kind, title, scene={}}) {
    const token=epoch, id=++selectionId;
    const current=()=>{check(token);if(id!==selectionId)throw stale();};
    selection=null; await raw.cancelFeedback(); current();
    const access=await readable([ref,source]); current();
    if (!access.has(ref) || !access.has(source)) throw Object.assign(new Error('所选记录的来源不可读'),{code:'permission_denied'});
    const baseline=await read(source); current();
    const object={ref,kind,status:'current',source,observed_at:new Date().toISOString()};
    await raw.focus(object); current();
    selection={object,title,scene,epoch:token,contentHash:baseline.metadata?.content_sha256};
    return object;
  }
  const sdk = {
    context: () => raw.context(),
    state: (...args) => raw.state(...args),
    openLineage: (...args) => raw.openLineage(...args),
    resource: (operation,args) => operation === 'read' ? read(args.address)
      : operation === 'resolve' ? resolve(args.address) : Promise.reject(new Error('研究视图仅使用只读资源接口')),
    async hatg(name,args) {
      if (!['GetLineage','ListResidentStates'].includes(name)) throw new Error('研究视图不执行变更指令');
      const token=epoch; check(token); const result=await raw.hatg(name,args); check(token); return result;
    }
  };
  return {sdk, read, resolve, readable, select,
    get epoch(){return epoch;}, get active(){return active&&!disposed;},
    reset(){++epoch; ++selectionId; roots.clear(); selection=null; raw.cancelFeedback().catch(()=>{}); return epoch;},
    clearSelection(){++selectionId;selection=null; raw.cancelFeedback().catch(()=>{});},
    async discuss(text) {
      const current=selection;
      if (!current || current.epoch !== epoch) throw stale();
      const token=epoch;
      const source=await read(current.object.source); check(token);
      if(!current.contentHash||source.metadata?.content_sha256!==current.contentHash){invalidate('sources');throw stale();}
      if (current !== selection) throw stale();
      return raw.feedback({ref:current.object.ref,subject:current.title,text,anchor:{kind:current.object.kind},scene:current.scene});
    },
    async openChat(){return raw.openPage(`/workbenches/view?id=${workbenchId}&region=facade`);},
    hide(){active=false; return invalidate('hidden');},
    show(){if(disposed)return; active=true; onInvalidate?.('show');},
    destroy(){active=false; const released=invalidate('destroy'); disposed=true; roots.clear(); return released;}
  };
}

export function addDiscussion(root, session) {
  const area=document.createElement('section'); area.className='researchDiscussion';
  const label=document.createElement('label'); label.textContent='讨论这条研究记录';
  const input=document.createElement('textarea'); input.rows=3; input.placeholder='写下对当前记录的疑问或建议'; label.append(input);
  const send=document.createElement('button'); send.className='portalButton'; send.textContent='交给研究团队';
  const chat=document.createElement('button'); chat.className='portalButton is-quiet'; chat.textContent='打开团队对话';
  const status=document.createElement('p'); status.setAttribute('role','status');
  send.onclick=async()=>{if(!input.value.trim())return; send.disabled=true; try{await session.discuss(input.value); status.textContent='已交给研究团队';input.value='';}catch(e){status.textContent=e.message;}finally{send.disabled=false;}};
  chat.onclick=async()=>{try{const r=await session.openChat();status.textContent=r.opened?'':'当前布局未允许打开对话';}catch(e){status.textContent=e.message;}};
  area.append(label,send,chat,status); root.querySelector('.matrixDetailBody').append(area);
}
