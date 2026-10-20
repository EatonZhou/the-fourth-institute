// Controlled release-service contract for page tests, not production defaults.
export function resolveFeatureFixture(snapshot={}){
 const features=[...['resident_semantic_search','model_request_timing','harness_recording','wall_scheduling','telepus','tau','test_framework','test_view','projects','society_templates'].map(id=>({id,title:id,initial_default:false,default_source:'fixture:init',code_requires:id==='tau'||id==='test_view'?['test_framework']:[],system_flow_ids:id==='model_request_timing'?['fbi_111111111111']:[]})),{id:'flow:resident_111111111111',title:'实际成员',initial_default:null,default_source:'fixture:unset',code_requires:[],system_flow_ids:['resident_111111111111']}];
 features.push({id:'permission_system',title:'权限系统',mandatory:true,initial_default:true,default_applicable:true,kind:'runtime_feature',default_source:'fixture:init',code_requires:[],system_flow_ids:[]});
 features.find(f=>f.id==='test_view').projection_builtin='test';
 let request=snapshot.feature_states;
 if(!request){request=Object.fromEntries(Object.entries(snapshot.optional_features||{}).map(([id,included])=>[id,{included,default_enabled:null}]));
  for(const [group,label,id] of [['tests','TAU 功能','tau'],['tests','测试框架','test_framework'],['tools','Telepus','telepus']])request[id]={included:snapshot.categories?.[group]?.includes(label)||false,default_enabled:null};
  for(const [id,key] of [['projects','projects_page'],['test_view','test_view']])if(typeof snapshot[key]==='boolean')request[id]={included:snapshot[key],default_enabled:null};
  for(const f of snapshot.system_lineage?.selected||[])request['flow:'+f.id]={included:true,default_enabled:null};
 }
 const states=Object.fromEntries(features.map(f=>{const r=request[f.id]||{};return [f.id,{included:f.mandatory||r.included===true,requested_included:r.included??null,default_enabled:r.default_enabled??null,resolved_default_enabled:(f.mandatory||r.included===true)?(r.default_enabled??f.initial_default):null,default_source:r.default_enabled==null?f.default_source:'explicit',readiness:r.included?'ready':'not_included',code_required_by:[],runtime_required_by:[],missing_requirements:[]}];}));
 const testProjection='workbench_aaaaaaaaaaaa';
 const manualTest=snapshot.projections?.some(p=>p.id===testProjection);
 if(manualTest)states.test_view.included=true;
 if(states.test_view.included)states.test_view.code_required_by.push('explicit_resolution','projections.'+testProjection);
 for(const f of features)if(states[f.id].included)for(const id of f.code_requires){states[id].included=true;states[id].code_required_by.push(f.id);}
 if(snapshot.society_templates?.selected?.length){states.society_templates.included=true;states.society_templates.code_required_by.push('selected_templates');}
 const conflicts=states.tau.included&&states.tau.default_enabled===true&&states.test_framework.default_enabled===false?[{id:'tau',dependency:'test_framework',reason:'Runtime dependency explicitly disabled'}]:[];
 return {features,resolution:{resource_dependencies:{schema:'tfi.release-resource-dependencies.v2',projections:states.test_view.included?[{id:testProjection,selection_feature:'test_view',builtin:'test',revision:1,bindings:[{alias:'main',lineage_id:'lineage_8794980b1bb7'}],required_by:[...(manualTest?['manual.projections']:[]),...(states.test_view.requested_included?['feature_states.test_view']:[])]}]:[],types:[],lineages:[],projects:[],gaps:[],valid:true},states,conflicts,valid:!conflicts.length,included_features:Object.keys(states).filter(id=>states[id].included)}};
}
