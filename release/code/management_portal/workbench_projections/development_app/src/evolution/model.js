// Local concept actions never create a Resident, booking, Plan or execution.
export function initialScene(direction) {
  return { step: 0, positionMs: 0, city: { actor: 'research', costume: 'indigo', zoom: 1, handoff: false, exhibit: false },
    command: { members: [], grouped: false, goal: '做一个雨天出行助手', intent: '先看看通勤的人需要什么', deployed: false, relayed: false, route: '' },
    studio: { goal: '给实验室做一个设备预约工具', scope: '一个实验室', grown: false, role: '', slot: '', purpose: '', reservation: null, maintenance: false },
    direction: direction.id };
}
export function reduceScene(direction, state, action) {
  const next = structuredClone(state), last = Math.max(0, (direction.visual?.storyboard?.length || 1) - 1);
  if (action.type === 'step') { next.step = Math.min(last, Math.max(0, action.value)); next.positionMs = 0; }
  if (action.type === 'reset') return initialScene(direction);
  if (action.type === 'city') Object.assign(next.city, action.value);
  if (action.type === 'member') {
    next.command.members = next.command.members.includes(action.id) ? next.command.members.filter(id => id !== action.id) : [...next.command.members, action.id];
    next.command.grouped = false;
  }
  if (action.type === 'group' && next.command.members.length) next.command.grouped = true;
  if (action.type === 'command') Object.assign(next.command, action.value);
  if (action.type === 'studio') Object.assign(next.studio, action.value);
  if (action.type === 'reserve' && next.studio.slot && next.studio.purpose.trim()) next.studio.reservation = { slot: next.studio.slot, purpose: next.studio.purpose.trim() };
  return next;
}

export function restoreScenes(facts, saved) {
  const scenes = Object.fromEntries(facts.directions.map(d => [d.id, initialScene(d)]));
  if (saved?.schema !== 'evolution-gallery.v1' || saved.baseline !== JSON.stringify(facts.sources)) return scenes;
  for (const d of facts.directions) {
    const s = saved.scenes?.[d.id];
    if (s?.direction === d.id && Number.isInteger(s.step) && s.step >= 0 && s.step < d.visual.storyboard.length && s.city && s.command && s.studio) scenes[d.id] = structuredClone(s);
  }
  return scenes;
}
