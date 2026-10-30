(() => {
  if (window.PortalChatWorkspaceFamilyStatus) return;

  // The Family status view, opened from the Chat and from a node's menu: each
  // member's work state, execution account and weekly quota, a session move
  // in progress, and the controls for a member's runtime.
  const DEPENDENCIES = Object.freeze([
    "escapeHtml", "footerTime", "sessionRestartActive",
  ]);

  function createFamilyStatus(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceFamilyStatus is missing: ${missing.join(", ")}`);
    const {
      escapeHtml, footerTime, sessionRestartActive,
    } = dependencies;

    function memberMixHtml(id, title, policy, busy = false) {
      const solo = policy?.solo_member_id || '';
      const muted = !solo && (policy?.muted_member_ids || []).includes(id);
      return `<span class="familyMemberMix" aria-busy="${busy}">${[
        ['mute', 'M', muted, 'Mute：排除路由并隐藏对话'],
        ['solo', 'S', solo === id, 'Solo：直连并只显示此成员'],
      ].map(([mode, label, active, hint]) => `<button type="button" class="familyMemberMixButton" data-family-mix="${mode}" data-member="${escapeHtml(id)}" aria-label="${escapeHtml(`${title || id} · ${hint}`)}" title="${escapeHtml(hint)}" aria-pressed="${active}" ${!policy || busy ? 'disabled' : ''}>${label}</button>`).join('')}</span>`;
    }

    // One in-flight write and one confirmed policy per Family across all mounts.
    // Snapshot notifications trigger a fresh read; their possibly older policy
    // never replaces the result of a just-completed write.
    const mixStates = new Map();
    function acquireMixState(residentId, render) {
      let state = mixStates.get(residentId);
      if (!state) {
        const listeners = new Set();
        let revision = 0, reading = null, unsubscribe = null, refreshPending = false;
        const notify = () => listeners.forEach(listener => listener());
        state = {
          policy: null, busy: false,
          refresh() {
            if (state.busy) { refreshPending = true; return Promise.resolve(); }
            if (reading) return reading;
            const started = ++revision;
            const request = window.PortalInstructionClient.execute('GetFamilyChatPreferences', {resident_id: residentId})
              .then(value => {
                if (started !== revision) return;
                state.policy = value; notify();
              }).catch(error => {
                if (started !== revision) return;
                state.policy = null; notify();
                window.PortalUI?.toast?.(error.message, 'error');
              }).finally(() => { if (reading === request) reading = null; });
            reading = request;
            return request;
          },
          async change(button) {
            if (state.busy || !state.policy) return;
            const args = {resident_id: residentId, member_id: button.dataset.member,
              control: button.dataset.familyMix, enabled: button.getAttribute('aria-pressed') !== 'true'};
            ++revision; reading = null;
            state.busy = true; notify();
            try {
              state.policy = await window.PortalInstructionClient.execute('UpdateFamilyChatPreferences', args);
              notify();
              window.PortalResidentProjection?.invalidate?.(residentId);
              await window.PortalResidentProtocol.loadSnapshot(residentId, {force: true});
            } finally {
              state.busy = false; notify();
              if (refreshPending) { refreshPending = false; void state.refresh(); }
            }
          },
          subscribe(listener) {
            listeners.add(listener);
            return () => {
              listeners.delete(listener);
              if (listeners.size) return;
              ++revision; unsubscribe?.(); mixStates.delete(residentId);
            };
          },
        };
        mixStates.set(residentId, state);
        // Delay the initial read until the first mount has assigned its handle.
        queueMicrotask(() => {
          if (!listeners.size) return;
          unsubscribe = window.PortalResidentProjection?.subscribe(residentId, () => { void state.refresh(); });
          void state.refresh();
        });
      }
      return {state, release: state.subscribe(render)};
    }

    function renderMemberMix(root, id, title, state) {
      const template = document.createElement('template');
      template.innerHTML = memberMixHtml(id, title, state.policy, state.busy);
      const next = template.content.firstElementChild;
      const current = root.querySelector('.familyMemberMix');
      if (!current || current.querySelector('button')?.dataset.member !== id) {
        root.replaceChildren(next);
        return;
      }
      // Keep the actual buttons (and focus/hover) throughout save and refresh.
      for (const [index, button] of [...current.querySelectorAll('button')].entries()) {
        const updated = next.children[index];
        for (const attribute of [...button.attributes]) if (!updated.hasAttribute(attribute.name)) button.removeAttribute(attribute.name);
        for (const attribute of [...updated.attributes]) {
          if (button.getAttribute(attribute.name) !== attribute.value) button.setAttribute(attribute.name, attribute.value);
        }
      }
      current.setAttribute('aria-busy', String(state.busy));
    }

    // Message attribution and the member table use the same preference owner.
    function createFamilyReplyControls(residentId, root) {
      let family = null, disposed = false, policySignature = '';
      const mix = acquireMixState(residentId, render);
      function render() {
        if (disposed) return;
        const members = new Map((family?.members || []).map(member => [member.resident_id, member]));
        for (const slot of root.querySelectorAll('[data-family-reply-controls]')) {
          const member = JSON.parse(slot.dataset.familyReplyChain).map(id => members.get(id)).find(Boolean);
          if (member) renderMemberMix(slot, member.resident_id, member.title, mix.state);
          slot.hidden = !member || !mix.state.policy;
        }
      }
      async function onClick(event) {
        const button = event.target.closest('[data-family-reply-controls] [data-family-mix]');
        if (!button || button.disabled || mix.state.busy) return;
        event.preventDefault();
        const slot = button.closest('[data-family-reply-controls]');
        try {
          await mix.state.change(button);
        } catch (error) { window.PortalUI?.toast?.(error.message, 'error'); }
        finally {
          if (!disposed && slot.isConnected) slot.querySelector(`[data-family-mix="${button.dataset.familyMix}"]`)?.focus({preventScroll: true});
        }
      }
      root.addEventListener('click', onClick);
      return {
        update(value) {
          family = value;
          const next = JSON.stringify(value?.chat_preferences);
          if (next !== policySignature) { policySignature = next; void mix.state.refresh(); }
          render();
        },
        render,
        destroy() { disposed = true; mix.release(); root.removeEventListener('click', onClick); },
      };
    }

    // A member's own Chat controls its participation in its parent Family.
    // The facade retains its existing Menu/status-panel controls only.
    function createFamilyMemberChatControls(member, root) {
      let mix = null, disposed = false;
      function render() {
        if (disposed) return;
        root.hidden = !mix?.state.policy;
        if (mix) renderMemberMix(root, member.resident_id, member.title, mix.state);
      }
      async function resolveFamily() {
        const {lineage} = await window.PortalInstructionClient.execute('GetLineage', {lineage_id: member.lineage_id});
        if (disposed) return;
        const family = (lineage.function_blocks || []).find(block =>
          block.id === member.family.family_id && (block.family?.members || []).includes(member.resident_id))?.family;
        if (!family?.facade) return;
        mix = acquireMixState(family.facade, render);
        render();
      }
      async function onClick(event) {
        const button = event.target.closest('[data-family-mix]');
        if (!button || button.disabled || !mix || mix.state.busy) return;
        event.preventDefault();
        try {
          await mix.state.change(button);
        } catch (error) { window.PortalUI?.toast?.(error.message, 'error'); }
        finally {
          if (!disposed) root.querySelector(`[data-family-mix="${button.dataset.familyMix}"]`)?.focus({preventScroll: true});
        }
      }
      root.addEventListener('click', onClick);
      void resolveFamily().catch(error => { if (!disposed) window.PortalUI?.toast?.(error.message, 'error'); });
      return {
        update(value) { member = value; render(); },
        destroy() { disposed = true; mix?.release(); root.removeEventListener('click', onClick); root.replaceChildren(); root.hidden = true; },
      };
    }

    // The same Family view is opened from Chat and from a node's menu.
    function createFamilyStatusView(residentId, anchor, onSummary = null, getFamily = null, container = null) {
      const root = document.createElement("section");
      root.className = `familyStatusPanel portalHatgOverlayComponent${container ? " is-inline" : ""}`;
      root.hidden = true;
      root.setAttribute("role", container ? "region" : "dialog");
      root.setAttribute("aria-label", window.PortalI18n.t("Family 状态"));
      (container || document.body).append(root);
      const rows = new Map();
      let mix;
      let accounts = [], family = {}, quotaError = "", disposed = false, loading = false, reloadRequested = false;

      const popoverOptions = { trigger: anchor, panel: root, portal: true,
        placement: "bottom-start", ariaHaspopup: "dialog", closeOnOutside: true, closeOnEscape: true,
        onClose: () => {
          rows.forEach(row => row.stops?.forEach(stop => stop?.()));
          rows.clear();
          if (!onSummary) cleanup();
        } };
      const popover = container ? null : window.PortalOverlay.bindPopover(popoverOptions);
      const isOpen = () => container ? root.isConnected && !root.closest("[hidden]") : popover.isOpen();
      const weekly = account => {
        const q = account?.quota_reserve || {};
        if (q.fresh !== true || (Number.isFinite(q.valid_until) && q.valid_until * 1000 <= Date.now())) return null;
        const ws = (q.windows || []).filter(w => w.window === "seven_day" || w.window_duration_mins === 10080);
        return ws.length && ws.every(w => typeof w.remaining_percent === "number" && Number.isFinite(w.remaining_percent) && w.remaining_percent >= 0 && w.remaining_percent <= 100)
          ? Math.min(...ws.map(w => w.remaining_percent)) : null;
      };
      const accountOf = row => {
        const d = row.detail || {}, bound = d.execution_account || {};
        return accounts.find(a => a.account_id === bound.account_id
          || (a.provider === (d.provider_session?.provider || bound.provider || d.resident?.backend)
            && a.backend_route_id === (bound.backend_route_id || d.provider_session?.account_id))) || {};
      };
      const rowState = row => window.PortalResidentProtocol.stateView(row.snapshot?.state);
      const working = row => rowState(row).working;
      const failed = row => row.snapshot?.input_states?.some(input => input.state === "failed");
      const migration = row => sessionRestartActive(row.restart) ? row.restart : null;
      const label = row => migration(row)?.phase === "waiting_current_execution" ? window.PortalI18n.t("等待本轮后迁移")
        : migration(row) ? window.PortalI18n.t("迁移中") : failed(row) && !working(row) ? window.PortalI18n.t("失败")
        : ({ running: window.PortalI18n.t("工作中"), queued: window.PortalI18n.t("等待执行"), stopping: window.PortalI18n.t("停止中"), idle: window.PortalI18n.t("空闲") }[row.snapshot ? rowState(row).display_work_state : ""] || window.PortalI18n.t("加载中"));
      const runtimeKey = row => JSON.stringify([row.detail?.resident?.backend, row.detail?.execution_account?.account_id,
        row.detail?.provider_session?.provider, row.detail?.provider_session?.account_id]);
      const canEditRuntime = row => row.runtime?.state().canEdit === true;
      const runtimeLabel = row => {
        const settings = row.snapshot?.runtime_settings || row.detail?.runtime_settings || {};
        const catalog = row.catalogKey === runtimeKey(row) ? row.catalog : null;
        const fields = catalog?.runtime_capabilities?.settings
          || row.detail?.backend_capabilities?.runtime_capabilities?.settings || [];
        return [settings.model || catalog?.default_model || window.PortalI18n.t("Provider default"),
          settings.reasoning_effort || window.PortalI18n.t("默认强度"),
          fields.includes("service_tier") ? catalog && !(catalog.service_tiers_by_model?.[settings.model || catalog.default_model] || []).length
            ? window.PortalI18n.t("Fast 不支持") : settings.service_tier === "priority" ? window.PortalI18n.t("Fast 开") : window.PortalI18n.t("Fast 关") : ""].filter(Boolean).join(" · ");
      };
      const render = () => {
        if (disposed) return;
        const chatPolicy = mix.state.policy, mixBusy = mix.state.busy;
        const all = [...rows.values()];
        const running = all.filter(working).length;
        const queued = all.reduce((n, row) => n + Number(row.snapshot?.state?.queued_count || 0), 0);
        const attention = all.filter(row => failed(row) || migration(row) || (weekly(accountOf(row)) !== null
          && (row.role === "steward" ? weekly(accountOf(row)) <= 10 : weekly(accountOf(row)) < 10))).length;
        onSummary?.(`运行 ${running} · 排队 ${queued} · 需关注 ${attention}`);
        const rowHtml = row => {
          const id = row.resident_id, d = row.detail || {}, snap = row.snapshot || {}, account = accountOf(row);
          const remain = weekly(account), q = account.quota_reserve || {}, backend = d.provider_session?.provider || d.resident?.backend || "—";
          const accountLabel = account.label || d.execution_account?.label || d.execution_account?.account_id || window.PortalI18n.t("自动");
          const queue = Number(snap.state?.queued_count || 0), closed = snap.state?.intake_state === "closed" || !!migration(row);
          const gate = window.PortalResidentProtocol.executionGateView(snap.state);
          const settings = d.runtime_settings || {}, error = row.error || snap.input_states?.find(input => input.state === "failed")?.error?.message || row.restart?.error || "";
          const task = (snap.messages || []).find(m => m.message_id === snap.state?.active_message_id) || (snap.messages || []).find(m => m.role === "user");
          const taskText = (task?.content_parts || []).filter(p => p.type === "text").map(p => p.text).join("\n");
          const week = (q.windows || []).find(w => w.window === "seven_day" || w.window_duration_mins === 10080);
          const reset = week?.resets_at ? footerTime(typeof week.resets_at === "number" ? new Date(week.resets_at * 1000).toISOString() : week.resets_at) : "未知";
          const low = remain !== null && (row.role === "steward" ? remain <= 10 : remain < 10);
          const note = low ? row.role === "steward" ? "额度不足 · 平台负责迁移" : row.role === "member" ? "低于 10% · 新请求须 steward 评估" : "低额度" : "";
          const mix = row.role === 'member' ? memberMixHtml(id, row.title, chatPolicy, mixBusy) : '';
          const href = `/lineage?${new URLSearchParams({ resident: id, lineage: family.lineage_id || "", open_chat: "1" })}`;
          return window.PortalI18n.html`<tr data-family-status-row="${escapeHtml(id)}"><td data-label="节点"><div class="familyMemberIdentity"><a class="messageMetaLink familyStatusName" href="${escapeHtml(href)}" title="${escapeHtml(row.title || id)}">${escapeHtml(row.title || id)}</a><button type="button" class="portalButton is-quiet familyStatusDetailsToggle" data-family-status-detail="${escapeHtml(id)}" title="${escapeHtml(label(row))} · 展开工作详情" aria-label="${escapeHtml(row.title || id)} · 工作详情" aria-expanded="${row.expanded ? "true" : "false"}">···</button></div></td><td data-label="backend"><button type="button" class="portalButton is-quiet familyStatusAccount" data-family-status-settings="${escapeHtml(id)}" aria-haspopup="dialog" title="${escapeHtml(window.PortalI18n.text`${row.title || id} · 成员运行设置`)}" ${!canEditRuntime(row) && row.runtime?.state().canRestart !== true || row.settingsLoading || migration(row) ? "disabled" : ""}><span><span>${escapeHtml(`${backend} · ${accountLabel}`)}</span>${canEditRuntime(row) || d.runtime_settings ? window.PortalI18n.html`<span class="familyStatusRuntime"><span>${escapeHtml(row.settingsLoading ? window.PortalI18n.t("读取设置…") : runtimeLabel(row))}</span></span>` : ""}</span><span aria-hidden="true">▾</span></button></td><td data-label="周剩余" ${low ? 'class="familyStatusAttention"' : ""} title="${escapeHtml(note || (remain === null ? q.fresh === false ? window.PortalI18n.t("观测过期，待刷新") : window.PortalI18n.t("无有效周额度观测") : window.PortalI18n.text`周额度重置：${reset}`))}"><span class="familyStatusQuotaValue">${remain === null ? q.currency && q.fresh === true ? escapeHtml(`${q.remaining_balance} ${q.currency}`) : window.PortalI18n.t("未知") : `${remain.toFixed(0)}%`}${low ? window.PortalI18n.markup('<span class="familyStatusQuotaWarning" aria-label="低额度">!</span>') : ""}</span>${remain !== null ? window.PortalI18n.html`<span class="familyStatusQuotaTrack" aria-hidden="true"><span style="width:${remain}%"></span></span>` : ""}</td><td data-label="工作状态"><button class="portalButton is-compact familyStatusIntake" role="switch" aria-checked="${closed ? "false" : "true"}" aria-label="${escapeHtml(window.PortalI18n.text`${row.title || id}接单`)}" aria-description="${escapeHtml(window.PortalI18n.t("关闭接单只暂停后续任务，当前工作继续"))}" data-portal-tooltip-opt-out="1" data-family-status-intake="${escapeHtml(id)}" ${d.allows?.OpenIntake !== true || migration(row) || row.intakeBusy ? "disabled" : ""}><span aria-hidden="true" class="familyIntakeTrack"></span><span>${closed ? "closed" : "open"}</span></button></td><td data-label="门面展示">${mix || "—"}</td></tr>${row.expanded ? window.PortalI18n.html`<tr><td colspan="5" class="familyStatusDetails"><dl>${gate.text ? `<dt>${escapeHtml(window.PortalI18n.t("执行保护"))}</dt><dd>${escapeHtml(gate.text)}</dd>` : ""}<dt>工作状态</dt><dd>${escapeHtml(label(row))}${queue ? ` · 排队 ${queue}` : ""}</dd><dt>职责</dt><dd>${escapeHtml(d.resident?.description || window.PortalI18n.t("未填写"))}</dd><dt>模型</dt><dd>${escapeHtml([settings.model, settings.reasoning_effort].filter(Boolean).join(" · ") || window.PortalI18n.t("默认"))}</dd><dt>当前任务</dt><dd>${escapeHtml(taskText.slice(0, 800) || window.PortalI18n.t("无当前任务"))}</dd><dt>开始处理</dt><dd>${escapeHtml(footerTime(task?.activated_at || task?.created_at) || "—")}</dd><dt>最近活动</dt><dd>${escapeHtml(footerTime(snap.messages?.at(-1)?.created_at) || "—")}</dd><dt>周额度重置</dt><dd>${escapeHtml(reset)}</dd>${note ? window.PortalI18n.html`<dt>额度提示</dt><dd>${escapeHtml(note)}</dd>` : ""}${error ? window.PortalI18n.html`<dt>错误</dt><dd>${escapeHtml(typeof error === "string" ? error : JSON.stringify(error))}</dd>` : ""}</dl><a class="messageMetaLink" href="/lineage?${escapeHtml(new URLSearchParams({ lineage: family.lineage_id || "", resident: id }).toString())}">在画布定位</a></td></tr>` : ""}`;
        };
        const missingSolo = chatPolicy?.solo_member_id && !rows.has(chatPolicy.solo_member_id);
        const soloNotice = missingSolo ? `<p class="paneNote">Solo 成员已离开 <button type="button" class="portalButton is-compact" data-family-mix="solo" data-member="${escapeHtml(chatPolicy.solo_member_id)}" aria-pressed="true" ${mixBusy ? 'disabled' : ''}>取消 Solo</button></p>` : '';
        const table = values => window.PortalI18n.html`<table><thead><tr><th scope="col">节点</th><th scope="col">backend</th><th scope="col">周剩余</th><th scope="col">工作状态</th><th scope="col">门面展示</th></tr></thead><tbody>${values.map(rowHtml).join("")}</tbody></table>`;
        // Preserve keyboard focus and detail expansion during live refresh.
        const active = document.activeElement;
        const focusKey = active?.closest("[data-family-status-row]")?.dataset.familyStatusRow;
        const mixAction = active?.dataset.familyMix;
        const action = active?.dataset.familyStatusDetail ? "detail" : active?.dataset.familyStatusSettings ? "settings" : active?.dataset.familyStatusIntake ? "intake" : "";
        const scrollTop = root.querySelector(".familyStatusBody")?.scrollTop || 0;
        const scrollLeft = root.querySelector(".familyStatusBody")?.scrollLeft || 0;
        root.setAttribute("aria-label", window.PortalI18n.text`${family.title || "Family"} · 成员状态`);
        const controls = new Map([...root.querySelectorAll('.familyMemberMix')].map(group => [group.querySelector('button').dataset.member, group]));
        const template = document.createElement('template');
        template.innerHTML = window.PortalI18n.html`<div class="familyStatusBody">${soloNotice}${quotaError ? window.PortalI18n.html`<p class="paneNote">额度暂不可用：${escapeHtml(quotaError)}</p>` : ""}${rows.size ? table(all) : window.PortalI18n.html`<p>${loading ? window.PortalI18n.t("正在加载…") : window.PortalI18n.t("暂无成员状态")}</p>`}</div>${rows.size ? window.PortalI18n.html`<p class="familyStatusIntakeNote">关闭接单只暂停后续任务，当前工作继续</p>` : ""}`;
        for (const group of template.content.querySelectorAll('.familyMemberMix')) {
          const id = group.querySelector('button').dataset.member, current = controls.get(id);
          if (!current) continue;
          renderMemberMix(current.parentElement, id, rows.get(id)?.title, mix.state);
          group.replaceWith(current);
        }
        root.replaceChildren(template.content);
        root.querySelector(".familyStatusBody").scrollTop = scrollTop;
        root.querySelector(".familyStatusBody").scrollLeft = scrollLeft;
        if (focusKey && mixAction) root.querySelector(`[data-family-status-row="${CSS.escape(focusKey)}"] [data-family-mix="${CSS.escape(mixAction)}"]`)?.focus({ preventScroll: true });
        if (focusKey && action) root.querySelector(`[data-family-status-${action}="${CSS.escape(focusKey)}"]`)?.focus({ preventScroll: true });
      };
      mix = acquireMixState(residentId, render);
      let quotaRequest = null;
      const loadQuota = () => {
        if (quotaRequest) return quotaRequest;
        // Home owns quota sampling and freshness. Reads here reuse its cached summary.
        quotaRequest = window.PortalAPI.get("/api/agent-status").then(summary => {
          if (disposed) return;
          accounts = summary.accounts || []; quotaError = ""; render();
        }).catch(error => {
          if (disposed) return;
          accounts = []; quotaError = error.message; render();
        }).finally(() => { quotaRequest = null; });
        return quotaRequest;
      };
      const load = async () => {
        loading = true; render();
        void loadQuota();
        try {
          await mix.state.refresh();
          const knownFamily = getFamily?.();
          family = knownFamily || (await window.PortalResidentService.projection({ residentId })).family_facade || {};
          if (disposed || !isOpen()) return;
          const nodes = family.nodes || (family.members || []).map(m => ({ ...m, role: "member" }));
          const liveIds = new Set(nodes.map(node => node.resident_id));
          for (const [id, row] of rows) if (!liveIds.has(id)) {
            row.stops?.forEach(stop => stop?.()); rows.delete(id);
          }
          for (const node of nodes) {
            let row = rows.get(node.resident_id);
            if (!row) {
              row = { ...node, stops: [] }; rows.set(node.resident_id, row);
              row.runtime = window.PortalResidentRuntimeController.bind(node.resident_id, {
                onState: view => {
                  if (disposed || rows.get(node.resident_id) !== row) return;
                  if (view.detail) {
                    row.detail = { ...view.detail, runtime_settings: view.settings };
                    row.snapshot = { ...row.snapshot, runtime_settings: view.settings };
                    row.catalog = view.catalog; row.catalogKey = runtimeKey(row);
                  }
                  row.settingsLoading = view.loading || view.busy;
                  row.restart = view.restart; render();
                },
                onOpen: () => { popoverOptions.closeOnOutside = false; },
                onClose: () => {
                  popoverOptions.closeOnOutside = true;
                  if (!disposed) root.querySelector(`[data-family-status-settings="${CSS.escape(node.resident_id)}"]`)?.focus();
                },
              });
              row.stops.push(() => row.runtime.destroy());
            } else Object.assign(row, node);
          }
          render();
          await Promise.all(nodes.map(async node => {
            const row = rows.get(node.resident_id);
            try {
              row.error = "";
              if (!row.subscription) {
                const subscription = window.PortalResidentProtocol.acquire(node.resident_id, update => {
                  if (!disposed && rows.get(node.resident_id) === row && update.snapshot) { row.snapshot = update.snapshot; render(); }
                }, { historyLimit: 1, consumer: "family_member", observationRoot: root });
                row.subscription = subscription;
                row.stops.push(() => subscription.destroy());
              }
              const [memberDetail, snapshot] = await Promise.all([
                row.runtime.load().then(view => ({ ...view.detail, runtime_settings: view.settings })),
                row.subscription?.load({ historyLimit: 1 }).then(snapshot => {
                  if (!disposed && rows.get(node.resident_id) === row) { row.snapshot = snapshot; render(); }
                  return snapshot;
                }),
              ]);
              if (disposed || rows.get(node.resident_id) !== row) return;
              row.detail = memberDetail;
              if (snapshot) row.snapshot = snapshot;
              const activeId = row.snapshot?.state?.active_message_id;
              if (activeId && !row.snapshot.messages?.some(m => m.message_id === activeId)) {
                const result = await window.PortalInstructionClient.execute("GetMessage", { resident_id: node.resident_id, message_id: activeId });
                row.snapshot = { ...row.snapshot, messages: [...(row.snapshot.messages || []), result.message] };
              }
            } catch (error) { row.error = error.message; }
            render();
          }));
          loading = false; render();
        } catch (error) { root.innerHTML = window.PortalI18n.html`<p>${escapeHtml(error.message)}</p>`; }
        finally {
          loading = false;
          if (reloadRequested) {
            reloadRequested = false;
            if (!disposed && isOpen()) void load();
          }
        }
      };
      root.addEventListener("click", async event => {
        try {
          const mixButton = event.target.closest('[data-family-mix]');
          if (mixButton && !mixButton.disabled) {
            await mix.state.change(mixButton);
            return;
          }
          const detail = event.target.closest("[data-family-status-detail]");
          if (detail) { const row = rows.get(detail.dataset.familyStatusDetail); row.expanded = !row.expanded; render(); return; }
          const intake = event.target.closest("[data-family-status-intake]");
          if (intake) {
            const row = rows.get(intake.dataset.familyStatusIntake);
            if (intake.disabled || row.intakeBusy) return;
            row.intakeBusy = true; render();
            try {
              await window.PortalResidentService.setIntake({ resident_id: row.resident_id, open: row.snapshot.state.intake_state === "closed" });
              await load();
            } finally { row.intakeBusy = false; render(); }
            return;
          }
          const settingsButton = event.target.closest("[data-family-status-settings]");
          if (settingsButton) {
            const row = rows.get(settingsButton.dataset.familyStatusSettings);
            if (!row || settingsButton.disabled) return;
            await row.runtime.openSettings(() => root.querySelector(`[data-family-status-settings="${CSS.escape(row.resident_id)}"]`));
            return;
          }
        } catch (error) { window.PortalUI?.toast?.(error.message, "error"); }
      });
      const freshnessTimer = window.setInterval(() => { if (isOpen()) { render(); void loadQuota(); } }, 15000);
      function cleanup() {
        if (disposed) return;
        window.clearInterval(freshnessTimer);
        disposed = true;
        mix.release();
        rows.forEach(row => row.stops?.forEach(stop => stop?.()));
        popover?.destroy(); root.remove();
      }
      return { open() { if (container) root.hidden = false; else popover.open(); if (!loading) void load(); },
        refresh() {
          if (disposed || !isOpen()) return;
          if (loading) reloadRequested = true;
          else void load();
        },
        close() { rows.forEach(row => row.runtime?.close()); if (container) root.hidden = true; else popover.close(); }, isOpen, destroy: cleanup };
    }

    function mountFamilyStatus(residentId, container) {
      const view = createFamilyStatusView(residentId, null, null, null, container);
      view.open();
      return view;
    }

    async function openFamilyStatus(residentId, options = {}) {
      const anchor = options.anchor || document.querySelector(".portalWorkspaceHead .paneHeadActions") || document.body;
      const view = createFamilyStatusView(residentId, anchor);
      view.open();
      return view;
    }

    return Object.freeze({
      createFamilyStatusView, createFamilyMemberChatControls, createFamilyReplyControls, mountFamilyStatus, openFamilyStatus,
    });
  }

  window.PortalChatWorkspaceFamilyStatus = Object.freeze({
    createFamilyStatus, dependencies: DEPENDENCIES,
  });
})();
