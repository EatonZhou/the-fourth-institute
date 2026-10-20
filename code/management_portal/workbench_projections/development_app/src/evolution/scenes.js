import { el, btn, field, input } from './view.js';

const names = { research: '研究员', build: '开发者', test: '体验员' };
const people = ['研究员', '访谈员', '分析员', '开发者', '体验员'];
const svgns = 'http://www.w3.org/2000/svg';
function svg(tag, attributes, ...children) {
  const n = document.createElementNS(svgns, tag); for (const [key, value] of Object.entries(attributes)) n.setAttribute(key, value);
  n.append(...children); return n;
}
function shape(kind, attrs) { return svg(kind, attrs); }
function building(x, y, color, name) {
  const label = svg('text', { x: x + 85, y: y + 40, 'text-anchor': 'middle', fill: '#203846', 'font-size': 18 }); label.textContent = name;
  return svg('g', {}, shape('path', { d: `M${x} ${y+35}l85 -48 85 48 -85 48z`, fill: color }),
    shape('path', { d: `M${x} ${y+35}l85 48 85 -48v75l-85 48 -85 -48z`, fill: '#f3e9ce', stroke: '#b8a981' }),
    shape('rect', { x: x+63, y: y+83, width: 42, height: 42, rx: 5, fill: '#5e7283' }),
    shape('path', { d: `M${x+10} ${y+57}l32 18v27l-32 -18z`, fill: '#fbd67c' }), label);
}
function avatar(color = '#7971cb') {
  return svg('svg', { viewBox: '0 0 60 64', 'aria-hidden': true },
    shape('ellipse', { cx: 30, cy: 57, rx: 20, ry: 5, fill: '#203846', opacity: '.15' }),
    shape('path', { d: 'M15 54v-15q15 -14 30 0v15z', fill: color }),
    shape('circle', { cx: 30, cy: 22, r: 13, fill: '#f2c7a5' }),
    shape('path', { d: 'M16 22q-2 -22 16 -18q17 -1 12 19l-6 -10 -12 3z', fill: '#283b4c' }),
    shape('circle', { cx: 26, cy: 23, r: 1.6, fill: '#263441' }), shape('circle', { cx: 35, cy: 23, r: 1.6, fill: '#263441' }),
    shape('path', { d: 'M27 29q4 4 8 0', fill: 'none', stroke: '#9b604d', 'stroke-width': 1.5 }));
}
export function thumbnail(id) {
  const icon = el('span', null, 'evo-thumbnail'); icon.setAttribute('aria-hidden', 'true');
  const art = svg('svg', { viewBox: '0 0 180 88' });
  if (id === 'agent-society-city') {
    art.append(shape('path', { d: 'M4 69l65 -38 98 26 -67 29z', fill: '#c0d3a1' }), shape('path', { d: 'M22 40l32 -18 36 18v29l-34 17 -34 -19z', fill: '#e9d1a3' }), shape('path', { d: 'M22 40l32 -22 36 22 -34 18z', fill: '#7971cb' }), shape('path', { d: 'M106 31l24 -14 33 15v35l-28 14 -29 -16z', fill: '#e9d1a3' }), shape('path', { d: 'M106 31l24 -20 33 21 -28 15z', fill: '#c48463' }));
  } else if (id === 'team-command-table') {
    art.append(shape('ellipse', { cx: 45, cy: 55, rx: 32, ry: 20, fill: '#8a83c5', opacity: '.5' }), shape('ellipse', { cx: 137, cy: 55, rx: 32, ry: 20, fill: '#66aa9b', opacity: '.5' }), shape('path', { d: 'M77 54h28m-8 -7 9 7 -9 7', fill: 'none', stroke: '#e0af57', 'stroke-width': 3 }));
    for (const x of [30,50,65,125,145]) art.append(shape('circle', { cx:x, cy:42, r:7, fill:'#f3c6a7' }),shape('path',{ d:`M${x-8} 63v-10q8 -7 16 0v10z`,fill:'#4d6777'}));
  } else art.append(shape('path', { d: 'M25 42h25m0 0 25 -22m-25 22 25 22', fill:'none', stroke:'#6b9c8e', 'stroke-width':3 }), shape('rect',{x:78,y:12,width:90,height:65,rx:8,fill:'#b6cfc5'}),shape('rect',{x:87,y:33,width:26,height:16,rx:3,fill:'#6a6ab3'}),shape('rect',{x:119,y:53,width:35,height:15,rx:3,fill:'#e2ad61'}),shape('circle',{cx:25,cy:42,r:12,fill:'#cda66a'}));
  icon.append(art); return icon;
}

