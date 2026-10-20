// Execute complete production readers in isolated VM globals. No browser,
// server mutation, real navigation, or live-tour action execution.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const STATIC = path.resolve(__dirname, '../../../../management_portal/static');
const clone = value => JSON.parse(JSON.stringify(value));
const template = (parts, ...values) => parts.reduce((out, part, i) => out + part + (values[i] ?? ''), '');
const i18n = { t: value => value, definition: value => value, html: template, text: template };
function load(file, globals) {
  const context = vm.createContext({ URL, console, ...globals });
  context.window = context;
  context.location ||= { pathname: '/', search: '', hash: '' };
  context.document ||= { title: '', readyState: 'loading', addEventListener() {} };
  context.addEventListener ||= () => {};
  context.__PORTAL_SITE__ = { compatibility: { format_namespaces: ['archive.'], browser_identifiers: {} } };
  vm.runInContext(fs.readFileSync(path.join(STATIC, 'portal_state.js'), 'utf8'), context,
    { filename: 'portal_state.js' });
  vm.runInContext(fs.readFileSync(path.join(STATIC, file), 'utf8'), context, { filename: file });
  return context;
}
function invalidTags(name) {
  return [undefined, null, '', 1, [ `tfi.${name}.v1` ], { schema: `tfi.${name}.v1` },
    `tfi.${name}.v2`, `archive.${name}.v1`, `archive.${name}.v2`, `other.${name}.v1`,
    'tfi.unrelated.v1', `archive.${name}-other.v1`, `tfi.${name}.v01`,
    `TFI.${name}.v1`, `tfi.${name}.v1.extra`, ` tfi.${name}.v1`, `tfi.${name}.v1\n`];
}

function routeReader(schema, overrides = {}) {
  const contract = {
    schema,
    routes: [{ resource_type: 'article', path: '/resource/article', parameter: 'item' }],
    text_resource_types: ['article'], navigation_resource_types: ['resident'],
    identity_parameters: ['item', 'resident'],
    content_route: { path: '/api/resources/content', parameter: 'address' },
    ...overrides,
  };
  const context = load('portal_resource_chips.js', {
    PortalResourceRoutes: contract,
    PortalI18n: i18n,
    PortalUIPrimitives: { escapeHtml: String, registerEnhancer() {} },
    location: { protocol: 'https:', origin: 'https://portal.test', href: 'https://portal.test/' },
    document: { readyState: 'loading', addEventListener() {} },
  });
  return { reader: context.PortalResourceChips, contract };
}
for (const prefix of ['tfi']) {
  test(`resource routes: ${prefix} same-name/version accepted, identity constraints retained`, () => {
    const schema = `${prefix}.resource-portal-routes.v1`;
    const { reader, contract } = routeReader(schema);
    const address = 'article_012345abcdef';
    assert.equal(reader.resourceAddressFromPortalHref(`/resource/article?item=${address}`), address);
    assert.equal(reader.resourceAddressFromPortalHref(`https://evil.test/resource/article?item=${address}`), '');
    assert.equal(reader.resourceAddressFromPortalHref(`/resource/article?item=${address}&item=${address}`), '');
    assert.equal(reader.resourceAddressFromPortalHref(`/resource/article?item=${address}&resident=resident_012345abcdef`), '');
    assert.equal(reader.resourceAddressFromPortalHref('/resource/article?item=resident_012345abcdef'), '');
    assert.equal(reader.normalizeResourceAddress(`${address}/file/notes.md`), `${address}/file/notes.md`);
    assert.equal(contract.schema, schema, 'reader must not rewrite producer tag');
    assert.throws(() => routeReader(schema, { routes: null }), /iterable/);
  });
}
test('resource routes reject wrong namespace/name/version/type and missing tags', () => {
  for (const tag of invalidTags('resource-portal-routes')) {
    assert.throws(() => routeReader(tag), /published resource route contract/, JSON.stringify(tag));
  }
});

const primitiveFixture = JSON.parse(fs.readFileSync(path.join(STATIC, 'lineage_primitive_definitions.json'), 'utf8'));
function primitiveReader(payload) {
  const calls = [];
  const context = load('lineage_program.js', {
    PortalI18n: i18n,
    PortalAPI: { get: async (url, options) => { calls.push({ url, options }); return clone(payload); } },
  });
  return { reader: context.PortalLineageProgram, calls };
}
for (const prefix of ['tfi']) {
  test(`primitive definitions: ${prefix} accepted through real loader, fields remain validated`, async () => {
    const payload = { ...clone(primitiveFixture), schema: `${prefix}.hatg-primitive-definitions.v1` };
    const { reader, calls } = primitiveReader(payload);
    const manifest = await reader.loadPrimitiveDefinitions();
    assert.equal(manifest.primitive_definitions_schema, payload.schema);
    assert.equal(manifest.default_types.event, payload.default_types.event);
    assert.ok(Object.isFrozen(manifest.event_types));
    assert.equal(manifest.program_schema, 'tfi.hatg-program.v2', 'new programs advertise the canonical writer tag');
    assert.equal(reader.normalizeProgram({}).schema, 'tfi.hatg-program.v2');
    assert.equal(await reader.loadPrimitiveDefinitions(), manifest);
    assert.equal(calls.length, 1, 'existing loader cache retained');
    assert.equal(calls[0].url, '/static/lineage_primitive_definitions.json');
    assert.equal(calls[0].options.cache, 'no-store');
    for (const key of ['default_types', 'event_type_categories', 'event_types']) {
      for (const invalid of [null, []]) {
        await assert.rejects(primitiveReader({ ...payload, [key]: invalid }).reader.loadPrimitiveDefinitions(), new RegExp(key));
      }
    }
    await assert.rejects(primitiveReader({ ...payload, event_common_fields: {} }).reader.loadPrimitiveDefinitions(), /event_common_fields/);
    await assert.rejects(primitiveReader({ ...payload, default_types: { event: 'unknown' } }).reader.loadPrimitiveDefinitions(), /默认事件类型无效/);
  });
}
test('primitive definitions reject wrong namespace/name/version/type and missing tags', async () => {
  for (const schema of invalidTags('hatg-primitive-definitions')) {
    await assert.rejects(primitiveReader({ ...primitiveFixture, schema }).reader.loadPrimitiveDefinitions(), /schema 无效/, JSON.stringify(schema));
  }
});

