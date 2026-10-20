// Presentation of the fixed Resident snapshot cases. All state comes from replay;
// labels and layout here do not accept snapshots or infer missing message bodies.
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const initial = (mechanism, id) => mechanism.state_fields.find(f => f.id === id)?.initial;
const passed = (scene, step) => scene.prefix.includes(step);
export const CURSOR_CASE_LABELS = Object.freeze({
  'out-of-order':'运行回执先到，消息随后补读', gap:'事件缺口 → 返回快照接纳',
  omitted:'正文被省略 → 返回快照接纳', actor_changed:'读取期间身份改变',
  wrong_resident:'返回了其他 Resident 的窗口', released:'读取期间最后订阅释放',
  pending_contiguous:'快照接纳后，续接连续事件', pending_gap:'快照接纳后，仍有下一处缺口',
});
export function cursorEndLabel(scene) {
  if (scene.stepId === 'resnapshot') return '演示停在快照请求，尚未恢复';
  if (scene.state.recovery_requested) return '已安排继续恢复，尚无下一批';
  if (scene.stepId === 'no_install') return '本次返回未安装到共享窗口';
  if (scene.state.error) return '返回被拒绝，保留原窗口';
  if (scene.state.snapshot_accepted) return '本段窗口已接纳';
  return '本段演示结束';
}

