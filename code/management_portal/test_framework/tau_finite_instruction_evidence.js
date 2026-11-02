'use strict';
const {observeFiniteResponses}=require('./tau_finite_response_evidence');

// Exact finite ISA targets; the shared body/close lifecycle remains unchanged.
function observeFiniteInstructionResponses(page, {targets, ...options}) {
  if (!Array.isArray(targets) || !targets.length) throw new TypeError('InvalidFiniteObservationOptions');
  const specs=targets.map(target=>{
    if (!target || !/^[A-Z][A-Za-z0-9]+$/.test(target.instruction) || target.instruction.startsWith('Watch')
        || !target.arguments || !Object.keys(target.arguments).length
        || Object.values(target.arguments).some(v=>!['string','number','boolean'].includes(typeof v)))
      throw new TypeError('FiniteObservationRequiresExactNonStreamingTarget');
    return structuredClone(target);
  });
  return observeFiniteResponses(page,{...options,identifyRequest(request){
    if (new URL(request.url()).pathname!=='/api/instructions/execute' || request.method()!=='POST') return null;
    const body=request.postDataJSON();
    const matching=specs.filter(s=>body?.instruction===s.instruction && Object.entries(s.arguments).every(([k,v])=>body.arguments?.[k]===v));
    if (!matching.length) return null;
    if (matching.length!==1) throw new Error('AmbiguousOriginalInstructionTarget');
    const metadata={instruction:body.instruction};
    for (const key of ['resident_id','message_id','lineage_id','restart_id'])
      if (typeof matching[0].arguments[key]==='string') metadata[key]=matching[0].arguments[key];
    return metadata;
  }});
}
module.exports={observeFiniteInstructionResponses};
