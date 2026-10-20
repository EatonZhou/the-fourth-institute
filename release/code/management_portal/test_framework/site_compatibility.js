// Node-side consumers read explicit installation parameters and canonical tags.
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');

function configuration() {
  const filename = path.resolve(root, process.env.PORTAL_SITE_CONFIG || 'config/site.json');
  let site;
  try { site = JSON.parse(fs.readFileSync(filename, 'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return {}; throw error; }
  const value = site.deployment?.compatibility ?? {};
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid site compatibility');
  return value;
}

function compatibilityValue(key, fallback) {
  const values = configuration().domain_values ?? {};
  if (!values || typeof values !== 'object' || Array.isArray(values)) throw new Error('Invalid domain values');
  return Object.hasOwn(values, key) ? values[key] : fallback;
}

function schemaMatches(actual, expected) {
  if (typeof expected !== 'string' || !expected.startsWith('tfi.') || expected.length <= 4) throw new Error('Invalid canonical schema');
  return typeof actual === 'string' && actual === expected;
}

function testActor() {
  const actor = compatibilityValue('test_framework.actor', 'test-actor');
  if (typeof actor !== 'string' || !actor.trim()) throw new Error('Invalid test actor');
  return actor.trim();
}

module.exports = { compatibilityValue, schemaMatches, testActor };
