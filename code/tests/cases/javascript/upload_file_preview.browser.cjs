'use strict';
// One isolated page exercises the shared file contract and actual Preview renderers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require('../../../management_portal/test_framework/tau_runtime/node_modules/playwright');
const root = path.resolve(__dirname, '../../../management_portal/static');
const scripts = ['portal_preview_contract', 'portal_instruction_client', 'portal_file_links',
  'portal_preview_runtime', 'portal_preview_renderers', 'portal_agent_preview_source',
  'chat_workspace_message_content', 'chat_workspace_composer', 'chat_workspace_composer_attachments'];
const ids = ['1', '2', '3'].map(end => 'upload_' + end.padStart(24, '0'));
const zip = Buffer.from('PK-isolated-upload-download');
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aK1sAAAAASUVORK5CYII=', 'base64');
const fixtures = new Map([
  [ids[0], { name: '鼓与吉他.zip', kind: 'binary', content_type: 'application/zip', size: zip.length, binary: true }],
  [ids[1], { name: '说明.txt', kind: 'text', content_type: 'text/plain', size: 12, binary: false, text: 'upload text' }],
  [ids[2], { name: '像素.png', kind: 'image', content_type: 'image/png', size: png.length, binary: true }],
]);
const calls = [], downloads = [];
const html = `<!doctype html><meta charset="utf-8"><div id="links"></div><div id="preview"></div><div id="tray"></div><div id="input" contenteditable="true"></div>
<script>
const template = (parts, ...args) => parts.reduce((s, part, i) => s + part + (args[i] ?? ''), '');
window.PortalI18n = { t: text => text, message: text => text, html: template, text: template };
window.PortalUIPrimitives = { randomUUID: () => crypto.randomUUID(), escapeHtml: value => String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;') };
window.PortalPdfPreview = {}; window.PortalResidentService = {};
window.PortalUI = { copyText: value => { window.copied = value; }, toast: message => { throw Error(message); } };
window.PortalOverlay = { bindFloatingPopover: ({ panel }) => ({ close() { panel.hidden = true; }, open() { panel.hidden = false; } }) };
window.PortalAPI = {
  async requestResult(url, options) {
    const response = await fetch(url, { ...options, headers: {...options.headers, 'Content-Type': 'application/json'}, body: JSON.stringify(options.body) });
    return { response, payload: await response.json() };
  },
  async get(url) { const response = await fetch(url); if (!response.ok) throw Error('Read failed'); return response.json(); }
};
</script>${scripts.map(name => '<script src="/static/' + name + '.js"></script>').join('')}`;

async function bodyJson(request) {
  let data = ''; for await (const chunk of request) data += chunk;
  return JSON.parse(data);
}

