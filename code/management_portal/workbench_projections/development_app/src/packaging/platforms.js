// Platform definitions and support facts belong to the release service. The
// Linux fallback below is only the explicitly agreed legacy/new-draft intent.
export function platformSelection(value={}) {
  const explicit=Array.isArray(value.target_platforms);
  return {target_platforms:explicit?[...new Set(value.target_platforms.filter(v=>typeof v==='string'&&v.trim()).map(v=>v.trim()))]:['linux'],
    target_platforms_source:explicit?(value.target_platforms_source||'explicit'):(Object.keys(value).length?'legacy-linux':'default')};
}
export const PLATFORM_RULES='target_platforms 是本次发行目标，不是已实现或已验证声明；按唯一目标目录解析必要功能×平台能力，缺口进入 needs_configuration 并列出原因，不将诊断/部分工程标为所有目标可安装。实现状态 implemented/incomplete/unsupported 与验证 not_run/passed/failed 分开；安装前核对实际 OS/架构、所选目标和必要能力。历史 run 快照保持原文，旧版 Linux 范围仅作明确派生事实。';

export function platformEditor(root,{sdk,signal,value,onChange,getFeatures=()=>[]}) {
  let state=platformSelection(value),catalogue=[],error='',loaded=false,verifying=false,resolution=null,generation=0,lastRequest='';
  const el=(tag,text='')=>{const n=document.createElement(tag);n.textContent=text;return n;};
  const box=el('fieldset'),body=el('div'),status=el('p');box.className='packagingPlatforms';status.className='packagingHint';
  box.append(el('legend','目标平台'),status,body);root.append(box);
  function draw(){
    if(signal.aborted)return;body.replaceChildren();
    status.textContent=[state.target_platforms_source==='legacy-linux'?'此配置来自旧版，编辑范围沿用 Linux；历史运行快照不变。':'选择要带出的目标平台。',error||(verifying?'正在核实所选目标…':!loaded?'正在读取平台目录…':'选择目标不代表已可安装或已验证。')].join(' ');
    const records=[...catalogue,...state.target_platforms.filter(id=>!catalogue.some(v=>v.id===id)).map(id=>({id,label:id}))];
    for(const item of records){
      const row=el('label'),input=el('input');row.className='packagingChoice';input.type='checkbox';input.value=item.id;input.checked=state.target_platforms.includes(item.id);
      input.addEventListener('change',()=>{state.target_platforms=state.target_platforms.filter(id=>id!==item.id);if(input.checked)state.target_platforms.push(item.id);state.target_platforms_source='explicit';draw();onChange();});
      row.append(input,document.createTextNode(item.label||item.id));body.append(row);
      const facts=el('p',`实现：${item.implementation||'尚未取得状态'}；验证：${item.verification||'尚未取得状态'}`);facts.className='packagingHint';body.append(facts);
      for(const gap of item.gaps||[])body.append(el('p',[gap.feature,gap.platform,gap.capability,gap.reason].filter(Boolean).join(' · ')));
    }
    if(!state.target_platforms.length)body.append(el('p','请至少选择一个目标平台。'));
    if(resolution)appendPlatformResolution(body,resolution);
  }
  async function load(force=true){
    const args={snapshot:{target_platforms:state.target_platforms},features:getFeatures()},key=JSON.stringify(args);
    if(!force&&key===lastRequest)return;lastRequest=key;const ticket=++generation;resolution=null;verifying=true;error='';draw();
    try{const result=await sdk.release('targets',args);if(ticket!==generation||signal.aborted)return;if(!Array.isArray(result.targets)||result.targets.some(v=>!v.id))throw Error('平台目录格式不可核实');catalogue=result.targets;resolution=result.resolution;loaded=true;verifying=false;error='';}
    catch(e){if(ticket!==generation||signal.aborted)return;resolution=null;verifying=false;error=`平台目录读取失败：${e.message}。已选目标保留。`;}draw();
  }
  const retry=el('button','重新读取平台');retry.type='button';retry.className='portalButton';retry.addEventListener('click',()=>load(true));box.append(retry);draw();
  return {read:()=>structuredClone(state),ready:()=>load(true),refresh:()=>load(false),validate(){if(!state.target_platforms.length)throw Error('请至少选择一个目标平台');}};
}

export function appendPlatformResolution(root,resolution){
  const rows=resolution?.support_matrix;if(!Array.isArray(rows))return;
  const el=(tag,text='')=>{const node=document.createElement(tag);node.textContent=text;return node;};
  const details=el('details'),gaps=resolution.capability_gaps||rows.filter(r=>r.implementation!=='implemented');
  details.append(el('summary',`功能 × 平台能力与验证（${gaps.length} 项实现缺口）`));
  const table=el('table'),head=el('tr');for(const title of ['功能','平台','能力','实现','验证','原因','证据'])head.append(el('th',title));table.append(head);
  for(const row of rows){const tr=el('tr');for(const key of ['feature','platform','capability','implementation','verification','reason'])tr.append(el('td',String(row[key]??'尚未记录')));const cell=el('td'),evidence=el('details');evidence.append(el('summary','记录详情'),el('pre',JSON.stringify(row,null,2)));cell.append(evidence);tr.append(cell);table.append(tr);}
  details.append(table);root.append(details);
}

export function appendPlatformResults(root,records){
  if(!records)return;
  const el=(tag,text='')=>{const n=document.createElement(tag);n.textContent=text;return n;};
  const box=el('section');box.append(el('h3','分平台新装与升级验证'),el('p','仅按记录中的平台及路径解释结果；未记录不表示通过。'));
  if(Array.isArray(records))for(const row of records){const details=el('details');details.append(el('summary',`${row.platform||'未注明平台'} · ${row.scenario||row.operation||'未注明验证路径'} · ${row.verification||row.status||'未注明验证结果'}`),el('pre',JSON.stringify(row,null,2)));box.append(details);}
  else box.append(el('pre',JSON.stringify(records,null,2)));
  root.append(box);
}
