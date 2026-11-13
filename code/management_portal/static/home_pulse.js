(() => {
  if (window.PortalHomePulse) return;

  // Home's daily news and Portal Pulse panels: public and personal activity by
  // period, and publishing a Pulse item.
  const DEPENDENCIES = Object.freeze([
    "esc", "formatPortalTime", "homeApi",
  ]);

  function createPulse(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHomePulse is missing: ${missing.join(", ")}`);
    const {
      esc, formatPortalTime, homeApi,
    } = dependencies;

    let portalPulseData = null;
    let portalPulsePublicRange = "daily";
    let portalPulsePersonalRange = "daily";
    let dailyNewsEdition = "";
    let dailyNewsRequest = 0;

    function homeStatusHtml(label, tone = "neutral", className = "") {
      return window.PortalUIPrimitives.statusHtml(tone, {
        className,
        label,
        tone,
      });
    }

    function pulsePeriodLabel(period, data = portalPulseData) {
      const item = data?.periods?.[period]?.public || {};
      if (item.label) return item.label;
      return period === "weekly" ? "Week" : period === "monthly" ? "Month" : "Day";
    }

    function pulseTabsHtml(scope, range) {
      return window.PortalI18n.html`
    <div class="pulse-tabs" role="tablist" aria-label="Pulse range">
      ${["daily", "weekly", "monthly"].map(period => window.PortalI18n.html`
        <button
          class="portalButton is-compact is-quiet pulse-tab ${range === period ? "active" : ""}"
          type="button"
          role="tab"
          aria-selected="${range === period ? "true" : "false"}"
          aria-pressed="${range === period ? "true" : "false"}"
          data-pulse-scope="${scope}"
          data-pulse-range="${period}"
        >
          ${period === "daily" ? "Day" : period === "weekly" ? "Week" : "Month"}
        </button>
      `).join("")}
    </div>
  `;
    }

    function pulseEvidenceHtml(paths = []) {
      const clean = (Array.isArray(paths) ? paths : []).filter(Boolean).slice(0, 2);
      if (!clean.length) return "";
      return window.PortalI18n.html`
    <div class="pulse-evidence" aria-label="Evidence">
      <span class="pulse-evidence-label">Evidence</span>
      ${clean.map(path => {
            const address = String(path).trim();
            const relative = address.replace(/^\/share\/Projects\//, "");
            const parts = relative.split("/").filter(Boolean);
            const label = parts.slice(-2).join("/") || relative;
            return window.PortalI18n.html`<a class="pulse-evidence-chip" href="/r/${encodeURIComponent(address)}" data-resource-address="${esc(address)}" title="${esc(relative)}">${esc(label)}</a>`;
          }).join("")}
    </div>
  `;
    }

    function renderPulseProjects(publicPulse) {
      const projects = Array.isArray(publicPulse?.project_updates) ? publicPulse.project_updates : [];
      if (!projects.length) return window.PortalI18n.html`<div class="pulse-empty">当前时间段没有项目更新摘要。</div>`;
      return window.PortalI18n.html`
    <div class="pulse-section">
      <div class="pulse-section-head">
        <div class="pulse-section-title">Project changes</div>
        ${homeStatusHtml(`${projects.length} projects`, "neutral")}
      </div>
      <div class="pulse-list">
        ${projects.slice(0, 5).map(item => window.PortalI18n.html`
          <article class="pulse-row">
            <div class="pulse-row-head">
              <div class="pulse-row-title" title="${esc(item.project || "")}">${esc(item.project || window.PortalI18n.t("Project"))}</div>
              ${homeStatusHtml(`${Number(item.change_count || 0)} updates`, "info", "pulse-update-count")}
            </div>
            <div class="pulse-row-text" title="${esc(item.summary || "")}">${esc(item.summary || "")}</div>
            ${pulseEvidenceHtml(item.evidence_paths)}
          </article>
        `).join("")}
      </div>
    </div>
  `;
    }

    function renderPulsePublications(data = portalPulseData) {
      const publications = Array.isArray(data?.publications) ? data.publications : [];
      if (!publications.length) return "";
      return window.PortalI18n.html`
    <div class="pulse-section">
      <div class="pulse-section-head">
        <div class="pulse-section-title">Published</div>
        ${homeStatusHtml(`${publications.length} items`, "neutral")}
      </div>
      <div class="pulse-list">
        ${publications.slice(0, 4).map(item => window.PortalI18n.html`
          <article class="pulse-row">
            <div class="pulse-row-head">
              <div class="pulse-row-title" title="${esc(item.project || "")}">${esc(item.project || item.owner || "Published")}</div>
              <div class="pulse-meta">${esc(item.owner || "")}</div>
            </div>
            <div class="pulse-row-text">${esc(item.text || "")}</div>
            ${pulseEvidenceHtml(item.evidence_paths)}
          </article>
        `).join("")}
      </div>
    </div>
  `;
    }

    function renderPulseMine(minePulse) {
      const items = Array.isArray(minePulse?.items) ? minePulse.items : [];
      if (!items.length) {
        return window.PortalI18n.html`<div class="pulse-empty">当前时间段没有识别到只属于你的进展条目。</div>`;
      }
      return window.PortalI18n.html`
    <div class="pulse-list">
      ${items.slice(0, 10).map(item => window.PortalI18n.html`
        <div class="pulse-row">
          <div class="pulse-row-head">
            <div class="pulse-row-title" title="${esc(item.project || "")}">${esc(item.project || "Personal")}</div>
            ${item.published
                  ? window.PortalI18n.html`<button class="portalButton is-compact is-active pulse-action" type="button" data-pulse-unpublish="1" data-source-period="${esc(item.source_period || "")}" data-source-key="${esc(item.source_key || "")}" data-item-id="${esc(item.id || "")}">Retract</button>`
                  : window.PortalI18n.html`<button class="portalButton is-compact pulse-action" type="button" data-pulse-publish="1" data-source-period="${esc(item.source_period || "")}" data-source-key="${esc(item.source_key || "")}" data-item-id="${esc(item.id || "")}">Publish</button>`}
          </div>
          <div class="pulse-row-text">${esc(item.text || "")}</div>
          ${pulseEvidenceHtml(item.evidence_paths)}
        </div>
      `).join("")}
    </div>
  `;
    }

    function attachPulseHandlers() {
      for (const button of document.querySelectorAll("[data-pulse-range]")) {
        button.addEventListener("click", () => {
          const nextRange = button.getAttribute("data-pulse-range") || "daily";
          if (button.getAttribute("data-pulse-scope") === "personal") {
            portalPulsePersonalRange = nextRange;
          } else {
            portalPulsePublicRange = nextRange;
          }
          renderPortalPulse();
        });
      }
      for (const button of document.querySelectorAll("[data-pulse-publish], [data-pulse-unpublish]")) {
        button.addEventListener("click", () => publishPortalPulseItem({
          published: button.hasAttribute("data-pulse-publish"),
          source_period: button.getAttribute("data-source-period") || "",
          source_key: button.getAttribute("data-source-key") || "",
          item_id: button.getAttribute("data-item-id") || "",
        }));
      }
    }

    function dailyNewsEditionsHtml(data) {
      const editions = Array.isArray(data?.editions) ? data.editions : [];
      if (editions.length < 2) return "";
      const current = data?.edition || dailyNewsEdition || editions[0].id;
      return window.PortalI18n.html`
    <div class="pulse-tabs daily-news-editions" role="tablist" aria-label="Daily News edition">
      ${editions.map(edition => window.PortalI18n.html`
        <button
          class="portalButton is-compact is-quiet pulse-tab ${edition.id === current ? "active" : ""}"
          type="button"
          role="tab"
          aria-selected="${edition.id === current ? "true" : "false"}"
          aria-pressed="${edition.id === current ? "true" : "false"}"
          data-daily-news-edition="${esc(edition.id)}"
        >${esc(edition.title)}</button>
      `).join("")}
    </div>
  `;
    }

    function attachDailyNewsHandlers() {
      for (const button of document.querySelectorAll("[data-daily-news-edition]")) {
        button.addEventListener("click", () => {
          dailyNewsEdition = button.getAttribute("data-daily-news-edition") || "";
          loadDailyNews();
        });
      }
      for (const link of document.querySelectorAll('[data-daily-news-jump]')) {
        link.addEventListener('click', event => {
          event.preventDefault();
          document.getElementById(link.getAttribute('data-daily-news-jump'))?.scrollIntoView({ block: 'start', behavior: 'smooth' });
        });
      }
    }

    function dailyNewsBodyHtml(content) {
      if (!content || !window.MD?.render) return window.PortalI18n.html`<div class="daily-news-empty">今天的新闻报告为空。</div>`;
      const report = document.createElement('div');
      report.innerHTML = window.MD.render(content);
      const headings = [...report.querySelectorAll('h2, h3')];
      const summaryHeading = headings.find(heading => /^(?:摘要|今日摘要|本期摘要|今日速览|要点速览|summary)$/i.test(heading.textContent.trim()));
      const summary = document.createElement('div');
      if (summaryHeading) {
        // Move the author's summary, preserving Markdown formatting and links.
        let sibling = summaryHeading.nextElementSibling;
        while (sibling && !/^H[1-3]$/.test(sibling.tagName)) {
          const next = sibling.nextElementSibling;
          summary.append(sibling);
          sibling = next;
        }
        summaryHeading.remove();
      }
      if (!summary.textContent.trim()) {
        // Older reports have no summary: give a short, source-faithful overview.
        const stories = headings.filter(heading => report.contains(heading))
          .filter(heading => /^\d+[.、．)）]\s*/.test(heading.textContent.trim())).slice(0, 5);
        const list = document.createElement('ul');
        for (const [index, heading] of stories.entries()) {
          heading.id = `daily-news-story-${index}`;
          const item = document.createElement('li');
          const link = document.createElement('a');
          link.href = `#${heading.id}`;
          link.dataset.dailyNewsJump = heading.id;
          link.textContent = heading.textContent.trim().replace(/^\d+[.、．)）]\s*/, '');
          item.append(link);
          list.append(item);
        }
        summary.append(list);
      }
      return window.PortalI18n.html`
        <section class="daily-news-summary" aria-label="今日摘要">
          <h2>今日摘要</h2>
          ${summary.textContent.trim() ? summary.innerHTML : '<p>本期未提供摘要，请阅读下方完整报告。</p>'}
        </section>
        <section class="daily-news-details" aria-label="详细信息与讨论">
          <h2>详细信息与讨论</h2>
          ${report.innerHTML}
        </section>`;
    }

    function renderDailyNews(data) {
      const node = document.querySelector("[data-daily-news]");
      if (!node) return;
      const editionsHtml = dailyNewsEditionsHtml(data);
      if (data?.status === "missing") {
        node.innerHTML = window.PortalI18n.html`${editionsHtml}<div class="daily-news-empty">今天的新闻报告还没有生成。</div>`;
        attachDailyNewsHandlers();
        return;
      }
      if (data?.status !== "ok") {
        node.innerHTML = window.PortalI18n.html`${editionsHtml}<div class="daily-news-error">${esc(data?.error || window.PortalI18n.t("今日新闻读取失败"))}</div>`;
        attachDailyNewsHandlers();
        return;
      }
      const content = String(data.content || "").trim();
      const updatedLabel = [
        data.stale ? window.PortalI18n.t("最近有效报告") : "",
        data.updated_at ? window.PortalI18n.text`更新于 ${formatPortalTime(data.updated_at)}` : "",
      ].filter(Boolean).join(" · ");
      node.innerHTML = window.PortalI18n.html`
    <div class="daily-news-panel">
      <div class="daily-news-toolbar">
        ${editionsHtml}
        ${homeStatusHtml(data.date || "Today", "info", "daily-news-date")}
        ${updatedLabel ? window.PortalI18n.html`<div class="daily-news-meta">${esc(updatedLabel)}</div>` : ""}
      </div>
      <div class="daily-news-body">${dailyNewsBodyHtml(content)}</div>
    </div>
  `;
      attachDailyNewsHandlers();
    }

    async function loadDailyNews() {
      const node = document.querySelector("[data-daily-news]");
      const request = ++dailyNewsRequest;
      const query = dailyNewsEdition ? `?edition=${encodeURIComponent(dailyNewsEdition)}` : "";
      try {
        const data = await homeApi.get(`/api/daily-news${query}`);
        if (request === dailyNewsRequest) renderDailyNews(data);
      } catch (err) {
        if (node && request === dailyNewsRequest) renderDailyNews({ status: 'error', edition: dailyNewsEdition, editions: [...node.querySelectorAll('[data-daily-news-edition]')].map(button => ({ id: button.dataset.dailyNewsEdition, title: button.textContent })), error: window.PortalUI.errorMessage(window.PortalI18n.t("读取今日新闻"), err) });
      }
    }

    function renderPortalPulse(data = portalPulseData) {
      portalPulseData = data || portalPulseData;
      const publicNode = document.querySelector('[data-summary-value="urgent-work"]');
      const mineNode = document.querySelector('[data-summary-value="contribution-window"]');
      if (portalPulseData?.enabled === false) {
        if (publicNode) publicNode.innerHTML = window.PortalI18n.html`<div class="pulse-empty">Portal Pulse 当前不可用。</div>`;
        if (mineNode) mineNode.innerHTML = window.PortalI18n.html`<div class="pulse-empty">Portal Pulse 当前不可用。</div>`;
        return;
      }
      const publicPulse = portalPulseData?.periods?.[portalPulsePublicRange]?.public || {};
      const minePulse = portalPulseData?.periods?.[portalPulsePersonalRange]?.mine || {};
      if (publicNode) {
        const hasSummary = Boolean(publicPulse.global_summary || (publicPulse.project_updates || []).length);
        const periodMeta = `${pulsePeriodLabel(portalPulsePublicRange)}${publicPulse.generated_at ? ` · ${formatPortalTime(publicPulse.generated_at)}` : ""}`;
        publicNode.innerHTML = window.PortalI18n.html`
      <div class="pulse-panel">
        <div class="pulse-toolbar">
          ${pulseTabsHtml("public", portalPulsePublicRange)}
          <div class="pulse-toolbar-meta">
            ${homeStatusHtml(periodMeta, "neutral", "pulse-period-status")}
          </div>
        </div>
        ${hasSummary ? window.PortalI18n.html`
          <div class="pulse-brief">
            <span class="pulse-brief-label">Overview</span>
            <p>${esc(publicPulse.global_summary || "")}</p>
          </div>
        ` : window.PortalI18n.html`<div class="pulse-empty">还没有生成这个时间段的 Pulse 摘要。</div>`}
        ${renderPulseProjects(publicPulse)}
        ${renderPulsePublications(portalPulseData)}
      </div>
    `;
      }
      if (mineNode) {
        mineNode.innerHTML = window.PortalI18n.html`
      <div class="pulse-panel">
        ${pulseTabsHtml("personal", portalPulsePersonalRange)}
        ${minePulse.summary ? window.PortalI18n.html`<div class="pulse-summary">${esc(minePulse.summary)}</div>` : ""}
        ${renderPulseMine(minePulse)}
      </div>
    `;
      }
      attachPulseHandlers();
    }

    async function loadPortalPulse() {
      const publicNode = document.querySelector('[data-summary-value="urgent-work"]');
      const mineNode = document.querySelector('[data-summary-value="contribution-window"]');
      try {
        // GetPulse goes through the permission system, which decides who may read Pulse.
        const userId = await window.PortalIdentity.userId();
        const result = await window.PortalInstructionClient.execute("GetPulse", { user_id: userId });
        renderPortalPulse(result.pulse);
      } catch (err) {
        if (publicNode) publicNode.innerHTML = window.PortalI18n.html`<div class="pulse-error">${esc(window.PortalUI.errorMessage(window.PortalI18n.t("读取 Pulse"), err))}</div>`;
        if (mineNode) mineNode.innerHTML = window.PortalI18n.html`<div class="pulse-error">个人 Pulse 读取失败。</div>`;
      }
    }

    async function publishPortalPulseItem(payload) {
      try {
        const published = payload?.published === true;
        const result = await window.PortalInstructionClient.execute(
          "PublishPulseItem",
          { source_period: payload.source_period, source_key: payload.source_key, item_id: payload.item_id, published },
          { idempotencyKey: `pulse:${globalThis.PortalUIPrimitives.randomUUID()}` },
        );
        portalPulseData = result.pulse;
        renderPortalPulse(portalPulseData);
        window.PortalUI.toast(
          published ? window.PortalI18n.t("Pulse 已发布") : window.PortalI18n.t("Pulse 已撤回"),
          "success",
        );
      } catch (err) {
        window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("发布 Pulse"), err), "error");
      }
    }

    let portalPulseInitialLoadPromise = null;

    function loadPortalPulseOnce() {
      if (!portalPulseInitialLoadPromise) portalPulseInitialLoadPromise = loadPortalPulse();
      return portalPulseInitialLoadPromise;
    }

    return Object.freeze({
      loadDailyNews, loadPortalPulseOnce,
    });
  }

  window.PortalHomePulse = Object.freeze({ createPulse, dependencies: DEPENDENCIES });
})();
