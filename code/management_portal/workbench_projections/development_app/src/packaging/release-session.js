import {newRunRequest} from './release-runs.js';

// JSONB may return object keys in a different order. Array order and every
// actual value remain significant when comparing a draft with the saved content.
const canonical=value=>JSON.stringify(value,(_,item)=>item&&typeof item==='object'&&!Array.isArray(item)
  ?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);

// Frozen relationships are service-owned evidence, not editable checklist fields.
const editable=({resource_dependencies,...selection})=>selection;

// Persistence belongs to the release service; this controller only retains
// navigation and uncertain request receipts in the existing page draft.
export function releaseSession({api,readState,writeState,collect,requestText}) {
  const state=()=>structuredClone(readState()||{});
  const update=async patch=>writeState({...state(),...patch});
  const remember=config=>({config_id:config.config_id,name:config.name});
  return {
    state,
    async save(name,{copy=false}={}) {
      name=String(name||'').trim();
      if(!name)throw Error('请填写配置名称');
      const previous=state().config;
      const request={name,checklist:structuredClone(collect(false)),request_key:crypto.randomUUID().replaceAll('-','')};
      if(previous&&!copy){request.config_id=previous.config_id;}
      // Persist a request identity before transport; the service generates the
      // actual configuration ID and replays lost save receipts by request_key.
      const pending=state().pending_config;
      const same=pending&&pending.payload.config_id===request.config_id
        &&pending.payload.name===request.name&&canonical(pending.payload.checklist)===canonical(request.checklist)&&pending.copy===copy;
      const payload=same?pending.payload:request;
      await update({pending_config:{copy,payload}});
      const config=await api.saveConfig(payload);
      await update({config:remember(config),pending_config:null});
      return config;
    },
    async load(id) {
      const config=await api.config(id);
      await update({config:remember(config)});
      return structuredClone(config.checklist);
    },
    async start(mode,validateTau) {
      const selected=state().config;
      if(!selected)throw Error('请先命名保存配置');
      const config=await api.config(selected.config_id);
      const checklist=collect(true);
      if(canonical(editable(checklist))!==canonical(editable(config.checklist)))throw Error('清单已修改，请先保存配置');
      const request=newRunRequest({config,mode,validateTau,requestText:requestText(checklist)});
      const pending=[...(state().pending_runs||[]),request];
      await update({pending_runs:pending});
      return this.retry(request.request_key);
    },
    async retry(id) {
      const request=(state().pending_runs||[]).find(item=>item.request_key===id);
      if(!request)throw Error('没有待核实的原运行请求');
      const run=await api.startRun(structuredClone(request));
      await update({pending_runs:(state().pending_runs||[]).filter(item=>item.request_key!==id),run_id:run.run_id});
      return run;
    },
    async selectRun(id) {await update({run_id:id});return api.run(id);},
  };
}