export function renderCursorSnapshot(scene, mechanism) {
  const s = scene.state, start = initial(mechanism,'conversation_cursor');
  const arrived = Boolean(s.returned_snapshot_label), requested = s.snapshot_reads > 0;
  const accepted = s.snapshot_accepted, rejected = Boolean(s.error), released = passed(scene,'no_install');
  const omitted = passed(scene,'omitted'), discontinuous = passed(scene,'gap');
  const high = Math.max(start + 2, s.state_cursor, s.conversation_cursor, s.returned_cursor, s.discarded_event_cursor);
  const track = (label, cursor, desc, attribute) => `<section class="web-snapshot-track" data-cursor-track="${attribute}"><h3>${label} <strong>${escape(cursor)}</strong></h3><div class="web-cursor">${Array.from({length:high-start+1},(_,i)=>start+i).map(n=>`<div class="${n<=cursor?'known':''} ${n===cursor?'needle':''}"><span>${n}</span><b>${n===cursor?'▼':'·'}</b></div>`).join('')}</div><p>${desc}</p></section>`;
  const ids = values => `<ul class="web-snapshot-messages">${values.map(id=>`<li>${escape(id)}</li>`).join('')}</ul>`;
  const check = (label, detail, status) => `<div class="web-snapshot-check ${status}"><b>${label}</b><span>${escape(detail)}</span></div>`;
  const currentWindow = `<section class="web-snapshot-window ${accepted?'installed':''}" data-snapshot="installed"><h3>共享消息窗口 <span>${accepted?'已安装返回窗口':'保留原窗口'}</span></h3><b>${escape(s.installed_snapshot_label)}</b>${ids(s.message_ids)}${s.applied.length?`<p class="web-snapshot-applied">连续事件已应用：${s.applied.map(escape).join('、')}</p>`:''}<p>消息刻度 ${escape(s.conversation_cursor)} · 运行状态刻度 ${escape(s.state_cursor)}</p></section>`;
  const returnStatus = accepted?'已接纳':rejected?'拒绝安装':released?'仅返回给调用者':arrived?'已到达，等待核验':requested?'读取中':'尚未发起读取';
  const returnWindow = `<section class="web-snapshot-window returned ${rejected?'rejected':''}" data-snapshot="returned"><h3>返回区 <span>${returnStatus}</span></h3>${arrived?`<b>${escape(s.returned_snapshot_label)}</b><p>目标 ${escape(s.returned_resident_id)} · 返回刻度 ${escape(s.returned_cursor)}</p>${s.returned_message_ids.length?ids(s.returned_message_ids):'<p>本情境未提供消息列表；不补造正文。</p>'}`:`<p>${requested?'旧窗口继续可用；返回前不推进消息刻度。':'事件连续时直接补读消息，无需替换窗口。'}</p>`}<p>${accepted?'经过核验后才复制进共享窗口。':released?'已无活跃订阅；调用者取得副本，共享窗口保持原样。':'返回到达本身不改变共享消息窗口。'}</p></section>`;
  const origin = omitted?`从刻度 ${start} 补读：事件 ${start+1} 正文被省略，未应用。`:discontinuous?`从刻度 ${start} 补读：缺少事件 ${start+1}，未越过缺口应用事件 ${start+2}。`:'按消息刻度连续补读；运行回执只更新右侧刻度。';
  let continuation = '';
  if (passed(scene,'pending_contiguous')) continuation = `<section class="web-snapshot-followup" data-snapshot-followup="continuous"><h3>返回窗口之后：连续续接</h3><p>返回刻度 ${escape(s.returned_cursor)} → 连续事件 ${escape(s.conversation_cursor)} 已应用。运行状态刻度仍为 ${escape(s.state_cursor)}。</p><p>重复事件跳过；消息窗口保留返回窗口，并接入 ${escape(s.message_ids.at(-1))}。</p></section>`;
  if (passed(scene,'pending_gap')) continuation = `<section class="web-snapshot-followup pending" data-snapshot-followup="gap"><h3>返回窗口之后：仍有缺口</h3><p>已接纳 ${escape(s.returned_cursor)} → 缺少 ${escape(s.returned_cursor+1)} → 等待事件 ${escape(s.discarded_event_cursor)} 未应用。</p><p>${passed(scene,'schedule')?'已安排再次恢复；下一批尚未返回。':'停止处理这批等待事件，并请求继续恢复。'}共享窗口仍到 ${escape(s.conversation_cursor)}。</p></section>`;
  const actorMatch = s.actor_revision === s.request_actor_revision;
  const identity = requested?`<section class="web-snapshot-gate" data-snapshot="checks"><h3>安装前的核验</h3><div class="web-snapshot-checks">${check('请求身份代次',`发起 ${s.request_actor_revision} / 当前 ${s.actor_revision}`,actorMatch?'':'rejected')}${check('目标 Resident',arrived?`${s.resident_id} ← ${s.returned_resident_id}`:`等待 ${s.resident_id} 的返回`,arrived&&s.returned_resident_id!==s.resident_id?'rejected':'')}${check('当前读取请求',accepted?'安装时仍为同一请求':released?'返回时仍为同一请求':!actorMatch?'旧请求身份已过期':'核对是否仍为发起时的读取请求',!actorMatch?'rejected':'')}${check('活跃订阅',`${s.ref_count} · ${s.ref_count>0?'存在共享窗口使用者':'没有使用者，不更新共享窗口'}`,s.ref_count>0?'':'inactive')}</div>${rejected?`<p class="web-snapshot-rejection">${actorMatch?'返回目标不匹配；保留原窗口。':'请求身份代次已过期；旧返回不能借新身份安装。'}</p>`:''}</section>`:'';
  return `<div class="web-snapshot-cursors"><div class="web-snapshot-tracks">${track('已连续读取的消息',s.conversation_cursor,'共享消息窗口沿这根刻度更新。','messages')}${track('已接受的运行状态',s.state_cursor,passed(scene,'ack')?'运行回执已到；不携带缺失消息正文。':'等待独立的运行状态回执。','state')}</div><p class="web-snapshot-origin">${origin}</p><div class="web-snapshot-windows">${currentWindow}${returnWindow}</div>${continuation}${identity}<p class="web-snapshot-caption">本地模拟：窗口标签和消息 ID 为事实包中的示例值，未展示正文。请求容量 ${escape(s.history_limit)} 是历史窗口上限，不保证返回条数，也不代表全部历史。</p></div>`;
}
