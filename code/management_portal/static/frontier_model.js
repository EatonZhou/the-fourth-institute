export const KEY = 'exploration.frontier.v1';
export const t = source => globalThis.PortalI18n?.t(source) ?? source;
export const LABELS = {optional:'可以介入', decision:'需要决定', explore:'留待探索'};
export const RELATIONS = {builds_on:'从此发展 →', depends_on:'依赖 →', converges:'汇合 ↔'};
export function progressOf(item, input) {
  if (item.closed) return item.closed === 'cancelled' ? '已取消' : '已收束';
  if (!item.dispatch) return '尚未投递';
  if (!input) return '待核实';
  return ({queued:'排队中',running:'推进中',completed:'已有回复',failed:'执行失败',cancelled:'已取消',pending:'已接收'})[input.state] || '待核实';
}
export function isHistory(item, input) {
  return !!item.closed || ['completed','cancelled'].includes(input?.state);
}
export function validateItem(item, items = []) {
  if (!/^[a-zA-Z0-9_-]{1,64}$/.test(item.id || '')) throw new Error(t('Map 节点局部标识无效。'));
  for (const field of ['title','line','progress','reason','next']) {
    if (!String(item[field] || '').trim()) throw new Error('请填写问题、工作线、进展、理由和下一步。');
  }
  if (!LABELS[item.attention]) throw new Error('请选择介入要求。');
  if (!item.sources?.length || item.sources.length > 8 || item.sources.some(s => !/^[a-z][a-z0-9_]*_[a-f0-9]{12}(\/[^\s]*)?$/.test(s))) throw new Error('请填写 1–8 个有效资源地址。');
  if (item.parent && (!items.some(x => x.id === item.parent) || item.parent === item.id)) throw new Error(t('关联节点不存在或指向自身。'));
  if (item.parent && !RELATIONS[item.relation]) throw new Error('关联类型无效。');
  if (items.some(x => x.id !== item.id && x.title.trim() === item.title.trim() && x.sources.join('\n') === item.sources.join('\n'))) throw new Error(t('这条来源下已有同一问题，请更新原 Map 节点。'));
  if (new TextEncoder().encode(JSON.stringify(item)).length > 12000) throw new Error('单条内容过长，请精简判断，完整材料通过来源关联。');
  return item;
}
export function readItems(doc) {
  if (doc == null) return [];
  if (doc.schema !== 'tfi.exploration-frontier.v1' || !Array.isArray(doc.items)) throw new Error(t('Map 节点数据格式不受支持。'));
  for (const item of doc.items) validateItem(item,doc.items);
  return doc.items;
}
export async function saveDocument(call, lineageId, previous, value) {
  if (new TextEncoder().encode(JSON.stringify(value)).length > 60000) throw new Error('预览容量已满，请精简内容；此次未保存。');
  const result = await call('UpdateSharedState', {lineage_id:lineageId,key:KEY,value,expected_value:previous});
  if (!result.matched) throw Object.assign(new Error('其他人已更新这条工作线。请核对最新内容再提交；你的输入仍保留。'), {code:'instruction.version_conflict',value:result.value});
  return result.value;
}
export async function deliver(call, item) {
  const d = item.dispatch;
  return call('DeliverMessage', {resident_id:d.owner,message:{message_id:d.message_id,content_parts:[{type:'text',text:d.text}]},activation_policy:'enqueue_and_trigger'}, {idempotencyKey:d.message_id});
}
