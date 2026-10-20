import test from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';

test('test_tfi_projection_format_compatibility_evolution_loading: browser entry resolves the parent helper without writes or mounting', async t => {
  const result = await build({
    absWorkingDir: fileURLToPath(new URL('..', import.meta.url)),
    entryPoints: ['src/evolution/index.js'],
    bundle: true, platform: 'browser', format: 'esm',
    write: false, metafile: true, outdir: 'tfi-evolution-in-memory',
    logLevel: 'silent',
  });
  const inputs = Object.keys(result.metafile.inputs);
  assert.ok(inputs.includes('src/evolution/data.js'));
  assert.ok(inputs.includes('src/shared/format-tags.js'));
  assert.equal(inputs.some(path => path.startsWith('src/architecture/')), false);
  assert.equal(inputs.some(path => path.startsWith('src/progress/')), false);
  assert.ok(result.outputFiles.some(file => file.path.endsWith('.js')));
  assert.ok(result.outputFiles.some(file => file.path.endsWith('.css')));
  t.diagnostic(JSON.stringify({
    entry: 'src/evolution/index.js', write: false, mounted: false,
    inputs,
    memoryOutputs: result.outputFiles.map(file => ({
      basename: file.path.split('/').at(-1), bytes: file.contents.byteLength,
      sha256: createHash('sha256').update(file.contents).digest('hex'),
    })),
  }));
});
