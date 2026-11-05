'use strict';
// Real explanation adapter, PortalAPI, native Markdown/Preview and browser media.
// Transport and identities are isolated fixtures; this is not a User/model TAU.
const fs = require('node:fs'), path = require('node:path'), http = require('node:http');
const assert = require('node:assert/strict'), { createHash } = require('node:crypto');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../../management_portal/static');
const out = process.env.HOME_EXPLANATION_EVIDENCE;
if (out) fs.mkdirSync(out, { recursive: true });
const scripts = ['portal_state.js', 'portal_text.js', 'portal_i18n.js', 'portal_ui_primitives.js', 'portal_api.js', 'markdown.js',
  'portal_preview_contract.js', 'portal_preview_runtime.js', 'portal_pdf_preview.js', 'portal_preview_renderers.js', 'home_explanation.js'];
const css = ['portal_themes.css', 'portal_design_system.css', 'portal_components.css', 'portal_components_markdown.css', 'portal_workbench.css', 'home_dialogue.css', 'home_explanation.css'];
const records = new Map(), requests = [], checks = [], errors = [];
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const prefix = 'workspace_0123456789ab/file/explanation/';
function put(name, data, type = 'text/plain') {
  const bytes = Buffer.isBuffer(data) ? data : Buffer.from(data);
  const address = prefix + name; records.set(address, { bytes, type }); return { address, sha256: sha(bytes) };
}
const supplement = put('reading.md', '# 从相关性，到信息汇总\n\n每个词按自己的问题寻找线索。**查询 Q** 与 **键 K** 匹配，得到归一化权重，再汇总值 V。\n\n|步骤|操作|\n|---|---|\n|匹配|比较 Q 与 K|\n|归一化|softmax|\n|汇总|按权重求和 V|\n\n<script>window.parent.injected=true</script>\n', 'text/markdown');
const interaction = put('interaction.html', `<!doctype html><html lang="zh"><meta charset="utf-8"><style>h3{margin:0}#bar{background:var(--accent);height:18px;border-radius:5px;width:60%}.tokens{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0}button{border:1px solid var(--border);padding:8px 14px;border-radius:7px;color:var(--text);background:var(--panel)}output{font-weight:600}label{display:block;margin-block:20px}</style><h3>“它”在寻找哪些信息？</h3><p>选择词语，观察权重。数字用于解释机制。</p><div class="tokens"><button id="cat">小猫</button><button id="sofa">沙发</button></div><output id="answer">小猫 · 60%</output><div id="bar"></div><label>匹配强度 <input type="range" aria-label="匹配强度" min="0" max="100" value="60"></label><button id="probe">隔离检查</button><span id="safe"></span><script>document.querySelector('#cat').onclick=()=>{document.querySelector('#answer').textContent='小猫 · 80%';document.querySelector('#bar').style.width='80%'};document.querySelector('#sofa').onclick=()=>{document.querySelector('#answer').textContent='沙发 · 20%';document.querySelector('#bar').style.width='20%'};document.querySelector('input').oninput=e=>{document.querySelector('#answer').textContent='权重 '+e.target.value+'%';document.querySelector('#bar').style.width=e.target.value+'%'};document.querySelector('#probe').onclick=async()=>{let p=false,s=false,n=false;try{parent.document.body.textContent='escaped'}catch(e){p=true}try{localStorage.setItem('secret','x')}catch(e){s=true}try{await fetch('/escape-network')}catch(e){n=true}document.querySelector('#safe').textContent=[p,s,n].join(',')};</script></html>`, 'text/html');
const plan = put('video-plan.json', JSON.stringify({ title: '查询、匹配、汇总' }), 'application/json');
const captions = put('captions.vtt', 'WEBVTT\n\n00:00.000 --> 00:02.500\n查询与键匹配，再按权重汇总。\n', 'text/vtt');
const transcript = put('transcript.txt', '先比较查询和键。接着归一化权重，最后汇总各位置的值。');
const base = { schema: 'portal-explanation/v1', revision: 1, title: '注意力：每个词，带着问题寻找信息',
  summary: '匹配得到权重，权重决定信息怎样汇入当前词。先动手，再回看计算过程。',
  video_plan: plan, interaction: { ...interaction, title: '改变匹配，观察信息的分配', height: 360 }, supplement,
  sources: [{ label: '原理说明', url: 'https://example.org/paper' }] };
