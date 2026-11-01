// Node-side consumers read the selected instance's site settings and canonical tags.
const fs = require('node:fs');
const path = require('node:path');

function siteSettings() {
  const root = process.env.PORTAL_INSTANCE_ROOT;
  if (!root || !path.isAbsolute(root)) throw new Error('Select an instance with PORTAL_INSTANCE_ROOT');
  const site = JSON.parse(fs.readFileSync(path.join(root, 'site.json'), 'utf8'));
  if (!site || typeof site !== 'object' || Array.isArray(site)) throw new Error('Invalid site settings');
  return site;
}

function testActor() {
  const framework = siteSettings().deployment?.test_framework ?? {};
  if (!framework || typeof framework !== 'object' || Array.isArray(framework)) throw new Error('Invalid test framework settings');
  const actor = framework.actor ?? 'test-actor';
  if (typeof actor !== 'string' || !actor.trim()) throw new Error('Invalid test actor');
  return actor.trim();
}

function schemaMatches(actual, expected) {
  if (typeof expected !== 'string' || !expected.startsWith('tfi.') || expected.length <= 4) throw new Error('Invalid canonical schema');
  return typeof actual === 'string' && actual === expected;
}

module.exports = { schemaMatches, testActor };
