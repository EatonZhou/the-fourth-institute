import { build } from 'esbuild';
import { readFile, mkdir, rm, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { existsSync } from 'node:fs';

const root = dirname(new URL(import.meta.url).pathname);
const entries = JSON.parse(await readFile(resolve(root, 'entries.json'), 'utf8'));
const dist = resolve(root, 'dist');
await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });
for (const [name, entry] of Object.entries(entries)) {
  // Each region is a separate application entry; its imports stay modular in source.
  await build({
    stdin: {
      contents: `import { start } from './src/shared/lifecycle.js'; import { mount } from './${entry}'; start(mount, ${JSON.stringify(name)});`,
      resolveDir: root, sourcefile: `${name}-entry.js`, loader: 'js',
    },
    outfile: resolve(dist, `${name}.js`), bundle: true, format: 'esm',
    target: ['es2022'], legalComments: 'linked', minify: true,
    loader: { '.png': 'dataurl', '.jpg': 'dataurl', '.jpeg': 'dataurl', '.gif': 'dataurl', '.webp': 'dataurl', '.svg': 'dataurl', '.woff2': 'dataurl' },
  });
  const css = existsSync(resolve(dist, `${name}.css`)) ? `<link rel="stylesheet" href="./${name}.css">` : '';
  await writeFile(resolve(dist, `${name}.html`), `<!doctype html><html><head><meta charset="utf-8">${css}</head><body><main id="app"></main><script type="module" src="./${name}.js"></script></body></html>`);
  const workers = [['layout.worker.js', name === 'example' ? 'layout.worker.js' : `${name}.worker.js`]];
  if (name === 'architecture') workers.push(['graph-layout.worker.js', 'architecture-layout.worker.js']);
  for (const [source, output] of workers) {
    const worker = resolve(root, dirname(entry), source);
    if (existsSync(worker)) {
      // Region workers remain optional and never reuse another region's asset.
      await build({ entryPoints: [worker], outfile: resolve(dist, output), bundle: true, format: 'iife', target: ['es2022'], minify: true });
    }
  }
}