function actorButton(id, title, action, key, color) {
  const b = btn('', action, key); b.classList.add('evo-actor'); b.append(avatar(color), el('span', title)); b.setAttribute('aria-label', title); return b;
}

function city(root, state, change) {
  const s = state.city, board = el('div', null, 'evo-city'); board.dataset.step = state.step;
  const world = el('div', null, 'evo-city-world'); world.style.transform = `scale(${s.zoom})`;
  const art = svg('svg', { viewBox:'0 0 760 360', role:'img', 'aria-label':'研究屋与开发工作室通过街道相连，作品交给右侧体验台' });
  art.append(shape('path',{d:'M10 235L305 52 746 220 442 355z',fill:'#c7d9ad'}),shape('path',{d:'M58 238l238 -136 385 142 -234 98z',fill:'#e2dfcc'}),shape('path',{d:'M102 246l207 -115 311 115',fill:'none',stroke:'#f8f5e9','stroke-width':20}),building(85,116,'#8b83c2','研究屋'),building(357,95,'#d29572','开发工作室'),shape('path',{d:'M562 263l60 -34 73 34 -61 34z',fill:'#72a89a'}));
  world.append(art);
  const positions = { research:[29,64], build:[62,60], test:[81,81] };
  for (const [id,title] of Object.entries(names)) {
    const b = actorButton(id,title,()=>change({type:'city',value:{actor:id}},id==='build'?2:null),`actor:${id}`,s.actor===id&&s.costume==='coral'?'#c97965':'#7971cb');
    b.style.left = `${positions[id][0]}%`; b.style.top = `${positions[id][1]}%`; b.setAttribute('aria-pressed', String(s.actor===id)); world.append(b);
  }
  if (state.step>=1 || s.handoff) {
    const plane = btn('↗ 想法便签',()=>change({type:'city',value:{handoff:true}},1),'city-handoff'); plane.classList.add('evo-plane'); plane.dataset.delivered = s.handoff; world.append(plane);
  }
  if (state.step>=2) { const work = el('div',null,'evo-mini-page'); work.append(el('i'),el('i'),el('i')); world.append(work); }
  if (state.step>=3) { const exhibit=btn('打开作品',()=>change({type:'city',value:{exhibit:!s.exhibit}}),'city-exhibit'); exhibit.classList.add('evo-exhibit'); world.append(exhibit); }
  board.append(world); root.append(board);
  const controls = el('div',null,'evo-toolbar'); controls.append(btn('拉近街区',()=>change({type:'city',value:{zoom:Math.min(1.4,s.zoom+.1)}}),'city-zoom-in',s.zoom>=1.4),btn('拉远街区',()=>change({type:'city',value:{zoom:Math.max(1,s.zoom-.1)}}),'city-zoom-out',s.zoom<=1),btn(s.costume==='indigo'?'换成珊瑚色造型':'换成靛蓝色造型',()=>change({type:'city',value:{costume:s.costume==='indigo'?'coral':'indigo'}}),'city-costume'));
  board.onwheel = event => { if (!event.ctrlKey) return; event.preventDefault(); change({type:'city',value:{zoom:Math.max(1,Math.min(1.4,s.zoom+(event.deltaY<0?.1:-.1)))}}); };
  const speech = el('div',null,'evo-result'); speech.setAttribute('role','status');
  speech.append(el('strong',`${names[s.actor]} · 示例角色`),el('p',s.handoff?'研究员把想法交给开发者，开发者将它铺成小画布。':({research:'我在和同伴讨论这个想法。点选便签，看看它如何变成作品。',build:'我正把讨论结果做成一个可以打开的小页面。',test:'作品到体验台以后，我会走进去试一试。'})[s.actor]));
  root.append(controls,speech);
  if(s.exhibit) {const sample=el('section',null,'evo-sample-work'); sample.append(el('h3','城市里的第一件作品'),el('p','讨论 → 制作 → 体验：这张本地工作台记录了三个示例角色的接力。'),btn('收回城市全景',()=>change({type:'city',value:{exhibit:false}}),'city-back'));root.append(sample);}
}

