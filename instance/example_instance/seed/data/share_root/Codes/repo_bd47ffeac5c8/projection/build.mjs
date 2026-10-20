import {cp,rm,readdir,readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {build} from 'esbuild';
await rm('dist',{recursive:true,force:true});
await cp('public','dist',{recursive:true});
// The opaque Portal frame cannot fetch JSON. The trusted host supplies
// revision-bound data through its Worker API, without external connections.
async function dataWorkers(dir){
 for(const entry of await readdir(dir,{withFileTypes:true})){
  const file=join(dir,entry.name);
  if(entry.isDirectory())await dataWorkers(file);
  else if(file.endsWith('.json')){
   const data=JSON.parse(await readFile(file,'utf8'));
   await writeFile(file.slice(0,-5)+'.js','self.onmessage=()=>{self.postMessage('+JSON.stringify(data)+');self.close();};\n');
   await rm(file);
  }
 }
}
await dataWorkers('dist');
await build({entryPoints:['source/viewer.js'],bundle:true,format:'esm',outfile:'dist/viewer.js'});
