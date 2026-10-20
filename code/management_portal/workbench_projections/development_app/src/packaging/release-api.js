// The host owns transport; no fetch, credentials or actor impersonation in the
// sandbox. Keep backend envelopes in this adapter rather than form controls.
export function releaseApi(sdk) {
  const call=(operation,args={})=>{
    if(typeof sdk.release!=='function')throw Error('发布运行服务尚未接入，请刷新宿主页后重试');
    return sdk.release(operation,args);
  };
  const config=value=>({...value,checklist:value.snapshot??value.checklist});
  const run=value=>({...value,...value.result,run_id:value.run_id,state:value.state,mode:value.mode,
    responsible_resident_id:value.responsible_resident_id,supervisor_resident_id:value.supervisor_resident_id,
    stages:(value.stages||[]).map(stage=>({...stage,name:stage.stage||stage.name,state:stage.evidence?.state||stage.state,evidence:stage.evidence})),result:value.result});
  return {
    configs:async()=>{const result=await call('configs.list');return result.configs.map(config);},
    config:async config_id=>config((await call('configs.get',{config_id})).config),
    saveConfig:async value=>config((await call('configs.save',{name:value.name,snapshot:value.checklist,request_key:value.request_key,
      ...(value.config_id?{config_id:value.config_id}:{})})).config),
    runs:async()=>{const result=await call('runs.list');return result.runs;},
    run:async run_id=>run((await call('runs.get',{run_id})).run),
    startRun:async value=>run((await call('runs.start',{request_key:value.request_key,config_id:value.config_id,...(value.use_config_snapshot?{}:{snapshot:value.checklist_snapshot}),
      mode:value.mode,tau_validation:value.validate_tau,provenance:{...(Object.hasOwn(value,'config_revision')?{config_revision:value.config_revision}:{}),request_text:value.request_text}})).run),
  };
}

// Only the packaging editor opts into this closed release-candidate surface.
// Chat/history keep their existing business transport and permissions.
export function releaseCandidates(sdk) {
  const candidate=async(operation,args={})=>{
    const result=await sdk.release('candidates',{operation,arguments:args});
    // The release index exposes authoritative ownership in attributes.
    if(operation==='SearchResources')return {...result,items:(result.items||[]).map(item=>({...item,project:item.project??item.attributes?.project_ids}))};
    return result;
  };
  return {...sdk,
    hatg:async(name,args={},options)=>{
      if(name==='DeliverMessage')return sdk.hatg(name,args,options);
      if(name==='GetLineage'&&!args.lineage_id&&options?.binding){
        const lineage_id=(await sdk.context()).bindings?.[options.binding]?.lineage_id;
        if(!lineage_id)throw Error('Lineage 绑定不可读取');
        args={...args,lineage_id};
      }
      return candidate(name,args);
    },
    resource:(operation,args)=>candidate(operation,args),
    directories:args=>candidate('directories',args),
  };
}