function command(root, state, change, edit) {
  const s=state.command, board=el('div',null,'evo-command');
  const actors=el('div',null,'evo-squad-pool');
  const pieces=people.map((name,i)=>{const b=actorButton(i,name,()=>change({type:'member',id:i}),`member:${i}`,i<3?'#8378ba':'#5a9b8c');b.setAttribute('aria-pressed',String(s.members.includes(i)));return b;});
  if(!s.grouped)actors.append(...pieces);
  board.append(actors);
  const zones=el('div',null,'evo-squad-zones');
  const explore=el('section',null,'evo-squad exploration'), make=el('section',null,'evo-squad production');
  explore.append(el('h3','探索小队'),el('p',s.grouped?s.members.map(i=>people[i]).join(' · '):'选中角色，为这次任务编队'),btn('将选中角色编入探索队',()=>change({type:'group'}),'group',!s.members.length));
  make.append(el('h3','制作小队'),el('p',s.grouped?people.filter((_,i)=>!s.members.includes(i)).join(' · ')||'等待新的成员':'等待想法，准备试做'));
  if(s.grouped){const left=el('div',null,'evo-team-pieces'),right=el('div',null,'evo-team-pieces');pieces.forEach((p,i)=>(s.members.includes(i)?left:right).append(p));explore.append(left);make.append(right);}
  const drop=(node,kind,action)=>{node.ondragover=e=>e.preventDefault();node.ondrop=e=>{e.preventDefault();if(e.dataTransfer.getData('text/plain')===kind)action();};};
  drop(explore,'goal',()=>change({type:'command',value:{deployed:true}},1));drop(make,'opportunity',()=>change({type:'command',value:{relayed:true}},2));
  if(s.deployed)explore.append(el('p',`⚑ ${s.goal}`,'evo-goal-marker'));
  if(s.relayed){const sample=el('div',null,'evo-prototype-result');sample.append(el('strong','雨天出行 · 界面样稿'),el('p',s.route||'从公交站开始'),el('div','● 公交站 ━━━ ● 有顶连廊 ━━━ ◎ 目的地','evo-route-line'),el('small',s.route==='无障碍路线'?'优先电梯 · 避开台阶':'步行路线 · 避开积水'));make.append(sample);}
  zones.append(explore,make);board.append(zones);root.append(board);
  if(state.step>=1){
    const goal=el('div',null,'evo-command-goal'); goal.draggable=true; goal.ondragstart=e=>e.dataTransfer.setData('text/plain','goal');
    goal.append(input('小队目标',s.goal,v=>edit({type:'command',value:{goal:v}}),'command-goal'),input('对队长说',s.intent,v=>edit({type:'command',value:{intent:v}}),'command-intent'),btn('把目标交给探索队',()=>change({type:'command',value:{deployed:true}}),'deploy'));
    root.append(goal);
  }
  if(state.step>=2){
    const opportunities=el('div',null,'evo-opportunities'); opportunities.append(el('p','探索带回三个样例发现：雨伞收纳、拥挤的公交站、积水路段。'));
    const bus=btn('把公交站发现交给制作队',()=>change({type:'command',value:{relayed:true}}),'relay');bus.draggable=true;bus.ondragstart=e=>e.dataTransfer.setData('text/plain','opportunity');opportunities.append(bus);root.append(opportunities);
  }
  if(state.step>=3){const routes=el('div',null,'evo-toolbar');for(const route of ['无障碍路线','更少淋雨']){const b=btn(`继续发展${route}`,()=>change({type:'command',value:{route}}),`route:${route}`);b.setAttribute('aria-pressed',String(s.route===route));routes.append(b);}root.append(routes);}
  const output=el('div',null,'evo-result');output.setAttribute('role','status');output.append(el('strong','队长 · 本地模拟回应'),el('p',s.route?`收到，围绕「${s.route}」重新组织探索与制作。`:s.relayed?'制作队收到公交站发现，开始展开雨天出行界面。':s.deployed?`我们先探索「${s.goal}」。${s.intent}`:s.grouped?'搭档已选好。下一幕把目标交给小队。':'选中几名角色，再把他们编成探索小队。'));root.append(output);
}