const manifest = (name, value) => put(name + '.json', JSON.stringify(value), 'application/json');
let state = null, qualityStates = null, deny = false, deniedAddress = '', delayed = '', releases = [], videoRef;
let reviewDenied = false, delayReview = false, reviewRelease;
let pageReviewState = null;
let pageReviewReadDenied = false;
let reviewCopyDenied = false, reviewCopyCorrupt = false;
const reviewEvidence = put('private-review.md', '# 固定审核证据\n\n必须补充初始条件对照。\n', 'text/markdown');
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://fixture');
  if (url.pathname === '/') {
    res.writeHead(200, { 'Content-Type': 'text/html' });
    res.end('<!doctype html><html lang="zh-CN" data-portal-theme="light"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' + css.map(n => `<link rel="stylesheet" href="/static/${n}">`).join('')
      + '<style>html,body{margin:0;height:100%;font-family:system-ui}#root{height:100dvh;width:100%}</style><main id="root" class="homeDialogueEntryContent previewBox"></main>'
      + scripts.map(n => `<script src="/static/${n}"></script>`).join('')); return;
  }
  if (url.pathname.startsWith('/static/')) {
    const file = path.resolve(root, url.pathname.slice(8));
    if (file.startsWith(root + '/') && fs.existsSync(file)) { res.writeHead(200, { 'Content-Type': file.endsWith('.css') ? 'text/css' : 'text/javascript' }); res.end(fs.readFileSync(file)); return; }
  }
  if (url.pathname === '/api/home-explanation/reviews/file') {
    requests.push({ url: url.pathname, method: req.method, group: url.searchParams.get('group'), sha256: url.searchParams.get('sha256') });
    res.writeHead(reviewCopyDenied ? 403 : 200, { 'Content-Type': 'text/markdown' });
    res.end(reviewCopyDenied ? 'denied' : reviewCopyCorrupt ? 'changed bytes' : records.get(reviewEvidence.address).bytes); return;
  }
  if (url.pathname === '/api/home-explanation/reviews/resume') {
    const entry = { url: url.pathname, method: req.method }; requests.push(entry);
    let body = ''; req.on('data', chunk => { body += chunk; }); req.on('end', () => {
      entry.body = JSON.parse(body); pageReviewState = { status: 'submitted', id: 'b'.repeat(64), resumes_review_id: entry.body.review_id,
        capabilities: { can_cancel: true, can_resume: false, can_retry: false, can_read_evidence: false } };
      res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ review: pageReviewState }));
    }); return;
  }
  if (url.pathname === '/api/home-explanation/reviews') {
    requests.push({ url: url.pathname, method: req.method });
    if (req.method === 'GET' && pageReviewReadDenied) { res.writeHead(403); res.end('denied'); return; }
    if (req.method === 'POST') pageReviewState = { status: 'submitted', id: 'a'.repeat(64) };
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ review: pageReviewState })); return;
  }
  if (url.pathname === '/api/home-explanation/media') {
    const entry = { url: url.pathname, method: req.method, quality: url.searchParams.get('quality') }; requests.push(entry);
    const respond = () => { res.writeHead(deny ? 403 : 200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(deny ? { error: 'no permit' } : { media: qualityStates ? qualityStates[entry.quality] || null : state })); };
    if (req.method === 'POST') { let body = ''; req.on('data', data => { body += data; }); req.on('end', () => { entry.body = JSON.parse(body); state = { status: 'generating', job_id: 'fixture-job', quality: entry.body.quality }; respond(); }); }
    else respond(); return;
  }
  if (url.pathname === '/api/home-explanation/media/review') {
    requests.push({ url: url.pathname, method: req.method });
    const respond = () => {
      if (!reviewDenied) state = { ...state, review: { status: 'submitted', message_id: 'msg_review' } };
      res.writeHead(reviewDenied ? 403 : 200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(reviewDenied ? { error: 'review permission denied' } : { media: state }));
    };
    if (delayReview) reviewRelease = respond; else respond();
    return;
  }
  if (url.pathname === '/api/home-explanation/media/cancel') {
    const entry = { url: url.pathname, method: req.method }; requests.push(entry);
    let body = ''; req.on('data', data => { body += data; }); req.on('end', () => {
      entry.body = JSON.parse(body); state = { status: 'cancelled', quality: entry.body.quality };
      res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ media: state }));
    }); return;
  }
  if (url.pathname === '/api/resources/content') {
    const address = url.searchParams.get('address'); requests.push({ address, method: req.method });
    const record = records.get(address);
    const respond = () => {
      if (deny || address === deniedAddress) { res.writeHead(403); res.end('denied'); return; }
      if (!record) { res.writeHead(404); res.end('missing'); return; }
      res.writeHead(200, { 'Content-Type': record.type, 'Content-Length': record.declaredSize || record.bytes.length }); res.end(record.bytes);
    };
    if (address === delayed) releases.push(respond); else respond();
    return;
  }
  requests.push({ forbidden: url.pathname }); res.writeHead(404); res.end();
});
const check = (name, actual, expected) => { assert.deepEqual(actual, expected, name); checks.push(name); };
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } }); page.setDefaultTimeout(8000);
  page.on('pageerror', error => errors.push(error.stack || error.message));
  try {
    await page.goto('http://127.0.0.1:' + server.address().port);
    // A tiny real browser-encoded video is a media fixture, not generated lesson evidence.
    const bytes = await page.evaluate(async () => {
      const canvas = document.createElement('canvas'); canvas.width = 640; canvas.height = 360;
      const ctx = canvas.getContext('2d'), stream = canvas.captureStream(24), chunks = [];
      const recorder = new MediaRecorder(stream, { mimeType: 'video/webm;codecs=vp8' });
      const ready = new Promise(resolve => { recorder.ondataavailable = e => chunks.push(e.data); recorder.onstop = async () => resolve(Array.from(new Uint8Array(await new Blob(chunks).arrayBuffer()))); });
      recorder.start(); const start = performance.now();
      await new Promise(resolve => {
        function draw(now) { const p = (now - start) / 2300; ctx.fillStyle = '#13223d'; ctx.fillRect(0, 0, 640, 360); ctx.fillStyle = '#a4c9ff'; ctx.font = '36px sans-serif'; ctx.fillText('Q → K → V', 190, 140); ctx.beginPath(); ctx.arc(100 + 440 * p, 240, 22, 0, 7); ctx.fill(); if (p < 1) requestAnimationFrame(draw); else resolve(); } requestAnimationFrame(draw);
      }); recorder.stop(); const result = await ready; stream.getTracks().forEach(track => track.stop()); return result;
    });
    videoRef = put('video.webm', Buffer.from(bytes), 'video/webm');
    const planned = manifest('planned', base);
    const fixed = manifest('fixed', { ...base, video_plan: undefined, video: { ...videoRef, captions, transcript, chapters: [{ time: 0, title: '匹配' }, { time: 1, title: '汇总' }] } });
    const setup = async target => { await page.evaluate(async target => {
      window.viewer ||= 'fixture'; window.PortalIdentity = { peek: () => ({ principal: { kind: 'resident', id: viewer } }) };
      window.opens = []; window.changes = []; window.urls ||= []; window.revoked ||= [];
      if (!window.urlInstrumented) {
        const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
        URL.createObjectURL = blob => { const value = create(blob); urls.push(value); return value; };
        URL.revokeObjectURL = url => { revoked.push(url); revoke(url); }; window.urlInstrumented = true;
      }
      window.adapter?.destroy();
      window.adapter = PortalHomeExplanationView.create({ root: document.getElementById('root'), onOpen: x => opens.push(x), onChange: x => changes.push(x.accessStatus) });
      await adapter.show(target, { requestId: 'fixture-request' });
    }, target);
      if (process.argv.some(arg => ['--review-transfer-only','--page-workflow-only','--review-only'].includes(arg))) await page.locator('.homeExplanationReviewDetails').evaluateAll(nodes => nodes.forEach(el => el.open = true));
    };
    if (process.argv.includes('--document-flow-only')) {
      const paragraphs = Array.from({ length: 25 }, (_, i) => `<section><h3>第${i + 1}步：比较两条轨迹</h3><p>同一条规则，从相近起点出发。改变初始条件，再比较两次计算的轨迹与差值；规则、时间和坐标尺度保持一致。</p></section>`).join('');
      const reading = put('flow.html', `<!doctype html><style>html,body{height:100%;overflow:auto}body{margin:0;font:inherit}section{margin:28px 0}h3{margin:0}p{margin:12px 0}button{font:inherit}svg{display:block;width:100%;height:120px}#extra{height:600px;background:var(--panel)}</style><h2>两条轨迹，同一条规则</h2><p>从正文开始，沿图示读到结论。</p><button id="expand">展开推导</button><div id="extra" hidden>展开内容增高后，下面的正文自然下移。</div><svg viewBox="0 0 600 120" aria-label="轨迹"><path d="M0 90 Q150 0 300 90 T600 90" fill="none" stroke="blue" stroke-width="3"/></svg>${paragraphs}<button id="end">读到结尾</button><output id="done"></output><script>document.getElementById('expand').onclick=()=>{const el=document.getElementById('extra');el.hidden=!el.hidden};document.getElementById('end').onclick=()=>document.getElementById('done').textContent='结尾操作完成';</script>`, 'text/html');
      const target = manifest('flow', { ...base, interaction: { ...reading, height: 240 }, video_plan: undefined, supplement: undefined });
      await setup(target);
      const demo = () => page.frameLocator('.homeExplanationInteraction').frameLocator('iframe');
      const size = () => page.locator('.homeExplanationInteraction').evaluate(el => el.getBoundingClientRect().height);
      const settled = async () => {
        await page.waitForFunction(() => document.querySelector('.homeExplanationInteraction')?.getBoundingClientRect().height > 240);
        await demo().locator('body').evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      };
      await settled();
      const initial = await size();
      check('long illustrated prose extends beyond the old fixed viewport', initial > 2500, true);
      check('no visible frame border or clipped demo region', await page.locator('.homeExplanationDemo').evaluate(el => {
        const s = getComputedStyle(el); return [s.borderTopWidth, s.overflowY];
      }), ['0px', 'visible']);
      check('document contents fit the expanded surface', await demo().locator('body').evaluate(el => el.scrollHeight <= innerHeight + 1), true);
      const surface = page.locator('.homeExplanationInteraction');
      await page.locator('#root').evaluate(el => { el.scrollTop = el.querySelector('.homeExplanationDemo').offsetTop - el.offsetTop - 100; });
      const box = await surface.boundingBox();
      await page.mouse.move(box.x + 100, Math.max(120, box.y + 80));
      const beforeWheel = await page.locator('#root').evaluate(el => el.scrollTop);
      await page.mouse.wheel(0, 700);
      await page.waitForFunction(before => document.getElementById('root').scrollTop > before + 400, beforeWheel);
      check('wheel over illustrated content scrolls the report only', await demo().locator('html').evaluate(() => scrollY), 0);
      await demo().locator('#expand').click();
      await page.waitForFunction(initial => document.querySelector('.homeExplanationInteraction').offsetHeight >= initial + 599, initial);
      check('expanded derivation becomes part of the report height', await size() >= initial + 599, true);
      await demo().locator('#expand').click();
      await page.waitForFunction(initial => Math.abs(document.querySelector('.homeExplanationInteraction').offsetHeight - initial) <= 1, initial);
      checks.push('collapsing derivation shrinks the report without a viewport height floor');
      await demo().locator('#end').focus(); await page.keyboard.press('Enter');
      check('keyboard can reach and operate the end of long prose', await demo().locator('#done').textContent(), '结尾操作完成');
      const endBounds = await demo().locator('#end').boundingBox();
      check('last control is visible through report scrolling', endBounds.y >= 0 && endBounds.y + endBounds.height <= 1000, true);
      if (out) await page.screenshot({ path: path.join(out, 'flow-end-desktop.png') });
      await page.locator('#root').evaluate(el => { el.scrollTop = 0; });
      if (out) await page.screenshot({ path: path.join(out, 'flow-start-desktop.png') });
      await page.setViewportSize({ width: 390, height: 844 });
      await page.locator('#root').evaluate(el => { el.style.fontSize = '24px'; });
      await setup(target); await settled();
      check('inherited prose typography follows the report font size', await demo().locator('body').evaluate(el => getComputedStyle(el).fontSize), '24px');
      check('narrow large-text prose has no horizontal overflow', await demo().locator('body').evaluate(el => el.scrollWidth <= innerWidth + 1), true);
      check('narrow host has one vertical scrolling surface', await page.locator('#root').evaluate(el => el.scrollHeight > el.clientHeight && el.scrollWidth <= el.clientWidth + 1), true);
      check('long narrow prose remains fully expanded', await demo().locator('body').evaluate(el => el.scrollHeight <= innerHeight + 1), true);
      await page.locator('#root').evaluate(el => { el.scrollTop = el.querySelector('.homeExplanationDemo').offsetTop - el.offsetTop - 100; });
      if (out) await page.screenshot({ path: path.join(out, 'flow-390.png') });
      const heightBefore = await size();
      await page.evaluate(() => window.postMessage({ type: 'portal-explanation-layout', height: 999999, channel: 'wrong' }, '*'));
      await page.waitForTimeout(60);
      check('unrelated layout messages cannot resize the explanation', await size(), heightBefore);
      await page.evaluate(() => adapter.destroy());
      check('closing the report removes the layout source', await page.locator('iframe').count(), 0);
      check('continuous prose has no browser errors', errors, []);
      check('reading continuous prose does not generate or resubmit', requests.filter(r => r.method === 'POST').length, 0);
      console.log(JSON.stringify({ checks, errors }));
      return;
    }
    if (process.argv.includes('--mount-lifecycle-only')) {
      await setup(planned);
      const target = manifest('detached', { ...base, video_plan: undefined });
      const beforeAttach = await page.evaluate(async target => {
        adapter.destroy();
        window.detachedRoot = document.createElement('div');
        adapter = PortalHomeExplanationView.create({ root: detachedRoot });
        await adapter.show(target);
        return { connected: detachedRoot.isConnected, text: detachedRoot.textContent,
          frames: detachedRoot.querySelectorAll('.homeExplanationInteraction').length };
      }, target);
      check('detached host does not fail while preparing the interaction', beforeAttach.text.includes("Cannot read properties of null"), false);
      check('detached host prepares exactly one interaction', beforeAttach.frames, 1);
      check('show resolves before the host is attached', beforeAttach.connected, false);
      await page.evaluate(() => document.getElementById('root').replaceChildren(detachedRoot));
      const demo = () => page.frameLocator('.homeExplanationInteraction').frameLocator('iframe');
      await demo().getByRole('button', { name: '沙发', exact: true }).click();
      check('interaction runs after late host attachment', await demo().locator('#answer').textContent(), '沙发 · 20%');
      check('successful mount does not duplicate static reading', await page.locator('.homeExplanationSupplement').count(), 0);
      await page.evaluate(() => { detachedRoot.remove(); document.getElementById('root').append(detachedRoot); });
      await demo().getByRole('button', { name: '小猫', exact: true }).click();
      check('reparented host recreates a usable interaction', await demo().locator('#answer').textContent(), '小猫 · 80%');
      await page.evaluate(() => adapter.setPresented(false));
      check('hidden host removes both frame layers', await page.locator('iframe').count(), 0);
      await page.evaluate(() => adapter.setPresented(true));
      await demo().getByRole('button', { name: '隔离检查', exact: true }).click();
      await demo().locator('#safe').filter({ hasText: 'true,true,true' }).waitFor();
      checks.push('restored interaction still isolates parent, storage and network');
      if (out) await page.screenshot({ path: path.join(out, 'attached-interaction.png'), fullPage: true });
      await demo().locator('body').evaluate(() => { location.href = '/escape-navigation'; });
      await page.waitForTimeout(150);
      check('inner self-navigation remains blocked', requests.filter(r => r.forbidden?.startsWith('/escape')).length, 0);
      const hostile = put('detached-hostile.html', '<img src="/escape-image"><style>body{background-image:url(/escape-css)}</style><button id="popup">popup</button><script>document.querySelector("#popup").onclick=()=>window.open("/escape-popup");fetch("/escape-fetch").catch(()=>{});</script>', 'text/html');
      await page.evaluate(async target => {
        adapter.destroy(); adapter = PortalHomeExplanationView.create({ root: detachedRoot });
        await adapter.show(target);
      }, manifest('hostile', { ...base, video_plan: undefined, interaction: hostile }));
      await demo().locator('#popup').click();
      await page.waitForTimeout(150);
      check('wrapper serialization does not enable external requests', requests.filter(r => r.forbidden?.startsWith('/escape')).length, 0);
      check('opaque interaction cannot open another window', page.context().pages().length, 1);
      const preview = await page.evaluate(async ({ address, content }) => {
        adapter.destroy(); detachedRoot.remove();
        window.previewRoot = document.createElement('div');
        window.previewHandle = PortalPreview.render({ root: previewRoot, document: {
          source: 'resource', resource_type: 'workspace', address, resource_address: address,
          contentKind: 'json', content,
        } });
        await previewHandle.promise;
        const connected = previewRoot.isConnected;
        document.getElementById('root').replaceChildren(previewRoot);
        return { renderer: previewHandle.renderer, connected };
      }, { address: target.address, content: records.get(target.address).bytes.toString() });
      check('native Preview can finish loading before attachment', preview, { renderer: 'explanation', connected: false });
      await demo().getByRole('button', { name: '沙发', exact: true }).click();
      check('late-mounted native Preview is interactive', await demo().locator('#answer').textContent(), '沙发 · 20%');
      await page.evaluate(() => PortalPreview.dispose(previewRoot));
      check('native Preview disposal removes the nested frames', await page.locator('iframe').count(), 0);
      const cancelled = await page.evaluate(async target => {
        const root = document.createElement('div');
        const view = PortalHomeExplanationView.create({ root });
        await view.show(target); view.destroy();
        document.getElementById('root').replaceChildren(root);
        return root.querySelectorAll('iframe').length;
      }, target);
      check('disposed detached content cannot start after attachment', cancelled, 0);
      check('mount lifecycle does not submit generation or reviews', requests.filter(r => r.method === 'POST').length, 0);
      check('mount lifecycle has no uncaught errors', errors, []);
      console.log(JSON.stringify({ checks, errors }));
      return;
    }
    if (process.argv.includes('--illustrated-only')) {
      const headings = () => page.locator('.homeExplanationSectionHead h2').allTextContents();
      await setup(planned);
      check('one illustrated narrative follows the optional video', await headings(), ['视频讲解', '图文解释']);
      check('interactive document replaces duplicated static chapter', await page.locator('.homeExplanationSupplement').count(), 0);
      const demo = () => page.frameLocator('.homeExplanationInteraction').frameLocator('iframe');
      await demo().getByRole('button', { name: '沙发', exact: true }).click();
      check('the unified narrative remains interactive', await demo().locator('#answer').textContent(), '沙发 · 20%');
      if (out) await page.screenshot({ path: path.join(out, 'illustrated-desktop.png'), fullPage: true });
      await page.setViewportSize({ width: 390, height: 844 });
      await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; document.documentElement.style.setProperty('--portal-font-size', '24px'); });
      check('unified section has no horizontal overflow', await page.locator('#root').evaluate(el => el.scrollWidth <= el.clientWidth + 1), true);
      if (out) await page.screenshot({ path: path.join(out, 'illustrated-390.png'), fullPage: true });
      await setup(manifest('text-only', { ...base, quality: 'low', interaction: undefined, video_plan: undefined }));
      check('static reading uses the same single section', await headings(), ['图文解释']);
      check('static reading remains available', await page.locator('.homeExplanationSupplement article').count(), 1);
      check('static reading remains escaped', await page.evaluate(() => !!window.injected), false);
      deniedAddress = interaction.address; await setup(planned);
      check('unreadable interaction preserves readable prose in the same section', await headings(), ['视频讲解', '图文解释']);
      check('failure preserves the static reading alternative', await page.locator('.homeExplanationSupplement article').count(), 1);
      check('failure is visible rather than silently replaced', await page.getByText('当前账号无权读取此内容。', { exact: true }).count(), 1);
      deniedAddress = ''; await page.getByRole('button', { name: '重试', exact: true }).click();
      await demo().getByRole('button', { name: '沙发', exact: true }).waitFor();
      check('successful retry removes the static alternative', await page.locator('.homeExplanationSupplement').count(), 0);
      await setup(manifest('interactive-only', { ...base, supplement: undefined }));
      check('interactive-only packages need no extra prose file', await headings(), ['视频讲解', '图文解释']);
      check('reading and retries do not request generation or review', requests.filter(r => r.method === 'POST').length, 0);
      check('unified narrative has no browser errors', errors, []);
      console.log(JSON.stringify({ checks, errors }));
      return;
    }
    if (process.argv.includes('--preview-only')) {
      await setup(fixed);
      await page.evaluate(() => { adapter.destroy(); delete window.PortalHomeExplanationView; });
      const writes = requests.filter(item => item.method === 'POST').length;
      const selected = await page.evaluate(async ({ address, content }) => {
        const handle = PortalPreview.render({ root: '#root', document: {
          source: 'resource', resource_type: 'workspace', address, resource_address: address,
          contentKind: 'json', content,
        } });
        await handle.promise;
        return handle.renderer;
      }, { address: fixed.address, content: records.get(fixed.address).bytes.toString() });
      check('manifest enters native explanation through shared Preview', selected, 'explanation');
      await page.frameLocator('.homeExplanationInteraction').frameLocator('iframe').getByRole('button', { name: '沙发', exact: true }).click();
      check('preview reopen does not create media or reviews', requests.filter(item => item.method === 'POST').length, writes);
      await page.evaluate(() => PortalPreview.render({ root: '#root', document: { contentKind: 'text', content: 'next document' } }));
      check('switching Preview destroys interaction frames', await page.locator('iframe').count(), 0);
      check('switching Preview releases asset URLs', await page.evaluate(() => urls.every(url => revoked.includes(url))), true);
      check('no uncaught preview errors', errors, []);
      console.log(JSON.stringify({ checks }));
      return;
    }
    if (process.argv.includes('--captions-only')) {
      const first = '两路频率没有改变。把同样宽的窗口移到零点二五秒，频差累积了半圈相位。原来的两条曲线上下相反，合成曲线几乎压平；窗口起点恰好抵消。'.repeat(3);
      const second = '窗口继续移动，比较两条声波和它们的和。';
      const longCaptions = put('long-captions.vtt', `WEBVTT\n\n00:00.000 --> 00:01.000\n${first}\n\n00:01.000 --> 00:02.500\n${second}\n`, 'text/vtt');
      await setup(manifest('caption-layout', { ...base, video_plan: undefined, video: { ...videoRef, captions: longCaptions } }));
      await page.locator('video').evaluate(async v => { v.muted = true; await v.play(); v.pause(); v.currentTime = .3; });
      await page.waitForFunction(text => document.querySelector('.homeExplanationCaptions')?.textContent === text, first);
      const position = () => page.locator('.homeExplanationVideo').evaluate(frame => {
        const v = frame.querySelector('video'), c = frame.querySelector('.homeExplanationCaptions');
        return { below: c.getBoundingClientRect().top >= v.getBoundingClientRect().bottom,
          fits: c.scrollWidth <= c.clientWidth, font: parseFloat(getComputedStyle(c).fontSize),
          external: frame.classList.contains('homeExplanationInlineCaptions'), hidden: c.hidden, mode: v.textTracks[0].mode };
      });
      check('inline captions retain native track and sit below picture', await position(), { below: true, fits: true, font: 16, external: true, hidden: false, mode: 'showing' });
      if (out) await page.locator('.homeExplanationVideo').screenshot({ path: path.join(out, 'captions-desktop.png') });
      await page.setViewportSize({ width: 390, height: 844 });
      await page.evaluate(() => { document.documentElement.style.fontSize = '20px'; });
      check('narrow captions keep reader font without covering diagram', await position(), { below: true, fits: true, font: 20, external: true, hidden: false, mode: 'showing' });
      if (out) await page.locator('.homeExplanationVideo').screenshot({ path: path.join(out, 'captions-390.png') });
      await page.locator('video').evaluate(v => { v.currentTime = 1.4; });
      await page.waitForFunction(text => document.querySelector('.homeExplanationCaptions').textContent === text, second);
      checks.push('seek updates actual active WebVTT text');
      await page.locator('video').evaluate(v => { v.textTracks[0].mode = 'disabled'; });
      await page.waitForFunction(() => document.querySelector('.homeExplanationCaptions').hidden);
      checks.push('native CC off clears external caption');
      await page.locator('video').evaluate(v => { v.textTracks[0].mode = 'showing'; });
      await page.waitForFunction(() => !document.querySelector('.homeExplanationCaptions').hidden);
      await page.locator('.homeExplanationExpand').click();
      await page.waitForFunction(() => document.fullscreenElement?.classList.contains('homeExplanationVideo'));
      check('fullscreen keeps captions below the diagram', await position(), { below: true, fits: true, font: 20, external: true, hidden: false, mode: 'showing' });
      check('fullscreen removes native control overlay from picture', await page.locator('video').evaluate(v => v.controls), false);
      const fullControls = page.locator('.homeExplanationTransport');
      await fullControls.getByRole('button', { name: '字幕', exact: true }).click();
      await page.waitForFunction(() => document.querySelector('.homeExplanationCaptions').hidden);
      await fullControls.getByRole('button', { name: '字幕', exact: true }).click();
      await page.waitForFunction(() => !document.querySelector('.homeExplanationCaptions').hidden);
      await fullControls.getByRole('button', { name: '播放', exact: true }).click();
      await fullControls.getByRole('button', { name: '暂停', exact: true }).click();
      check('fullscreen transport actually pauses video', await page.locator('video').evaluate(v => v.paused), true);
      await fullControls.getByRole('slider', { name: '播放位置' }).evaluate(e => { e.value = '.3'; e.dispatchEvent(new Event('input', { bubbles: true })); });
      await page.waitForFunction(text => document.querySelector('.homeExplanationCaptions').textContent === text, first);
      checks.push('fullscreen seek and CC operate the real track');
      check('fullscreen exit is accessible', await page.locator('.homeExplanationExpand').getAttribute('aria-pressed'), 'true');
      await page.locator('.homeExplanationExpand').click();
      await page.waitForFunction(() => !document.fullscreenElement);
      await page.setViewportSize({ width: 844, height: 390 });
      await page.locator('.homeExplanationExpand').click();
      await page.waitForFunction(() => document.fullscreenElement?.classList.contains('homeExplanationVideo'));
      await page.locator('video').evaluate(v => { v.currentTime = .3; });
      await page.waitForFunction(text => document.querySelector('.homeExplanationCaptions').textContent === text, first);
      check('landscape fullscreen gives picture and captions separate columns', await page.locator('.homeExplanationVideo').evaluate(f => {
        const v = f.querySelector('video').getBoundingClientRect(), c = f.querySelector('.homeExplanationCaptions').getBoundingClientRect();
        return c.left >= v.right && v.width > 500 && c.bottom <= innerHeight;
      }), true);
      check('landscape fullscreen offers scrolling for long captions', await page.locator('.homeExplanationCaptions').evaluate(c => c.scrollHeight > c.clientHeight && getComputedStyle(c).overflowY === 'auto'), true);
      if (out) await page.locator('.homeExplanationVideo').screenshot({ path: path.join(out, 'captions-fullscreen.png') });
      await page.locator('.homeExplanationExpand').click();
      await page.waitForFunction(() => !document.fullscreenElement);
      check('fullscreen exit restores page layout', (await position()).external, true);
      check('fullscreen exit restores native controls', await page.locator('video').evaluate(v => v.controls), true);
      await page.locator('video').evaluate(v => v.requestFullscreen());
      await page.waitForFunction(() => !!document.fullscreenElement && !document.querySelector('.homeExplanationVideo').classList.contains('homeExplanationInlineCaptions'));
      check('native fullscreen restores native captions only', (await position()).hidden, true);
      await page.evaluate(() => document.exitFullscreen());
      await page.waitForFunction(() => document.querySelector('.homeExplanationVideo').classList.contains('homeExplanationInlineCaptions'));
      check('leaving fullscreen restores external caption', (await position()).hidden, false);
      await page.evaluate(() => adapter.destroy());
      check('destroy releases caption media URLs', await page.evaluate(() => urls.every(u => revoked.includes(u))), true);
      check('caption transitions produce no page errors', errors, []);
      return;
    }
    await setup(planned);
    if (process.argv.includes('--presentation-only')) {
      const subject = manifest('reader-presentation', {...base, quality:'high', title:'The beauty of chaos: two worlds, one rule'});
      pageReviewState = {status:'passed', feedback:'内部核SHA及检查过程不进入阅读正文。'};
      state = {status:'ready', quality:'medium', video_url:'/api/resources/content?address='+encodeURIComponent(videoRef.address),
        captions_url:'/api/resources/content?address='+encodeURIComponent(captions.address), transcript_url:'/api/resources/content?address='+encodeURIComponent(transcript.address),
        chapters:['Simple rules, intricate paths','Same start: the control','Change one millionth','Player 1: paths separate','Player 2: feedback carries the change','Structure without a repeating script'].map((title,i)=>({title,time:i*.3})), review:{status:'not_required'}};
      qualityStates={medium:state}; state=null;
      await setup(subject);
      check('reader header has neither slogan nor audit prose', await page.locator('.homeExplanationHeader').innerText().then(t=>/探索与理解|审核|SHA/.test(t)), false);
      check('audit feedback is available only on demand', await page.getByText(pageReviewState.feedback).isVisible(),false);
      check('unneeded video audit prose is absent', await page.getByText('此质量档位无需独立审核。').count(),0);
      check('Preview code typography does not leak into prose',await page.locator('#root').evaluate(el=>{const s=getComputedStyle(el);return [s.whiteSpace,s.fontFamily.includes('monospace')]}),['normal',false]);
      for (const width of [1440,390]) {
        await page.setViewportSize({width,height:1000});
        await page.evaluate(width=>{document.documentElement.style.fontSize=width===390?'24px':'16px';document.getElementById('root').scrollTop=0;},width);
        check(`reading layout ${width} has no horizontal overflow`,await page.locator('#root').evaluate(el=>el.scrollWidth<=el.clientWidth+1),true);
        check(`quality labels ${width} remain on one line`,await page.locator('.homeExplanationQuality label').evaluateAll(els=>new Set(els.map(el=>el.getBoundingClientRect().y)).size),1);
        check(`header ${width} uses normal document flow`,await page.locator('.homeExplanationHeader').evaluate(el=>parseFloat(getComputedStyle(el).paddingBottom)<=18),true);
        if(out) await page.screenshot({path:path.join(out,`reader-${width}.png`)});
      }
      await page.locator('.homeExplanationChapters button').nth(1).click();
      check('compact chapter navigation still seeks',await page.locator('video').evaluate(v=>v.currentTime>=.3),true);
      await page.locator('.homeExplanationReviewDetails summary').click();
      check('reader can explicitly inspect audit record',await page.getByText(pageReviewState.feedback).isVisible(),true);
      qualityStates=null; state={status:'awaiting_authoring',authoring:{status:'submitted'}}; await setup(subject);
      check('waiting state gives a short user-facing explanation',await page.getByText('正在准备分镜，完成后即可生成视频。').isVisible(),true);
      if(out) await page.screenshot({path:path.join(out,'waiting-390.png')});
      check('presentation reads never dispatch generation',requests.filter(r=>r.method==='POST').length,0);
      check('presentation has no script errors',errors,[]);
      if(out) fs.writeFileSync(path.join(out,'presentation-result.json'),JSON.stringify({checks,errors},null,2));
      return;
    }
    if (process.argv.includes('--authoring-only')) {
      state = { status: 'awaiting_authoring', quality: 'medium', quality_met: false,
        authoring: { status: 'submitted', id: 'a'.repeat(64) } };
      await setup(planned);
      await page.getByText('正在准备分镜，完成后即可生成视频。', { exact: true }).waitFor();
      check('authoring receipt is not a playable ready movie', await page.locator('video').count(), 0);
      await page.getByRole('button', { name: '刷新生成状态', exact: true }).click();
      check('authoring refresh does not dispatch or generate', requests.filter(r => r.method === 'POST').length, 0);
      await page.setViewportSize({ width: 390, height: 844 });
      await page.evaluate(() => { document.documentElement.style.fontSize = '20px'; });
      if (out) await page.screenshot({ path: path.join(out, 'authoring-390.png') });
      await page.getByRole('button', { name: '取消生成', exact: true }).click();
      await page.getByText('视频生成已取消。', { exact: true }).waitFor();
      check('explicit cancel pins fixed reference and quality', requests.find(r => r.url?.endsWith('/cancel')).body,
        { address: planned.address, sha256: planned.sha256, quality: 'medium' });
      const successor = manifest('successor-video', {...base, quality:'medium'});
      const original = manifest('original-high-page', {...base, quality:'high', video_plan:undefined});
      qualityStates = {medium:{status:'ready',quality:'medium',result_target:successor,
        video_url:'/api/resources/content?address='+encodeURIComponent(videoRef.address),review:{status:'not_required'}}};
      const postCount = requests.filter(r=>r.method==='POST').length;
      await setup(original);
      await page.locator('video').waitFor();
      check('opening high page discovers existing medium video', await page.getByRole('group',{name:'视频质量'}).locator(':checked').inputValue(), 'medium');
      await page.getByRole('button',{name:'查看视频对应版本',exact:true}).click();
      check('video link opens exact authored successor', await page.evaluate(()=>opens.at(-1)), {view:'explanation',target:successor});
      if(out) await page.screenshot({path:path.join(out,'authored-result-390.png')});
      await page.getByRole('group',{name:'视频质量'}).locator('input[value=high]').check();
      await page.getByRole('button',{name:'生成讲解视频',exact:true}).waitFor();
      check('explicit high selection never relabels medium movie', await page.locator('video').count(),0);
      check('result discovery and tier selection never generate',requests.filter(r=>r.method==='POST').length,postCount);
      qualityStates = null;
      check('authoring UI has no script errors', errors, []);
      if (out) fs.writeFileSync(path.join(out, 'authoring-result.json'), JSON.stringify({ checks, requests, errors }, null, 2));
      return;
    }
    if (process.argv.includes('--review-transfer-only')) {
      const pageOnly = manifest('review-transfer', { ...base, quality: 'high', video_plan: undefined });
      pageReviewState = { status: 'blocked', id: 'a'.repeat(64), feedback: '等待恢复检查前提。', evidence: [reviewEvidence],
        capabilities: { can_cancel: false, can_resume: true, can_retry: false, can_read_evidence: true },
        handoff: { evidence: { entries: [{ ...reviewEvidence, name: 'private-review.md', content_type: 'text/markdown' }] } } };
      state = null; requests.length = 0; deniedAddress = reviewEvidence.address;
      await setup(pageOnly);
      check('opening blocked does not resume or preload private evidence', requests.filter(r => r.method === 'POST' || r.url === '/api/home-explanation/reviews/file').length, 0);
      await page.getByRole('button', { name: '查看审核证据', exact: true }).click();
      await page.locator('.homeExplanationPageReview').getByText('必须补充初始条件对照。', { exact: true }).waitFor();
      check('fixed evidence uses the bounded handoff', requests.find(r => r.url === '/api/home-explanation/reviews/file').sha256, reviewEvidence.sha256);
      check('fixed evidence never opens private origin', await page.evaluate(() => opens.length), 0);
      reviewCopyDenied = true;
      await page.getByRole('button', { name: '查看审核证据', exact: true }).click();
      await page.getByText('当前账号无权读取此内容。', { exact: true }).waitFor();
      check('revoked evidence removes previously shown content', await page.getByText('必须补充初始条件对照。', { exact: true }).count(), 0);
      reviewCopyDenied = false; reviewCopyCorrupt = true;
      await page.getByRole('button', { name: '查看审核证据', exact: true }).click();
      await page.getByText('文件内容已改变，无法打开这个固定版本。', { exact: true }).waitFor();
      await page.getByRole('button', { name: '恢复独立检查', exact: true }).click();
      await page.getByText('解释页已提交独立检查，尚未通过。', { exact: true }).waitFor();
      check('explicit resume binds previous fixed review', requests.find(r => r.url === '/api/home-explanation/reviews/resume').body, { review_id: 'a'.repeat(64) });
      check('resume does not generate media or claim passed', requests.filter(r => r.url === '/api/home-explanation/media' && r.method === 'POST').length, 0);
      check('resume never claims passed', await page.getByText('此固定版本已通过独立审核。').count(), 0);
      const readerCapabilities = { can_cancel: false, can_resume: false, can_retry: false, can_read_evidence: false };
      for (const status of ['blocked', 'submitted', 'submission_failed', 'unavailable']) {
        pageReviewState = { id: 'b'.repeat(64), status, feedback: '请补充实际演化轨迹和初值对照。', capabilities: readerCapabilities,
          evidence: [reviewEvidence] };
        requests.length = 0;
        await setup(pageOnly);
        await page.locator('.homeExplanationPageReview').getByText(pageReviewState.feedback, { exact: true }).waitFor();
        check(`${status}: artifact reader sees authenticated feedback`, await page.locator('.homeExplanationPageReview').getByText(pageReviewState.feedback).count(), 1);
        check(`${status}: artifact reader has no review mutation or evidence buttons`, await page.locator('.homeExplanationPageReview').getByRole('button').allTextContents(), status === 'submitted' ? ['刷新审核结果'] : []);
        check(`${status}: artifact reader does not fetch evidence or mutate`, requests.filter(r => r.method === 'POST' || r.url === '/api/home-explanation/reviews/file').length, 0);
        if (status === 'submitted') {
          pageReviewReadDenied = true;
          await page.locator('.homeExplanationPageReview').getByRole('button', { name: '刷新审核结果', exact: true }).click();
          await page.locator('.homeExplanationPageReview').getByText('HTTP 403', { exact: true }).waitFor();
          check('refresh rejection preserves reader capabilities and only permits reads', await page.locator('.homeExplanationPageReview').getByRole('button').allTextContents(), ['刷新审核结果', '重试']);
          pageReviewReadDenied = false;
        }
      }
      await page.setViewportSize({ width: 390, height: 844 });
      if (out) await page.screenshot({ path: path.join(out, 'review-transfer-390.png') });
      check('review transfer has no script errors', errors, []);
      if (out) fs.writeFileSync(path.join(out, 'review-transfer-result.json'), JSON.stringify({ checks, requests, errors }, null, 2));
      return;
    }
    if (process.argv.includes('--page-workflow-only')) {
      const pageOnly = manifest('page-without-plan', { ...base, quality: 'high', video_plan: undefined });
      state = null; requests.length = 0;
      await setup(pageOnly);
      await page.getByRole('button', { name: '生成讲解视频', exact: true }).waitFor();
      check('opening without a plan does not create audio, video or review work', requests.filter(r => r.method === 'POST').length, 0);
      await page.getByText('解释页尚未经过独立检查，不能视为高质量达标。', { exact: true }).waitFor();
      await page.getByRole('button', { name: '提交解释页独立检查', exact: true }).click();
      await page.getByText('解释页已提交独立检查，尚未通过。', { exact: true }).waitFor();
      check('explicit review submits once', requests.filter(r => r.url === '/api/home-explanation/reviews' && r.method === 'POST').length, 1);
      pageReviewState = { status: 'changes_requested', feedback: '缺少可操作的反例与因果解释。', evidence: [supplement] };
      await page.locator('.homeExplanationPageReview').getByRole('button', { name: '刷新审核结果' }).click();
      await page.getByText(pageReviewState.feedback, { exact: true }).waitFor();
      await page.getByRole('button', { name: '查看审核证据', exact: true }).click();
      check('evidence uses native authenticated preview', await page.evaluate(() => opens.at(-1)), { view: 'preview', target: { address: supplement.address } });
      await page.getByRole('button', { name: '生成讲解视频', exact: true }).click();
      check('video request without plan retains high quality', requests.find(r => r.url === '/api/home-explanation/media' && r.method === 'POST').body,
        { ...pageOnly, quality: 'high' });
      state = { status: 'awaiting_authoring', authoring: { status: 'submitted' } };
      await setup(pageOnly);
      await page.getByText('正在准备分镜，完成后即可生成视频。', { exact: true }).waitFor();
      check('reopen retains feedback without resubmitting', requests.filter(r => r.url === '/api/home-explanation/reviews' && r.method === 'POST').length, 1);
      await page.setViewportSize({ width: 390, height: 844 });
      await page.evaluate(() => { document.documentElement.style.fontSize = '20px'; });
      check('narrow explanation stays within viewport', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      if (out) await page.screenshot({ path: path.join(out, 'page-review-390.png') });
      check('page workflow has no script errors', errors, []);
      if (out) fs.writeFileSync(path.join(out, 'page-workflow-result.json'), JSON.stringify({ checks, requests, errors }, null, 2));
      return;
    }
    if (process.argv.includes('--invalidated-only')) {
      state = { status: 'ready', quality: 'high', quality_met: false,
        video_url: '/api/resources/content?address='+encodeURIComponent(videoRef.address),
        review: { status: 'invalidated' } };
      await setup(manifest('invalidated-quality', { ...base, quality: 'high' }));
      await page.getByText('固定产物已变化，原审核通过结论失效；请保存新固定版本后重新送审。', { exact: true }).waitFor();
      check('invalidated does not offer same-version resubmission', await page.locator('.homeExplanationVideoReview button').count(), 0);
      check('read does not replay generation or review', requests.filter(r => r.method === 'POST').length, 0);
      check('invalidated UI has no script errors', errors, []);
      if (out) fs.writeFileSync(path.join(out, 'invalidated-result.json'), JSON.stringify({ checks, requests, errors }, null, 2));
      return;
    }
    if (process.argv.includes('--quality-only')) {
      const quality = page.getByRole('group', {name:'视频质量'});
      const choose = value => quality.locator(`input[value=${value}]`).check();
      check('initial read medium without POST', [await quality.locator('input:checked').inputValue(), requests.filter(r => r.method === 'POST').length], ['medium', 0]);
      for (const value of ['low', 'high', 'medium']) {
        await choose(value);
        await page.waitForTimeout(80);
        check('selection '+value+' is read only', requests.filter(r => r.method === 'POST').length, 0);
        check('GET binds quality '+value, requests.filter(r => r.url === '/api/home-explanation/media').at(-1).quality, value);
      }
      await choose('high'); await page.waitForTimeout(80);
      await page.getByRole('button', { name: '生成讲解视频', exact: true }).click();
      await page.waitForTimeout(120);
      check('explicit click carries fixed version and high', requests.filter(r => r.method === 'POST').at(-1).body,
        { address: planned.address, sha256: planned.sha256, quality: 'high' });
      state = { status: 'ready', quality: 'high', quality_met: false, video_url: '/api/resources/content?address='+encodeURIComponent(videoRef.address),
        review: { status: 'changes_requested', feedback: '请补充因果对照，并保存新固定版本。' } };
      const high = manifest('high-quality', { ...base, quality: 'high' });
      await setup(high);
      await page.getByText('审核要求修改；请保存新固定版本后重新送审。', { exact: true }).waitFor();
      check('byte ready is not review passed', await page.getByText('此固定版本已通过独立审核。').count(), 0);
      check('reopen does not resubmit', requests.filter(r => r.method === 'POST').length, 1);
      if (out) await page.screenshot({ path: path.join(out, 'quality-desktop.png') });
      await page.setViewportSize({ width: 390, height: 844 });
      await page.evaluate(() => { document.documentElement.style.fontSize = '20px'; });
      if (out) await page.screenshot({ path: path.join(out, 'quality-390.png') });
      check('narrow quality radios inside viewport', await quality.evaluate(el => { const r=el.getBoundingClientRect(); return r.left>=0 && r.right<=innerWidth && r.height>=44; }), true);
      const count = requests.filter(r => r.method === 'POST').length;
      await choose('low'); await page.waitForTimeout(100);
      check('quality switch does not replay generation', requests.filter(r => r.method === 'POST').length, count);
      check('no script errors', errors, []);
      if (out) fs.writeFileSync(path.join(out, 'quality-result.json'), JSON.stringify({ checks, requests, errors }, null, 2));
      return;
    }
    if (process.argv.includes('--review-only')) {
      state = { status: 'ready', job_id: 'fixture-job', video_url: '/api/resources/content?address=' + encodeURIComponent(videoRef.address) };
      await setup(planned);
      check('legacy ready media does not submit review on read', requests.filter(r => r.method === 'POST').length, 0);
      await page.getByText('视频尚未经过独立检查。', { exact: true }).waitFor();
      await page.locator('video').evaluate(v => { v.pause(); v.dataset.stablePlayer = 'yes'; });
      reviewDenied = true;
      await page.getByRole('button', { name: '检查视频质量', exact: true }).click();
      await page.getByRole('button', { name: '重试视频检查', exact: true }).waitFor();
      check('failed review keeps the saved player', await page.locator('video').getAttribute('data-stable-player'), 'yes');
      reviewDenied = false;
      await page.getByRole('button', { name: '重试视频检查', exact: true }).click();
      await page.getByText('视频已提交独立检查，结果待回读。', { exact: true }).waitFor();
      check('successful review submission keeps player and does not claim passed', await page.locator('video').getAttribute('data-stable-player'), 'yes');
      check('review retries never regenerate media', requests.filter(r => r.method === 'POST' && r.url === '/api/home-explanation/media').length, 0);
      state.review = { status: 'pending' }; await setup(planned);
      check('pending review has no duplicate submit button', await page.locator('.homeExplanationVideoReview button').count(), 0);
      await page.locator('video').evaluate(v => { v.dataset.reviewPollPlayer = 'same'; });
      state.review = { status: 'submitted' };
      await page.getByText('视频已提交独立检查，结果待回读。', { exact: true }).waitFor();
      check('pending review polling updates only review UI', await page.locator('video').getAttribute('data-review-poll-player'), 'same');
      const reviewPollCount = requests.length; await page.waitForTimeout(2700);
      check('submitted review stops status polling', requests.length, reviewPollCount);
      state.review = { status: 'not_requested' }; delayReview = true; await setup(planned);
      await page.getByRole('button', { name: '检查视频质量', exact: true }).click();
      await page.waitForTimeout(50); await page.evaluate(() => adapter.destroy()); reviewRelease?.();
      await page.waitForTimeout(50);
      check('late review response after destroy cannot remount', await page.locator('#root').evaluate(el => el.childElementCount), 0);
      check('review UI has no browser errors', errors, []);
      return;
    }
    check('no implicit generation', requests.filter(r => r.method === 'POST').length, 0);
    check('plan opens one interactive narrative without a duplicated chapter', await page.locator('.homeExplanationSupplement article').count(), 0);
    check('native Home host gives interactive diagram the reading width', await page.locator('.homeExplanationInteraction').evaluate(el => el.getBoundingClientRect().width > Math.min(800, innerWidth * .7)), true);
    check('normal markdown stays escaped', await page.evaluate(() => !!window.injected), false);
    const demo = () => page.frameLocator('.homeExplanationInteraction').frameLocator('iframe');
    await demo().getByRole('button', { name: '沙发', exact: true }).click();
    check('local interactive action works', await demo().locator('#answer').textContent(), '沙发 · 20%');
    await demo().getByRole('button', { name: '隔离检查' }).click();
    await demo().locator('#safe').filter({ hasText: 'true,true,true' }).waitFor();
    check('opaque origin denies parent, storage and network', await demo().locator('#safe').textContent(), 'true,true,true');
    if (out) { await page.locator('#root').evaluate(el => { el.scrollTop = 0; }); await page.screenshot({ path: path.join(out, 'planned-desktop.png'), fullPage: true }); }
    await page.getByRole('button', { name: '生成讲解视频', exact: true }).click();
    await page.getByText('讲解视频正在生成，可以先探索交互和图文。', { exact: true }).waitFor();
    check('only explicit button submits generation', requests.filter(r => r.method === 'POST').length, 1);
    await page.evaluate(() => adapter.setPresented(false));
    const count = requests.length; await page.waitForTimeout(2700);
    check('hidden generation has no polling', requests.length, count);
    check('hidden page destroys active demos', await page.locator('iframe').count(), 0);
    state = { status: 'ready', job_id: 'fixture-job', video_url: '/api/resources/content?address=' + encodeURIComponent(videoRef.address),
      captions_url: '/api/resources/content?address=' + encodeURIComponent(captions.address), chapters: [{ time: 1, title: '汇总' }] };
    await page.evaluate(() => adapter.setPresented(true));
    await page.locator('video track').waitFor({ state: 'attached' });
    check('persisted ready media restores without POST', requests.filter(r => r.method === 'POST').length, 1);
    await page.locator('video').evaluate(async video => { await video.play(); });
    await page.waitForFunction(() => document.querySelector('video').currentTime > .2);
    await page.getByRole('button', { name: /汇总/ }).click();
    check('chapter changes the actual player time', await page.locator('video').evaluate(v => v.currentTime >= 1), true);
    await page.locator('video').evaluate(v => { v.pause(); v.textTracks[0].mode = 'showing'; v.currentTime = .5; });
    await page.waitForFunction(() => document.querySelector('video').textTracks[0].activeCues?.length === 1);
    check('actual WebVTT cue is active', await page.locator('video').evaluate(v => v.textTracks[0].activeCues[0].text), '查询与键匹配，再按权重汇总。');
    await page.evaluate(() => adapter.setPresented(false));
    check('hide revokes all Blob URLs', await page.evaluate(() => urls.every(u => revoked.includes(u))), true);
    const beforeReopen = requests.length; await page.evaluate(() => adapter.setPresented(true));
    check('reopen rechecks manifest, assets and backend state', requests.length > beforeReopen, true);
    await setup(fixed);
    await page.locator('video').evaluate(v => v.pause());
    if (out) { await page.locator('#root').evaluate(el => { el.scrollTop = 0; }); await page.screenshot({ path: path.join(out, 'ready-desktop.png'), fullPage: true }); }
    check('existing fixed video has native transcript', await page.getByText('阅读讲稿', { exact: true }).count(), 1);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.evaluate(() => { document.documentElement.style.fontSize = '20px'; document.documentElement.dataset.portalTheme = 'dark'; });
    await setup(fixed);
    await demo().getByRole('button', { name: '小猫', exact: true }).click();
    if (out) {
      await page.screenshot({ path: path.join(out, 'interaction-390-font20-dark.png'), fullPage: true });
      await page.locator('#root').evaluate(el => { el.scrollTop = 0; });
      await page.screenshot({ path: path.join(out, 'ready-390-font20-dark.png'), fullPage: true });
    }
    const overflow = await page.locator('#root').evaluate(root => ({ width: root.clientWidth, scroll: root.scrollWidth,
      elements: [...root.querySelectorAll('*')].filter(el => el.getBoundingClientRect().right > root.clientWidth).map(el => [el.tagName, el.className, el.getBoundingClientRect().right]) }));
    if (overflow.scroll > overflow.width) console.error(JSON.stringify(overflow));
    check('390 large-font root has no horizontal overflow', await page.locator('#root').evaluate(el => el.scrollWidth <= el.clientWidth), true);
    check('interactive keyboard control usable', await demo().getByRole('button', { name: '小猫', exact: true }).evaluate(el => { el.focus(); return document.activeElement === el; }), true);
    check('demo receives reduced-motion preference', await demo().locator('body').evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches), true);
    await demo().getByRole('button', { name: '沙发', exact: true }).focus();
    await demo().getByRole('button', { name: '沙发', exact: true }).press('Enter');
    check('keyboard activation updates the actual demonstration', await demo().locator('#answer').textContent(), '沙发 · 20%');
    await demo().locator('body').evaluate(() => { location.href = '/escape-navigation'; });
    await page.waitForTimeout(100);
    check('navigation cannot escape through own frame', requests.filter(r => r.forbidden?.startsWith('/escape')).length, 0);
    const attacking = put('network.html', '<img src="/escape-image"><style>body{background-image:url(/escape-css)}</style><button id="popup">popup</button><script>document.querySelector("#popup").onclick=()=>window.open("/escape-popup");fetch("/escape-fetch").catch(()=>{});</script>', 'text/html');
    await setup(manifest('network', { ...base, video_plan: undefined, interaction: attacking }));
    await demo().getByRole('button', { name: 'popup' }).click();
    await page.waitForTimeout(100);
    check('HTML parse, CSS, scripts and popups cannot send network requests', requests.filter(r => r.forbidden?.startsWith('/escape')).length, 0);
    check('sandbox cannot create a popup window', page.context().pages().length, 1);
    deniedAddress = interaction.address; await setup(fixed);
    check('one unreadable module retains other material', await page.locator('.homeExplanationSupplement article').count(), 1);
    check('unreadable module shows retry', await page.getByText('当前账号无权读取此内容。', { exact: true }).count(), 1);
    deniedAddress = '';
    const corrupt = manifest('corrupt', { ...base, video_plan: undefined, interaction: { ...interaction, sha256: '0'.repeat(64) } });
    await setup(corrupt);
    check('asset hash mismatch is readable failure', await page.getByText('文件内容已改变，无法打开这个固定版本。', { exact: true }).count(), 1);
    await setup({ ...fixed, sha256: '0'.repeat(64) });
    check('manifest hash mismatch renders no modules', await page.locator('.homeExplanationSection').count(), 0);
    records.get(videoRef.address).declaredSize = 65 * 1024 * 1024;
    await setup(fixed);
    check('oversized video refused, prose retained', await page.getByText('文件超过解释页的读取上限。', { exact: true }).count(), 1);
    delete records.get(videoRef.address).declaredSize;
    deny = true; await setup(fixed);
    check('revoked manifest hides all old content', await page.locator('.homeExplanationSection').count(), 0);
    deny = false; await page.getByRole('button', { name: '重试', exact: true }).click();
    await page.locator('.homeExplanationInteraction').waitFor();
    check('retry uses fresh authorization', await page.locator('.homeExplanationInteraction').count(), 1);
    state = { status: 'generating', job_id: 'fixture-job' }; await setup(planned);
    const pollCount = requests.filter(r => r.url).length;
    state = { status: 'ready', job_id: 'fixture-job', video_url: '/api/resources/content?address=' + encodeURIComponent(videoRef.address) };
    await page.locator('video').waitFor({ state: 'attached' });
    check('current generating job polls to ready', requests.filter(r => r.url).length > pollCount, true);
    const readyPollCount = requests.filter(r => r.url).length; await page.waitForTimeout(2700);
    check('ready result stops job polling', requests.filter(r => r.url).length, readyPollCount);
    const requestCount = requests.length;
    await page.evaluate(() => { viewer = 'other-resident'; window.dispatchEvent(new Event('portal:identity-changed')); });
    check('identity switch never fetches old history as new actor', requests.length, requestCount);
    check('identity switch discards content', await page.locator('video,iframe,article').count(), 0);
    await page.evaluate(() => { viewer = 'fixture'; });
    delayed = interaction.address;
    await page.evaluate(target => { adapter.destroy(); adapter = PortalHomeExplanationView.create({ root: document.getElementById('root') }); window.pending = adapter.show(target); }, fixed);
    await page.waitForFunction(() => !!document.querySelector('.homeExplanationHeader'));
    await page.evaluate(() => adapter.destroy()); releases.forEach(release => release()); releases = []; delayed = '';
    await page.evaluate(() => pending);
    check('late reads after destroy cannot remount', await page.locator('#root').evaluate(el => el.childElementCount), 0);
    check('destroy releases all object URLs', await page.evaluate(() => urls.every(u => revoked.includes(u))), true);
    const touch = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true });
    const touchPage = await touch.newPage(); await touchPage.goto('http://127.0.0.1:' + server.address().port);
    await touchPage.evaluate(async target => { PortalIdentity = { username: () => 'fixture-touch' }; window.adapter = PortalHomeExplanationView.create({ root: document.getElementById('root') }); await adapter.show(target); }, planned);
    const touchDemo = touchPage.frameLocator('.homeExplanationInteraction').frameLocator('iframe');
    await touchDemo.getByRole('button', { name: '沙发', exact: true }).tap();
    check('actual touch interaction works', await touchDemo.locator('#answer').textContent(), '沙发 · 20%');
    await touchPage.evaluate(() => adapter.destroy()); await touch.close();
    const largeDemo = put('large-demo.html', '<button id="large">large local demo</button><!--' + ' '.repeat(1024 * 1024) + '-->', 'text/html');
    const cryptoState = await page.evaluate(() => {
      window.originalSubtle = crypto.subtle;
      Object.defineProperty(crypto, 'subtle', { configurable: true, value: undefined });
      return typeof crypto.subtle;
    });
    check('LAN HTTP fallback precondition', cryptoState, 'undefined');
    await setup(manifest('worker-hash', { schema: base.schema, revision: 1, title: 'Large local demo', interaction: largeDemo }));
    check('large byte integrity succeeds through worker fallback', await demo().getByRole('button', { name: 'large local demo' }).count(), 1);
    await page.evaluate(() => { Object.defineProperty(crypto, 'subtle', { configurable: true, value: originalSubtle }); adapter.destroy(); });
    check('worker and document Blob URLs released', await page.evaluate(() => urls.every(u => revoked.includes(u))), true);
    check('no uncaught JS errors', errors, []);
  } finally {
    if (out) fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify({ boundary: 'native components; isolated transport and resident identities; no live User/model chain', checks, errors, requests }, null, 2));
    await browser.close(); await new Promise(resolve => server.close(resolve));
  }
  console.log(JSON.stringify({ checks, errors }));
})().catch(error => { console.error(error); process.exitCode = 1; });
