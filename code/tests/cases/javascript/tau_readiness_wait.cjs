'use strict';
const assert=require('node:assert/strict');
const {waitForEvidenceReady}=require('../../../management_portal/test_framework/tau_readiness_wait');
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));

(async()=>{
  const samples=[], budgets=[];
  const states=[{identity_ready:true,ready:false,edges:['old-managed-wire']},
    {identity_ready:true,ready:false,edges:[]}, {identity_ready:true,ready:true,edges:[]}];
  let index=0;
  const result=await waitForEvidenceReady(async budget=>{
    budgets.push(budget); return states[index++];
  },{record:row=>samples.push(row),timeoutMs:1000,intervalMs:2});
  assert.equal(result,states[2]);
  assert.equal(samples.length,3,'identity alone must not end the original wait');
  assert.deepEqual(samples.map(row=>row.snapshot),states);
  assert.equal(new Set(budgets.map(row=>row.deadlineMs)).size,1);
  assert.ok(budgets[2].timeoutMs<=budgets[0].timeoutMs);
  const missing=[];
  await assert.rejects(waitForEvidenceReady(async()=>({identity_ready:true}),
    {record:row=>missing.push(row)}),/boolean ready/);
  assert.equal(missing.length,1,'invalid snapshots remain original evidence');
  const late=[];
  await assert.rejects(waitForEvidenceReady(async()=>{
    await delay(30); return {ready:true};
  },{record:row=>late.push(row),timeoutMs:10,intervalMs:1}),error=>
    error.code==='tau.readiness_timeout' && error.snapshot.ready===true);
  assert.equal(late.length,1);
  const timed=[];
  await assert.rejects(waitForEvidenceReady(async()=>({identity_ready:true,ready:false}),
    {record:row=>timed.push(row),timeoutMs:15,intervalMs:2}),error=>
    error.code==='tau.readiness_timeout' && error.snapshot.ready===false);
  assert.ok(timed.length>=1);
  let reads=0;
  const original=new Error('read failed');
  await assert.rejects(waitForEvidenceReady(async()=>{reads++;throw original;},
    {record:()=>{}}),error=>error===original);
  assert.equal(reads,1,'a generic readiness wait must not replay failed reads');
  let persistedReads=0;
  await assert.rejects(waitForEvidenceReady(async()=>{persistedReads++;return {ready:true};},
    {record:()=>{throw new Error('persistence failed');}}),/persistence failed/);
  assert.equal(persistedReads,1);
  console.log('strict readiness, original deadline, persistence and error propagation passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
