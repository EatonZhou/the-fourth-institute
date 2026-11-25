'use strict';
// Exercise the real source boundaries. Only authorized I/O answers are fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('', { url: 'https://portal.test/', runScripts: 'outside-only' });
const w = dom.window;
const load = name => w.eval(fs.readFileSync(path.resolve(__dirname, '../../../management_portal/static', name + '.js'), 'utf8'));
w.PortalI18n = { t: String };
w.PortalUIPrimitives = { escapeHtml: String, registerEnhancer() {} };
w.PortalCompatibility = { schemaMatches: (value, expected) => value === expected };
w.PortalResourceRoutes = { schema: 'tfi.resource-portal-routes.v1', text_resource_types: [],
  navigation_resource_types: [], identity_parameters: [], routes: [] };
load('portal_preview_contract');
load('portal_resource_chips');
load('portal_file_links');
load('portal_resident_service');
load('portal_agent_preview_source');

(async () => {
  const address = 'workspace_0123456789ab/file/slides.pptx';
  const resource = await w.PortalResourceChips.loadPreview(address, { reference: {
    resource_id: 'workspace_0123456789ab', filename: 'slides.pptx', content_kind: 'binary',
  } });
  const resourceUrl = new URL(resource.preview_url, w.location);
  assert.equal(resourceUrl.pathname, '/api/resources/content');
  assert.equal(resourceUrl.searchParams.get('address'), address);
  assert.equal(resourceUrl.searchParams.get('preview'), 'pdf');
  assert.equal(new URL(resource.download_url, w.location).searchParams.get('download'), '1');
  assert.equal(new URL(resource.raw_url, w.location).searchParams.has('preview'), false);
  for (const name of ['draft.docx', '合同.DOC']) {
    const word = await w.PortalResourceChips.loadPreview('workspace_0123456789ab/file/' + name, {
      reference: { filename: name, content_kind: 'binary' },
    });
    assert.equal(new URL(word.preview_url, w.location).searchParams.get('preview'), 'pdf');
    assert.equal(new URL(word.raw_url, w.location).searchParams.has('preview'), false);
  }
  const other = await w.PortalResourceChips.loadPreview('workspace_0123456789ab/file/sheet.xlsx', {
    reference: { filename: 'sheet.xlsx', content_kind: 'binary' },
  });
  assert.equal(other.preview_url, '');

  const metadata = { kind: 'binary', name: '幻灯片.pptx', binary: true,
    content_url: '/api/uploads/upload_0123456789abcdef01234567', preview_url: '/declared/converted.pdf?v=1' };
  w.PortalInstructionClient = { execute: async (name, args) => {
    assert.equal(name, 'ReadResource');
    assert.deepEqual(JSON.parse(JSON.stringify(args.address)), { upload_id: 'upload_0123456789abcdef01234567' });
    return metadata;
  } };
  const upload = await w.PortalFileLinks.load({ upload_id: 'upload_0123456789abcdef01234567' });
  assert.equal(upload.preview_url, metadata.preview_url);
  assert.equal(upload.raw_url, metadata.content_url);
  assert.equal(upload.download_url, metadata.content_url + '?download=1');

  const resident = w.PortalResidentService.createClient({ execute: async (name, args) => {
    assert.equal(name, 'ReadResource');
    assert.equal(args.address, address);
    return { ...metadata, content_url: resource.raw_url };
  } });
  const residentFile = await resident.file({ directoryId: 'workspace_0123456789ab', path: 'slides.pptx' });
  assert.equal(residentFile.preview_url, metadata.preview_url);

  let localPreviewUrl = '/declared/local.pdf?v=1';
  const apiGet = async url => {
    const parsed = new URL(url, w.location);
    assert.equal(parsed.pathname, '/api/agent-workbench/link-file');
    return { path: parsed.searchParams.get('path'), kind: 'binary',
      raw_url: '/local-original.pptx', preview_url: localPreviewUrl };
  };
  w.PortalAPI = { get: apiGet };
  const source = w.PortalAgentPreviewSource.create({ apiGet, apiPost() {}, residentService: resident });
  let local = await source.reload({ source: 'workspace_link', path: '/slides.pptx' });
  assert.equal(local.previewUrl, localPreviewUrl);
  assert.equal(local.rawUrl, '/local-original.pptx');
  localPreviewUrl = '/declared/local.pdf?v=2';
  local = await source.reload(local);
  assert.equal(local.previewUrl, localPreviewUrl, 'refresh uses the current source descriptor, not the previous normalized field');
  localPreviewUrl = '';
  local = await source.reload(local);
  assert.equal(local.previewUrl, '', 'withdrawn conversion capability must clear the previous URL');
  assert.equal(source.normalize(residentFile).previewUrl, metadata.preview_url);
  assert.equal(source.normalize(upload).previewUrl, metadata.preview_url);
  console.log('Resource, upload, Resident file and workspace link adapters supply explicit preview URLs; refresh and original download passed');
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => dom.window.close());
