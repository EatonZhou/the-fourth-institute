// Shared native surface; each mount owns its DOM and event handlers.
export default `
  <main class="graphWorkspace">
    <div class="graphToolbar">
      <div class="graphTools"><button id="zoom-out" aria-label="缩小">−</button><output id="zoom-level">100%</output><button id="zoom-in" aria-label="放大">＋</button><button id="fit" title="适应全图 (F)">适应</button><button id="undo" title="撤销上次节点移动">↶</button><button id="arrange" title="恢复自动布局">整理</button></div>
      <div class="graphTools"><input id="search" type="search" aria-label="搜索线头" placeholder="搜索问题、工作线"><button id="focus" aria-pressed="false">局部</button><button id="refresh">刷新</button><button id="new" class="primary" disabled>＋ 线头</button></div>
    </div>
    <nav id="filters" aria-label="线头筛选"></nav>
    <div id="notice" role="status" aria-live="polite"></div>
    <div class="graphStage"><div id="graph"></div><aside id="detail" hidden aria-label="线头详情"><div id="detail-body"></div></aside></div>
    <footer class="graphFooter"><span class="legend"><i></i>发展 <i class="dependency"></i>依赖 <i class="merge"></i>汇合 <i class="team"></i>团队关联</span><span id="checked">正在读取…</span><span>拖动画布 · 滚轮缩放 · 点击节点查看</span></footer>
  </main>
  <dialog id="source"><div class="dialog-heading"><h2>来源原文</h2><button type="button" data-close="source" aria-label="关闭">×</button></div><pre id="source-text" style="white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;line-height:1.9"></pre></dialog>
  <dialog id="editor"><form id="edit-form"><div class="dialog-heading"><h2 id="edit-title">留下一条线头</h2><button type="button" data-close="editor" aria-label="关闭">×</button></div><p class="muted">围绕一个推进目标持续更新，让下一次思考有所依据。</p>
    <label>正在探索什么<input name="title" required maxlength="120" placeholder="一个值得继续追问的问题"></label>
    <div class="form-grid"><label>工作线<input name="line" required maxlength="80" placeholder="例如：长期研究判断"></label><label>介入要求<select name="attention"><option value="optional">可以介入 · 已授权工作继续</option><option value="decision">需要决定 · 对应选择等待</option><option value="explore">留待探索 · 尚未启动</option></select></label></div>
    <label>已经推进到哪里<textarea name="progress" required maxlength="1200" rows="2"></textarea></label>
    <label>为什么值得继续<textarea name="reason" required maxlength="1200" rows="2" placeholder="它可能带来什么新认识？哪些判断还需要检验？"></textarea></label>
    <label>下一步 / 需要作出的选择<textarea name="next" required maxlength="1200" rows="2"></textarea></label>
    <label>依据（资源地址，每行一个）<textarea name="sources" required rows="2" placeholder="thinking_…&#10;plan_…"></textarea></label>
    <div class="form-grid"><label>负责 Agent<select name="owner"><option value="">暂不指定</option></select></label><label>关联线头<select name="parent"><option value="">独立线索</option></select></label><label>关系<select name="relation"><option value="builds_on">从它发展</option><option value="depends_on">依赖它</option><option value="converges">与它汇合</option></select></label><label>入口状态<select name="closed"><option value="">仍可展开</option><option value="concluded">已收束</option><option value="cancelled">已取消</option></select></label></div>
    <p class="muted">内容向本团队共享；请只填写可在此范围分享的判断。来源正文按当前访问权限打开。</p><p class="form-error" role="alert"></p><footer><button type="button" data-close="editor">取消</button><button class="primary">保存线头</button></footer>
  </form></dialog>
  <dialog id="dispatch"><form id="dispatch-form"><div class="dialog-heading"><h2>接着推进这条线</h2><button type="button" data-close="dispatch" aria-label="关闭">×</button></div><label>负责 Agent<select name="owner" required></select></label><label>你的判断或希望探索的下一步<textarea name="instruction" required rows="5" maxlength="6000"></textarea></label><p class="muted">确认后发送真实消息。接收、执行和结果将分开显示；重复提交同一请求不会重复启动。</p><p class="form-error" role="alert"></p><footer><button type="button" data-close="dispatch">取消</button><button class="primary">确认交给 Agent</button></footer></form></dialog>
`;