const server = http.createServer(async (request, response) => {
  try {
    const url = new URL(request.url, 'http://fixture');
    if (url.pathname.startsWith('/api/')) assert.equal(request.headers.cookie, 'fixture_session=isolated');
    const send = value => { response.setHeader('Content-Type', 'application/json'); response.end(JSON.stringify(value)); };
    if (url.pathname === '/api/instructions/execute') {
      const call = await bodyJson(request); calls.push(call);
      let result;
      if (call.instruction === 'CreateUpload') result = { upload_id: ids[0], upload_url: '/api/uploads/' + ids[0] };
      else {
        assert.equal(call.instruction, 'ReadResource');
        const id = call.arguments.address.upload_id;
        assert.deepEqual(Object.keys(call.arguments.address), ['upload_id']);
        assert.ok(fixtures.has(id));
        const { text, ...metadata } = fixtures.get(id);
        result = { ...metadata, file_reference: {upload_id: id}, content_url: '/api/uploads/' + id,
          ...(call.arguments.meta_only ? {} : {text}) };
        if (!call.arguments.meta_only) {
          assert.equal(call.arguments.preview, true);
          assert.equal(call.arguments.max_bytes, 2 * 1024 * 1024);
        }
      }
      return send({status: 'committed', result});
    }
    if (url.pathname === '/api/agent-workbench/link-file') {
      assert.equal(url.searchParams.get('path'), '/srv/普通文件.txt');
      return send({name: '普通文件.txt', path: '/srv/普通文件.txt', kind: 'text', content: 'local text'});
    }
    if (url.pathname.startsWith('/api/uploads/')) {
      const id = url.pathname.split('/').at(-1);
      assert.ok(fixtures.has(id));
      if (request.method === 'PUT') { for await (const _ of request) {} return send({ upload_id: id, ready: true }); }
      const data = id === ids[2] ? png : zip;
      if (url.searchParams.get('download') === '1') {
        downloads.push(id);
        response.setHeader('Content-Disposition', "attachment; filename=fixture.zip; filename*=UTF-8''" + encodeURIComponent(fixtures.get(id).name));
      }
      response.setHeader('Content-Type', fixtures.get(id).content_type);
      response.setHeader('Content-Length', data.length);
      return response.end(data);
    }
    const script = scripts.find(name => url.pathname === '/static/' + name + '.js');
    response.setHeader('Content-Type', script ? 'text/javascript' : 'text/html');
    response.end(script ? fs.readFileSync(path.join(root, script + '.js')) : html);
  } catch (error) { response.statusCode = 500; response.end(String(error.stack)); }
});

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  let browser;
  try {
    browser = await chromium.launch({headless: true});
    const context = await browser.newContext();
    await context.addCookies([{name: 'fixture_session', value: 'isolated', url: origin}]);
    const page = await context.newPage();
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin);
    const facts = await page.evaluate(async ids => {
      const check = (value, message) => { if (!value) throw Error(message); };
      const links = PortalFileLinks, relative = '/api/uploads/' + ids[0];
      for (const input of [relative, location.origin + relative, relative + '?download=1', {upload_id: ids[0]}]) {
        const file = links.describe(input);
        check(file.path === relative && file.file_reference.upload_id === ids[0], 'canonical upload descriptor');
        check(links.downloadUrl(input) === relative + '?download=1', 'canonical download');
        check(links.imagePreviewUrl(input) === relative, 'shared image URL');
      }
      for (const input of ['https://other.invalid' + relative, '//'+location.host+relative, location.origin.replace('://', '://user:pass@')+relative,
        relative + '#fragment', relative + '#', relative+'?x=1', relative+'?download=1&download=1', relative+'?download=0',
        relative+'?download=1&', relative+'?preview=pdf', relative+'/', relative+'/../'+ids[0], relative.replace('upload_', '%75pload_'),
        '/api/uploads/upload_123', relative + '\\file.txt']) {
        check(links.describe(input, {allowAnyLocalPath: true}) === null, 'reject ambiguous upload: '+input);
        check(!links.imagePreviewUrl(input), 'do not map outside URL to local upload: '+input);
        check(await links.probe(input) === null, 'invalid upload must not reach filesystem endpoint');
      }
      check(links.describe('/srv/普通文件.txt').path === '/srv/普通文件.txt', 'ordinary local descriptor');
      check((await links.load('/srv/普通文件.txt')).content === 'local text', 'ordinary local content');
      window.PortalResourceChips = {portalReferenceFromPortalHref: input => input === '/r/workspace_0123456789ab' ? input : ''};
      check(links.classifyLocator('/r/workspace_0123456789ab').kind === 'resource', 'resource links retained');
      delete window.PortalResourceChips;
      const source = PortalAgentPreviewSource.create({apiGet: PortalAPI.get, apiPost: () => {}, linkFileUrl: links.apiUrl});
      const zip = source.normalize(await links.load(location.origin + relative), {source: 'workspace_link'});
      check(zip.title === '鼓与吉他.zip' && zip.downloadName === zip.title && zip.kind === 'binary', 'actual metadata');
      check(zip.capabilities.edit === false && zip.file_reference.upload_id === ids[0], 'upload stays read-only file reference');
      check((await source.fetchMeta(zip)).name === zip.name, 'refresh metadata stays in shared owner');
      check((await source.reload(zip)).title === zip.title, 'reload stays in shared owner');
      window.openFile = async request => {
        const document = await links.load(request.path);
        await PortalPreview.render({root: '#preview', document}).promise;
        return true;
      };
      PortalPreview.registerHost('fixture', openFile);
      document.querySelector('#links').innerHTML = '<a href="'+location.origin+relative+'">ZIP</a>';
      links.bind(document.querySelector('#links'));
      document.querySelector('#links a').click();
      return {title: zip.title, size: zip.size};
    }, ids);
    await page.waitForSelector('#preview a');
    const fallback = await page.locator('#preview').innerText();
    assert.ok(fallback.includes(facts.title) && fallback.includes('application/zip') && fallback.includes(facts.size + ' B'));
    assert.ok(fallback.includes('不支持预览'));
    let pending = page.waitForEvent('download');
    await page.locator('#preview a').click();
    let download = await pending;
    assert.equal(download.suggestedFilename(), facts.title);
    assert.deepEqual(fs.readFileSync(await download.path()), zip);
    await page.evaluate(() => {
      const anchor = document.querySelector('#links a');
      anchor.dispatchEvent(new MouseEvent('contextmenu', {bubbles:true}));
      document.querySelector('[data-preview-file-action="copy-path"]').click();
    });
    assert.equal(await page.evaluate(() => window.copied), '/api/uploads/'+ids[0]);
    pending = page.waitForEvent('download');
    await page.evaluate(() => {
      document.querySelector('#links a').dispatchEvent(new MouseEvent('contextmenu', {bubbles:true}));
      document.querySelector('[data-preview-file-action="download"]').click();
    });
    download = await pending;
    assert.equal(download.suggestedFilename(), facts.title);
    const final = await page.evaluate(async ids => {
      const check = (value, message) => { if (!value) throw Error(message); };
      await openFile({path: '/api/uploads/'+ids[1]});
      check(document.querySelector('#preview').textContent.includes('upload text'), 'text renderer');
      await openFile({path: '/api/uploads/'+ids[2]});
      await document.querySelector('#preview img').decode();
      const uploaded = await PortalChatWorkspaceComposerAttachments.defaultWorkspaceUpload(new File(['zip'], 'client.zip', {type: 'application/octet-stream'}));
      check(uploaded.filename === '鼓与吉他.zip' && uploaded.content_type === 'application/zip', 'upload uses server metadata');
      check(uploaded.file_reference.upload_id === ids[0] && uploaded.path === '/api/uploads/'+ids[0], 'upload returns persistent reference');
      const message = PortalChatWorkspaceMessageContent;
      const restored = PortalChatWorkspaceComposer.normalizeComposerAttachment({upload_id: ids[0], name: '鼓与吉他.zip', content_type:'application/zip'});
      check(restored.path === uploaded.path && restored.file_reference.upload_id === ids[0], 'restored draft has same descriptor');
      check(message.mediaPartPath(restored) === uploaded.path, 'message uses shared descriptor');
      check(message.attachmentPreviewUrl({upload_id: ids[2]}) === '/api/uploads/'+ids[2], 'restored image preview');
      const composer = PortalChatWorkspaceComposer.createComposer({attachmentKind: message.attachmentKind, attachmentPreviewUrl: message.attachmentPreviewUrl,
        bindImagePreview() {}, bindResourceChips() {}, escapeHtml: PortalUIPrimitives.escapeHtml, messageAvatarPlaceholderHtml() {return '';} });
      composer.renderComposerAttachmentTray(document.querySelector('#tray'), [restored]);
      document.querySelector('#tray [data-portal-file-path]').click();
      composer.setComposerDraftInput(document.querySelector('#input'), '[[portal-file:'+restored.id+']]', [restored], {focus:false});
      check(document.querySelector('#input [data-portal-file-path]'), 'restored composer token is a file button');
      return {path: restored.path};
    }, ids);
    await page.waitForSelector('#preview a');
    assert.equal(final.path, '/api/uploads/'+ids[0]);
    assert.equal(page.url(), origin+'/');
    assert.deepEqual(errors, []);
    assert.deepEqual(downloads, [ids[0], ids[0]]);
    assert.ok(calls.some(call => call.arguments.meta_only));
    console.log('PASS unified upload URL validation, local/resource regression, ZIP metadata/download/menu, text/image, upload metadata, restored composer attachments');
  } finally {
    await browser?.close(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