function studio(root,state,change,edit){
  const s=state.studio, tree=el('div',null,'evo-studio');
  const seed=el('div',null,'evo-seed');seed.append(el('span','从一个目标开始','evo-eyebrow'),el('h3',s.goal));tree.append(seed);
  if(state.step===0){root.append(tree,input('样例软件目标',s.goal,v=>edit({type:'studio',value:{goal:v}}),'studio-goal'));
    const scope=el('select');for(const name of ['一个实验室','先试一台显微镜']){const option=el('option',name);option.selected=s.scope===name;scope.append(option);}scope.dataset.key='studio-scope';scope.onchange=()=>change({type:'studio',value:{scope:scope.value}});
    root.append(field('样例范围',scope),el('p','此处演示实验室设备预约，不会生成真实软件。','evo-muted'),btn('长出示例工作室',()=>change({type:'studio',value:{grown:true}},1),'grow'));return;
  }
  const roles=el('div',null,'evo-studio-team');for(const [id,title]of [['design','设计'],['code','开发'],['experience','体验']]){const b=actorButton(id,`${title}角色`,()=>change({type:'studio',value:{role:id}}),`studio-role:${id}`,id==='design'?'#8b80b6':id==='code'?'#68a393':'#c29864');b.setAttribute('aria-pressed',String(s.role===id));roles.append(b);}tree.append(roles);root.append(tree);
  if(s.role){const flow={design:'先看空闲时段 → 选择设备 → 写下用途 → 确认预约。',code:'把设备与日历连接成可操作的工作台。',experience:'试一次预约，看看哪些操作需要解释。'};root.append(el('p',flow[s.role],'evo-result'));}
  if(state.step===1){root.append(btn('进入示例预约日历',()=>change({type:'studio',value:{grown:true}},2),'open-calendar'));return;}
  const calendar=el('section',null,'evo-calendar');calendar.append(el('div','光学显微镜 / 样例周三','evo-calendar-head'));
  const slots=el('div',null,'evo-slots');for(const time of ['09:00–10:00','10:00–11:00','14:00–15:00']){const b=btn(time,()=>change({type:'studio',value:{slot:time}}),`slot:${time}`);b.setAttribute('aria-pressed',String(s.slot===time));if(s.reservation?.slot===time)b.append(el('span',s.reservation.purpose,'evo-booked'));slots.append(b);}calendar.append(slots);
  if(s.maintenance)calendar.append(el('p','维护 · 15:00–16:00 / 镜头清洁与校准','evo-maintenance'));
  calendar.append(input('预约用途',s.purpose,v=>edit({type:'studio',value:{purpose:v}}),'studio-purpose'),btn('在本地日历试约',()=>change({type:'reserve'}),'reserve',!s.slot||!s.purpose.trim()));root.append(calendar);
  const result=el('p',s.reservation?`已在本地样例中预约 ${s.reservation.slot} · ${s.reservation.purpose}`:'请选择一个时段，并写下示例用途。','evo-result');result.setAttribute('role','status');root.append(result);
  if(state.step>=3)root.append(el('p','使用之后，新的需要让工作室继续生长。'),btn(s.maintenance?'已加入维护日程':'加入维护日程',()=>change({type:'studio',value:{maintenance:true}}),'maintenance',s.maintenance));
}

export function renderScene(root,direction,state,change,edit){
  root.className=`evo-scene evo-scene-${direction.id}`;root.dataset.frame=direction.visual.storyboard[state.step]?.id;
  if(direction.id==='agent-society-city')city(root,state,change);
  else if(direction.id==='team-command-table')command(root,state,change,edit);
  else if(direction.id==='goal-grown-studio')studio(root,state,change,edit);
  else root.append(el('p',direction.visual.storyboard[state.step]?.scene||'暂未登记分镜。'));
}
