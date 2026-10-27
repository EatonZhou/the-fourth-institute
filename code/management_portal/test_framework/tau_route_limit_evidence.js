'use strict';

const copy = value => JSON.parse(JSON.stringify(value));
const normalizeText = value => String(value ?? '').replace(/\s+/g, ' ').trim();
const requireValue = (condition, code) => { if (!condition) throw new Error(`RouteLimitEvidence:${code}`); };

// Bind in the original save action before clicking. The caller owns the passive
// UpdateWire receipt, public GetLineage read, bounded ready waits and timing.
// This helper only samples the original Inspector; it never scrolls, requests,
// waits for convergence or repeats a mutation. It does not replace visual review.
async function openRouteLimitSaveEvidence(inspector, { actionId, lineageId, wireId, sourceId, targetId, expected, record }) {
  requireValue(actionId && lineageId && wireId && sourceId && targetId && sourceId !== targetId
    && typeof record === 'function', 'MissingOriginalContext');
  const want = copy(expected);
  const nonnegative = n => Number.isSafeInteger(n) && n >= 0;
  requireValue(nonnegative(want.sent) && (want.max === null || nonnegative(want.max))
    && (want.max === null ? want.remaining === null : nonnegative(want.remaining))
    && typeof want.reached === 'boolean', 'InvalidExpectedCounts');
  const identity = { action_id: actionId, lineage_id: lineageId, wire_id: wireId, source_id: sourceId, target_id: targetId };
  const rootCount = await inspector.count();
  await record(copy({ stage: 'bind', identity, expected: want, inspector_count: rootCount }));
  requireValue(rootCount === 1, 'AmbiguousInspector');
  const handle = await inspector.elementHandle();
  requireValue(handle, 'MissingInspector');
  let closed = false, finishing = false;
  const close = async () => { if (!closed) { closed = true; await handle.dispose(); } };
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
  const rootMatches = dom => dom.connected && dom.visible && dom.kind === 'wire' && dom.wire_id === wireId
    && dom.addresses.length === 1 && dom.addresses[0] === `${lineageId}/program/wire/${wireId}`;
  try {
    const before = await sample();
    await record(copy({ stage: 'before_save', identity, sample: before }));
    requireValue(rootMatches(before), 'OriginalInspectorIdentity');
  } catch (error) { await close(); throw error; }

  return {
    close,
    async finish({ receipt, lineageResult }) {
      requireValue(!closed && !finishing, 'WindowClosed');
      finishing = true;
      try {
        // Preserve the original envelope, the exact public wire/runtime and the
        // DOM even when any of them disagree. Never normalize away missing fields.
        const original = copy({ receipt, lineageResult });
        const graph = original.lineageResult?.lineage;
        const wires = Array.isArray(graph?.program?.wires) ? graph.program.wires.filter(w => w.id === wireId) : [];
        const proof = { stage: 'finish', identity, expected: want, receipt: original.receipt,
          public_read: { lineage_id: graph?.lineage_id ?? null, version: original.lineageResult?.version ?? null,
            wires, runtime: graph?.program_invoke_wire_runtime?.[wireId] ?? null } };
        try { proof.sample = await sample(); }
        catch (error) {
          proof.sample_error = { name: error.name, message: String(error.message).slice(0, 500) };
          await record(copy(proof));
          throw error;
        }
        const failures = [];
        const equal = (field, actual, expected) => {
          if (actual !== expected) failures.push({ field, actual: actual === undefined ? { missing: true } : actual, expected });
        };
        const checkWire = (prefix, wire) => {
          for (const [key, value] of Object.entries({ id: wireId, kind: 'route', source: sourceId, target: targetId, source_port: 'output', target_port: 'invoke' })) equal(`${prefix}.${key}`, wire?.[key], value);
          equal(`${prefix}.config.max_messages`, wire?.config?.max_messages ?? null, want.max);
        };
        equal('receipt.instruction', original.receipt?.instruction, 'UpdateWire');
        equal('receipt.status', original.receipt?.status, 'committed');
        equal('receipt.instruction_id.present', !!original.receipt?.instruction_id, true);
        checkWire('receipt.wire', original.receipt?.result?.wire);
        equal('public_read.lineage_id', graph?.lineage_id, lineageId);
        equal('public_read.wire_count', wires.length, 1);
        checkWire('public_read.wire', wires.length === 1 ? wires[0] : null);
        const runtime = proof.public_read.runtime;
        for (const [key, value] of Object.entries({ max_messages: want.max, sent_count: want.sent, remaining_messages: want.remaining, message_limit_reached: want.reached })) equal(`public_read.runtime.${key}`, runtime?.[key], value);
        const dom = proof.sample;
        equal('inspector.identity', rootMatches(dom), true);
        for (const key of ['input', 'save', 'budget', 'sent', 'remaining', 'state']) {
          equal(`dom.${key}.count`, dom[key].count, 1);
          equal(`dom.${key}.visible`, dom[key].visible, true);
        }
        equal('dom.input.value', dom.input.value, want.max === null ? '' : String(want.max));
        equal('dom.input.disabled', dom.input.disabled, false);
        equal('dom.save.disabled', dom.save.disabled, false);
        equal('dom.labels.present', !!dom.labels, true);
        for (const key of ['sent', 'remaining', 'state']) equal(`dom.${key}.text`, normalizeText(dom[key].text), normalizeText(dom.labels?.[key]));
        proof.failures = failures;
        await record(copy(proof));
        if (failures.length) {
          const error = new Error(`RouteLimitEvidence:${failures.map(f => f.field).join(',')}`);
          error.failures = copy(failures);
          throw error;
        }
        return copy({ identity, ready: true });
      } finally { await close(); }
    },
  };
}

module.exports = { openRouteLimitSaveEvidence };
