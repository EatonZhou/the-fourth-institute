'use strict';

// The caller defines the whole readiness condition in one original snapshot.
// Identity and other partial conditions never override snapshot.ready.
async function waitForEvidenceReady(read, {record = () => {}, timeoutMs=60000, intervalMs=75} = {}) {
  if (typeof read !== 'function' || typeof record !== 'function'
      || !Number.isFinite(timeoutMs) || timeoutMs<=0 || timeoutMs>600000
      || !Number.isFinite(intervalMs) || intervalMs<1 || intervalMs>timeoutMs)
    throw new TypeError('A reader, persistence callback and bounded original deadline are required');
  const started=Date.now(), deadline=started+timeoutMs;
  let snapshot=null;
  const evidenceGaps=[];
  const expired=()=>Object.assign(new Error('Original readiness deadline exceeded'),
    {code:'tau.readiness_timeout', snapshot});
  for (;;) {
    const remaining=deadline-Date.now();
    if (remaining<=0) throw expired();
    // No racing read or automatic error retry. A reader must apply this budget
    // to its own transport; a late response remains evidence but cannot pass.
    snapshot=await read({timeoutMs:remaining, deadlineMs:deadline});
    try { await record({observed_at_ms:Date.now(), elapsed_ms:Date.now()-started, snapshot}); }
    catch (_) { if (!evidenceGaps.length) evidenceGaps.push('未记录：观察记录写入失败'); }
    if (!snapshot || typeof snapshot.ready !== 'boolean')
      throw new TypeError('The original snapshot must explicitly define boolean ready');
    if (Date.now()>deadline) throw expired();
    if (snapshot.ready===true) return evidenceGaps.length ? {...snapshot,evidence_gaps:evidenceGaps} : snapshot;
    await new Promise(resolve=>setTimeout(resolve,Math.min(intervalMs,Math.max(0,deadline-Date.now()))));
  }
}

module.exports={waitForEvidenceReady};
