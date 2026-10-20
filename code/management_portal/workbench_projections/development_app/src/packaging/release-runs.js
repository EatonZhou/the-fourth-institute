// A run is an explicit user action. Version names and config IDs are never
// idempotency keys; the serialized request is retained for uncertain retries.
export function newRunRequest({config,mode,validateTau=false,requestText=''}) {
  if(!['publish','full_test'].includes(mode))throw new Error('请选择发布或完整发布测试');
  if(!config?.config_id||!config.checklist)throw new Error('请先保存配置');
  return {
    request_key:crypto.randomUUID().replaceAll('-',''),
    config_id:config.config_id,use_config_snapshot:true,
    mode,validate_tau:mode==='full_test'&&validateTau===true,
    checklist_snapshot:structuredClone(config.checklist),request_text:String(requestText),
  };
}

const STATES={queued:'等待执行',awaiting_supervisor:'等待监督接手',resolving:'解析发布范围',generating:'生成工程',generated:'工程已生成，等待后续验证',installing:'安装独立实例',checking:'验证中',running:'运行中',installed:'实例已安装，等待完整验证',needs_configuration:'需要补齐配置或能力',succeeded:'已完成',failed:'失败',cancelled:'已取消'};
export function runPresentation(run={}) {
  const state=run.state||run.status||'unknown';
  return {state,label:STATES[state]||`状态：${state}`,success:state==='succeeded',terminal:['succeeded','failed','cancelled','needs_configuration'].includes(state)};
}
export function safeResultUrl(value) {
  if(typeof value!=='string'||value!==value.trim()||/[\u0000-\u0020\\]/.test(value))return null;
  if(value.startsWith('/')&&!value.startsWith('//'))return value;
  try { const url=new URL(value);return ['http:','https:'].includes(url.protocol)&&!url.username&&!url.password?url.href:null; } catch{return null;}
}
