import {build} from 'esbuild';
import {mkdir,rm,writeFile,readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
await rm('dist',{recursive:true,force:true}); await mkdir('dist');
const snapshotHash=createHash('sha256').update(await readFile('data/semantic-snapshot.json')).digest('hex');
for(const name of ['semantic','matrix']) {
 await build({entryPoints:[`src/${name}/entry.js`],outfile:`dist/${name}.js`,bundle:true,format:'esm',target:'es2022',minify:true,legalComments:'none',loader:{'.html':'text'},define:{__SNAPSHOT_HASH__:JSON.stringify(snapshotHash)}});
 await writeFile(`dist/${name}.html`,`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><link rel="stylesheet" href="./${name}.css"></head><body><main id="app"></main><script type="module" src="./${name}.js"></script></body></html>`);
}

