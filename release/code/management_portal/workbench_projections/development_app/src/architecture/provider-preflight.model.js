import {createDeclaredProviderSimulation} from './provider-mechanisms.model.js';

export const PREFLIGHT_ID='provider-capabilities.preflight';
export const PREFLIGHT_ADAPTER='provider-capabilities.preflight.v1';
const contract=Object.freeze({adapter:PREFLIGHT_ADAPTER,mechanismId:PREFLIGHT_ID,view:'providerPreflight',fields:[
  'variant','provider_declarations','account_observation','schema_fixture','provider_id','operation','catalog_origin',
  'requested_model','requested_effort','requested_tier','requested_field','required_capabilities','provided_capabilities',
  'validation','feature_status','unmet_requirement','native_call_count','isa_call_count','isa_authorization',
  'native_permission_preset','error','requirement_catalog','feature_id','requirement_alternatives','alternative_checks','satisfied_alternative_indices',
]});
export const supportsProviderPreflight=m=>m?.id===PREFLIGHT_ID&&m.adapter===PREFLIGHT_ADAPTER&&m.mode==='simulated';
export function createProviderPreflightSimulation(content){
  if(!supportsProviderPreflight(content?.mechanism))throw new Error('preflight_unsupported_adapter');
  return createDeclaredProviderSimulation(content,contract);
}
const native={mechanismId:'provider.native-socket',adapter:'provider.native-socket.v1',factory:'createNativeWorkbench'};
const lifecycle={mechanismId:'execution.lifecycle',adapter:'execution.lifecycle.v1',factory:'createExecutionWorkbench'};
const session=id=>({mechanismId:id,adapter:id+'.v1',factory:'createProviderSessionWorkbench'});
export const PREFLIGHT_OBJECT_ENTRIES=Object.freeze({
  'provider-capabilities':{kind:'protocol',defaultMechanismId:PREFLIGHT_ID,factory:'createProviderPreflight',primaryMechanismId:PREFLIGHT_ID,modeMechanismIds:[PREFLIGHT_ID,'provider-capabilities.negotiate','execution.lifecycle'],delegatedModes:[{mechanismId:'provider-capabilities.negotiate',adapter:'provider-capabilities.negotiate.v1',factory:'createProviderMechanisms'},lifecycle]},
  'execution.providers.catalog':{kind:'mechanism',defaultMechanismId:'provider.catalog-settings',factory:'createProviderSessionWorkbench',primaryMechanismId:'provider.catalog-settings',modeMechanismIds:['provider.catalog-settings',PREFLIGHT_ID],delegatedModes:[session('provider.catalog-settings')]},
  'execution.providers':{kind:'composition',defaultMechanismId:null,factory:null,primaryMechanismId:PREFLIGHT_ID,modeMechanismIds:['execution.lifecycle','provider.catalog-settings','provider.adapter-boundary','provider.session-resolve','provider.binding-cas',PREFLIGHT_ID,'provider.native-socket'],delegatedModes:[lifecycle,session('provider.catalog-settings'),session('provider.adapter-boundary'),session('provider.session-resolve'),session('provider.binding-cas'),native]},
});
// These bodies belong to the catalog author. The parent resolves their factory.
for(const entry of Object.values(PREFLIGHT_OBJECT_ENTRIES))for(const id of ['provider.runtime-catalog','provider.settings-tuple']){
  entry.modeMechanismIds.push(id);
  entry.delegatedModes.push({mechanismId:id,adapter:id+'.v1'});
}
export function getProviderPreflightEntry(objectId){return Object.hasOwn(PREFLIGHT_OBJECT_ENTRIES,objectId)?structuredClone({objectId,...PREFLIGHT_OBJECT_ENTRIES[objectId]}):null;}
export function getProviderPreflightModes(content,objectId){
  const entry=getProviderPreflightEntry(objectId),subject=content.objects.find(o=>o.id===objectId);
  if(!entry||!subject?.mechanism_ids.includes(PREFLIGHT_ID))return [];
  return entry.delegatedModes.flatMap(mode=>{
    if(!subject.mechanism_ids.includes(mode.mechanismId))return [];
    const matches=(content.mechanisms||[]).filter(m=>m.id===mode.mechanismId&&m.adapter===mode.adapter&&m.mode==='simulated');
    if(matches.length!==1)return [];
    const m=matches[0],eids=[...(m.evidence_ids||[]),...(m.steps||[]).flatMap(s=>s.evidence_ids||[]),...(m.cases||[]).flatMap(c=>c.evidence_ids||[])];
    if(!eids.length||eids.some(id=>!content.evidence.some(e=>e.id===id)))return [];
    return [{...mode,title:m.title,route:{kind:entry.kind==='protocol'?'protocol':'mechanism',objectId,mechanismId:m.id}}];
  });
}
