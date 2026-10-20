import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
const seed=path.resolve(process.argv[2]);
const projection=path.join(seed,'data/share_root/Codes/repo_bd47ffeac5c8/projection');
// Simulate the LAN HTTP environment in which WebCrypto is unavailable.
const descriptor=Object.getOwnPropertyDescriptor(globalThis,'crypto');
Object.defineProperty(globalThis,'crypto',{configurable:true,value:undefined});
try {
 const helper=fs.readFileSync(path.join(projection,'source/sha256.js'),'utf8');
 const {sha256}=await import('data:text/javascript;base64,'+Buffer.from(helper).toString('base64'));
 const digest=bytes=>createHash('sha256').update(bytes).digest('hex');
 for(const length of [0,3,55,56,63,64,65,1024]){
  const bytes=Uint8Array.from({length},(_,i)=>i%251);
  assert.equal(await sha256(bytes),digest(bytes));
 }
 const catalogue=JSON.parse(fs.readFileSync(path.join(projection,'public/catalog.json')));
 for(const entry of catalogue){
  const record=JSON.parse(fs.readFileSync(path.join(projection,'public',entry.offlineClip)));
  const payload=JSON.parse(fs.readFileSync(path.join(projection,'public',record.framesFile)));
  const bytes=Buffer.from(payload.data,'base64');
  assert.equal(await sha256(bytes),record.sha256,entry.id);
  assert.equal(bytes.length,record.width*record.height*record.steps.length);
  bytes[0]^=1;
  assert.notEqual(await sha256(bytes),record.sha256,'Corrupt frames must still fail verification');
 }
 const sql=fs.readFileSync(path.join(seed,'database/distribution.sql'),'utf8');
 const line=sql.split('\n').find(l=>l.startsWith('INSERT INTO "workbenches"."application_packages"')&&l.includes('workbench_a8735d84f9af'));
 const values=[...line.matchAll(/'((?:[^']|'')*)'/g)].map(m=>m[1].replaceAll("''","'"));
 const build=JSON.parse(values.at(-1));
 const artifact=path.join(seed,'data/staging_root/shared/projection_applications',build.artifact_hash);
 assert.equal(digest(fs.readFileSync(path.join(artifact,'artifact.json'))),build.artifact_hash);
 for(const [name,meta] of Object.entries(build.files)){
  const bytes=fs.readFileSync(path.join(artifact,'files',name));
  assert.equal(digest(bytes),meta.sha256,name);assert.equal(bytes.length,meta.size,name);
 }
 assert.deepEqual(fs.readFileSync(path.join(artifact,'files/viewer.js')),fs.readFileSync(path.join(projection,'public/viewer.js')));
 console.log('HTTP hashing, valid/corrupt clips and published artifact integrity passed.');
} finally {
 if(descriptor)Object.defineProperty(globalThis,'crypto',descriptor);else delete globalThis.crypto;
}
