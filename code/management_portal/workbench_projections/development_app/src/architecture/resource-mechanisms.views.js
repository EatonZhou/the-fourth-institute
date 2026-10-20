// Approved causal forms from resource review-v2; inputs are declared model state.
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const text=(x,y,s,extra='')=>`<text x="${x}" y="${y}" ${extra}>${esc(s)}</text>`;
export function lockView(s,frames,cursor){
 const xs=frames.map((_,i)=>150+i*650/(frames.length-1));
 const start=frames.findIndex(f=>f.state.held),end=frames.findIndex((f,i)=>i>start&&!f.state.held);
 const startX=xs[Math.max(0,start)],endX=end<0?840:xs[end];
 const b=!s.cooperating?'绕锁写者：同一锁无法约束它':s.b_can_enter?'B 可以进入':s.held?'B 等待同一 lockfile':'B 尚未尝试';
 return `<h3>拖动时间游标：正文换了 inode，锁的身份仍保持不变</h3>
 <div class="causal-wrap"><svg class="causal" viewBox="0 0 960 420" role="img" aria-label="同一稳定lockfile上的A持锁与B等待时间线">
 <defs><pattern id="waiting" width="8" height="8" patternUnits="userSpaceOnUse"><path d="M0 8L8 0" stroke="#c79e53"/></pattern></defs>
 ${text(20,45,'稳定 lockfile','font-weight="650"')}<rect x="150" y="20" width="650" height="40" rx="8" fill="#dfeee8" stroke="#4a8a76"/>${text(170,46,'同一个锁文件；不随正文 replace 改变','class="sub"')}
 ${text(20,132,'写者 A','font-weight="650"')}<line x1="150" y1="130" x2="800" y2="130" stroke="#d1dad6" stroke-width="9"/>
 <rect x="${startX}" y="108" width="${Math.max(3,endX-startX)}" height="44" rx="6" fill="#307e69" opacity=".2"/>${text(startX+10,136,'A 的临界区','fill="#1e6350"')}
 ${text(20,220,'写者 B','font-weight="650"')}<line x1="150" y1="218" x2="800" y2="218" stroke="#d1dad6" stroke-width="9"/>
 ${s.cooperating?`<rect x="${startX}" y="197" width="${Math.max(3,endX-startX)}" height="42" fill="url(#waiting)" opacity=".65"/>${text(startX+12,223,'等待 A 解锁','fill="#805e1f"')}`:text(startX+12,223,'不参与同一锁协议：不能承诺排队','fill="#995741"')}
 <line x1="${xs[cursor]}" y1="65" x2="${xs[cursor]}" y2="270" stroke="#163f53" stroke-width="2"/><circle cx="${xs[cursor]}" cy="218" r="9" fill="${s.b_can_enter?'#237a61':'#b58435'}"/>
 <g>${text(20,320,'正文目标','font-weight="650"')}<rect x="150" y="290" width="225" height="72" rx="8" fill="${s.content_replaced?'#edf0ee':'#d9eee3'}" stroke="#829c91"/>${text(170,320,'替换前的 inode')}${text(170,343,s.content_replaced?'已从目标名称移走':'目标名称仍指向这里','class="sub"')}
 <path d="M390 325H455" stroke="#608f83" stroke-width="2"/><path d="M447 319l9 6-9 6" fill="none" stroke="#608f83"/>
 <rect x="470" y="290" width="250" height="72" rx="8" fill="${s.content_replaced?'#d9eee3':'#fff'}" stroke="#829c91" stroke-dasharray="${s.content_replaced?'none':'5 4'}"/>${text(490,320,'替换后的 inode')}${text(490,343,s.content_replaced?'目标名称现在指向这里':'尚未完成 replace','class="sub"')}</g>
 ${text(150,399,'横轴是模型中的逻辑时点，不是实际耗时；不虚构 inode 号。','class="sub"')}
 </svg></div><div class="on-canvas"><button data-probe="lock">让 B 尝试进入</button><b id="probe-observation" aria-live="polite">${esc(b)}</b><button data-advance>${esc(frames[cursor+1]?.step?.title||'回到开始观察')}</button></div>`;
}
export function permissionSnapshotView(s){
 const finished=s.decision!=='pending';
 const result={pending:'本次判定尚未完成',allow:'只允许这一次请求',deny_subject:'主体已失效，拒绝本次请求',deny_level:'当前等级不足，拒绝本次请求',authorization_unavailable:'当前授权事实不可用'};
 return `<div class="rs-request"><section><h3>一份请求</h3><p>资源地址有效，只能确定目标；这次执行的主体仍需授权。</p><div class="rs-line">请求 →</div></section><section class="rs-snapshot ${s.snapshot_open?'rs-open':''}"><small>同一授权事务快照</small><h3>${s.snapshot_open?'当前事实已打开':'等待读取当前事实'}</h3><dl><dt>当前主体</dt><dd>${s.snapshot_open?(s.subject_valid?'有效且绑定匹配':'主体已失效'):'尚未读取'}</dd><dt>父图</dt><dd>${s.snapshot_open?(s.graph_valid?'完整无环':'不可用'):'尚未读取'}</dd><dt>凭证集合</dt><dd>${s.snapshot_open?(s.credentials_ready?'与 principal revision 一致':'revision 不一致，不能按空集合处理'):'尚未读取'}</dd><dt>所需等级</dt><dd>${s.snapshot_open?(s.sufficient?'当前派生等级满足请求':'当前派生等级不足'):'尚未读取'}</dd></dl></section><section><h3>本次出口</h3><strong>${esc(result[s.decision]||s.decision)}</strong><p>${finished?'结果属于当前请求；后续请求重新读取其当前事实。':'判定完成前不放行业务操作。'}</p><div class="rs-line">下一份请求 ↩ 重新打开快照</div></section></div>`;
}
