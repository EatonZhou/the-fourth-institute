// Reading emphasis is a view concern. It never removes admitted relationships
// or changes composition, direction, protocol identity, or source contracts.
const items = value => Array.isArray(value) ? value : [];
// A named capability is an editorial view over admitted roots, never a model
// object, composition parent, deployment unit, or SDK subject.
export const executionCapability = Object.freeze({ id: 'model-execution', nodeId: 'view:model-execution',
  title: '模型执行', focusKind: 'architecture-view:model-execution[execution,worker,providers]', memberIds: Object.freeze(['execution', 'worker', 'providers']) });
export function hasExecutionCapability(objects) {
  return executionCapability.memberIds.every(id => items(objects).some(o => o.id === id && o.parent_id === null && o.kind !== 'protocol'));
}
export function projectExecutionReading(content, viewId = null) {
  if (!content.overview || !hasExecutionCapability(content.children)) {
    if (viewId) throw new TypeError('Execution capability members unavailable');
    return content;
  }
  if (viewId && viewId !== executionCapability.id) throw new TypeError('Unknown capability view');
  const ids = new Set(executionCapability.memberIds), relations = readingRelations(content);
  const members = content.children.filter(o => ids.has(o.id));
  const inside = r => ids.has(r.from) && ids.has(r.to);
  if (viewId) {
    const relevant = relations.filter(r => ids.has(r.from) || ids.has(r.to));
    const visible = new Set([...ids, ...relevant.flatMap(r => [r.from, r.to])]);
    return { ...content, capabilityView: viewId, memberIds: [...ids],
      children: content.children.filter(o => visible.has(o.id)),
      internalRelations: relevant, boundaryRelations: [],
      readingLayers: [
        { title: '调用与观察', ids: ['web', 'lineage', 'resources'] },
        { title: '模型执行 · 跨进程协作，成员保持真实归属', ids: [...ids], band: true, memberBand: true },
        { title: '持久化与外部服务', ids: ['storage', 'postgres', 'model-services'] },
        { title: '通知、发布与维护', ids: ['notifications', 'delivery', 'engineering'] },
      ] };
  }
  const group = { id: executionCapability.nodeId, kind: 'capability_view', viewId: executionCapability.id,
    title: executionCapability.title, responsibility: 'ExecutionService · Worker · Provider｜跨进程协作',
    viewMembers: members, containedRelations: relations.filter(inside),
    evidence_ids: [...new Set(members.flatMap(o => items(o.evidence_ids)))] };
  const endpoint = id => ids.has(id) ? group.id : id;
  return { ...content, executionGrouped: true,
    children: content.children.flatMap(o => o.id === 'execution' ? [group] : ids.has(o.id) ? [] : [o]),
    internalRelations: relations.filter(r => !inside(r)).map(r => ({ ...r,
      from: endpoint(r.from), to: endpoint(r.to), readingOriginal: r })), boundaryRelations: [],
    readingLayers: overviewLayers.filter(l => !l.ids.every(id => ids.has(id)))
      .map(l => ({ ...l, ids: l.ids.map(id => id === 'execution' ? group.id : id).filter(id => !ids.has(id)) })) };
}
export const overviewLayers = [
  { title: '浏览器', ids: ['interface'] },
  { title: '路由', ids: ['router'] },
  { title: 'Web 与变更传递', ids: ['web', 'notifications'] },
  { title: '领域服务 · 各有状态所有者', ids: ['resources', 'lineage', 'execution'] },
  { title: '执行与模型边界', ids: ['worker', 'providers'] },
  { title: '持久化与外部服务', ids: ['storage', 'postgres', 'model-services'] },
  { title: '发布与工程维护', ids: ['delivery', 'engineering'] },
];
export function readingProfile(content) {
  return { title: content.root?.title || 'Portal 协作结构',
    explanation: '沿职责从上到下阅读 · 同方向的交互共线；具名端口接续远端协作，选线或端口展开每条协议 · 主题只强调，不隐藏结构' };
}
export function readingRelations(content) {
  return [...items(content.internalRelations), ...items(content.boundaryRelations)];
}
export function relationEmphasized(relation, lens = 'all', focused = null) {
  if (lens === 'all' || !lens) return true;
  const members = relation.members || [relation];
  if (lens === 'relation') return members.some(r => r.id === focused);
  if (lens === 'focus') return members.some(r => r.from === focused || r.to === focused);
  const protocols = members.flatMap(r => items(r.summary?.relations).length ? r.summary.relations : [r]).map(r => r.protocol_id);
  const match = lens === 'data' ? /resource|file-storage|database|provider-capabilities/
    : lens === 'ops' ? /change-refresh|deployment|release|application-target/ : /http-interface|signed-rpc|resident-execution|worker-lease|provider-native|model-api|hatg-isa|projection-family/;
  return protocols.some(id => match.test(id || ''));
}
// One visual connection per DIRECTED endpoint pair. The individual directed protocol
// groups remain intact under members; this is not a new callable relation.
export function readingConnections(content) {
  const pairs = new Map();
  for (const relation of readingRelations(content)) {
    const key = JSON.stringify([relation.from, relation.to]);
    if (!pairs.has(key)) pairs.set(key, []);
    pairs.get(key).push(relation);
  }
  const aliases = {'http-interface':'HTTP 路由','hatg-isa':'HATG 指令','projection-family':'投影宿主','resource-authorization':'资源授权','signed-rpc':'签名 RPC','application-target':'应用目标','resident-execution':'执行请求 / 回执','worker-lease':'领取 / 续租 / 回执','provider-native':'原生会话 / 事件','file-storage':'文件锁 / 原子提交','database-schema':'schema / 领域事务','model-api':'模型 API','change-refresh':'变更提示 / SSE','deployment-control':'服务代次控制','release-events':'发布完成事件','provider-capabilities':'能力预检'};
  const names = new Map(items(content.protocols).map(o => [o.id, aliases[o.id] || o.title]));
  return [...pairs.values()].map(members => {
    const first = members[0], from = first.from, to = first.to;
    const protocols = [...new Set(members.flatMap(r => r.protocol_ids || (r.protocol_id ? [r.protocol_id] : [])))];
    const raw = members.flatMap(r => r.summary?.relations || [r]);
    const labels = [...new Set(members.map(r => r.protocol_id ? (names.get(r.protocol_id) || r.label) : r.label))];
    return { ...first, from, to, members, memberIds: members.map(r => r.id),
      protocol_ids: protocols, protocol_id: members.length === 1 ? first.protocol_id : null,
      label: labels.join(' · '), labels,
      readingLabel: members.some(r => r.readingOriginal)
        ? [...new Set(members.map(r => { const original = r.readingOriginal || r; return directedCaption(original.from, original.to, [original], [r.protocol_id ? names.get(r.protocol_id) || r.label : r.label], content.overview); }))].join(' · ')
        : directedCaption(from, to, members, labels, content.overview),
      summary: { kind: 'visual_connection', relations: raw },
      evidence_ids: [...new Set(raw.flatMap(r => items(r.evidence_ids)))] };
  });
}

