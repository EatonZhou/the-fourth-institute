const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { babelParse } = require('./babel_parse.cjs');

const sourcePath = path.resolve(__dirname, '../../../management_portal/static/portal_resource_chips.js');
const DELETED = 'resident_111111111111';

// Load the complete production module; only the HTTP answers are fixtures.
function loadChips(routeScript, answer) {
  const requests = [];
  const window = {
    location: { href: 'http://portal.invalid/lineage', protocol: 'http:', origin: 'http://portal.invalid' },
    clearTimeout,
    PortalUIPrimitives: {
      escapeHtml: () => { throw new Error('Unexpected presentation dependency'); },
      registerEnhancer: () => {},
    },
    PortalAPI: { get: url => { requests.push(url); return answer(requests.length); } },
    PortalUI: { toast: () => {} },
    PortalI18n: {
      t: value => value,
      text: (parts, ...values) => parts.reduce((text, part, i) => text + part + (values[i] ?? ''), ''),
    },
  };
  const context = vm.createContext({ window, document: { readyState: 'complete' }, URL, URLSearchParams });
  const identitySource = fs.readFileSync(path.join(path.dirname(sourcePath), 'portal_state.js'), 'utf8');
  const identityModule = babelParse(identitySource, 'portal_state.js').program.body[0];
  vm.runInContext(identitySource.slice(identityModule.start, identityModule.end), context);
  vm.runInContext(routeScript, context, { filename: 'portal_resource_routes.js' });
  vm.runInContext(fs.readFileSync(sourcePath, 'utf8'), context, { filename: sourcePath });
  return { chips: window.PortalResourceChips, requests };
}

function failure(status, retryable) {
  const error = new Error(`HTTP ${status}`);
  error.status = status;
  error.data = { retryable };
  return Promise.reject(error);
}

async function loadTwice(chips) {
  const errors = [];
  for (let i = 0; i < 2; i += 1) {
    await chips.loadReference(DELETED).then(
      () => assert.fail('a failed reference must not resolve'),
      error => errors.push(error.status),
    );
  }
  return errors;
}

const checks = {
  async 'deleted-is-remembered'(routeScript) {
    const { chips, requests } = loadChips(routeScript, () => failure(404, false));
    assert.deepEqual(await loadTwice(chips), [404, 404], 'every caller still sees the failure');
    assert.equal(requests.length, 1, 'a redrawn label does not ask again for a deleted Resource');
    await chips.loadReference(DELETED, { allowCached: false }).catch(() => {});
    assert.equal(requests.length, 2, 'an explicit fresh read still asks the server');
  },
  async 'retryable-asks-again'(routeScript) {
    const { chips, requests } = loadChips(routeScript, () => failure(503, true));
    assert.deepEqual(await loadTwice(chips), [503, 503]);
    assert.equal(requests.length, 2, 'a retryable failure is asked again at once');
  },
};

if (require.main === module) {
  const selected = process.argv[2];
  assert.ok(Object.hasOwn(checks, selected), `select one of ${Object.keys(checks).join(', ')}`);
  const routeScript = fs.readFileSync(0, 'utf8');
  test(selected, () => checks[selected](routeScript));
}
