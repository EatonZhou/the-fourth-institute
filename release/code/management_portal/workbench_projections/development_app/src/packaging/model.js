import {featureStateFields,FEATURE_STATE_RULES} from './feature-states.js';
import {platformSelection,PLATFORM_RULES} from './platforms.js';
import {releaseFeatures,DEPENDENCY_RULES} from './feature-dependencies.js';
import {templateSelection,TEMPLATE_RULES} from './society-templates.js';
import { migrateSelection, lineageProject } from './selection-model.js';
import {workspaceOptions,WORKSPACE_RULES} from './workspace.js';
import {normalizeThemes,THEME_RULES} from './themes.js';
import {normalizeDocuments,DOCUMENT_RULES,DOCUMENT_LINK_RULES} from './documents.js';
import {includeDocumentTypes} from './document-dependencies.js';
import {normalizeOptionalFeatures,OPTIONAL_FEATURE_RULES} from './optional-features.js';
import {normalizeSystem,SYSTEM_RULES} from './system-flows.js';
import {normalizePermissions,PERMISSION_RULES} from './permissions.js';
export { lineageProject } from './selection-model.js';
export const FAMILY = 'family_1ee0fb9090f4';
export const TARGET = Object.freeze({ binding: 'packaging', role: 'facade' });
// Only used to distinguish known old defaults from user-authored notes.
const LEGACY_BASE_FEATURES = ['后台服务', 'Home', 'Resource', 'Society', '登录与账号', '管理页'];
export const CATEGORIES = [
  ['tests', '测试', ['测试框架', '测试用例', 'TAU 功能']],
  ['docs', '文档', ['README', '安装文档']],
  ['tools', '工具', ['Telepus']],
];
export function normalizeChecklist(value = {}, { validate = true } = {}) {
  const platforms=platformSelection(value);
  value=migrateSelection(value);
  const text = key => String(value[key] || '').trim();
  const projects = [...new Set((value.projects || []).map(item => String(item.id || '').trim()).filter(Boolean))].sort().map(id => ({id}));
  const selected = new Set(projects.map(item => item.id));
  const legacyBase = [...new Set((value.categories?.base || []).map(String).map(v => v.trim()).filter(v => v && v !== 'Lineage' && !LEGACY_BASE_FEATURES.includes(v)))];
  const documents=normalizeDocuments(value.document_indexes);
  if(value.categories?.docs?.includes('规范索引'))documents['SPEC.md'].pending_legacy=true;
  const checklist = { selection_version:3, ...platforms,...featureStateFields(value),
    ...(Object.hasOwn(value,'native_switch_site_config')?{native_switch_site_config:structuredClone(value.native_switch_site_config)}:{}),
    society_templates:templateSelection(value.society_templates),
    legacy_mandatory_content:[...new Set([...(value.legacy_mandatory_content||[]),...(value.categories?.docs||[]).filter(v=>v==='许可证与第三方声明')])],
    excluded_device_selections:value.excluded_device_selections, version: text('version'), source: text('source'), destination: text('destination'), projects,
    manual_projects:value.manual_projects,manual_lineages:value.manual_lineages,
    resource_lineage_options:Object.fromEntries(Object.entries(value.resource_lineage_options||{}).map(([id,options])=>[id,{history:options.history===true,workspace:workspaceOptions(options.workspace)}])),
    resource_types:includeDocumentTypes(value.resource_types,documents),projections:value.projections,projects_page:value.projects_page,
    test_view:value.test_view,pending_test_view:value.pending_test_view,
    document_indexes:documents,optional_features:normalizeOptionalFeatures(value.optional_features),
    system_lineage:value.system_lineage?normalizeSystem(value.system_lineage):null,permission_selection:normalizePermissions(value.permission_selection),
    society_description:value.society_description,projection_description:value.projection_description,
    issues:value.issues,pending_workspace_selections:value.pending_workspace_selections,themes:normalizeThemes(value.themes),
    attachment_descriptions:Object.fromEntries(['tests','docs','tools'].map(key=>[key,typeof value.attachment_descriptions?.[key]==='string'?value.attachment_descriptions[key]:''])),
    categories: Object.fromEntries(CATEGORIES.map(([key]) => [key,[...new Set((value.categories?.[key] || []).map(String).map(v => v.trim()).filter(v=>v&&!(key==='docs'&&['规范索引','许可证与第三方声明','runbooks'].includes(v))))].sort()])),
    lineages: [...new Map((value.lineages || []).filter(item => selected.has(item.project_id)).map(item => [String(item.id), { id: String(item.id), project_id:item.project_id, history: item.history === true,workspace:workspaceOptions(item.workspace) }])).values()].sort((a,b) => a.id.localeCompare(b.id)),
    unresolved_lineages: (value.unresolved_lineages || []).map(item => ({id:String(item.id),history:item.history === true,workspace:workspaceOptions(item.workspace)})),
    notes: [String(value.notes||''), legacyBase.length ? `旧基础功能自定义内容（待核对，未列为固定包含）：\n${legacyBase.join('\n')}` : ''].filter(Boolean).join('\n\n'),
  };
  checklist.effective_features=releaseFeatures(checklist);
  if (validate) {
    if (!checklist.target_platforms.length) throw new Error('请至少选择一个目标平台');
    for (const [key, label] of [['version','版本名称'], ['source','源码来源'], ['destination','发布位置']]) {
      if (!checklist[key]) throw new Error(`请填写${label}`);
    }
    if (checklist.version.length > 80 || /[\u0000-\u001f\u007f]/.test(checklist.version)) throw new Error('版本名称须为最多 80 字的单行文字');
    if (checklist.lineages.some(item => !/^lineage_[0-9a-f]{12}$/.test(item.id))) throw new Error('请选择有效的 Lineage');
    if (checklist.unresolved_lineages.length) throw new Error('有 Lineage 归属尚未核实，请移除并按项目重新选择后再提交');
    if (checklist.issues.length || checklist.projections.some(v=>!v.verified)) throw new Error('有资源或投影关联待处理，请重新核实或移除相关选择');
    if (checklist.resource_types.some(v=>v.instances.some(id=>!v.ownership?.[id]?.verified))) throw new Error('有资源实例归属待核实，请重新核实后提交');
    if (new TextEncoder().encode(JSON.stringify(checklist)).length > 16000) throw new Error('清单过长，请缩减后提交');
  }
  return checklist;
}
export async function restoreChecklist(value = {}, resolveLineage, readProject) {
  const legacy = ![2,3].includes(value.selection_version);
  const projects = new Map((value.projects || []).map(item => [item.id,{id:item.id}]));
  const selected = [], unresolved = [...(value.unresolved_lineages || [])];
  const accessible = new Map();
  const project = id => {
    if (!accessible.has(id)) accessible.set(id, readProject(id).then(result => result.project?.resource_id === id));
    return accessible.get(id);
  };
  const items = (value.lineages || []).filter(item => legacy || projects.has(item.project_id));
  // Bound concurrent reads; never use a title or a saved project as proof of ownership.
  for (let start=0; start<items.length; start+=8) {
    const batch = await Promise.all(items.slice(start,start+8).map(async item => {
      try {
        const result = await resolveLineage(item.id), owner = lineageProject(result.resource);
        if (result.resource?.resource_id !== item.id || !owner || (!legacy && owner !== item.project_id) || !await project(owner)) throw Error('ownership unavailable');
        return {...item,project_id:owner};
      } catch { return {...item,unresolved:true}; }
    }));
    for (const item of batch) {
      if (item.unresolved) unresolved.push(item);
      else { if (legacy) projects.set(item.project_id,{id:item.project_id}); selected.push(item); }
    }
  }
  return normalizeChecklist({...value,projects:[...projects.values()],lineages:selected,unresolved_lineages:unresolved},{validate:false});
}
export function requestFor(workbench, checklist, {runId} = {}) {
  checklist = normalizeChecklist(checklist);
  // New requests belong to one explicit run. Already persisted legacy requests
  // retain their original key/text; they never pass through this constructor.
  const key = `packaging:${workbench}:${runId || crypto.randomUUID()}`;
  const text = `请 Portal 开源版本团队按下面已核对的完整清单打包发布。\n源码可来自 Dev 或 Release 或清单指定位置。交付无源 Git 历史的独立代码仓库、发行数据库和安装说明。Project 仅包含清单选中的项目及其必要归属数据，不自动带出整个项目的资源、知识或其他 Lineage；仅带出明确选择的 Lineage，保留其 Project 归属。保留 Lineage 经历时，仅在独立发行数据库中将人类消息发送者及关联身份匿名化为 TFI portal，源数据库不改；不要带出源站点凭据、密钥与执行账号绑定。\n复用现有智能功能和脚本。缺少打包能力时如实说明并处理，不把请求受理或部分步骤当作发布成功；通过本请求的关联回复给出进度结论、问题、实际产物地址与发布地址。若范围需改变，先通过对话与用户核对清单。\n\n发布清单：\n${JSON.stringify(checklist, null, 2)}`;
  const requirements = `Portal 基础代码随发行完整保留。\nprojects_page 仅表示“Projects 页面”及导航入口；未选中时仍须保留 Project 后端资源、相关服务及项目归属能力。具体 Project、Lineage 数据独立带出，不受 Projects 页面是否选中影响。\nresource_types 表示类型所需声明、提供者及服务；instances 为空且 description 为空时只支持类型，不带现有实例，绝不表示全量数据。Society 的必要 Resident/Family/组件/连线随 Lineage；Portal System 按 system_lineage 的功能范围处理。投影 dependencies 均需保留，自动加入不代表带历史，经历以 history 为准。各区自然语言原样保留供执行团队查询理解，有歧义通过对话厘清，不将未解析文字冒充具体 ID。测试/文档/工具为附带内容，安装说明按交付要求保留；LICENSE、NOTICE 和实际发行内容所需第三方声明固定带出，不以旧草稿是否勾选为条件，legacy_mandatory_content 仅保留原勾选记录，不是范围开关。“运维脚本”旧文字仅待核对，不表示已定义的功能或全部 scripts；运行安装所需脚本随实际选择的功能处理。`;
  const ownership='已选资源实例自动带出其真实所属 Project，归属见 resource_types[].ownership；只包含这些项目及必要归属，不自动带出项目其他资源或 Lineage。无所属 Project 的资源不强行归类。自然语言解析为具体资源后也遵守此规则；归属未知或不可读时先核实，不猜测或绕过权限。';
  const testing='测试框架提供测试目录、执行与结果记录；测试用例按所选范围带出，不预设公开版分类。TAU 功能由 Agent 实际使用页面完成验证，选择时包含必要测试框架支持。test_view 对应实际 builtin=test 的 Workbench；以服务端解析的真实ID、发布修订和绑定带出界面及必要测试框架支持，同一投影去重。撤选不代表停止 TAU 或测试框架执行API。pending_test_view 表示旧 Test Workbench 勾选尚待核对，不推断为具体 Workbench ID，执行前须确认界面选择。选择 TAU 或测试界面不自动复制源测试账号、执行槽位、历史运行或全部测试团队数据；新部署自行配置运行身份与执行条件。';
  return { key, text: `${requirements}\n${PLATFORM_RULES}\n${ownership}\n${WORKSPACE_RULES}\n${THEME_RULES}\n${testing}\n${OPTIONAL_FEATURE_RULES}\n${FEATURE_STATE_RULES}\n${SYSTEM_RULES}\n${PERMISSION_RULES}\n${DEPENDENCY_RULES}\n${TEMPLATE_RULES}\n${DOCUMENT_RULES}\n${DOCUMENT_LINK_RULES}\nenabled=false 的文档区域不带出，其保留的条目仅供下次编辑；未设置关联不表示适用性已确认。\n\n${text}` };
}
export function requestState(state) {
  return ({ queued:'排队中', running:'处理中', completed:'团队已回复，请核对结果', failed:'处理失败', cancelled:'已取消' })[state?.state] || '已提交';
}
