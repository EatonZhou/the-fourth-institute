import {featureStateEditor} from './feature-states.js';
import {platformEditor} from './platforms.js';
import {releasePanel} from './release-panel.js';
import {releaseApi,releaseCandidates} from './release-api.js';
import {societyTemplateEditor} from './society-templates.js';
import {releaseFeatures,FEATURE_TITLES} from './feature-dependencies.js';
import './style.css';
import './layout.css';
import { CATEGORIES, FAMILY, TARGET, normalizeChecklist, restoreChecklist, requestFor, requestState } from './model.js';
import { renderMarkdown } from '../shared/markdown.js';
import { chooseDirectory } from './directory-picker.js';
import { createSelectionEditor } from './selection-editor.js';
import {themeEditor} from './themes.js';
import {documentEditor} from './documents.js';
import {optionalFeatureEditor} from './optional-features.js';
import {systemFlowEditor} from './system-flows.js';
import {permissionEditor} from './permissions.js';

export async function mount(root, { sdk, signal: parentSignal }) {
  sdk=releaseCandidates(sdk);
  const lifecycle=new AbortController(), signal=lifecycle.signal;
  if(parentSignal.aborted)lifecycle.abort();
  parentSignal.addEventListener('abort',()=>lifecycle.abort(),{once:true,signal});
  let successor=null;

  let saved = await sdk.state(), visible = true, timer, busy = false;
  let editRevision = 0, savedRevision = 0, stateQueue = Promise.resolve(), autoSaveTimer;
  signal.addEventListener('abort',()=>clearTimeout(autoSaveTimer),{once:true});
  const writeState = () => {
    const snapshot = structuredClone(saved);
    const writing = stateQueue.catch(() => {}).then(() => sdk.state(snapshot));
    stateQueue = writing;
    return writing;
  };
  let draft = await restoreChecklist(saved.checklist,
    id => sdk.hatg('ResolveResource', {address:id}), id => sdk.hatg('GetProject', {project_id:id}));
  const node = (tag, text, cls) => { const el = document.createElement(tag); if (text) el.textContent = text; if (cls) el.className = cls; return el; };
  root.className = 'packaging';
  const header = node('header'); header.append(node('h1', '打包发布'), node('p', '选择要带出的功能和数据，核对后交给 Portal 开源版本团队。'));
  const notice = node('div', '', 'packagingNotice'); notice.setAttribute('role', 'status');
  const showError = (error, context='操作未完成') => {
    const message = error?.message || '未知错误';
    // A bare KeyError field name is diagnostic data, not a user-facing status.
    const missingField = /^(['"])[\w.]+\1$/.test(message);
    notice.replaceChildren(node('span', `${context}：${missingField ? '服务端处理失败，请重试；当前输入仍保留。' : message}`));
    if (missingField) {
      const details = node('details');
      details.append(node('summary', '诊断信息'), node('pre', message));
      notice.append(details);
    }
  };
  const configRoot=node('section','','packagingConfig');
  // Configuration controls manage their own state; they are not checklist edits.
  configRoot.addEventListener('input',event=>event.stopPropagation());
  const form = node('form', '', 'packagingForm');
  const setup=node('div','','packagingSetup'),basics=node('section','','packagingBasics');
  basics.append(node('h2','版本与位置'));setup.append(configRoot,basics);form.append(setup);
  const editScope=node('fieldset','','packagingEditScope');
  const fields = {};
  const sourceLabel = node('label', '源码来源'), source = node('select', '', 'portalField'); source.name = 'source';
  for (const [value, title] of [['','请选择源码来源'], ['Dev','Dev'], ['Release','Release'], ['other','其他来源']]) {
    const option = node('option', title); option.value = value; source.append(option);
  }
  const customSourceLabel = node('label', '源码目录或仓库地址'), customSource = node('input', '', 'portalField');
  customSource.name = 'custom_source'; customSource.maxLength = 2000; customSource.placeholder = '填写源码目录或仓库地址';
  source.value = ['Dev','Release',''].includes(draft.source) ? draft.source : 'other';
  customSource.value = source.value === 'other' ? draft.source : '';
  const showCustomSource = () => { customSourceLabel.hidden = source.value !== 'other'; customSource.disabled = customSourceLabel.hidden; };
  source.addEventListener('change', () => { showCustomSource(); markDirty(); });
  showCustomSource(); sourceLabel.append(source); customSourceLabel.append(customSource);
  for (const [key, title, placeholder] of [['version','版本名称','例如 v1.0'], ['destination','发布位置','填写产物目录或目标仓库地址']]) {
    const label = node('label', title); const input = node('input', '', 'portalField'); input.name = key; input.value = draft[key]; input.placeholder = placeholder; input.maxLength = key === 'version' ? 80 : 2000; fields[key] = input; label.append(input); basics.append(label);
    if (key === 'destination') {
      // Keep the button outside the input label so its accessible name stays stable.
      input.id='packaging-destination';label.htmlFor=input.id;
      const row=node('div','','packagingDestination'), browse=node('button','浏览','portalButton');browse.type='button';
      row.append(input,browse);label.after(row);
      browse.addEventListener('click',()=>run(async()=>{
        const path=await chooseDirectory({sdk,signal,initialPath:input.value.trim()});
        if(path!==null&&!signal.aborted){input.value=path;markDirty();}
      }));
    }
    if (key === 'version') basics.append(sourceLabel, customSourceLabel);
  }
  const choices = node('section', '', 'packagingCategories'); const categoryFields = {};
  for (const [key, title, options] of CATEGORIES) {
    const group = node('fieldset'); group.append(node('legend', title));
    if(key==='tests')group.append(node('p','测试框架：目录、执行与结果记录。TAU：Agent 实际使用验证，包含必要框架支持。测试页面在关联投影中选择；账号、执行槽位、历史运行及全部测试团队不自动带出。','packagingHint'));
    const checks = options.map(title => {
      const label = node('label', '', 'packagingChoice'), input = node('input'); input.type = 'checkbox'; input.value = title; input.checked = draft.categories[key].includes(title); input.manualChecked=input.checked; input.addEventListener('input',()=>{input.manualChecked=input.checked;}); label.append(input, document.createTextNode(title)); group.append(label); return input;
    });
    const custom = node('textarea', '', 'portalField'); custom.rows = 2; custom.placeholder = key==='tests'?'测试用例范围或补充说明':'其他内容，每行一项'; custom.setAttribute('aria-label', `${title}其他内容`);
    const description=draft.attachment_descriptions[key]||'',extras=draft.categories[key].filter(v=>!options.includes(v));
    custom.value=['tests','docs'].includes(key)?[description,...extras.filter(v=>!description.split('\n').map(line=>line.trim()).includes(v))].filter(Boolean).join('\n'):description||extras.join('\n');
    group.append(custom); categoryFields[key] = { checks, custom }; choices.append(group);
  }
  const optionalArea=node('section','','packagingOptionalArea');form.append(optionalArea);
  const optionalFeatures=optionalFeatureEditor(optionalArea,{value:draft.optional_features,aliases:[categoryFields.tests.checks.find(v=>v.value==='TAU 功能').parentElement,categoryFields.tools.checks.find(v=>v.value==='Telepus').parentElement]});
  const featureStates=featureStateEditor(optionalArea,{sdk,signal,value:draft,getSnapshot:()=>collect(false),onChange:()=>markDirty(),onResolved:resolution=>{system.refresh();void editor.syncProjectionFeatures(resolution);void platforms.refresh();}});
  for(const input of optionalArea.querySelectorAll('.packagingOptionalFeatures input'))featureStates.bind(({"TAU 功能":'tau',Telepus:'telepus'})[input.value]||input.value,input,input.parentElement.textContent);
  featureStates.bind('test_framework',categoryFields.tests.checks.find(v=>v.value==='测试框架'),'测试框架');
  const button = (text, fn, readOnly=false) => { const b = node('button', text, 'portalButton'); b.type = 'button'; b.addEventListener('click', () => readOnly?read(fn):run(fn)); return b; };
  const selections=node('section','','packagingSelections');form.append(selections);
  const editor=createSelectionEditor(selections,{sdk,signal,value:draft,featureStates,onChange:()=>markDirty()});
  const templates=societyTemplateEditor(editor.societyRoot,{sdk,signal,value:draft.society_templates,onChange:()=>markDirty()});
  const system=systemFlowEditor(optionalArea,{sdk,signal,value:draft.system_lineage,featureStates,getFeatures:()=>optionalFeatures.read(),onChange:()=>markDirty(),onDependency:value=>editor.setSystem(value)});
  optionalArea.addEventListener('input',event=>{if(event.target.closest('.packagingOptionalFeatures'))system.refresh();});
  const platforms=platformEditor(form,{sdk,signal,value:draft,getFeatures:()=>featureStates.isReady()?featureStates.effectiveFeatures():releaseFeatures(collect(false)).included,onChange:()=>markDirty()});
  const permissions=permissionEditor(form,{sdk,signal,value:draft.permission_selection,onChange:()=>markDirty()});
  const themes=themeEditor(form,{sdk,signal,value:draft.themes,onChange:()=>markDirty()});
  const documents=documentEditor(form,{sdk,signal,value:draft.document_indexes,getSource:()=>source.value==='other'?customSource.value:source.value,onChange:()=>{editor.setDocuments(documents.read());markDirty();}});
  source.addEventListener('change',()=>documents.sourceChanged());customSource.addEventListener('input',()=>documents.sourceChanged());
  form.append(node('h2','附带内容'),choices);
  const notesLabel = node('label', '补充说明'), notes = node('textarea', '', 'portalField'); notes.rows = 3; notes.value = draft.notes; notesLabel.append(notes); form.append(notesLabel);
  const collect = validate => normalizeChecklist({ ...(Object.hasOwn(draft,'native_switch_site_config')?{native_switch_site_config:draft.native_switch_site_config}:{}), ...editor.read(),...platforms.read(),...featureStates.read(),society_templates:templates.read(),legacy_mandatory_content:draft.legacy_mandatory_content,system_lineage:system.read(),permission_selection:permissions.read(),optional_features:optionalFeatures.read(),themes:themes.read(),document_indexes:documents.read(), ...Object.fromEntries(Object.entries(fields).map(([k,v]) => [k,v.value])), source:source.value === 'other' ? customSource.value : source.value, categories:Object.fromEntries(Object.entries(categoryFields).map(([k,{checks,custom}]) => [k,[...checks.filter(v => v.manualChecked).map(v => v.value),...custom.value.split('\n')]])), attachment_descriptions:Object.fromEntries(Object.entries(categoryFields).map(([k,{custom}])=>[k,custom.value])), notes:notes.value }, {validate});
  const framework=categoryFields.tests.checks.find(v=>v.value==='测试框架');
  const dependencyHint=node('p','','packagingHint');dependencyHint.dataset.featureDependency='test_framework';framework.parentElement.after(dependencyHint);
  const refreshDependencies=()=>{
    if(Object.hasOwn(featureStates.read(),'feature_states')){dependencyHint.textContent='';return;}
    const features=releaseFeatures({...editor.read(),society_templates:templates.read(),optional_features:optionalFeatures.read(),categories:Object.fromEntries(Object.entries(categoryFields).map(([key,{checks}])=>[key,checks.filter(v=>v.manualChecked).map(v=>v.value)]))});
    const reasons=features.required_by.test_framework||[];framework.checked=framework.manualChecked||reasons.length>0;framework.disabled=reasons.length>0;
    dependencyHint.textContent=reasons.length?`自动包含：${reasons.map(id=>FEATURE_TITLES[id]||id).join('、')} 需要测试框架。撤销依赖后保留原手动选择。`:'';
  };
  const persist = async (automatic = false) => {
    // A checkbox may still be resolving its actual Workbench and bindings.
    // Explicit save must capture the completed edit, not the preceding model.
    if (!automatic) {
      clearTimeout(autoSaveTimer);
      await editor.settle(); await documents.settle(); await system.settle();
      await permissions.settle(); await templates.settle(); await featureStates.settle();
      await editor.settle();
    }
    const revision = editRevision;
    draft = collect(false); saved = {...saved,checklist:draft};
    await writeState(); savedRevision = Math.max(savedRevision, revision);
    if (!signal.aborted && revision === editRevision) notice.textContent = automatic ? '清单已自动保存' : '清单已保存';
  };
  const reviewed = node('input'); reviewed.type = 'checkbox';
  const reviewLabel = node('label', '', 'packagingChoice'); reviewLabel.append(reviewed, document.createTextNode('已核对本次功能、数据和发布位置'));
  const markDirty = () => {
    if(signal.aborted)return;
    refreshDependencies();void featureStates.refresh();void platforms.refresh();reviewed.checked = false; editRevision++; notice.textContent = '正在自动保存…';
    clearTimeout(autoSaveTimer);
    autoSaveTimer=setTimeout(()=>{void persist(true).catch(error => {
      if (!signal.aborted) showError(error,'自动保存失败（请点击保存清单重试，暂勿刷新）');
    });},180);
  };
  form.addEventListener('input', event => { if (event.target !== reviewed) markDirty(); }); form.addEventListener('submit', event => event.preventDefault());
  const results = node('section', '', 'packagingResults'), status = node('p'), resultBody = node('div'); results.append(node('h2','执行与结果'), status, resultBody);
  const openLink = async ref => {
    if (/^https?:\/\//.test(ref)) { await sdk.openChat(); return; }
    const resource = await sdk.resource('resolve', {address:ref});
    if (resource.readable && resource.portal_url) await sdk.openPage(resource.portal_url);
    else throw new Error('该产物不可读取，请在团队对话中查看');
  };
  const refresh = async () => {
    clearTimeout(timer);
    const submission = saved.submission;
    if (!submission?.message_id) { status.textContent = submission ? '提交结果待核实，可用原请求继续核实' : '尚未提交打包任务'; return; }
    status.textContent = `${submission.version} · 正在读取状态`;
    try {
      const result = await sdk.inputResult(submission.message_id, TARGET);
      if (signal.aborted) return;
      status.textContent = `${submission.version} · ${requestState(result)}`;
      const error = result.error ? (typeof result.error === 'string' ? result.error : JSON.stringify(result.error)) : '';
      renderMarkdown(resultBody, result.text || error || '请求已交给团队；尚无发布结果。', {onLink:ref => run(() => openLink(ref))});
      for (const message of result.messages || []) for (const part of message.content_parts || []) {
        if (part.type === 'resource_reference' && part.address) resultBody.append(button(part.title || part.address, () => openLink(part.address)));
      }
      if (visible && ['running','queued'].includes(result.state)) timer = setTimeout(() => {void refresh().catch(error=>{if(!signal.aborted)showError(error,'读取失败');});}, 15000);
    } catch (error) { status.textContent = '状态读取失败'; throw error; }
  };
  const prepareRelease = async () => {
    await editor.settle();await documents.settle();
    await system.settle();await permissions.settle();await templates.settle();
    if (!reviewed.checked) throw new Error('请先核对清单并勾选确认');
    await featureStates.validate();await editor.validate();await themes.validate();await documents.validate();
    await system.validate();await permissions.validate();await templates.validate();
    await persist();
    platforms.validate();
    collect(true);
  };
  const sendOriginal = async () => {
    if (editRevision !== savedRevision) await persist();
    const submission=saved.submission;
    if(!submission?.key || !submission.text)throw new Error('没有可重试的原请求');
    if(submission.message_id) { await refresh(); await sdk.openChat(); return; }
    const receipt = await sdk.hatg('DeliverMessage', { message:{content_parts:[{type:'text',text:submission.text}]}, business_ref:FAMILY }, {...TARGET,idempotency_key:submission.key});
    if (!receipt?.message_id) throw new Error('未取得投递回执，请保留原清单并继续核实');
    saved.submission.message_id = receipt.message_id;
    await writeState(); notice.textContent = '已提交给团队';
    await refresh(); await sdk.openChat();
  };
  const run = async fn => {
    if (busy || signal.aborted) return;
    busy = true; editScope.disabled=true; root.setAttribute('aria-busy','true');
    try { notice.replaceChildren(); await fn(); } catch (error) { if (!signal.aborted) showError(error); }
    finally { busy = false; if(!signal.aborted){editScope.disabled=false; root.removeAttribute('aria-busy');} }
  };
  const read = async fn => {
    try { notice.replaceChildren(); await fn(); } catch(error) { if(!signal.aborted)showError(error,'读取失败'); }
  };
  const actions = node('div', '', 'packagingActions');
  actions.append(button('保存清单', persist), button('与团队讨论', async () => { await persist(); await sdk.openChat(); }), reviewLabel);
  form.append(actions);
  results.append(button('刷新执行结果', refresh, true), button('查看团队对话与产物', () => sdk.openChat()));
  if(saved.submission?.text) {
    const original=node('details'), summary=node('summary','已投递的原始清单'); original.append(summary,node('pre',saved.submission.text));
    if(!saved.submission.message_id)original.append(button('用原内容重试投递',sendOriginal));
    results.append(original);
  }
  results.append(node('p','任务由团队实际执行。请求受理不表示发布完成；尚未具备的打包、数据库处理或安装能力由团队在回复中说明。','packagingHint'));
  const runResults=node('section','','packagingResults');
  const releases=releasePanel({configRoot,resultRoot:runResults,api:releaseApi(sdk),sdk,signal,act:run,
    readState:()=>saved.release_ui||{},writeState:async value=>{saved={...saved,release_ui:value};await writeState();},
    collect,requestText:checklist=>requestFor('',checklist).text,prepare:prepareRelease,
    beforeSave:async()=>{await featureStates.settle();platforms.validate();await editor.settle();await documents.settle();await system.settle();await permissions.settle();await templates.settle();await editor.refresh();await persist();},
    loadChecklist:async checklist=>{
      await stateQueue; saved={...saved,checklist};await writeState();
      lifecycle.abort();releases.destroy();clearTimeout(timer);root.replaceChildren();root.inert=false;root.removeAttribute('aria-busy');
      successor=await mount(root,{sdk,signal:parentSignal});
    }});
  editScope.append(form);
  root.append(header,notice,editScope,runResults);
  if(saved.submission) {const history=node('details');history.append(node('summary','以前投递的任务'),results);root.append(history);}

  await read(async () => {
    // Candidate and execution reads do not own the draft's interaction lock.
    // A server restart or slow lookup must leave independent fields editable.
    // Existing configurations and runs remain readable when a candidate source
    // is temporarily unavailable. Each editor keeps its own error presentation.
    const settled=await Promise.allSettled([
      (async()=>{await Promise.all([editor.ready(),platforms.ready(),themes.ready(),documents.ready(),permissions.ready(),templates.ready()]);await system.ready();await featureStates.ready();refreshDependencies();})(),
      releases.ready(),
      saved.submission?refresh():Promise.resolve(),
    ]);
    const failures=settled.filter(item=>item.status==='rejected').map(item=>item.reason?.message||String(item.reason));
    if(failures.length)throw Error(failures.join('；'));
  });
  return { show() { if(successor)return successor.show();visible = true;void releases.show(); }, hide() { if(successor)return successor.hide();visible = false;clearTimeout(timer);releases.hide(); }, destroy() { lifecycle.abort();clearTimeout(timer);releases.destroy();successor?.destroy(); } };
}
