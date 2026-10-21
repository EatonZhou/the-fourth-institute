const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test, after } = require('node:test');

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'tfi-framework-reader-'));
process.env.PORTAL_SITE_CONFIG = path.join(root, 'site.json');
fs.writeFileSync(process.env.PORTAL_SITE_CONFIG, JSON.stringify({ deployment: { compatibility: { format_namespaces: ['archive.'] } } }));
process.env.PORTAL_TEST_TARGET_FILE = path.join(root, 'target.json');
const reader = require('../../../management_portal/test_framework/dev_target.js');
after(() => fs.rmSync(root, { recursive: true, force: true }));

function payload(schema = 'tfi.portal-test-target.v1') {
  return {schema, deployment: {id:'release', kind:'release', label:'Test',
    origin:'http://127.0.0.1:28399', browser_origin:'http://localhost:28399', build_id:'fixture-build'}};
}
function observe(document, expectedError) {
  const original=JSON.stringify(document,null,2)+'\n';
  fs.writeFileSync(reader.targetFile(),original);
  try {
    if (expectedError) assert.throws(reader.resolveTestTarget,expectedError);
    else assert.deepEqual(reader.resolveTestTarget(), {
      instance:'release', origin:'http://127.0.0.1:28399', browserOrigin:'http://localhost:28399', expectedBuildId:'fixture-build',
    });
  } finally { assert.equal(fs.readFileSync(reader.targetFile(),'utf8'),original); }
}
test('read explicit instance and build without rewriting configuration',()=>observe(payload()));
test('require build and matching local browser origin',()=>{
  const document=payload();
  document.deployment.build_id='';observe(document,/expected build ID/);
  document.deployment.build_id='fixture-build';document.deployment.browser_origin='http://localhost:99';
  observe(document,/backend port/);
});
for (const schema of ['archive.portal-test-target.v1','tfi.portal-deployments.v1','tfi.portal-test-target.v2',null]) {
  test(`reject obsolete or invalid target schema ${schema}`,()=>observe(payload(schema),/target schema/));
}

test('only explicit deployment identity remains configurable', () => {
  const { testActor, schemaMatches } = require('../../../management_portal/test_framework/site_compatibility.js');
  fs.writeFileSync(process.env.PORTAL_SITE_CONFIG, JSON.stringify({ deployment: { compatibility: {
    format_namespaces: ['archive.'], domain_values: { 'test_framework.actor': 'site-operator' },
  } } }));
  const original = fs.readFileSync(process.env.PORTAL_SITE_CONFIG, 'utf8');
  assert.equal(testActor(), 'site-operator');
  assert.equal(schemaMatches('archive.portal-deployments.v1', 'tfi.portal-deployments.v1'), false);
  assert.equal(schemaMatches('tfi.portal-deployments.v1', 'tfi.portal-deployments.v1'), true);
  assert.throws(() => schemaMatches('tfi.x.v1', 'archive.x.v1'), /canonical schema/);
  assert.equal(fs.readFileSync(process.env.PORTAL_SITE_CONFIG, 'utf8'), original);
});