// Editorial reading names refer only to admitted directed connections. They
// neither create a relation nor substitute for the per-member contracts.
const overviewCaptions = {
 'worker>execution':'领取 / 进度 / 终态；会话映射',
 'execution>worker':'读取既有会话绑定',
 'lineage>execution':'提交 / 观察 / 确认',
 'worker>providers':'原生请求',
 'providers>worker':'事件返回',
 'providers>execution':'Relay 提交无状态执行 · 模型 API',
 'web>resources':'资源解析 / 授权 / 投影读取',
 'web>lineage':'签名 RPC / 指令 / 应用目标',
 'resources>lineage':'Workspace 绑定检查',
 'web>notifications':'发布变更提示',
 'notifications>web':'独立订阅提示',
 'notifications>storage':'监听文件变化',
 'web>interface':'SSE 提示',
 'resources>postgres':'授权 / ID 事务',
 'providers>postgres':'Relay 注册表',
 'lineage>postgres':'Lineage 事实 / schema 事务',
 'execution>postgres':'执行账 / Agent 事实 / schema 事务',
 'web>postgres':'身份 / 工作台 / 业务状态事务',
 'worker>postgres':'Harness 策略 / 效果存储',
 'lineage>resources':'资源读取 / 登记投影',
 'worker>resources':'当前 actor 引用读取',
 'resources>web':'注销回调 Chatroom owner',
 'resources>execution':'只读观察执行关联',
};
function directedCaption(from,to,members,labels,overview) {
 if (overview && from === 'resources' && to === 'lineage'
   && members.some(m => (m.summary?.relations || [m]).some(r => r.id === 'service-resources.r.resource-delete-lineage-owner')))
   return 'Workspace 绑定 / 注销回调';
 return overviewCaptions[from+'>'+to] ||
   (!overview && members.length === 1 ? members[0].label : labels.join(' · '));
}
export const objectReadingNames = {
 interface:'浏览器',router:'Router',web:'Web 服务',resources:'ResourceService',lineage:'SocietyService',execution:'Execution',worker:'Worker',providers:'Provider',notifications:'通知总线',postgres:'PostgreSQL',storage:'共享文件 / Artifact','model-services':'外部模型服务',delivery:'部署与发布',engineering:'工程维护',
};
// Main paths provide the spatial skeleton. Remaining directed connections are
// visible, named continuations attached to their REAL source, never a filter.
const overviewPaths = new Set([
 'interface>router','router>web','interface>web','web>resources','web>lineage',
 'resources>lineage','lineage>execution','worker>execution','execution>worker',
 'worker>providers','providers>worker','resources>storage','resources>postgres',
 'providers>postgres','providers>model-services','web>notifications','notifications>web','web>interface','notifications>storage',
]);
export function connectionPresentation(content, connection) {
 if (content.executionGrouped && content.narrowReading) return ['interface>router','router>web'].includes(connection.from+'>'+connection.to) ? 'path' : 'port';
 if (content.executionGrouped) {
   const from = connection.from === executionCapability.nodeId ? 'execution' : connection.from;
   const to = connection.to === executionCapability.nodeId ? 'execution' : connection.to;
   if (from === 'execution' && ['storage', 'postgres', 'model-services'].includes(to)) return 'path';
   return overviewPaths.has(from+'>'+to) ? 'path' : 'port';
 }
 if (content.capabilityView) {
   // Three primary directed interactions use dedicated corridors. Session
   // lookup and Relay submission remain independent source ports.
   return new Set(['lineage>execution','worker>execution','worker>providers','providers>worker']).has(connection.from+'>'+connection.to) ? 'path' : 'port';
 }
 if (content.overview) return overviewPaths.has(connection.from+'>'+connection.to)?'path':'port';
 // The facade's cancellation bypass must stay attached to its actual source,
 // rather than float above the facade beside the composition title.
 if (content.root?.id === 'execution' && connection.from === 'execution.client' && connection.to === 'execution.scheduling') return 'port';
 // Resource callers keep their real source and target in the diagram. Only
 // the common access path crosses the boundary; the other independent callers
 // use source-attached direction ports rather than stacking parallel long lines.
 if (content.root?.id === 'resources') {
   if (connection.to === 'resources.access' && connection.from !== 'web'
     && content.boundaryEndpoints.some(o => o.id === connection.from)) return 'port';
   if (connection.from === 'resources.types' && ['lineage.definition','web.community'].includes(connection.to)) return 'port';
   if (connection.from === 'resources.access' && connection.to === 'execution.server') return 'port';
 }
 return 'path';
}

export const readingResponsibilities = {
 interface:'对话、标签页与工作台',router:'认证入口与用户路由',web:'HTTP、指令与投影宿主',
 resources:'统一地址、类型能力与授权',lineage:'组织、对话与事件自动化',execution:'执行身份、调度与结果账',
 worker:'领取资格后运行并保存结果',providers:'原生运行时与 Relay 入口',notifications:'变更提示后重读快照',
 storage:'工作区正文、消息与工具产物',postgres:'授权事务与领域状态',
 'model-services':'模型 API 与原生云端',delivery:'服务代次与发布事实',engineering:'业务应用准入与工程任务',
};
// Role order within a known composition is a semantic reading hint, not pixel
// coordinates or a new relation. Unrecognized compositions use topology ranks.
export const compositionLayers = {
 execution:[['execution.client'],['execution.server'],['execution.scheduling'],['execution.preparation','execution.agent-data']],
 resources:[['resources.access'],['resources.types','resources.permissions']],
};
