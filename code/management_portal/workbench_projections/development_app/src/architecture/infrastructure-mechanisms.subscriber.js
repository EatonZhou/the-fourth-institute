import './infrastructure-mechanisms.subscriber.css';

const node = (tag, cls, text) => {
  const e = document.createElement(tag); e.className = cls || '';
  if (text !== undefined) e.textContent = text;
  return e;
};
const group = (cls, ...children) => { const e = node('div', cls); e.append(...children); return e; };
const label = text => node('p', 'fanout-label', text);
const token = (text, marker = false, schematic = false) => node('span', `fanout-token ${marker ? 'is-marker' : ''} ${schematic ? 'is-schematic' : ''}`, text);
const arrow = (text, warning = false) => group(`fanout-arrow ${warning ? 'is-marker' : ''}`, node('span', '', text), node('b', '', '↓'));

// Only the left queue is replayed by the frozen model. The right branch explains
// the evidenced fanout invariant; it is not an invented second state machine.
export function renderSubscriber(scene) {
  const {state: s, caseId, events} = scene;
  const all = s.snapshot_scope === 'all', single = s.snapshot_scope === 'single';
  const consumed = events.includes('consume'), offered = events.includes('offer');
  const before = events.length === 0, slowPremise = before && caseId === 'slow';
  const burstPremise = before && caseId === 'burst';
  const root = node('section', `fanout-map ${all ? 'is-all' : ''} ${single ? 'is-single' : ''}`);
  root.setAttribute('aria-label', '共享入口与独立订阅队列');
  root.dataset.scope = s.snapshot_scope;

  root.append(group('fanout-intro', node('strong', '', all ? '拥塞在共享入口：每条支路都收到快照标记' : single ? '拥塞在一条支路：旁路继续消费' : '同一入口，各有自己的队列'),
    node('p', '', '先选拥塞发生的位置，再沿连线看谁需要重读。')));
  const ingress = group(`fanout-ingress ${all || burstPremise ? 'is-affected' : ''}`,
    label('Hub → 跨线程共享入口 → Fanout'),
    node('h3', '', all ? '清空共享待分发提示' : burstPremise ? '本情境前提：共享入口已溢出' : '共享入口'),
    node('p', '', all ? '不再沿用不完整提示；向各订阅放入快照标记。' : burstPremise ? '下一动作会影响下面每条订阅。' : '提示逐个分发到匹配的订阅，不等慢页面读完。'));
  root.append(ingress);
  root.append(group(`fanout-fork ${all ? 'is-affected' : ''}`, node('span', '', all ? '全部订阅' : '分别投递')));
  const branches = group('fanout-branches');

  const selected = group('fanout-branch'); selected.dataset.branch = 'selected';
  selected.append(label(caseId === 'slow' ? '所选订阅 · 本情境的慢页面' : '所选订阅 · 本次观察对象'));
  const queue = group(`fanout-bin ${all || single || slowPremise ? 'is-affected' : ''}`,
    node('h3', '', '独立队列'));
  if (slowPremise) queue.append(node('p', 'fanout-premise', '动作前提：队列已满'), node('small', '', '示意拥塞位置，不表示容量或条目数。'));
  else if (s.subscriber_queue.includes('snapshot-required')) {
    queue.append(node('s', '', '过时提示已清除'), token('重读快照', true));
  } else if (offered && !consumed) queue.append(token('变更提示'));
  else queue.append(node('p', 'fanout-empty', consumed ? '本次批次已取出' : '等待本次动作'));
  selected.append(queue, arrow(consumed ? '已由 wait 取出' : all || single ? '等待该订阅消费标记' : '独立 wait / 消费', all || single));
  const recipient = group(`fanout-reader ${consumed ? 'is-received' : ''}`,
    label('SSE 消费端 · 所选订阅'),
    node('strong', '', consumed ? all || single ? '收到快照标记' : '收到变更批次' : '尚未取出本次批次'),
    node('p', '', consumed && (all || single) ? '此处只到标记交付；未演示快照返回或恢复完成。' : '后续仍按当前 actor 过滤；提示不是业务正文。'));
  selected.append(recipient);

  const other = group('fanout-branch'); other.dataset.branch = 'other';
  other.append(label('其他匹配、未满的订阅 · 代表支路'));
  const otherQueue = group(`fanout-bin ${all ? 'is-affected' : 'is-independent'}`, node('h3', '', '另一条独立队列'));
  if (all) otherQueue.append(node('s', '', '过时提示同样清除'), token('重读快照', true));
  else otherQueue.append(token('符合筛选的变更提示', false, true), node('small', '', single || slowPremise ? '不会被左侧队列清空，也不等待左侧恢复。' : '各订阅按自己的读取节奏消费。'));
  other.append(otherQueue, arrow(all ? '各自等待消费标记' : '继续 offer → wait', all));
  other.append(group(`fanout-reader ${all ? '' : 'is-independent'}`,
    label('SSE 消费端 · 其他订阅'), node('strong', '', all ? '也需要重读快照' : '仍可继续消费变更'),
    node('p', '', all ? '本次步进只观察左侧；此支路未单独回放消费时点。' : '这条消费通路保持开放；不把左侧的拥塞扩散到这里。')));
  branches.append(selected, other); root.append(branches);
  root.append(node('p', 'fanout-scope-note', '两条支路用于解释隔离范围，不代表实际订阅数量。右侧虚线提示是关系示意，不是另一份运行记录；不设虚构容量、提示 ID 或耗时。'));
  return root;
}
