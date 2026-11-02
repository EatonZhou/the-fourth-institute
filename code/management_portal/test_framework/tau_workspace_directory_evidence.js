'use strict';
const {createHash}=require('node:crypto');
const {observeFiniteResponses}=require('./tau_finite_response_evidence');

// Observe the two native transports for this exact, formally resolved root.
// A cached reopening may send neither. Only an original response supplies a
// new directory body; this helper never refreshes or infers readiness.
function observeWorkspaceDirectoryResponses(page,{workspaceId,directoryPath,...options}) {
  if (!/^workspace_[0-9a-f]{12}$/.test(workspaceId || '') || typeof directoryPath!=='string'
      || !directoryPath.trim() || /[\x00-\x1f\x7f]/.test(directoryPath))
    throw new TypeError('ExactWorkspaceAndResolvedDirectoryRequired');
  const address=workspaceId+'/file';
  const targetSha256=createHash('sha256').update(directoryPath).digest('hex');
  return observeFiniteResponses(page,{...options,identifyRequest(request){
    const url=new URL(request.url());
    if (request.method()==='GET' && url.pathname==='/api/agent-workbench/link-file'
        && url.searchParams.getAll('path').length===1 && url.searchParams.get('path')===directoryPath
        && !url.searchParams.has('meta'))
      return {transport:'file_link',workspace_id:workspaceId,target_path_sha256:targetSha256};
    if (request.method()==='POST' && url.pathname==='/api/instructions/execute') {
      const body=request.postDataJSON();
      if (body?.instruction==='ReadResource' && body.arguments?.address===address)
        return {transport:'instruction',instruction:'ReadResource',workspace_id:workspaceId,target_path_sha256:targetSha256};
    }
    return null;
  }});
}
module.exports={observeWorkspaceDirectoryResponses};
