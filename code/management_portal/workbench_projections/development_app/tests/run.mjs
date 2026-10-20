import {mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {gunzipSync} from 'node:zlib';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';

// Domain tests pin sanitized, immutable fixture copies. Materialize exact bytes
// outside the checkout so npm test needs neither a Resident workspace nor a server.
const fixtureDirectory = new URL('./fixtures/frozen-exploration/', import.meta.url);
const manifest = JSON.parse(readFileSync(new URL('manifest.json', fixtureDirectory)));
const temporaryDirectory = mkdtempSync(join(tmpdir(), 'projection-test-facts-'));
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const env = {...process.env};
const files = new Map();
function fixture(name) {
  if (files.has(name)) return files.get(name);
  const descriptor = manifest.fixtures[name];
  const compressed = readFileSync(new URL(descriptor.file, fixtureDirectory));
  if (sha256(compressed) !== descriptor.gzip_sha256) throw Error(`Corrupt compressed fixture: ${name}`);
  const bytes = gunzipSync(compressed);
  if (bytes.length !== descriptor.size || sha256(bytes) !== descriptor.sha256) throw Error(`Corrupt fixture: ${name}`);
  const path = join(temporaryDirectory, `${name}.json`);
  writeFileSync(path, bytes);
  files.set(name, path);
  return path;
}
try {
  for (const [variable, name] of Object.entries({
    HARNESS_DISCOVERY_FIXTURE: 'provider',
    PROVIDER_SESSION_FACTS: 'provider',
    NATIVE_OPERATIONS_FACTS: 'provider',
    PROJECTION_MODEL_FIXTURE: 'projection',
    LINEAGE_MODEL_FIXTURE: 'lineage-model',
    RESOURCE_MODEL_FIXTURE: 'model',
    HARNESS_OFFLOADING_CAPTURE: 'mechanism-increment',
    PROVIDER_CATALOG_FACTS: 'mechanism-increment',
    PROVIDER_FEEDBACK_FACTS: 'provider-feedback',
    PROVIDER_PREFLIGHT_MODES_FACTS: 'mechanism-increment',
  })) {
    if (!env[variable]) env[variable] = fixture(name);
  }
  const testDirectory = fileURLToPath(new URL('./', import.meta.url));
  const arguments_ = process.argv.slice(2);
  const selected = arguments_.filter(name => name.endsWith('.test.mjs'));
  const tests = selected.length ? selected : readdirSync(testDirectory).filter(name => name.endsWith('.test.mjs')).sort();
  const options = arguments_.filter(name => !name.endsWith('.test.mjs'));
  const result = spawnSync(process.execPath, ['--test', ...options, ...tests.map(name => join(testDirectory, name))], {
    env, stdio: 'inherit',
  });
  if (result.error) throw result.error;
  process.exitCode = result.status ?? 1;
} finally {
  rmSync(temporaryDirectory, {recursive: true, force: true});
}
