/* Optional Home report view. Every show/reopen reads current authorized facts. */
(function () {
  'use strict';
  const API = '/api/home-report';
  const el = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text != null) node.textContent = String(text);
    if (cls) node.className = cls;
    return node;
  };
  const button = (label, action) => { const b = el('button', label); b.type = 'button'; b.onclick = action; return b; };
  const date = value => value ? new Date(value).toLocaleString() : '时间未登记';
  async function json(url, options) {
    const response = await fetch(url, {credentials: 'same-origin', cache: 'no-store', ...options});
    const data = await response.json();
    if (!response.ok) throw Object.assign(new Error(typeof data.error === 'string' ? data.error : data.error?.message || '暂时无法读取'), {status: response.status});
    return data;
  }
  function create({root, onOpen, onSelection}) {
    if (!root) throw new Error('报告容器不存在');
    root.classList.add('home-report');
    let target, report, media, video, mediaRegion, banner, list, sourceRegion;
    let presented = true, destroyed = false, epoch = 0, controller, externalSignal, externalAbort;
    let refreshTimer, mediaTimer, query = '', status = '', busy = false, rechecking = false;
    const active = token => !destroyed && presented && token === epoch && !controller?.signal.aborted;
    const request = (url, options = {}) => json(url, {...options, signal: controller.signal});
    function stopMedia() { if (video) { video.pause(); video.removeAttribute('src'); video.load(); video = null; } }
    function stop() { ++epoch; controller?.abort(); clearTimeout(refreshTimer); clearTimeout(mediaTimer); busy = false; rechecking = false; }
    function message(value, retry) {
      stopMedia(); root.replaceChildren(el('p', value, 'hr-notice'));
      if (retry) root.append(button('重新读取', () => load().catch(() => {})));
    }
    function sourceButton(source) {
      const b = button(source.label, () => {
        if (source.read.instruction === 'GetMessage' || source.read.instruction === 'GetInputState') {
          const args = source.read.arguments;
          if (target.kind === 'task') { onOpen?.({view:'report',target:{...target,focus:args.message_id}}); return; }
          onOpen?.({view:'report',target:{kind:'task',id:`${args.resident_id}/${args.message_id}`}}); return;
        }
        if (source.address) onOpen?.({view: 'preview', target: {address: source.address}});
        else if (source.read.instruction === 'GetResearchMap') onOpen?.({view: 'map', target: {kind: 'project', id: target.id}});
        else if (target.kind === 'task') onOpen?.({view: 'preview', target: {address: target.id.split('/')[0]}});
        else onOpen?.({view: 'map', target: {...target}});
      });
      b.title = `${source.read.instruction} · ${source.version_kind === 'revision' ? '修订' : '内容 SHA256'} ${source.version}`;
      return b;
    }
    function renderRows() {
      list.replaceChildren();
      const sources = new Map(report.sources.map(s => [s.id, s]));
      const shown = report.rows.filter(row => (!status || row.status === status) && (!query || [row.title,row.body,row.group,row.next].join(' ').toLocaleLowerCase().includes(query.toLocaleLowerCase())));
      list.append(el('p', `显示 ${shown.length} / ${report.rows.length} 项`, 'hr-muted'));
      for (const row of shown) {
        const article = el('article', null, 'hr-card');
        article.dataset.row = row.id;
        const heading = el('div', null, 'hr-card-heading');
        heading.append(el('span', row.group, 'hr-muted'), el('span', row.status, 'hr-status'));
        article.append(heading, el('h3', row.title), el('p', row.body));
        if (row.request_text !== undefined) {
          const original = el('details'); original.append(el('summary','原始任务全文'),el('pre',row.request_text));
          if (target.focus === target.id.split('/')[1]) original.open = true;
          article.append(original);
          for (const reply of row.replies || []) {
            const detail = el('details'); detail.append(el('summary',`原始回复 · ${reply.message_id}`),el('pre',reply.text));
            if (target.focus === reply.message_id) detail.open = true;
            article.append(detail);
          }
        }
        if (row.detail) article.append(el('p', row.detail, 'hr-muted'));
        if (row.conclusion) article.append(el('p', `已登记结论：${row.conclusion}`));
        for (const note of row.notes || []) article.append(el('p', note));
        if (row.next) article.append(el('p', `下一步：${row.next}`));
        const relationLabels = {builds_on:'建立在此基础',depends_on:'依赖',converges:'汇合',alternative:'互为替代'};
        for (const relation of report.relations || []) if (relation.from === row.id && relationLabels[relation.kind]) {
          const related = report.rows.find(item => item.id === relation.to);
          if (related) article.append(button(`${relationLabels[relation.kind]}：${related.title}`, () => onOpen?.(related.open)));
        }
        article.append(el('p', `来源更新时间：${date(row.updated_at)}`, 'hr-muted'));
        const actions = el('div', null, 'hr-actions');
        actions.append(button('打开原始对象', () => onOpen?.(row.open)), button('带入对话', () => onSelection?.({
          kind: 'report', target: {...target}, focus: row.id, label: row.title,
          version: report.version, checked_at: report.checked_at,
          text: `${row.title}\n${row.status}\n${row.body}`, sources: row.sources.map(id => sources.get(id)).filter(Boolean).map(s => ({address: s.address, read: s.read, version: s.version}))
        })));
        article.append(actions);
        const details = el('details'); details.append(el('summary', `${row.sources.length} 个核对来源`));
        row.sources.forEach(id => { if (sources.has(id)) details.append(sourceButton(sources.get(id))); });
        article.append(details); list.append(article);
      }
      if (!shown.length) list.append(el('p', report.rows.length ? '没有匹配的条目。' : '当前可读范围没有工作条目，可从下方来源继续核对。', 'hr-notice'));
      const focus = target.focus;
      if (focus) [...list.querySelectorAll('[data-row]')].find(n => n.dataset.row === focus || n.dataset.row.endsWith('/' + focus))?.classList.add('hr-focused');
    }
    function renderReasoning(plan) {
      if (!plan?.available) {
        if (plan?.reason) root.append(el('p', plan.reason, 'hr-muted'));
        return;
      }
      const section = el('section', null, 'hr-reasoning');
      section.setAttribute('aria-label', '本地结构推理');
      section.append(el('h3', plan.question), el('p', plan.assumption, 'hr-notice'));
      const controls = el('div', null, 'hr-actions');
      const diagrams = el('div', null, 'hr-diagrams');
      const result = el('p', null, 'hr-reasoning-result'); result.setAttribute('role','status');
      const names = new Map(plan.nodes.map(n => [n.id,n.title]));
      const dependency = plan.mode === 'dependency';
      let mode = 'source';
      const svgNode = (tag, attrs = {}, text) => {
        const node = document.createElementNS('http://www.w3.org/2000/svg',tag);
        Object.entries(attrs).forEach(([key,value]) => node.setAttribute(key,value));
        if (text !== undefined) node.textContent = text;
        return node;
      };
      function diagram(restored) {
        const box = el('figure'), label = el('figcaption');
        const svg = svgNode('svg', {viewBox:'0 0 640 420', role:'img'});
        const title = svgNode('title',{},restored ? '恢复遍历后的依赖图' : '原始关系图');
        svg.append(title);
        const points = new Map(plan.nodes.map(n=>[n.id,[35+n.x*570,40+n.y*335]]));
        function line(from,to,color,labelText,dashed=false) {
          const [x1,y1] = points.get(from), [x2,y2] = points.get(to);
          const dx=x2-x1,dy=y2-y1,t=Math.min(65/Math.max(.01,Math.abs(dx)),30/Math.max(.01,Math.abs(dy)),.44);
          const ax=x1+dx*t,ay=y1+dy*t,bx=x2-dx*t,by=y2-dy*t;
          const g=svgNode('g',{'stroke':color,'fill':color});
          g.append(svgNode('line',{x1:ax,y1:ay,x2:bx,y2:by,'stroke-width':dashed?4:2,...(dashed?{'stroke-dasharray':'7 4'}:{})}));
          const angle=Math.atan2(dy,dx),pointsText=[[bx,by],[bx-9*Math.cos(angle-.5),by-9*Math.sin(angle-.5)],[bx-9*Math.cos(angle+.5),by-9*Math.sin(angle+.5)]].map(p=>p.join(',')).join(' ');
          g.append(svgNode('polygon',{points:pointsText}));
          if(labelText) g.append(svgNode('text',{x:(x1+x2)/2,y:(y1+y2)/2-12,'text-anchor':'middle','stroke':'none','font-size':13},labelText));
          svg.append(g);return g;
        }
        const edgeNodes=plan.edges.map(edge=>({edge,node:line(edge.from,edge.to,edge.kind==='depends_on'?'#4263c7':'#9aa5b8',edge.kind==='depends_on'?'依赖':edge.label)}));
        const trace=plan.after.trace.map(p=>line(p.from,p.to,'#835cc4','',true));
        const nodeGroups=plan.nodes.map(n=>{
          const [x,y]=points.get(n.id),g=svgNode('g',{'data-node':n.id});
          const rect=svgNode('rect',{x:x-60,y:y-27,width:120,height:54,rx:8,fill:'#fff',stroke:n.id===plan.seed?'#4263c7':'#aebbd0','stroke-width':2});
          g.append(rect);
          const chars=Array.from(n.title);
          [chars.slice(0,8).join(''),chars.slice(8,16).join('')].forEach((value,i)=>g.append(svgNode('text',{x,y:y-3+i*18,'text-anchor':'middle','font-size':15},value)));
          g.append(svgNode('title',{},n.title));svg.append(g);return {n,rect};
        });
        const [sx,sy]=points.get(plan.seed),gate=svgNode('g',{fill:'#ad661d'});
        gate.append(svgNode('rect',{x:sx+65,y:sy-29,width:6,height:58}),svgNode('text',{x:sx,y:sy-39,'text-anchor':'middle','font-size':13},'假设禁用'));
        svg.append(gate);
        box.append(label,svg);
        function update() {
          box.hidden=restored && mode!=='compare';
          const showTrace=dependency && (restored || mode==='restored');
          trace.forEach(n=>n.style.opacity=showTrace?'1':'0');
          gate.style.opacity=dependency && !showTrace && mode!=='source'?'1':'0';
          edgeNodes.forEach(({edge,node})=>node.style.opacity=!dependency && (restored || mode==='restored') && edge.kind!=='depends_on'?'.12':'1');
          nodeGroups.forEach(({n,rect})=>rect.setAttribute('fill',showTrace && plan.after.reached.includes(n.id)?'#e8f5ef':'#fff'));
          label.textContent=mode==='source'?'原始关系（箭头方向不变）':dependency?(showTrace?'恢复起点 · 可达 '+plan.after.reached.length+' 项':'假设禁用 · 可达 '+plan.before.reached.length+' 项'):(restored || mode==='restored'?'依赖证据不足 · 影响未知':'来源原图');
        }
        return {box,update};
      }
      const left=diagram(false),right=diagram(true);diagrams.append(left.box,right.box);
      const buttons=[];
      for(const [value,label] of [['source','原始关系'],['blocked',dependency?'假设禁用':'语义核对'],['restored',dependency?'恢复起点':'严格依赖图'],['compare','并排比较']]) {
        const b=button(label,()=>{mode=value;update();});buttons.push([value,b]);controls.append(b);
      }
      function update() {
        left.update();right.update();diagrams.classList.toggle('hr-compare',mode==='compare');
        buttons.forEach(([value,b])=>b.setAttribute('aria-pressed',String(mode===value)));
        result.textContent=dependency?'恢复后的可达集合：'+(plan.after.reached.map(id=>names.get(id)).join('、')||'空集')+'。未连入本次集合：'+(plan.unconnected.map(id=>names.get(id)).join('、')||'无')+'。':'当前没有 depends_on 证据；影响未知，不能判定没有影响。';
      }
      section.append(controls,diagrams,result,el('p',plan.rule,'hr-muted'),el('p',plan.limit,'hr-muted'));
      root.append(section);update();
    }
    function render() {
      stopMedia(); root.replaceChildren();
      const header = el('header');
      header.append(el('h2', report.title), el('p', report.goal));
      header.append(el('p', `${target.kind} · ${target.id} · 核对 ${date(report.checked_at)} · 版本 ${report.version.slice(0, 12)}`, 'hr-muted'));
      const actions = el('div', null, 'hr-actions');
      actions.append(button('刷新报告', () => load().catch(() => {})));
      if (report.reasoning?.available) actions.append(button('生成讲解视频', () => generate(false)));
      header.append(actions); root.append(header);
      banner = el('div', null, 'hr-notice'); banner.setAttribute('role','status'); banner.hidden = true; root.append(banner);
      for (const warning of report.warnings) root.append(el('p', warning, 'hr-notice'));
      mediaRegion = el('section', null, 'hr-media'); mediaRegion.hidden = true; mediaRegion.setAttribute('aria-label', '讲解视频'); root.append(mediaRegion);
      renderReasoning(report.reasoning);
      if (report.reasoning?.available) root.append(el('p','配音使用本地机械合成；当前没有已配置的自然语音服务。','hr-muted'));
      if (report.rows.length) {
        const interactive = el('section', null, 'hr-interactive'); interactive.setAttribute('aria-label', '筛选汇报条目');
        const filters = el('div', null, 'hr-actions');
        filters.append(button(`全部 ${report.rows.length}`, () => {status = ''; renderRows();}));
        for (const [label, count] of Object.entries(report.counts)) filters.append(button(`${label} ${count}`, () => {status = label; renderRows();}));
        const search = el('input'); search.type = 'search'; search.placeholder = '搜索工作线、方向或进展'; search.setAttribute('aria-label', '搜索汇报'); search.value = query;
        search.oninput = () => {query = search.value; renderRows();}; interactive.append(filters, search); root.append(interactive);
      }
      list = el('section', null, 'hr-text'); list.setAttribute('aria-label', '汇报正文'); root.append(list); renderRows();
      sourceRegion = el('details', null, 'hr-sources'); sourceRegion.append(el('summary', `全部来源与版本 · ${report.sources.length}`));
      for (const source of report.sources) {
        const p = el('p'); p.append(sourceButton(source), el('small', ` ${source.version_kind === 'revision' ? '修订' : 'SHA256'} ${source.version}`)); sourceRegion.append(p);
      }
      root.append(sourceRegion);
    }
    function renderMedia(state) {
      media = state;
      mediaRegion.hidden = false;
      mediaRegion.replaceChildren();
      if (state.status !== 'ready') {
        mediaRegion.append(el('p', state.status === 'pending' ? '讲解视频正在生成，可以先阅读下方报告。' : `视频暂未生成：${state.error || '请重试'}`, 'hr-notice'));
        if (state.status === 'failed') mediaRegion.append(button('重试视频', () => generate(true)));
        return;
      }
      const base = `${API}/media/${state.job_id}`;
      video = el('video'); video.controls = true; video.preload = 'metadata'; video.playsInline = true;
      video.src = `${base}/video.mp4`; video.poster = `${base}/poster.png`;
      const track = el('track'); track.kind = 'subtitles'; track.srclang = 'zh'; track.label = '中文字幕'; track.src = `${base}/captions.vtt`; video.append(track);
      video.onerror = () => { if (presented) validate().catch(() => {}); };
      mediaRegion.append(el('h3', '视频讲解'), video);
      const chapters = el('nav', null, 'hr-actions'); chapters.setAttribute('aria-label', '视频章节');
      for (const chapter of state.chapters) chapters.append(button(`${Math.floor(chapter.start / 60)}:${String(Math.floor(chapter.start % 60)).padStart(2,'0')} ${chapter.title}`, () => {video.currentTime = chapter.start;}));
      const transcript = el('details'); transcript.append(el('summary', '文字稿'), el('pre', state.transcript));
      mediaRegion.append(chapters, transcript, el('small', `讲解时长 ${Math.round(state.duration)} 秒 · 生成用时 ${state.generation_seconds} 秒 · 报告版本 ${state.version.slice(0,12)}`));
      if (state.scope_note) mediaRegion.append(el('p', state.scope_note, 'hr-muted'));
      if (state.voice_note) mediaRegion.append(el('p', state.voice_note, 'hr-muted'));
    }
    async function pollMedia(token) {
      if (!active(token) || !media?.job_id) return;
      try {
        const state = await request(`${API}/media/${media.job_id}`);
        if (!active(token)) return;
        if (state.status === 'pending') mediaTimer = setTimeout(() => pollMedia(token), 3000);
        else renderMedia(state);
      } catch (error) { if (active(token)) message(`来源暂不可读，已撤去报告：${error.message}`, true); }
    }
    async function generate(retry) {
      if (!presented || !report || busy || destroyed) return;
      busy = true; const token = epoch;
      mediaRegion.hidden = false; mediaRegion.replaceChildren(el('p', '正在核对来源并准备讲解…', 'hr-notice'));
      try {
        const state = await request(`${API}/media`, {method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({target, version: report.version, retry})});
        if (!active(token)) return;
        stopMedia(); renderMedia(state);
        if (state.status === 'pending') mediaTimer = setTimeout(() => pollMedia(token), 1000);
      } catch (error) {
        if (active(token)) {
          if ([401,403,404].includes(error.status)) message('来源已不可读，报告已撤去。', true);
          else renderMedia({status:'failed',error:error.message});
        }
      } finally { if (token === epoch) busy = false; }
    }
    function schedule() { clearTimeout(refreshTimer); if (presented && !destroyed) refreshTimer = setTimeout(() => validate().catch(() => {}), 20000); }
    async function validate() {
      if (!presented || destroyed || !target || !report || rechecking) return;
      rechecking = true; const token = epoch;
      try {
        // Reopen old snapshot dependencies as well as the new target. A source
        // removed from the current report can still be present in its old video.
        await request(`${API}/validate`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sources:report.sources})});
        const fresh = await request(`${API}?${new URLSearchParams(target)}`);
        if (!active(token)) return;
        if (fresh.version !== report.version) {
          banner.hidden = false; banner.replaceChildren(el('span', '来源已有新版本；当前报告保留原快照。'), button('显示新版', () => load().catch(() => {})));
        }
        root.hidden = false;
      } catch (error) { if (active(token)) { message(`来源暂不可读，已撤去报告：${error.message}`, true); root.hidden = false; } }
      finally { if (token === epoch) {rechecking = false; schedule();} }
    }
    async function load() {
      stop(); if (!presented || destroyed || !target || externalSignal?.aborted) return;
      controller = new AbortController(); const token = epoch;
      message('正在读取来源…'); root.hidden = false;
      try {
        const result = await request(`${API}?${new URLSearchParams(target)}`);
        if (!active(token)) return;
        report = result; media = null; render(); schedule();
      } catch (error) { if (active(token)) {message(`报告暂不可读：${error.message}`, true); throw error;} }
    }
    async function show(nextTarget, options = {}) {
      if (destroyed) throw new Error('报告组件已关闭');
      externalSignal?.removeEventListener('abort', externalAbort);
      externalSignal = options.signal;
      externalAbort = () => {stop(); stopMedia(); root.replaceChildren();};
      externalSignal?.addEventListener('abort', externalAbort, {once:true});
      target = {...nextTarget}; query = ''; status = ''; report = null; media = null;
      root.dataset.requestId = options.requestId || '';
      if (externalSignal?.aborted) {externalAbort(); return;}
      return load();
    }
    async function setPresented(value) {
      if (destroyed) return;
      presented = !!value;
      if (!presented) {stop(); video?.pause(); root.hidden = true; return;}
      // History restoration keeps the instance, but permission precedes reveal.
      if (externalSignal?.aborted) { root.replaceChildren(); return; }
      controller = new AbortController();
      if (report) {await validate(); if (media?.status === 'pending') pollMedia(epoch);}
      else await load();
    }
    const recheck = () => {if (presented) {root.hidden = true; video?.pause(); validate().catch(() => {});}};
    const visibility = () => {if (document.hidden) {clearTimeout(refreshTimer); clearTimeout(mediaTimer); video?.pause();} else if (presented) recheck();};
    window.addEventListener('focus', recheck);
    window.addEventListener('portal:permissions-changed', recheck);
    window.addEventListener('portal:identity-changed', recheck);
    document.addEventListener('visibilitychange', visibility);
    return {show, setPresented, destroy() {
      destroyed = true; stop(); stopMedia(); root.replaceChildren();
      externalSignal?.removeEventListener('abort', externalAbort);
      window.removeEventListener('focus', recheck); window.removeEventListener('portal:permissions-changed', recheck);
      window.removeEventListener('portal:identity-changed', recheck); document.removeEventListener('visibilitychange', visibility);
    }};
  }
  window.PortalHomeReportView = {create};
  const standalone = document.querySelector('[data-home-report-standalone]');
  if (standalone) {
    const query = new URLSearchParams(location.search);
    const view = create({root:standalone, onOpen({view,target}) {
      const href = target.address ? `/r/${encodeURIComponent(target.address)}` : view === 'map' ? `/projects?project=${encodeURIComponent(target.id)}` : `/lineage?view=frontier&lineage=${encodeURIComponent(target.id)}`;
      window.open(href, '_blank', 'noopener');
    }, onSelection(value) {navigator.clipboard?.writeText(value.text).catch(() => {});} });
    view.show(Object.fromEntries(['kind','id','focus'].map(key => [key,query.get(key) || '']))).catch(() => {});
  }
})();
