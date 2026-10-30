(() => {
  if (window.PortalHomeActivity) return;

  // The activity runboard in Home's Agent Status panel: each account's running and
  // queued Residents as creatures, and the menu that opens or clears them.
  const DEPENDENCIES = Object.freeze([
    "currentAgentStatusData", "ensureActiveAgentStatusRefresh", "esc", "executionAccounts", "listen",
    "residentNavigationUrl",
  ]);

  function createActivity(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHomeActivity is missing: ${missing.join(", ")}`);
    const {
      currentAgentStatusData, ensureActiveAgentStatusRefresh, esc, executionAccounts, listen,
      residentNavigationUrl,
    } = dependencies;

    function agentStatusRunHref(run) {
      if (!run || run.type !== "resident" || !run.resident_id) return "";
      const explicitUrl = String(run.url || "").trim();
      return residentNavigationUrl({
        residentId: run.resident_id,
        lineageId: run.lineage_id,
        url: explicitUrl,
        openChat: true,
      });
    }

    function agentStatusCreature(index, activityState, label, run = null) {
      const residentSnapshot = run?.type === "resident" && run.resident_id
        ? window.PortalResidentProtocol.stateSnapshot(run.resident_id)
        : null;
      const stateLabel = { running: window.PortalI18n.t("运行中"), queued: window.PortalI18n.t("排队"), unread: window.PortalI18n.t("未读回复") }[activityState] || activityState;
      if (residentSnapshot) {
        // This icon represents one message. Resident work_state is aggregate state and
        // must not overwrite a queued message while another message is running.
        label = `${label} · ${stateLabel}${residentSnapshot.state?.intake_state === "closed" ? " · 接单关闭" : ""}`;
        const queuedCount = residentSnapshot.state?.queued_count;
        const queueLabel = residentSnapshot.ready && Number.isInteger(queuedCount) && queuedCount >= 0
          ? window.PortalI18n.text`Mailbox 排队：${queuedCount} 条`
          : window.PortalI18n.t("Mailbox 排队：待更新");
        label = `${label} · ${queueLabel}`;
      }
      const avatar = window.PortalResidentAvatar.presentation(run);
      const delay = `${index * -0.18}s`;
      const href = agentStatusRunHref(run);
      const rarity = avatar.rarity;
      const accessibleLabel = rarity ? `${label} · ${rarity}` : label;
      const rarityClass = rarity === "SSSR"
        ? " rarity-sssr"
        : rarity === "SSR" ? " rarity-ssr" : "";
      const attrs = `class="agent-status-creature ${avatar.className} ${esc(activityState)}${rarityClass}" style="${esc(avatar.style)}; --creature-delay:${delay}" title="${esc(accessibleLabel)}" data-sprite-rarity="${esc(rarity)}"`;
      const content = rarity === "SSSR" && activityState === "running"
        ? window.PortalI18n.markup('<span class="agent-status-creature-sssr-glow outer"><span class="agent-status-creature-sssr-glow-source"></span></span><span class="agent-status-creature-sssr-glow inner"><span class="agent-status-creature-sssr-glow-source"></span></span>')
        : "";
      return href
        ? window.PortalI18n.html`<a ${attrs} href="${esc(href)}" aria-label="${esc(accessibleLabel)}" data-agent-status-resident="${esc(run.resident_id)}" data-agent-status-lineage="${esc(run.lineage_id || "")}">${content}</a>`
        : window.PortalI18n.html`<span ${attrs} role="img" aria-label="${esc(accessibleLabel)}">${content}</span>`;
    }

    function agentStatusCreatureGroup(count, state, offset, runs = []) {
      const total = Math.max(0, Number(count || 0));
      const visible = Math.min(total, runs.length, 18);
      const icons = [];
      for (let i = 0; i < visible; i += 1) {
        const run = runs[i];
        const fullTitle = String(run?.title || "").trim();
        const label = String(run?.session_title || fullTitle || "Agent").trim();
        icons.push(agentStatusCreature(offset + i, state, label, run));
      }
      if (total > visible) icons.push(window.PortalI18n.html`<span class="agent-status-creature-more">+${total - visible}</span>`);
      return icons.join("");
    }

    function agentStatusAccountDivider(leftAccountId, rightAccountId) {
      const left = String(leftAccountId || "all");
      const right = String(rightAccountId || "all");
      return window.PortalI18n.html`
    <span class="agent-status-account-divider" role="separator" aria-label="${esc(`${left} and ${right} account divider`)}" data-account-divider-from="${esc(left)}" data-account-divider-to="${esc(right)}">
      <span class="agent-status-account-divider-line" aria-hidden="true"></span>
    </span>
  `;
    }

    function agentStatusRunboard(active) {
      const running = Math.max(0, Number(active?.running || 0));
      const queued = Math.max(0, Number(active?.queued || 0));
      const maxConcurrency = Math.max(1, Number(active?.max_concurrency || 8));
      // The activity owner selects queue entries before limiting the response.
      const sourceGroups = Array.isArray(active?.account_groups) && active.account_groups.length
        ? active.account_groups
        : [{
            account_id: "",
            running,
            queued,
            queued_display_count: active?.queued_display_count,
            running_runs: Array.isArray(active?.running_runs) ? active.running_runs : [],
            queued_runs: Array.isArray(active?.queued_runs) ? active.queued_runs : [],
          }];
      const activeResidentIds = new Set(sourceGroups.flatMap(group => [
        ...(Array.isArray(group?.running_runs) ? group.running_runs : []),
        ...(Array.isArray(group?.queued_runs) ? group.queued_runs : []),
      ]).filter(run => run?.type === "resident" && run.resident_id).map(run => String(run.resident_id)));
      const unreadRuns = (window.PortalConversationStore?.entries?.() || [])
        .filter(entry => (
          entry?.unread === true
          && entry?.resident_id
          && !activeResidentIds.has(String(entry.resident_id))
          && !["queued", "running", "stopping"].includes(String(entry.status || "").toLowerCase())
        ))
        .map(entry => ({
          ...entry,
          type: "resident",
          title: String(entry.title || entry.resident_title || entry.resident_id),
          session_title: String(entry.title || entry.resident_title || entry.resident_id),
          status: "unread",
        }));
      const groupsByAccount = new Map(sourceGroups.map(group => [
        String(group?.account_id || ""), { ...group, unread_runs: [] },
      ]));
      for (const run of unreadRuns) {
        const accountId = String(run.account_id || "other");
        if (!groupsByAccount.has(accountId)) groupsByAccount.set(accountId, { account_id: accountId, unread_runs: [] });
        groupsByAccount.get(accountId).unread_runs.push(run);
      }
      const catalogOrder = new Map(executionAccounts(currentAgentStatusData()).map((account, index) => [account.account_id, index]));
      const groups = [...groupsByAccount.values()].sort((left, right) =>
        (catalogOrder.get(left.account_id) ?? Number.MAX_SAFE_INTEGER)
          - (catalogOrder.get(right.account_id) ?? Number.MAX_SAFE_INTEGER));
      const renderedGroups = [];
      let offset = 0;
      for (const group of groups) {
        const accountId = String(group?.account_id || "");
        const groupRunning = Math.max(0, Number(group?.running || 0));
        const groupQueued = Math.max(0, Number(group?.queued_display_count || 0));
        const runningRuns = Array.isArray(group?.running_runs) ? group.running_runs : [];
        const queuedRuns = Array.isArray(group?.queued_runs) ? group.queued_runs : [];
        const icons = [
          agentStatusCreatureGroup(groupRunning, "running", offset, runningRuns),
          agentStatusCreatureGroup(groupQueued, "queued", offset + groupRunning, queuedRuns),
          agentStatusCreatureGroup(group.unread_runs.length, "unread", offset + groupRunning + groupQueued, group.unread_runs),
        ].filter(Boolean).join("");
        if (!icons) continue;
        renderedGroups.push({
          accountId: accountId || "all",
          html: window.PortalI18n.html`
        <span class="agent-status-account-group" data-agent-account-group="${esc(accountId || "all")}">
          <span class="agent-status-account-icons">${icons}</span>
          ${accountId ? window.PortalI18n.html`<span class="agent-status-account-label">${esc(accountId)}</span>` : ""}
        </span>
      `,
        });
        offset += groupRunning + groupQueued + group.unread_runs.length;
      }
      if (!renderedGroups.length) return "";
      const icons = renderedGroups.map((group, index) => {
        if (!index) return group.html;
        return `${agentStatusAccountDivider(renderedGroups[index - 1].accountId, group.accountId)}${group.html}`;
      }).join("");
      const summary = `${running} running, ${queued} queued, ${unreadRuns.length} unread, concurrency limit ${maxConcurrency}`;
      return window.PortalI18n.html`
    <div class="agent-status-creature-strip" aria-label="${esc(summary)}" title="${esc(summary)}">
      ${icons}
    </div>
  `;
    }

    function focusedResidentIcon(root) {
      const focused = document.activeElement;
      return root?.contains(focused) ? focused.getAttribute("data-agent-status-resident") : "";
    }

    function restoreResidentIconFocus(root, residentId) {
      if (!residentId) return;
      root?.querySelector(`[data-agent-status-resident="${CSS.escape(residentId)}"]`)
        ?.focus({ preventScroll: true });
    }

    const agentStatusQueueClears = new Set();

    function bindAgentStatusResidentMenu(signal) {
      const menu = document.createElement("div");
      menu.className = "portalDetailSurface agent-status-resident-menu";
      menu.setAttribute("role", "menu");
      menu.setAttribute("aria-label", window.PortalI18n.t("Resident 排队消息操作"));
      menu.hidden = true;
      const title = document.createElement("strong");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "portalButton";
      button.setAttribute("role", "menuitem");
      const note = document.createElement("small");
      note.textContent = window.PortalI18n.t("不打断当前运行，不改变 Open / Closed 状态");
      menu.append(title, button, note);
      document.body.appendChild(menu);
      let selected = null;
      let trigger = null;
      const popover = window.PortalOverlay.bindFloatingPopover({
        panel: menu,
        placement: "bottom",
        align: "start",
        closeOnScroll: true,
        onClose: event => {
          if (event?.key === "Escape" && trigger?.isConnected) trigger.focus({ preventScroll: true });
        },
      });
      const syncButton = () => {
        const busy = selected && agentStatusQueueClears.has(selected.resident_id);
        button.disabled = !selected?.lineage_id || busy;
        button.textContent = busy ? window.PortalI18n.t("正在清除…") : window.PortalI18n.t("清除所有排队消息");
        button.setAttribute("aria-busy", String(Boolean(busy)));
      };
      listen(document, "portal:resident-queue-clear-settled", syncButton, signal);
      const open = event => {
        const link = event.target instanceof Element
          ? event.target.closest("[data-agent-status-resident]") : null;
        if (!link) return;
        event.preventDefault();
        selected = {
          resident_id: link.getAttribute("data-agent-status-resident"),
          lineage_id: link.getAttribute("data-agent-status-lineage"),
          title: link.getAttribute("aria-label") || "Resident",
        };
        trigger = link;
        title.textContent = selected.title;
        syncButton();
        const bounds = link.getBoundingClientRect();
        popover.open({
          x: event.clientX || bounds.left,
          y: event.clientY || bounds.bottom,
        });
        button.focus({ preventScroll: true });
      };
      listen(document, "contextmenu", open, signal);
      listen(document, "keydown", event => {
        if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) open(event);
        if (event.key === "Tab" && popover.isOpen()) popover.close(event);
      }, signal);
      listen(button, "click", async () => {
        const target = selected;
        if (!target?.lineage_id || agentStatusQueueClears.has(target.resident_id)) return;
        if (!window.confirm(window.PortalI18n.text`清除「${target.title}」当前所有排队消息？\n正在执行的消息不受影响，接单状态保持不变。`)) return;
        agentStatusQueueClears.add(target.resident_id);
        syncButton();
        try {
          const result = await window.PortalResidentProtocol.clearMailbox({
            resident_id: target.resident_id,
            lineage_id: target.lineage_id,
          });
          if (signal.aborted) return;
          if (selected?.resident_id === target.resident_id) popover.close();
          window.PortalChatWorkspace.noticeQueueCleared(result);
          try {
            await ensureActiveAgentStatusRefresh().refresh({ force: true });
          } catch (_error) {
            if (!signal.aborted) window.PortalUI.toast(window.PortalI18n.t("清理已完成，但状态刷新失败，请手动刷新。"), "error");
          }
        } catch (error) {
          if (!signal.aborted) window.PortalUI.toast(window.PortalUI.errorMessage(window.PortalI18n.t("清除排队消息"), error), "error");
        } finally {
          agentStatusQueueClears.delete(target.resident_id);
          document.dispatchEvent(new Event("portal:resident-queue-clear-settled"));
        }
      }, signal);
      return () => {
        popover.destroy();
        menu.remove();
      };
    }

    return Object.freeze({
      agentStatusRunboard, focusedResidentIcon, restoreResidentIconFocus, bindAgentStatusResidentMenu,
    });
  }

  window.PortalHomeActivity = Object.freeze({ createActivity, dependencies: DEPENDENCIES });
})();
