(() => {
  "use strict";

  if (window.PortalResidentMenuJournal) return;

  // The Work Journal tab: the Resident's ownership and activity summary.

  const DEPENDENCIES = Object.freeze([
    "$id", "esc", "menuState",
  ]);

  function createJournal(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalResidentMenuJournal is missing: ${missing.join(", ")}`);
    const {
      $id, esc, menuState,
    } = dependencies;

    function nodeJournalSection(title, values, empty = "") {
      const items = (Array.isArray(values) ? values : [values]).filter(value => typeof value === "string" && value.trim());
      if (!items.length && !empty) return "";
      return window.PortalI18n.html`<section class="residentMenuJournalSection"><h4>${esc(title)}</h4>${items.length
        ? window.PortalI18n.html`<ul>${items.map(value => window.PortalI18n.html`<li>${esc(value)}</li>`).join("")}</ul>`
        : window.PortalI18n.html`<p class="residentMenuJournalMeta">${esc(empty)}</p>`}</section>`;
    }

    async function loadNodeJournal() {
      const context = menuState.nodeMenuContext;
      if (!context || context.journalBusy || context.journalLoaded) return;
      context.journalBusy = true;
      const button = $id("nodeJournalRetry"); button.disabled = true; button.hidden = true;
      $id("nodeJournalRows").textContent = window.PortalI18n.t("正在读取职责与活动摘要…");
      try {
        const result = await window.PortalInstructionClient.execute("GetResidentSummaries", {resident_ids: [context.residentId]});
        if (menuState.nodeMenuContext !== context) return;
        const entry = (result.summaries || []).find(item => item.resident_id === context.residentId);
        const summary = entry?.summary;
        const rows = $id("nodeJournalRows");
        if (!summary || typeof summary !== "object") rows.textContent = window.PortalI18n.t("暂无可读取的职责与活动摘要。");
        else {
          const updated = entry.summary_updated_at ? new Date(entry.summary_updated_at) : null;
          const timestamp = updated && !Number.isNaN(updated.getTime()) ? updated.toLocaleString(window.PortalI18n.locale(), {hour12: false}) : "";
          rows.innerHTML = window.PortalI18n.html`<p class="residentMenuJournalMeta">摘要服务维护${timestamp ? window.PortalI18n.text` · 更新于 ${esc(timestamp)}` : ""}${entry.summary_stale ? window.PortalI18n.t(" · 有新活动，摘要待更新") : ""}</p>`
            + nodeJournalSection("职责范围", summary.primary_ownership, "摘要尚未记录职责范围。")
            + nodeJournalSection("当前重点", summary.current_focus)
            + nodeJournalSection("近期成果", summary.recent_results, "暂无近期成果摘要。")
            + nodeJournalSection("进行中的工作", summary.active_threads)
            + nodeJournalSection("近期变化", summary.branch_delta);
        }
        context.journalLoaded = true;
      } catch (error) {
        if (menuState.nodeMenuContext !== context) return;
        $id("nodeJournalRows").textContent = window.PortalI18n.text`职责与活动摘要读取失败：${error.message}`;
        button.hidden = false;
      } finally { if (menuState.nodeMenuContext === context) { context.journalBusy = false; button.disabled = false; } }
    }

    return {
      loadNodeJournal,
    };
  }

  window.PortalResidentMenuJournal = Object.freeze({ createJournal, dependencies: DEPENDENCIES });
})();
