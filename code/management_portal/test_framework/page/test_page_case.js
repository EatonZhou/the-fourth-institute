(() => {
  "use strict";

  // A case's page (decision_f2a6a5dd4646): what it checks, how it is
  // implemented, the runs that included it and its Live demo.  The definition
  // is read-only here; it lives in the test source.
  const T = window.PortalTest;
  const t = value => window.PortalI18n.t(value);
  const esc = T.escapeHtml;

  // The implementation "运行" uses, per case, for as long as the page stays open.
  const chosen = new Map();

  function sourceDialog(documentValue) {
    const overlay = document.createElement("div");
    overlay.className = "portalDetailOverlay";
    overlay.hidden = true;
    overlay.dataset.portalDetailOverlay = "1";
    overlay.dataset.portalOverlayLayer = "dialog";
    const lines = String(documentValue.content || "").split("\n");
    const target = Number(documentValue.line || 0);
    overlay.innerHTML = window.PortalI18n.html`<section class="portalDetailSurface testSourceSurface" data-portal-detail-surface role="dialog" aria-modal="true" aria-labelledby="testSourceTitle">
      <header class="portalDetailHeader"><div><h2 id="testSourceTitle"><code>${esc(documentValue.path || documentValue.title || "")}</code></h2>${target ? window.PortalI18n.html`<p class="testSourceLine">第 ${target} 行 · <code>${esc(documentValue.entrypoint || "")}</code></p>` : ""}</div>
        <button class="portalDetailClose" type="button" data-source-close aria-label="关闭" data-portal-icon="close"></button></header>
      <div class="portalDetailBody testSourceBody"><ol class="testSourceCode" translate="no"></ol></div>
    </section>`;
    const list = overlay.querySelector(".testSourceCode");
    lines.forEach((text, index) => {
      const item = document.createElement("li");
      item.textContent = text || " ";
      if (index + 1 === target) item.dataset.target = "true";
      list.append(item);
    });
    document.body.append(overlay);
    window.PortalUIPrimitives?.mountIcons?.(overlay);
    const controller = window.PortalUI.createDialogController({
      modal: overlay, backdropClose: true, escapeClose: true,
      onClose: () => { controller.destroy(); overlay.remove(); },
    });
    overlay.querySelector("[data-source-close]").addEventListener("click", () => controller.close());
    controller.open({ focusTarget: overlay.querySelector("[data-source-close]") });
    list.querySelector("[data-target]")?.scrollIntoView({ block: "center" });
  }

  async function renderDesignBinding(item, host) {
    if (!item.design_point_ref) {
      host.dataset.bindingState = "unbound";
      host.textContent = item.is_design_point ? t("还没有绑定 WALL 设计点；关联设计不代表点级绑定。") : t("不是设计点。");
      return;
    }
    host.dataset.bindingState = "loading";
    host.textContent = t("正在核对 WALL 设计点…");
    try {
      const binding = await T.api(`/api/wall/test-binding?case_id=${encodeURIComponent(item.id)}`);
      if (!host.isConnected) return;
      if (binding.ref !== item.design_point_ref) throw new Error(t("点绑定已变化，请刷新页面。"));
      host.dataset.bindingState = binding.status;
      if (binding.status === "valid" && binding.url?.startsWith("/wall?")) {
        const link = document.createElement("a");
        link.href = binding.url;
        link.textContent = window.PortalI18n.text`WALL 设计点 · ${binding.title || binding.ref}`;
        link.dataset.wallDesignPoint = binding.ref;
        host.replaceChildren(link);
      } else {
        host.textContent = window.PortalI18n.text`WALL 设计点不可用：${binding.reason || t("请重新选择设计点")}`;
      }
    } catch (error) {
      if (!host.isConnected) return;
      host.dataset.bindingState = "error";
      host.textContent = window.PortalI18n.text`WALL 设计点核对失败：${error.message}`;
    }
  }

  function create({ root }) {
    let item = null;
    let disposed = false;
    let current = "";

    const implementations = () => item?.implementations || [];
    const choice = () => chosen.get(item.id) || implementations()[0]?.id || "";
    const runs = () => T.recentRuns().filter(run => T.runCaseIds(run).includes(item.id));

    function heroHtml() {
      const runsHere = runs();
      const latest = runsHere[0];
      const icon = implementations().some(impl => impl.runner === "tau") ? "chat" : implementations().length ? "code" : "alert-triangle";
      return window.PortalI18n.html`<header class="testHero is-case">
        <div class="testHeroIcon" data-portal-icon="${icon}" aria-hidden="true"></div>
        <div class="testHeroMain">
          <div class="testHeroTitle"><h1>${esc(item.title || item.key)}</h1><div class="testHeroMeta">${T.caseChipsHtml(item)}</div></div>
          <p class="testHeroDescription">${esc(item.description || t("没有说明。"))}</p>
          <code class="testHeroKey" title="${esc(item.id)}">${esc(item.key)}</code>
        </div>
        <div class="testHeroActions">
          <button class="portalButton is-compact is-primary" type="button" data-case-run data-portal-icon="play" ${implementations().length ? "" : "disabled"}><span>运行</span></button>
          <button class="portalButton is-compact" type="button" data-case-copy data-portal-icon="copy"><span>复制链接</span></button>
        </div>
        <div class="testStats">
          <span class="testStat"><strong>${implementations().length}</strong><span>个实现</span></span>
          <span class="testStat"><strong>${(item.design_refs || []).length}</strong><span>关联设计</span></span>
          <span class="testStat"><strong>${runsHere.length}</strong><span>次最近运行</span></span>
          <span class="testStat">${latest ? `${T.statusHtml(latest.status)}<span>${esc(window.PortalI18n.text`最近一次 · ${T.relativeTime(latest.created_at)}`)}</span>` : `<strong>—</strong><span>${esc(t("还没有运行过"))}</span>`}</span>
        </div>
      </header>`;
    }

    function implementationHtml(impl) {
      const several = implementations().length > 1;
      const risk = impl.risk || "unspecified";
      const prompt = String(impl.task_prompt || "");
      return window.PortalI18n.html`<article class="testImpl" data-implementation-id="${esc(impl.id)}" data-chosen="${impl.id === choice()}">
        <header>
          ${several ? `<input type="radio" name="testImplementation" value="${esc(impl.id)}" ${impl.id === choice() ? "checked" : ""} aria-label="${esc(window.PortalI18n.text`默认使用 ${impl.id}`)}">` : ""}
          <strong>${esc(T.runner(impl.runner)?.label || impl.runner)}</strong>
          <code>${esc(impl.id)}</code>
          <span class="testChipRow">${T.chip(window.PortalI18n.text`执行风险 · ${T.riskLabel(risk)}`, T.riskTone(risk))}${(impl.profiles || []).map(profile => T.chip(profile)).join("")}</span>
        </header>
        <dl class="testImplFacts"><dt>入口</dt><dd><code>${esc(impl.entrypoint || "—")}</code></dd><dt>源码</dt><dd><code>${esc(impl.source || "—")}</code></dd></dl>
        ${impl.lineage_fixture ? window.PortalI18n.html`<p class="testImplNote"><strong>团队准备：</strong>${esc(impl.lineage_fixture)}</p>` : ""}
        ${prompt ? window.PortalI18n.html`<details class="testImplPrompt"><summary>任务说明 · ${prompt.length} 字</summary><div class="testPromptText"></div></details>` : ""}
        <div class="testImplActions">
          <button class="portalButton is-compact" type="button" data-source-impl="${esc(impl.id)}" data-portal-icon="file-text"><span>查看源码</span></button>
          <button class="portalButton is-compact" type="button" data-run-impl="${esc(impl.id)}" data-portal-icon="play"><span>用这个实现运行</span></button>
        </div>
      </article>`;
    }

    function definitionHtml() {
      const module = T.moduleInfo(item.module);
      const designs = (item.design_refs || []).map(ref => window.PortalResourceChips?.resourceChipHtml?.(ref) || `<code>${esc(ref)}</code>`).join("");
      const features = (item.features || []).map(feature => (T.overview()?.filters?.features || []).find(entry => entry.id === feature)?.label || feature);
      return window.PortalI18n.html`<section class="testSection" data-test-section="definition">
        <div class="testSectionHead"><h2>定义</h2><span class="testSectionNote">标题、说明、分类与设计点绑定写在测试源码中，修改源码后这里随之更新。</span></div>
        <dl class="testFacts">
          <div><dt>功能模块</dt><dd><a href="${esc(T.catalogHref({ module: item.module }))}">${esc(module?.label || item.module)}</a><small>${esc(module?.group?.label || "")}</small></dd></div>
          <div><dt>重要等级</dt><dd>${esc(item.importance)}</dd></div>
          <div><dt>测试方面</dt><dd>${esc((item.aspects || []).map(aspect => T.aspectLabels[aspect] || aspect).join(" · ") || "—")}</dd></div>
          <div><dt>所需功能</dt><dd>${esc(features.join(" · ") || t("核心"))}</dd></div>
          <div><dt>生命周期</dt><dd>${esc(item.lifecycle || "active")}</dd></div>
          <div><dt>WALL 设计点</dt><dd data-design-point-binding role="status"></dd></div>
        </dl>
        <div class="testDesigns"><span>关联设计</span>${designs || `<span class="testMuted">${esc(t("没有关联设计"))}</span>`}</div>
      </section>`;
    }

    function runsHtml() {
      const rows = runs().map(run => T.runRowHtml(run)).join("");
      return window.PortalI18n.html`<section class="testSection" data-test-section="runs">
        <div class="testSectionHead"><h2>运行记录</h2><span class="testSectionNote">保留的运行中包含这条用例的那些，谁发起的都在这里。</span></div>
        ${rows ? `<div class="testRunRows">${rows}</div>` : window.PortalI18n.html`<p class="testEmpty">保留的运行中还没有这条用例。</p>`}
      </section>`;
    }

    function liveHtml() {
      if (!item.live) return "";
      const live = item.live;
      const steps = (live.direct_steps || live.steps || []).length;
      return window.PortalI18n.html`<section class="testSection" data-test-section="live">
        <div class="testSectionHead"><h2>Live 演示</h2><span class="testSectionNote">按登记的步骤演示这条用例，只读；演示不产生运行结果。</span></div>
        <div class="testLive">
          <div><strong>${esc(live.title || t("Live 演示"))}</strong><p>${esc(live.mode === "direct" ? window.PortalI18n.text`在真实页面上演示 · ${steps} 步` : window.PortalI18n.text`在隔离的沙盒中回放 · ${steps} 步`)}</p></div>
          <button class="portalButton is-compact is-primary" type="button" data-live data-portal-icon="play"><span>播放</span></button>
        </div>
        <p class="testMuted" data-live-result role="status"></p>
      </section>`;
    }

    function render() {
      const module = T.moduleInfo(item.module);
      root.innerHTML = window.PortalI18n.html`<article class="testCasePage">
        ${T.crumbsHtml([{ label: "Test", href: T.href() }, { label: t("用例目录"), href: T.catalogHref() }, { label: module?.label || item.module, href: T.catalogHref({ module: item.module }) }, { label: item.title || item.key }])}
        ${heroHtml()}
        <section class="testSection" data-test-section="implementations">
          <div class="testSectionHead"><h2>实现</h2><span class="testSectionNote">${esc(implementations().length > 1 ? t("选中的实现是“运行”默认使用的那一个。") : t("这条用例由下面的实现执行。"))}</span></div>
          ${implementations().length ? `<div class="testImpls">${implementations().map(implementationHtml).join("")}</div>` : window.PortalI18n.html`<p class="testEmpty">这条用例还没有可执行的实现。</p>`}
        </section>
        <div class="testColumns">${definitionHtml()}${runsHtml()}</div>
        ${liveHtml()}
      </article>`;
      window.PortalUIPrimitives?.mountIcons?.(root);
      window.PortalResourceChips?.bind?.(root);
      T.fillNames(root);
      root.querySelectorAll(".testImplPrompt").forEach(details => {
        const impl = implementations().find(entry => entry.id === details.closest("[data-implementation-id]").dataset.implementationId);
        details.querySelector(".testPromptText").textContent = impl?.task_prompt || "";
      });
      void renderDesignBinding(item, root.querySelector("[data-design-point-binding]"));
      bind();
    }

    function runWith(implementationId) {
      const impl = implementations().find(entry => entry.id === implementationId);
      if (!impl) return;
      const spec = { implementation_ids: [impl.id], runners: [impl.runner], ...(impl.profiles?.length ? { profile: impl.profiles[0] } : {}) };
      T.prepareRun([item.id], spec, window.PortalI18n.text`用例「${item.title || item.key}」`);
    }

    function bind() {
      root.querySelector("[data-case-run]")?.addEventListener("click", () => runWith(choice()));
      root.querySelector("[data-case-copy]")?.addEventListener("click", () => void T.copyLink(T.caseHref(item.id)));
      root.querySelectorAll('input[name="testImplementation"]').forEach(input => input.addEventListener("change", () => {
        chosen.set(item.id, input.value);
        root.querySelectorAll("[data-implementation-id]").forEach(card => { card.dataset.chosen = String(card.dataset.implementationId === input.value); });
      }));
      root.querySelectorAll("[data-run-impl]").forEach(button => button.addEventListener("click", () => runWith(button.dataset.runImpl)));
      root.querySelectorAll("[data-source-impl]").forEach(button => button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          sourceDialog(await T.api(`/test/api/source?case_id=${encodeURIComponent(item.id)}&implementation_id=${encodeURIComponent(button.dataset.sourceImpl)}`));
        } catch (error) {
          T.notify(window.PortalI18n.text`无法读取源码：${error.message || error}`, "error");
        } finally {
          button.disabled = false;
        }
      }));
      root.querySelector("[data-live]")?.addEventListener("click", async () => {
        const status = root.querySelector("[data-live-result]");
        try {
          if (!window.PortalTestLive) throw new Error(t("Live 播放器尚未就绪"));
          status.textContent = t("正在准备只读演示…");
          await window.PortalTestLive.open(item);
          status.textContent = "";
        } catch (error) {
          status.textContent = window.PortalI18n.text`Live 无法开始：${error.message || error}`;
        }
      });
    }

    async function load(caseId) {
      current = caseId;
      if (!item || item.id !== caseId) root.innerHTML = T.stateHtml("loading", t("正在读取用例…"));
      const [detail] = await Promise.all([T.caseDetail(caseId), T.loadOverview()]);
      if (disposed || current !== caseId) return null;
      item = detail;
      render();
      return item;
    }

    const unsubscribe = T.subscribe(event => { if (event === "runs" && item && !disposed && root.isConnected) render(); });
    return {
      load,
      item: () => item,
      choose(implementationId) {
        if (!item || !implementations().some(entry => entry.id === implementationId)) return false;
        chosen.set(item.id, implementationId);
        render();
        return true;
      },
      destroy() { disposed = true; unsubscribe(); },
    };
  }

  window.PortalTestCase = Object.freeze({ create });
})();