function descriptor(schema) {
  return { schema, case_id: 'demo-case', revision: 'rev-1', demo: {
    mode: 'direct', risk: 'read-only', adapter: 'design-lineage', adapter_version: 1,
    target: '/lineage', direct_steps: [{ capability: 'inspect' }],
  } };
}
function tourReader(payload, origin = '/development?view=test') {
  const writes = [], navigation = [], requests = [];
  const current = new URL(origin, 'https://portal.test');
  const context = load('portal_live_tour.js', {
    PortalLiveTourSessionStore: { key: 'unchanged-key', readRaw: () => null, write: value => writes.push(clone(value)), clear() {} },
    PortalUIPrimitives: { randomUUID: () => '01234567-89ab-cdef-0123-456789abcdef' },
    PortalAPI: { get: async (url, options) => { requests.push({ url, options }); return clone(payload); } },
    location: { origin: current.origin, href: current.href, pathname: current.pathname,
      search: current.search, hash: current.hash, replace: target => navigation.push(target) },
  });
  return { reader: context.PortalLiveTour, writes, navigation, requests };
}
const caseRecord = { id: 'demo-case', demo: { mode: 'direct', risk: 'read-only' } };
for (const prefix of ['tfi']) {
  test(`live tour: ${prefix} accepted through public start; session/navigation contract preserved`, async () => {
    const { reader, writes, navigation, requests } = tourReader(descriptor(`${prefix}.portal-live-tour.v1`));
    assert.equal(await reader.start(caseRecord), true);
    assert.deepEqual(navigation, ['/lineage']);
    assert.equal(writes.length, 1);
    assert.equal(writes[0].v, 1);
    assert.equal(writes[0].caseId, 'demo-case');
    assert.equal(writes[0].revision, 'rev-1');
    assert.equal(writes[0].phase, 'target');
    assert.equal(reader.key, 'unchanged-key');
    assert.equal(requests[0].url, '/test/api/demo?case_id=demo-case');
    assert.equal(requests[0].options.headers.Accept, 'application/json');
  });
  test(`live tour: ${prefix} retains case, mode, risk, version, step and origin guards`, async () => {
    const baseline = descriptor(`${prefix}.portal-live-tour.v1`);
    const changes = [
      p => { p.case_id = 'another-case'; }, p => { delete p.demo; },
      p => { p.demo.mode = 'embedded'; }, p => { p.demo.risk = 'write'; },
      p => { p.demo.adapter = ''; }, p => { p.demo.adapter_version = 2; },
      p => { p.demo.direct_steps = {}; }, p => { p.demo.direct_steps = []; },
      p => { p.demo.direct_steps = Array.from({ length: 41 }, () => ({ capability: 'inspect' })); },
      p => { p.demo.direct_steps = [null]; }, p => { p.demo.direct_steps = [{}]; },
      p => { p.demo.target = 'https://evil.test/lineage'; },
      ...['selector', 'target', 'action', 'expect', 'url', 'method'].map(key => p => { p.demo.direct_steps[0][key] = 'blocked'; }),
    ];
    for (const change of changes) {
      const payload = clone(baseline); change(payload);
      const { reader, writes, navigation } = tourReader(payload);
      await assert.rejects(reader.start(caseRecord));
      assert.deepEqual(writes, []);
      assert.deepEqual(navigation, []);
    }
    const wrongOrigin = tourReader(baseline, '/lineage');
    await assert.rejects(wrongOrigin.reader.start(caseRecord), /Test 工作区/);
    assert.deepEqual(wrongOrigin.writes, []);
  });
}
test('live tour rejects wrong namespace/name/version/type and missing tags', async () => {
  for (const schema of invalidTags('portal-live-tour')) {
    const { reader, writes, navigation } = tourReader(descriptor(schema));
    await assert.rejects(reader.start(caseRecord), /没有返回有效/);
    assert.deepEqual(writes, []);
    assert.deepEqual(navigation, []);
  }
});
