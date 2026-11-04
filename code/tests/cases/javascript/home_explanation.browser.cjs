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
const css = ['portal_themes.css', 'portal_design_system.css', 'portal_components.css', 'portal_components_markdown.css', 'home_explanation.css'];
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
let state = null, deny = false, deniedAddress = '', delayed = '', releases = [], videoRef;
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://fixture');
  if (url.pathname === '/') {
    res.writeHead(200, { 'Content-Type': 'text/html' });
    res.end('<!doctype html><html lang="zh-CN" data-portal-theme="light"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' + css.map(n => `<link rel="stylesheet" href="/static/${n}">`).join('')
      + '<style>html,body{margin:0;height:100%;font-family:system-ui}#root{height:100dvh;width:100%}</style><main id="root"></main>'
      + scripts.map(n => `<script src="/static/${n}"></script>`).join('')); return;
  }
  if (url.pathname.startsWith('/static/')) {
    const file = path.resolve(root, url.pathname.slice(8));
    if (file.startsWith(root + '/') && fs.existsSync(file)) { res.writeHead(200, { 'Content-Type': file.endsWith('.css') ? 'text/css' : 'text/javascript' }); res.end(fs.readFileSync(file)); return; }
  }
  if (url.pathname === '/api/home-explanation/media') {
    requests.push({ url: url.pathname, method: req.method });
    if (req.method === 'POST') state = { status: 'generating', job_id: 'fixture-job' };
    res.writeHead(deny ? 403 : 200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(deny ? { error: 'no permit' } : { media: state })); return;
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
    const setup = async target => page.evaluate(async target => {
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
    await setup(planned);
    check('no implicit generation', requests.filter(r => r.method === 'POST').length, 0);
    check('plan opens native supplement and interactive page', await page.locator('.homeExplanationSupplement article').count(), 1);
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
    await page.locator('.homeExplanationSupplement article').waitFor();
    check('retry uses fresh authorization', await page.locator('.homeExplanationSupplement article').count(), 1);
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
    delayed = supplement.address;
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
