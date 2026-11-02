'use strict';

const copy = value => JSON.parse(JSON.stringify(value));

// Read the current Inspector and supplied public responses as report material.
// These observations never turn missing evidence into a product verdict.
async function openRouteLimitSaveEvidence(inspector, {
  actionId, lineageId, wireId, sourceId, targetId, expected = {}, record = () => {},
} = {}) {
  const want = copy(expected);
  const identity = {action_id: actionId, lineage_id: lineageId, wire_id: wireId, source_id: sourceId, target_id: targetId};
  const rootCount = await inspector.count();
  const handle = rootCount === 1 ? await inspector.elementHandle() : null;
  let closed = false;
  const close = async () => { if (!closed) { closed = true; await handle?.dispose(); } };
  const gaps = [];
  const save = async row => {
    try { await record(copy(row)); }
    catch (_) { if (!gaps.includes('未记录：观察记录写入失败')) gaps.push('未记录：观察记录写入失败'); }
  };
  const sample = () => handle.evaluate((root, want) => {
    const visible = node => {
      if (!node?.isConnected || !node.getClientRects().length) return false;
      for (let p = node; p; p = p.parentElement) {
        const css = getComputedStyle(p);
        if (p.hidden || p.inert || css.display === 'none' || css.visibility !== 'visible' || Number(css.opacity) === 0) return false;
        if (p.tagName === 'DETAILS' && !p.open && !p.querySelector(':scope > summary')?.contains(node)) return false;
      }
      return true;
    };
    const field = selector => {
      const nodes = [...root.querySelectorAll(selector)], node = nodes.length === 1 ? nodes[0] : null;
      return { count: nodes.length, visible: visible(node), text: node?.innerText ?? null,
        value: node?.value ?? null, disabled: node?.disabled ?? null };
    };
    const i18n = window.PortalI18n;
    return {
      connected: root.isConnected, visible: visible(root), kind: root.dataset.inspectorKind ?? null,
      wire_id: root.dataset.wireId ?? null,
      addresses: [...root.querySelectorAll('[data-program-resource-address]')].map(e => e.dataset.programResourceAddress),
      input: field('[data-program-wire-form] [name="max_messages"]'), save: field('[data-program-wire-form] [data-program-save]'),
      budget: field('[data-wire-delivery-budget]'), sent: field('[data-wire-delivery-budget] [data-wire-sent-count]'),
      remaining: field('[data-wire-delivery-budget] [data-wire-remaining]'), state: field('[data-wire-delivery-budget] [data-wire-limit-state]'),
      language: document.documentElement.lang,
      labels: i18n ? {
        sent: i18n.t('{p0} 条消息已发送', { p0: want.sent }),
        remaining: want.max === null ? i18n.t('剩余次数：无限次') : i18n.t('剩余次数：{p0}', { p0: want.remaining }),
        state: i18n.t(want.reached ? '已达到发送上限，新的消息将不再发送。' : '计数持久保存；重置后继续接收新消息，不补发已丢弃的消息。'),
      } : null,
    };
  }, want);

  await save({stage:'bind', identity, expected:want, inspector_count:rootCount});
  return { close, async finish({receipt, lineageResult} = {}) {
    const graph = lineageResult?.lineage;
    const row = {stage:'finish', observed_at:new Date().toISOString(), identity, expected:want,
      receipt:receipt ?? null, public_read:{
        lineage_id:graph?.lineage_id ?? null, version:lineageResult?.version ?? null,
        wires:Array.isArray(graph?.program?.wires) ? graph.program.wires.filter(w => w.id === wireId) : [],
        runtime:graph?.program_invoke_wire_runtime?.[wireId] ?? null,
      }, evidence_gaps:[...gaps]};
    for (const [key,value] of Object.entries({receipt,lineageResult})) if (value == null) row.evidence_gaps.push('未记录：'+key);
    try {
      if (closed || !handle) throw new Error('Observation unavailable');
      row.sample = await sample();
    } catch (_) { row.evidence_gaps.push('未记录：页面观察'); }
    await save(row);
    row.evidence_gaps = [...new Set([...row.evidence_gaps,...gaps])];
    return copy(row);
  }};
}

module.exports = { openRouteLimitSaveEvidence };
