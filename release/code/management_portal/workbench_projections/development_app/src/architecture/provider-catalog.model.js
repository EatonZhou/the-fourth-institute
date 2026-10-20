import {createDeclaredProviderSimulation} from './provider-mechanisms.model.js';

export const CATALOG_ID = 'provider.runtime-catalog';
export const TUPLE_ID = 'provider.settings-tuple';
export const catalogAdapters = Object.freeze({[CATALOG_ID]:'provider.runtime-catalog.v1', [TUPLE_ID]:'provider.settings-tuple.v1'});
export const catalogObjectEntries = Object.freeze({'execution.providers.catalog':CATALOG_ID});
export const catalogObjectModes = Object.freeze({'execution.providers.catalog':Object.freeze([
  CATALOG_ID, TUPLE_ID, 'provider.catalog-settings', 'provider-capabilities.preflight',
])});
const fields = {
  [CATALOG_ID]:['scenario','phase','account_id','route_id','cache_key','cache_hit','cursor','next_cursor','request_count',
    'observed_model_ids','hidden_count','catalog_accepted','catalog','error','live_observation','native_turn_calls',
    'registration_entries','registration_request','account_lookups','adapter_calls','registry_result'],
  [TUPLE_ID]:['scenario','phase','catalog','requested','explicit_fields','accepted','resolved','error','selected_model_modalities',
    'native_turn_calls','isa_authorization','live_observation','provider_media','image_input_transport','route_catalog_module',
    'route_catalog_calls','route_catalog_observation','base_harness_capabilities','active_turn_input','effective_harness_capabilities','native_image_input'],
};
export const supportsProviderCatalog = m => !!m && Object.hasOwn(catalogAdapters,m.id) && m.adapter === catalogAdapters[m.id] && m.mode === 'simulated';
export function createProviderCatalogSimulation(content) {
  if (!supportsProviderCatalog(content?.mechanism)) throw Error('provider_catalog:unsupported');
  // Reuse the existing typed replay contract; each subject owns a distinct view.
  const m=content.mechanism;
  const simulation=createDeclaredProviderSimulation({mechanism:m,objects:content.objects,relations:content.relations,
    evidence:content.evidence,batchKey:content.batchKey}, {adapter:m.adapter,mechanismId:m.id,fields:fields[m.id]});
  for(const step of m.steps) if(step.relation_id) {
    const relation=content.relations?.find(r=>r.id===step.relation_id);
    if(!relation||relation.from!==step.from||relation.to!==step.to)throw Error('provider_catalog:relation_unavailable');
  }
  return {...simulation,perform(id){const next=simulation.getScene().next;return !!next&&next.id===id?simulation.perform(id):false;}};
}
const externalAdapters = {'provider.catalog-settings':'provider.catalog-settings.v1','provider-capabilities.preflight':'provider-capabilities.preflight.v1'};
export function getProviderCatalogModes(content,objectId) {
  const object=content.objects.find(o=>o.id===objectId);
  if(!object)return [];
  return (catalogObjectModes[objectId]||[CATALOG_ID,TUPLE_ID]).flatMap(id=>{
    if(!object.mechanism_ids?.includes(id))return [];
    const candidates=(content.mechanisms||[]).filter(m=>m.id===id&&m.mode==='simulated'&&m.adapter===(catalogAdapters[id]||externalAdapters[id]));
    if(candidates.length!==1)return [];
    const mechanism=candidates[0],ids=[...(mechanism.evidence_ids||[]),...(mechanism.steps||[]).flatMap(s=>s.evidence_ids||[]),...(mechanism.cases||[]).flatMap(c=>c.evidence_ids||[])];
    if(!ids.length||ids.some(id=>!content.evidence.some(e=>e.id===id)))return [];
    if(supportsProviderCatalog(mechanism))try{createProviderCatalogSimulation({...content,mechanism});}catch{return [];}
    return [{id,title:mechanism.title,local:supportsProviderCatalog(mechanism),route:{kind:object.kind==='protocol'?'protocol':'mechanism',objectId,mechanismId:id}}];
  });
}
export function catalogCaseGroup(mechanismId,caseId) {
  if(mechanismId===TUPLE_ID)return caseId.startsWith('media_')?'media':'tuple';
  return ['registered_routes','selected_account','default_account','missing_account'].includes(caseId)?'registration':'pages';
}
// A field's initial value carries no result before a step actually writes it.
export const catalogFieldObserved = (scene,field) => scene.trace.some(step=>step.changes.some(change=>change.field===field));
