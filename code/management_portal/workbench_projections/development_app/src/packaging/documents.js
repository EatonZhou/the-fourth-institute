import {marked} from 'marked';
import {readInstance,resolvedType} from './selection-model.js';
const raw=v=>typeof v==='string'?v:'';
export const DOCUMENT_KINDS=['SPEC.md','IMPROVE.md'];
export function normalizeDocuments(value={}) {
  return Object.fromEntries(DOCUMENT_KINDS.map(kind=>{const v=value?.[kind]||{};return [kind,{
    enabled:v.enabled===true,address:raw(v.address),source:raw(v.source),entries:[...new Set((v.entries||[]).filter(v=>typeof v==='string'))],
    description:raw(v.description),pending_legacy:v.pending_legacy===true,scope:'applicable_only',
    requirements:(v.requirements||[]).filter(item=>typeof item.type==='string'&&Array.isArray(item.project_ids)&&Array.isArray(item.entries)).map(item=>({type:item.type,project_ids:[...new Set(item.project_ids.filter(v=>typeof v==='string'))],entries:[...new Set(item.entries.filter(id=>v.entries?.includes(id)))]})).filter(item=>item.entries.length),
  }];}));
}
// Parse index tables and primary Markdown list links, never prose references.
export function parseDocumentIndex(text,kind,address) {
  const entries=new Map();let path=[];
  const base=address.slice(0,address.lastIndexOf('/')+1);
  const clean=value=>value.replace(/[`*_]/g,'').trim();
  const add=(id,title)=>{if(!entries.has(id))entries.set(id,{id,title:clean(title)||id,path:[...path]});};
  for(const token of marked.lexer(text)){
    if(token.type==='heading'&&token.depth===2)path=[clean(token.text)];
    else if(token.type==='heading'&&token.depth===3)path=[path[0]||'未分组',clean(token.text)];
    else if(token.type==='table')for(const row of token.rows){
      const cells=row.map(v=>v.text),ids=cells.flatMap(v=>v.match(/\b[a-z][a-z0-9_]*_[0-9a-f]{12}\b/g)||[]);
      for(const id of ids)if(kind!=='IMPROVE.md'||id.startsWith('plan_'))add(id,cells[0]);
    }
    else if(token.type==='list')for(const item of token.items){
      for(const nested of item.tokens||[])for(const link of nested.tokens||[]){
        if(link.type!=='link'||!link.href?.endsWith('.md')||link.href.includes('://')||link.href.startsWith('/')||link.href.split('/').includes('..'))continue;
        if(kind==='IMPROVE.md'&&!link.href.startsWith('.wall/plans/'))continue;
        add(base+link.href,link.text);break;
      }
    }
  }
  return [...entries.values()];
}
export const DOCUMENT_RULES='SPEC.md 与 IMPROVE.md 由 document_indexes 分别选择；entries 是索引中已选正文地址，scope=applicable_only 表示仅带出本次实际发布功能适用的内容。全选与模块内全选也受此范围限制。未发布功能的对应测试用例、规范、Plan及索引项默认不带；测试框架不等于全部用例。共享及跨模块条目结合保留能力处理，禁止仅凭名称关键词删除。页面未完成自动功能关联筛选，关联未知、索引来源与源码不一致或选择冲突时，执行Resident查询核实并说明，不静默带回功能。只修改发行副本SPEC.md/IMPROVE.md索引、所选正文和必要引用，源文档不变；带出WALL时读取发行索引与正文，不沿用已排除条目的源缓存。选择Plan只是携带文档，不执行计划。pending_legacy为原规范索引选择待核对，不代表全选。';

export const DOCUMENT_LINK_RULES='所选 SPEC.md/IMPROVE.md 正文所需真实资源类型自动包含，resource_types 中 manual=false 表示仅由 document_entries 依赖带入；未手动勾选不能排除它，不带出该类型全部实例，也不自动启用规范描述的业务功能。document_indexes.requirements 保存所选正文实际类型、所属 Project 与地址，既有所属 Project 和必要结构规则继续适用。知识条目的二次关联不递归带出；只有用户也选择的目标才包含。打包时仅在发行副本调整元数据 links 和正文资源链接：已选目标保留链接并适配发行地址，未选目标去链接或改为普通文字，不保留未发行资源或源站点的失效跳转；源文档、源索引及源链接保持不变。';

export function documentEditor(root,{sdk,signal,value,getSource,onChange}) {
  const state=normalizeDocuments(value),records=new Map();let pending=Promise.resolve();
  const el=(tag,text='',cls='')=>{const n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;return n;};
  const section=el('section','','packagingDocuments');root.append(section);section.addEventListener('input',e=>e.stopPropagation());
  const button=(text,fn)=>{const b=el('button',text,'portalButton');b.type='button';b.onclick=()=>void fn();return b;};
  const checkbox=(text,checked,fn)=>{const label=el('label','','packagingChoice'),c=el('input');c.type='checkbox';c.checked=checked;c.onchange=()=>fn(c.checked);label.append(c,document.createTextNode(text));return label;};
  const change=()=>{draw();onChange();};
  async function resolveRequirements(kind){
    const v=state[kind],r=records.get(kind),generation=r.generation;
    if(!r.loaded||r.address!==v.address)return;
    const details=new Map(r.details);
    const ids=[...v.entries];const failures=[];
    for(let i=0;i<ids.length;i+=8)await Promise.all(ids.slice(i,i+8).map(async id=>{
      try{
        if(!r.items.some(item=>item.id===id&&item.readable))throw Error('不在当前可读索引中');
        if(details.has(id))return;
        const resolved=await sdk.hatg('ResolveResource',{address:id}),type=resolvedType(resolved),root=resolved.resource?.resource_id;
        if(!type||!root)throw Error('正文真实类型无法核实');
        const ownership=await readInstance(sdk,type,root);
        if(!ownership.verified)throw Error(ownership.error);
        details.set(id,{type,project_ids:ownership.project_ids});
      }catch(error){failures.push(`${id}：${error.message}`);}
    }));
    if(signal.aborted||generation!==r.generation)return;
    r.details=details;
    const groups=new Map();
    for(const id of v.entries){const item=r.details.get(id);if(!item)continue;const key=JSON.stringify(item);
      if(!groups.has(key))groups.set(key,{...item,entries:[]});groups.get(key).entries.push(id);}
    // Keep last-known dependencies visible when verification fails, never claim
    // they disappeared merely because the current actor cannot read them.
    for(const previous of v.requirements)for(const id of previous.entries)if(v.entries.includes(id)&&!r.details.has(id)){
      const item={type:previous.type,project_ids:previous.project_ids},key=JSON.stringify(item);
      if(!groups.has(key))groups.set(key,{...item,entries:[]});groups.get(key).entries.push(id);
    }
    v.requirements=[...groups.values()];r.dependencyError=failures.join('；');
  }
  const select=(kind,entries)=>{
    state[kind].entries=entries;state[kind].requirements=normalizeDocuments(state)[kind].requirements;change();
    pending=pending.then(async()=>{await resolveRequirements(kind);if(!signal.aborted){draw();onChange();}});
  };
  async function load(kind){
    const v=state[kind],r=records.get(kind);const generation=++r.generation;r.loading=true;r.loaded=false;r.error='';r.dependencyError='';draw();
    try{
      if(!v.address){
        const context=await sdk.context(),resource=await sdk.resource('read',{address:context.workbench_id});
        const app=JSON.parse(resource.content||resource.text);if(!app.application?.repo_id)throw Error('请填写实际源码索引的资源地址');
        v.address=`${app.application.repo_id}/file/${kind}`;
      }
      const address=v.address,result=await sdk.resource('read',{address});
      if(result.encoding&&result.encoding!=='utf-8')throw Error('索引不是可读取文本');
      const items=parseDocumentIndex(result.content??result.text,kind,address);
      for(let i=0;i<items.length;i+=128){const batch=items.slice(i,i+128),resolved=await sdk.resource('resolve_many',{addresses:batch.map(v=>v.id)});
        for(const item of batch){const result=resolved.results.find(v=>v.address===item.id);item.readable=result?.ok===true&&result.result?.readable===true;}}
      if(signal.aborted||generation!==r.generation)return;
      r.items=items;r.details.clear();r.address=address;if(!v.source)v.source=getSource();r.loaded=true;
      await resolveRequirements(kind);
    }catch(e){if(generation===r.generation){r.error=`读取失败：${e.message}`;r.loaded=false;r.items=[];}}
    finally{if(!signal.aborted&&generation===r.generation){r.loading=false;draw();onChange();}}
  }
  function draw(){
    if(signal.aborted)return;
    const opened=new Set([...section.querySelectorAll('details[open]')].map(n=>n.dataset.group));section.replaceChildren();
    for(const kind of DOCUMENT_KINDS){
      const v=state[kind],r=records.get(kind),group=el('fieldset');group.append(el('legend',kind));
      const verified=r.loaded&&!r.loading&&r.address===v.address;
      group.append(checkbox(`带出 ${kind}`,v.enabled,checked=>{v.enabled=checked;change();if(checked&&!r.loaded)pending=load(kind);}));
      if(v.pending_legacy)group.append(el('p','原“规范索引”选择待核对；未自动选中所有文档。','packagingHint'),button('已核对原规范索引选择',()=>{v.pending_legacy=false;change();}));
      if(v.enabled){
        group.append(el('p','按源索引分组；功能关联待核实。未带功能的相关内容默认排除，共享条目需结合实际范围。全选仅表达适用范围内的选择，未执行自动筛选；冲突不会自动带回功能。','packagingHint'));
        const label=el('label',`${kind} 索引资源地址`),input=el('input','','portalField');input.value=v.address;input.placeholder='仓库资源ID/file/'+kind;
        input.oninput=()=>{v.address=input.value;v.source='';r.generation++;r.loaded=false;r.loading=false;r.items=[];r.error='';r.dependencyError='';onChange();};label.append(input);group.append(label,button('读取索引',()=>pending=load(kind)));
        group.append(el('p',`索引来源：${v.address||'尚未读取'}。须与源码“${getSource()||'未设置'}”对应；来源及功能适用性待执行团队核实。`,'packagingHint'));
        if(v.source&&v.source!==getSource())group.append(el('p','源码来源已变化：当前文档选择与源码可能冲突，请重新读取并核对。','packagingSelectionErrors'));
        if(r.loading)group.append(el('p','正在读取索引并核对条目…'));
        if(r.error)group.append(el('p',r.error,'packagingSelectionErrors'));
        if(!verified&&v.entries.length)group.append(el('p',`已保留 ${v.entries.length} 项选择，等待索引读取成功后核对。`,'packagingHint'));
        if(verified&&r.dependencyError)group.append(el('p',`正文类型或所属项目待核实：${r.dependencyError}`,'packagingSelectionErrors'));
        for(const required of v.requirements)group.append(el('p',`所选正文需要 ${required.type} 类型支持，已自动包含；所属 Project：${required.project_ids.join('、')||'无'}。`,'packagingHint'));
        const pick=items=>select(kind,[...new Set([...v.entries,...items.filter(v=>v.readable).map(v=>v.id)])]);
        const all=button('全选（仅适用内容）',()=>pick(r.items));all.disabled=!verified;group.append(all,button('清空选择',()=>select(kind,[])));
        const groups=new Map();for(const item of verified?r.items:[]){const name=item.path[0]||'未分组';if(!groups.has(name))groups.set(name,[]);groups.get(name).push(item);}
        for(const [name,items] of groups){const details=el('details'),summary=el('summary',name);details.dataset.group=kind+':'+name;details.open=opened.has(details.dataset.group);details.append(summary,button('模块内全选（仅适用内容）',()=>pick(items)));
          for(const item of items){const row=checkbox(`${item.title} · ${item.id}${item.readable?'':'（不可读取）'}`,v.entries.includes(item.id),selected=>select(kind,selected?[...new Set([...v.entries,item.id])]:v.entries.filter(id=>id!==item.id)));row.title=item.path.join(' → ');row.querySelector('input').disabled=!item.readable;details.append(row);}
          group.append(details);
        }
        const unavailable=verified?v.entries.filter(id=>!r.items.some(v=>v.id===id&&v.readable)):[];
        for(const id of unavailable){const indexed=r.items.some(v=>v.id===id);group.append(el('p',`${id}：${indexed?'条目在索引中，但资源未能读取，请重试或核对资源状态。':'当前索引未包含此已选条目，请核对索引来源。'}`),button(`移除 ${id}`,()=>select(kind,v.entries.filter(v=>v!==id))));}
        if(verified&&!r.items.length)group.append(el('p','索引中没有可选择条目。'));
      }
      const label=el('label',`${kind} 文字范围`),area=el('textarea','','portalField');area.value=v.description;area.oninput=()=>{v.description=area.value;onChange();};label.append(area);group.append(label);section.append(group);
    }
  }
  for(const kind of DOCUMENT_KINDS)records.set(kind,{items:[],details:new Map(),dependencyError:'',loaded:false,loading:false,error:'',generation:0});draw();
  return {read:()=>normalizeDocuments(state),sourceChanged:draw,ready:()=>pending=Promise.all(DOCUMENT_KINDS.filter(k=>state[k].enabled).map(load)),settle:()=>pending,validate:async()=>{
    await pending;
    for(const kind of DOCUMENT_KINDS)if(state[kind].enabled){const before=JSON.stringify(state[kind].requirements);await load(kind);const r=records.get(kind);if(!r.loaded||r.dependencyError||state[kind].entries.some(id=>!r.items.some(v=>v.id===id&&v.readable)))throw Error(`${kind} 有不可读取、类型/归属待核实或已离开索引的选择，请核对`);if(before!==JSON.stringify(state[kind].requirements))throw Error(`${kind} 正文类型或归属发生变化，已更新清单，请重新核对后提交`);}
  }};
}
