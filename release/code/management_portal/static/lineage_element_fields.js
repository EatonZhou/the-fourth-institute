(function (global) {
  "use strict";

  // Field editors of HATG program elements, shared by the Lineage HATG inspector
  // and the template graph editor. Builders return markup; readers take the
  // element that contains the fields and return the configuration or an error.
  // Runtime views (queues, diagnostics, manual triggers) stay with the Lineage
  // inspector, which owns a live Lineage.

  const esc = value => global.PortalUIPrimitives.escapeHtml(value);
  const i18n = () => global.PortalI18n;
  const field = (root, name) => root?.querySelector?.(`[name="${CSS.escape(name)}"]`) || null;
  const value = (root, name, fallback = "") => String(field(root, name)?.value ?? fallback);

  function isOperatorKind(kind) {
    return global.PortalLineageProgram?.packetOperatorKinds?.includes(String(kind || "")) === true;
  }

  function operatorImplementation(config, kind = "") {
    const configured = String(config?.operator_implementation || "").trim().toLowerCase();
    if (["rules", "resident"].includes(configured)) return configured;
    return ["filter", "selector"].includes(kind) ? "rules" : "resident";
  }

  // The Invoke profile a wire writes is chosen by the kind of its target.
  function invokeProfileKey(targetKind) {
    if (["effect", "loop"].includes(targetKind)) return targetKind;
    if (isOperatorKind(targetKind)) return targetKind;
    return "agent";
  }

  // ---------- Loop ----------

  function loopFieldsHtml(config = {}, readonly = false) {
    return i18n().html`<label><span>最大轮数</span><input name="max_rounds" type="number" min="1" max="1000" value="${esc(config.max_rounds || 5)}" required${readonly ? " disabled" : ""}></label>
      <label><span>停止之后</span><select name="completion"${readonly ? " disabled" : ""}><option value="complete"${config.completion !== "rearm" ? " selected" : ""}>保持停止</option><option value="rearm"${config.completion === "rearm" ? " selected" : ""}>下次外部触发时重新计数</option></select></label>
      <p class="programInspectorNote">invoke 入口每收到一次输入进行一轮：未到最大轮数时从 continue 输出，达到最大轮数或 done 入口收到完成信号时从 stop 输出。</p>`;
  }

  function loopConfigFromFields(root) {
    return {
      max_rounds: Math.max(1, Math.trunc(Number(value(root, "max_rounds", "5") || 5))),
      completion: value(root, "completion", "complete") === "rearm" ? "rearm" : "complete",
    };
  }

  // ---------- Effect ----------

  // Non-streaming instructions an Effect script may call (decision_4adcc1ff2f2c “Effect：用脚本组合指令”).
  let instructionCatalog = null;
  let instructionCatalogLoading = null;
  const instructionSchemas = new Map();

  function ensureInstructionCatalog() {
    if (instructionCatalog) return Promise.resolve(instructionCatalog);
    if (!instructionCatalogLoading) {
      instructionCatalogLoading = global.PortalInstructionClient.execute("ListInstructions", {})
        .then(result => i18n().definition(result))
        .then(result => {
          instructionCatalog = (result?.instructions || [])
            .filter(item => !item.streaming)
            .sort((left, right) => String(left.category || "").localeCompare(String(right.category || ""))
              || String(left.instruction).localeCompare(String(right.instruction)));
          return instructionCatalog;
        })
        .finally(() => { instructionCatalogLoading = null; });
    }
    return instructionCatalogLoading;
  }

  function instructionOptionsHtml(selected = "") {
    const items = instructionCatalog || (selected ? [{ instruction: selected }] : []);
    const groups = new Map();
    items.forEach(item => {
      const key = String(item.category_title || item.category || "");
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(item);
    });
    const options = [...groups.entries()].map(([title, entries]) => {
      const rows = entries.map(item => `<option value="${esc(item.instruction)}"${item.instruction === selected ? " selected" : ""}>${esc(item.instruction)}${item.description ? ` · ${esc(item.description)}` : ""}</option>`).join("");
      return title ? `<optgroup label="${esc(title)}">${rows}</optgroup>` : rows;
    }).join("");
    return `${selected ? "" : '<option value="" selected disabled>选择要调用的指令</option>'}${options}`;
  }

  async function instructionSchema(instruction) {
    if (!instructionSchemas.has(instruction)) {
      const result = i18n().definition(await global.PortalInstructionClient.execute("GetInstructionSchema", { instruction }));
      instructionSchemas.set(instruction, result?.schema || {});
    }
    return instructionSchemas.get(instruction) || {};
  }

  // One call line with every parameter name: required ones as arguments, optional ones in a comment.
  function effectCallLine(instruction, schema) {
    const parameters = Array.isArray(schema?.parameters) ? schema.parameters : [];
    const placeholder = item => {
      if (instruction === "DeliverMessage" && item.name === "message" && item.type === "object") {
        return 'message:={"content_parts":[{"type":"text","text":""}]}';
      }
      return item.type === "object" ? `${item.name}:={}` : item.type === "array" ? `${item.name}:=[]` : `${item.name}=""`;
    };
    const required = parameters.filter(item => item.required).map(placeholder);
    const optional = parameters.filter(item => !item.required).map(item => item.name);
    return [instruction, ...required].join(" ") + (optional.length ? `  # 可选：${optional.join(" ")}` : "");
  }

  // options.effectId: a live Effect, whose recipients the inspector saves on their own.
  function effectFieldsHtml(config = {}, readonly = false, options = {}) {
    return i18n().html`<p class="programInspectorNote">Effect 被 Event 或 Packet 触发时运行下面的脚本：每行调用一条 HATG ISA 指令，可以用 if、for 把多次调用串联起来。</p>
      <div class="programEffectScriptInsert">
        <label><span>插入指令</span><select data-effect-instruction${readonly ? " disabled" : ""}>${instructionOptionsHtml("")}</select></label>
        <button type="button" data-effect-insert${readonly ? " disabled" : ""}>插入调用行</button>
      </div>
      <div class="programInspectorNote" data-effect-instruction-schema></div>
      <label><span>脚本</span><textarea name="script" rows="12" spellcheck="false" class="programEffectScript" data-effect-script${readonly ? " disabled" : ""}>${esc(config.script || "")}</textarea>
        <small>每行写成 指令名 参数="文字" 或 参数:=JSON；用 变量=$(指令名 …) 保存结果，用 \${变量.字段} 读取。可读取 \${arguments.名称}、\${event.payload.字段}、\${packet.message.data.字段}、\${endpoint.端点名}。用 if [ … ]; then … fi 与 for x in …; do … done 串联，output 名称=值 设置输出，fail "原因" 以失败结束。</small></label>
      <label><span>具名端点</span><input name="endpoints" maxlength="400" value="${esc((config.endpoints || []).join(", "))}"${readonly ? " disabled" : ""}>
        <small>用逗号分隔；每个端点在卡片上显示为一个 Target 出口，连到调用对象，脚本用 \${endpoint.端点名} 读取它的 ID。</small></label>
      ${effectRecipientsHtml(config, { readonly, effectId: options.effectId || "" })}`;
  }

  function effectConfigFromFields(root) {
    const endpoints = value(root, "endpoints").split(/[,，\s]+/).map(item => item.trim()).filter(Boolean);
    const config = { script: value(root, "script"), endpoints: [...new Set(endpoints)] };
    // A live Effect saves its recipients on their own (the inspector has no recipients
    // field), so its script save leaves the stored list as it is.
    const recipients = field(root, "failure_recipients");
    if (recipients?.dataset.effectRecipientsConfigured === "true") {
      config.failure_recipients = recipientList(recipients.value);
    }
    return config;
  }

  // ---------- Effect failure recipients ----------
  // One editor serves one Effect and many (decision_4adcc1ff2f2c “Effect 失败通知”):
  // the Effect inspector, a folded module's objects and the template editor open it.
  // Its caller decides what saving writes: the Lineage, or the template draft.

  let recipientUsers = null;
  let recipientUsersLoading = null;
  let recipientUsersFailed = false;

  function recipientList(raw) {
    let items = raw;
    if (typeof raw === "string") {
      try { items = JSON.parse(raw || "[]"); } catch { items = raw.split(/[,，\s]+/); }
    }
    return [...new Set((Array.isArray(items) ? items : []).map(item => String(item || "").trim()).filter(Boolean))];
  }

  function loadRecipientUsers() {
    if (recipientUsers) return Promise.resolve(recipientUsers);
    if (!recipientUsersLoading) {
      recipientUsersLoading = global.PortalInstructionClient.execute("ListUsers", { status: "active" })
        .then(result => {
          recipientUsers = (result?.users || []).filter(user => user?.username).map(user => ({
            username: String(user.username),
            name: String(user.display_name || user.username),
            avatar: user.avatar || null,
          }));
          recipientUsersFailed = false;
          return recipientUsers;
        }, () => {
          recipientUsersFailed = true;
          return [];
        })
        .finally(() => { recipientUsersLoading = null; });
    }
    return recipientUsersLoading;
  }

  function recipientUser(username) {
    return recipientUsers?.find(user => user.username === username) || null;
  }

  function recipientName(username) {
    return recipientUser(username)?.name || username;
  }

  function recipientNames(usernames) {
    return usernames.map(recipientName).join("、");
  }

  // Uploaded portraits tell people apart; everyone else gets a lettered disc.
  function recipientAvatarHtml(username) {
    const avatar = recipientUser(username)?.avatar;
    if (avatar?.kind === "upload" && String(avatar.url || "").startsWith("/api/account/avatar/image?")) {
      return `<span class="effectRecipientAvatar" aria-hidden="true"><img src="${esc(avatar.url)}" alt=""></span>`;
    }
    const letter = Array.from(recipientName(username))[0]?.toUpperCase() || "?";
    const hue = [...username].reduce((sum, char) => (sum * 31 + char.charCodeAt(0)) % 360, 7);
    return `<span class="effectRecipientAvatar" style="--recipient-hue:${hue}" aria-hidden="true">${esc(letter)}</span>`;
  }

  // The recipient's state reads from the list: inactive or unknown names are marked.
  function recipientChipHtml(username, { extra = "", partial = false } = {}) {
    const user = recipientUser(username);
    const missing = recipientUsers && !user;
    const name = recipientName(username);
    return i18n().html`<span class="effectRecipientChip"${missing ? ' data-recipient-missing="true"' : ""}${partial ? ' data-recipient-partial="true"' : ""} data-recipient="${esc(username)}" title="${esc(missing ? i18n().text`${username}：不是当前有效的 User，收不到提醒` : username)}">${recipientAvatarHtml(username)}<span class="effectRecipientName">${esc(name)}</span>${name !== username ? `<small>${esc(username)}</small>` : ""}${missing ? i18n().html`<em>已失效</em>` : ""}${extra}</span>`;
  }

  // Three states, never merged: a list ("on"), an explicit empty list ("off"), and no
  // list at all. A live Effect without one is not notified ("unset"); a new Effect or
  // template draft without one notifies the user who sets it up ("default").
  function recipientState(config, { draft = false } = {}) {
    if (!config || !Object.hasOwn(config, "failure_recipients")) return draft ? "default" : "unset";
    return recipientList(config.failure_recipients).length ? "on" : "off";
  }

  function recipientChipsHtml(usernames, { limit = 0, state = "" } = {}) {
    if (state === "default") return i18n().html`<span class="effectRecipientsOff" data-recipients-state="default">新建时通知设立它的用户</span>`;
    if (state === "unset") return i18n().html`<span class="effectRecipientsOff" data-recipients-state="unset" title="这个 Effect 还没有设置失败通知名单，失败时不提醒任何人。">尚未配置 · 当前不通知</span>`;
    if (!usernames.length) return i18n().html`<span class="effectRecipientsOff" data-recipients-state="off">已关闭 · 失败时不提醒任何人</span>`;
    const shown = limit ? usernames.slice(0, limit) : usernames;
    const more = usernames.length - shown.length;
    return shown.map(username => recipientChipHtml(username)).join("") + (more > 0 ? `<span class="effectRecipientMore">+${more}</span>` : "");
  }

  // The inspector's summary: who is told now, and one button to change it.
  function effectRecipientsHtml(config = {}, { readonly = false, effectId = "" } = {}) {
    const state = recipientState(config, { draft: !effectId });
    const recipients = recipientList(config.failure_recipients || []);
    const draftField = effectId ? "" : `<input type="hidden" name="failure_recipients" value="${esc(JSON.stringify(recipients))}" data-effect-recipients-configured="${state === "default" ? "false" : "true"}">`;
    return i18n().html`<section class="effectRecipients" data-effect-recipients="${esc(effectId)}">
        <header><span>失败通知</span><button type="button" class="portalButton is-compact" data-effect-recipients-edit${readonly ? " disabled" : ""}>修改收件人…</button></header>
        <div class="effectRecipientChips" data-recipient-chips="${esc(JSON.stringify(recipients))}" data-recipients-state="${state}">${recipientChipsHtml(recipients, { state })}</div>
        <small>Effect 执行失败时，收件人会在自己的 Mailbox 收到提醒。</small>${draftField}
      </section>`;
  }

  // Redraw chips once the User list arrives, so names, portraits and invalid entries show.
  function hydrateRecipientChips(root) {
    const paint = () => root?.querySelectorAll?.("[data-recipient-chips]").forEach(node => {
      node.innerHTML = recipientChipsHtml(recipientList(node.dataset.recipientChips), {
        limit: Number(node.dataset.recipientLimit || 0),
        state: node.dataset.recipientsState || "",
      });
    });
    if (recipientUsers) return paint();
    loadRecipientUsers().then(paint);
  }

  // A saved list is always configured: on, or explicitly off.
  function setRecipientChips(node, recipients) {
    if (!node) return;
    node.dataset.recipientChips = JSON.stringify(recipients);
    node.dataset.recipientsState = recipients.length ? "on" : "off";
    node.innerHTML = recipientChipsHtml(recipients, { limit: Number(node.dataset.recipientLimit || 0) });
  }

  // The template editor keeps recipients in its draft: the editor writes the hidden field.
  function bindDraftRecipients(root) {
    const section = root?.querySelector('[data-effect-recipients=""]');
    const input = section?.querySelector('[name="failure_recipients"]');
    if (!input) return;
    section.querySelector("[data-effect-recipients-edit]")?.addEventListener("click", () => {
      const recipients = recipientList(input.value);
      const configured = input.dataset.effectRecipientsConfigured === "true";
      openRecipientEditor({
        effects: [{ id: "draft", title: i18n().t("模板中的 Effect"), recipients, state: configured ? (recipients.length ? "on" : "off") : "default" }],
        confirmLabel: i18n().t("用于模板"),
        onSave: async ([change]) => {
          input.value = JSON.stringify(change.recipients);
          input.dataset.effectRecipientsConfigured = "true";
          setRecipientChips(section.querySelector("[data-recipient-chips]"), change.recipients);
          input.dispatchEvent(new Event("change", { bubbles: true }));
        },
      });
    });
  }

  let recipientEditorSerial = 0;

  // Each Effect's list after editing. chosen maps a username to "all" (on every Effect)
  // or "some" (kept only where it already was); a username not in chosen is removed.
  // An Effect without a list keeps having none unless someone is added to it or the
  // user chose to close notifications (close), which writes an explicit empty list.
  function recipientResults(effects, chosen, { close = false } = {}) {
    const order = [];
    for (const username of [...effects.flatMap(effect => effect.recipients), ...chosen.keys()]) {
      if (!order.includes(username)) order.push(username);
    }
    return effects.map(effect => {
      const recipients = order.filter(username => chosen.get(username) === "all"
        || (chosen.get(username) === "some" && effect.recipients.includes(username)));
      if ((effect.state === "unset" || effect.state === "default") && !recipients.length && !close) {
        return { id: effect.id, title: effect.title, recipients, state: effect.state, changed: false };
      }
      const changed = effect.state === "unset" || effect.state === "default"
        || recipients.length !== effect.recipients.length
        || recipients.some((username, index) => username !== effect.recipients[index]);
      return { id: effect.id, title: effect.title, recipients, state: recipients.length ? "on" : "off", changed };
    });
  }

  // effects: [{id, title, recipients, state}] (state from recipientState); onSave receives
  // the Effects whose list changes, each with its complete new list. A chip on only some
  // Effects keeps that coverage unless the user adds it to all of them or removes it.
  function openRecipientEditor({ effects, me = "", confirmLabel = "", onSave }) {
    const t = i18n().t;
    const list = effects.map(effect => {
      const recipients = recipientList(effect.recipients);
      return { id: effect.id, title: effect.title || effect.id, recipients, state: effect.state || (recipients.length ? "on" : "off") };
    });
    const total = list.length;
    const unconfigured = list.filter(effect => effect.state === "unset" || effect.state === "default");
    const unset = unconfigured.filter(effect => effect.state === "unset").length;
    const coverage = new Map();
    list.forEach(effect => effect.recipients.forEach(username => coverage.set(username, (coverage.get(username) || 0) + 1)));
    const chosen = new Map([...coverage].map(([username, count]) => [username, count === total ? "all" : "some"]));
    let close = false;
    const results = () => recipientResults(list, chosen, { close });
    const changes = () => results().filter(result => result.changed);
    const sameList = (left, right) => left.length === right.length && left.every((item, index) => item === right[index]);
    const serial = ++recipientEditorSerial;
    const titleId = `effectRecipientsTitle${serial}`;
    const listId = `effectRecipientsOptions${serial}`;
    let active = 0;
    let saving = false;

    const overlay = document.createElement("div");
    overlay.className = "portalDetailOverlay portalHatgOverlayComponent effectRecipientsOverlay";
    overlay.hidden = true;
    overlay.dataset.portalDetailOverlay = "";
    overlay.innerHTML = i18n().html`<section class="portalDetailSurface effectRecipientsDialog" data-portal-detail-surface data-effect-recipients-dialog role="dialog" aria-modal="true" aria-labelledby="${titleId}">
        <header class="portalDetailHeader" data-portal-detail-header>
          <div><span class="portalDetailEyebrow">失败通知</span><h2 id="${titleId}">${esc(total === 1 ? list[0].title : i18n().text`${total} 个 Effect`)}</h2></div>
          <button class="portalDetailClose" data-portal-detail-close data-portal-icon="close" type="button" aria-label="关闭"></button>
        </header>
        <div class="portalDetailBody effectRecipientsBody" data-portal-detail-body>
          <p class="effectRecipientsIntro">${esc(total === 1 ? t("这个 Effect 执行失败时，下面的用户会在自己的 Mailbox 收到提醒。") : t("这些 Effect 执行失败时，下面的用户会在自己的 Mailbox 收到提醒。只出现在部分 Effect 中的收件人标出覆盖数，可以补到全部。"))}</p>
          ${unset ? i18n().html`<p class="effectRecipientsIntro" data-recipients-unset>${esc(total === 1 ? t("这个 Effect 尚未配置名单，当前失败时不提醒任何人。添加收件人，或选择“关闭通知”明确关闭。") : i18n().text`其中 ${unset} 个 Effect 尚未配置名单，当前不通知；不给它们添加收件人时保持原状，选择“关闭通知”会把它们明确关闭。`)}</p>` : ""}
          <section class="effectRecipientsCurrent">
            <header><strong data-recipients-count></strong><span><button type="button" class="portalButton is-compact" data-recipients-me>只通知我</button><button type="button" class="portalButton is-compact" data-recipients-clear title="移除全部收件人，失败时不提醒任何人">关闭通知</button></span></header>
            <div class="effectRecipientChips is-editable" data-recipients-chosen></div>
          </section>
          <label class="portalField effectRecipientsSearch"><span>添加收件人</span>
            <input type="search" autocomplete="off" role="combobox" aria-expanded="true" aria-autocomplete="list" aria-controls="${listId}" placeholder="搜索用户名或显示名称，回车添加" data-recipients-search></label>
          <div class="effectRecipientOptions" id="${listId}" role="listbox" aria-label="可添加的用户" data-recipients-options></div>
          <div class="effectRecipientsPreview" data-recipients-preview aria-live="polite"></div>
          <p class="effectRecipientsError" data-recipients-error role="alert" hidden></p>
        </div>
        <footer class="portalDetailFooter">
          <button type="button" class="portalButton" data-recipients-cancel>取消</button>
          <button type="button" class="portalButton is-primary" data-recipients-save></button>
        </footer>
      </section>`;
    document.body.append(overlay);
    const surface = global.PortalDetailSurface?.mount?.(overlay.firstElementChild) || null;
    const $ = selector => overlay.querySelector(selector);
    const search = $("[data-recipients-search]");
    const dialog = global.PortalUI.createDialogController({
      modal: overlay, openClass: "portalDialogOpen", backdropClose: true, escapeClose: true,
      manageHidden: true, initialFocus: () => search,
      dirty: () => !saving && changes().length > 0,
      discardMessage: t("关闭后，这次对收件人的修改不会保存。"),
      onClose: () => {
        window.setTimeout(() => {
          surface?.destroy?.();
          dialog.destroy();
          overlay.remove();
        }, 0);
      },
    });

    const candidates = () => {
      const query = search.value.trim().toLowerCase();
      return (recipientUsers || [])
        .filter(user => chosen.get(user.username) !== "all")
        .filter(user => !query || user.username.toLowerCase().includes(query) || user.name.toLowerCase().includes(query))
        .sort((left, right) => (right.username === me) - (left.username === me))
        .slice(0, 8);
    };

    function add(username) {
      const name = String(username || "").trim();
      if (!name) return;
      chosen.set(name, "all");
      search.value = "";
      active = 0;
      render();
      search.focus();
    }

    // What a result means once saved; a list left unconfigured says so, never "closed".
    function resultText(result) {
      if (result.state === "unset") return t("尚未配置 · 当前不通知");
      if (result.state === "default") return t("新建时通知设立它的用户");
      return result.recipients.length ? recipientNames(result.recipients) : t("关闭失败通知");
    }

    function previewHtml() {
      const outcome = results();
      if (!outcome.some(result => result.changed)) return i18n().html`<p data-preview-state="unchanged">名单没有修改。</p>`;
      const finals = outcome.map(result => result.recipients);
      if (outcome.every(result => result.state === outcome[0].state && sameList(result.recipients, finals[0]))) {
        const names = recipientNames(finals[0]);
        if (total === 1) {
          return finals[0].length
            ? i18n().html`<p data-preview-state="on">保存后，失败时提醒：<strong>${esc(names)}</strong></p>`
            : i18n().html`<p data-preview-state="off">保存后关闭失败通知：这个 Effect 失败时不提醒任何人。</p>`;
        }
        return finals[0].length
          ? i18n().html`<p data-preview-state="on">保存后，这 ${total} 个 Effect 失败时都提醒：<strong>${esc(names)}</strong></p>`
          : i18n().html`<p data-preview-state="off">保存后关闭这 ${total} 个 Effect 的失败通知。</p>`;
      }
      return i18n().html`<p data-preview-state="mixed">保存后各 Effect 的收件人：</p><ul>${outcome.map(result => i18n().html`<li data-result-state="${result.state}"><strong>${esc(result.title)}</strong><span>${esc(resultText(result))}</span></li>`).join("")}</ul>`;
    }

    // With nobody chosen, Effects that had no list keep that state until the user closes them.
    function emptyChipsHtml() {
      if (!close && unconfigured.length === total) return recipientChipsHtml([], { state: list[0].state });
      return i18n().html`<span class="effectRecipientsOff" data-recipients-state="off">没有收件人 · 保存后失败时不提醒任何人</span>`;
    }

    function render() {
      const names = [...chosen.keys()];
      const chipsNode = $("[data-recipients-chosen]");
      chipsNode.innerHTML = names.length ? names.map(username => {
        const partial = chosen.get(username) === "some";
        const count = coverage.get(username) || 0;
        const extra = (partial ? i18n().html`<em class="effectRecipientCoverage" title="${esc(i18n().text`在 ${count} / ${total} 个 Effect 中`)}">${count}/${total}</em><button type="button" class="effectRecipientAddAll" data-recipient-all="${esc(username)}" title="${esc(i18n().text`加到全部 ${total} 个 Effect`)}">补到全部</button>` : "")
          + i18n().html`<button type="button" class="effectRecipientRemove" data-recipient-remove="${esc(username)}" aria-label="${esc(i18n().text`移除 ${recipientName(username)}`)}" title="移除">×</button>`;
        return recipientChipHtml(username, { extra, partial });
      }).join("") : emptyChipsHtml();
      $("[data-recipients-count]").textContent = i18n().text`收件人（${names.length}）`;
      $("[data-recipients-clear]").disabled = !names.length && (close || !unconfigured.length);
      $("[data-recipients-me]").hidden = !me;
      const options = candidates();
      active = Math.min(active, Math.max(0, options.length - 1));
      const optionsNode = $("[data-recipients-options]");
      optionsNode.innerHTML = options.length ? options.map((user, index) => i18n().html`<button type="button" role="option" id="${listId}-${index}" class="effectRecipientOption" data-recipient-add="${esc(user.username)}" aria-selected="${index === active ? "true" : "false"}" tabindex="-1">${recipientAvatarHtml(user.username)}<span class="effectRecipientName">${esc(user.name)}</span><small>${esc(user.username)}</small>${user.username === me ? i18n().html`<em>你</em>` : ""}${chosen.get(user.username) === "some" ? i18n().html`<em>已在 ${coverage.get(user.username)} / ${total} 个 Effect 中</em>` : ""}</button>`).join("")
        : i18n().html`<p class="effectRecipientsNoMatch">${esc(recipientUsersLoading ? t("正在读取用户…") : recipientUsersFailed ? t("无法读取用户列表；输入完整用户名后按回车添加。") : search.value.trim() ? t("没有匹配的有效用户。") : t("所有有效用户都已在名单中。"))}</p>`;
      search.setAttribute("aria-activedescendant", options.length ? `${listId}-${active}` : "");
      $("[data-recipients-preview]").innerHTML = previewHtml();
      const pending = changes();
      const overLimit = results().some(result => result.recipients.length > 20);
      const save = $("[data-recipients-save]");
      save.textContent = saving ? t("保存中…") : confirmLabel || (total === 1 ? t("保存") : i18n().text`保存到 ${pending.length} 个 Effect`);
      save.disabled = saving || !pending.length || overLimit;
      if (overLimit) showError(t("每个 Effect 最多 20 位收件人。"));
      else if (!saving) showError("");
    }

    function showError(message) {
      const node = $("[data-recipients-error]");
      node.textContent = message;
      node.hidden = !message;
    }

    overlay.addEventListener("click", event => {
      const target = event.target.closest("button");
      if (!target || !overlay.contains(target) || saving) return;
      if (target.dataset.recipientAdd) add(target.dataset.recipientAdd);
      else if (target.dataset.recipientAll) { chosen.set(target.dataset.recipientAll, "all"); render(); }
      else if (target.dataset.recipientRemove) { chosen.delete(target.dataset.recipientRemove); render(); search.focus(); }
    });
    search.addEventListener("input", () => { active = 0; render(); });
    search.addEventListener("keydown", event => {
      const options = candidates();
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (!options.length) return;
        active = (active + (event.key === "ArrowDown" ? 1 : options.length - 1)) % options.length;
        render();
      } else if (event.key === "Enter") {
        event.preventDefault();
        if (options[active]) add(options[active].username);
        else if (recipientUsersFailed) add(search.value);
      } else if (event.key === "Backspace" && !search.value && chosen.size) {
        chosen.delete([...chosen.keys()].at(-1));
        render();
      }
    });
    $("[data-recipients-me]").addEventListener("click", () => {
      chosen.clear();
      if (me) chosen.set(me, "all");
      render();
    });
    $("[data-recipients-clear]").addEventListener("click", () => { chosen.clear(); close = true; render(); });
    $("[data-recipients-cancel]").addEventListener("click", () => dialog.close());
    $("[data-portal-detail-close]").addEventListener("click", () => dialog.close());
    $("[data-recipients-save]").addEventListener("click", async () => {
      const pending = changes();
      if (!pending.length || saving) return;
      saving = true;
      render();
      try {
        await onSave(pending);
        saving = false;
        dialog.close({ force: true });
      } catch (error) {
        saving = false;
        render();
        showError(error?.message || String(error));
      }
    });
    render();
    dialog.open();
    if (!recipientUsers) loadRecipientUsers().then(() => { if (overlay.isConnected) render(); });
    return dialog;
  }

  async function renderInstructionSchema(root, instruction) {
    const slot = root?.querySelector("[data-effect-instruction-schema]");
    if (!slot) return;
    if (!instruction) {
      slot.textContent = "";
      return;
    }
    try {
      const schema = await instructionSchema(instruction);
      const parameters = Array.isArray(schema.parameters) ? schema.parameters : [];
      slot.innerHTML = parameters.length
        ? `<strong>${esc(instruction)} 的参数</strong><ul>${parameters.map(item => `<li><code>${esc(item.name)}</code>${item.required ? "（必填）" : ""} ${esc(item.description || "")}</li>`).join("")}</ul>`
        : `<strong>${esc(instruction)}</strong> 不需要参数。`;
    } catch (error) {
      slot.textContent = `无法读取指令定义：${error.message || error}`;
    }
  }

  async function insertCallLine(root, instruction, options = {}) {
    const textarea = root?.querySelector("[data-effect-script]");
    if (!textarea || textarea.disabled) return;
    if (!instruction) {
      options.notify?.(i18n().t("先选择要插入的指令。"));
      return;
    }
    const line = effectCallLine(instruction, await instructionSchema(instruction));
    if (!root.contains(textarea)) return;
    const start = Number.isInteger(textarea.selectionStart) ? textarea.selectionStart : textarea.value.length;
    const end = Number.isInteger(textarea.selectionEnd) ? textarea.selectionEnd : start;
    const before = textarea.value.slice(0, start);
    const after = textarea.value.slice(end);
    const prefix = before && !before.endsWith("\n") ? "\n" : "";
    const suffix = after && !after.startsWith("\n") ? "\n" : "";
    textarea.value = `${before}${prefix}${line}${suffix}${after}`;
    const caret = before.length + prefix.length + line.length;
    textarea.focus();
    textarea.setSelectionRange(caret, caret);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  }

  // Instruction picker, parameter list and call-line insertion of the script field.
  function bindEffectFields(root, options = {}) {
    const select = root?.querySelector("[data-effect-instruction]");
    const renderSchema = () => renderInstructionSchema(root, select?.value || "");
    select?.addEventListener("change", renderSchema);
    ensureInstructionCatalog().then(() => {
      if (!select || !root.contains(select)) return;
      const current = select.value || "";
      select.innerHTML = instructionOptionsHtml(current);
      renderSchema();
    }).catch(() => {});
    root?.querySelector("[data-effect-insert]")?.addEventListener("click", () => {
      insertCallLine(root, select?.value || "", options).catch(error => {
        options.notify?.(i18n().text`无法插入调用行：${error.message || error}`);
      });
    });
    hydrateRecipientChips(root);
    bindDraftRecipients(root);
  }

  // ---------- Packet operators ----------

  function packetSelectionEditorHtml(rawSelection, readonly, prefix = "selection", title = "Packet 选择规则") {
    const filter = global.PortalLineageProgram?.normalizePacketFilter?.(rawSelection)
      || { mode: "all", conditions: [], order_by: "input", limit: null };
    return i18n().html`<section class="programPacketFilter" data-route-filter-editor="${esc(prefix)}">
      <header><strong>${esc(title)}</strong><small>只选择，不改写 Packet</small></header>
      <label><span>条件组合</span><select name="${esc(prefix)}_mode"${readonly ? " disabled" : ""}>
        <option value="all"${filter.mode === "all" ? " selected" : ""}>全部条件都满足（all）</option>
        <option value="any"${filter.mode === "any" ? " selected" : ""}>任一条件满足（any）</option>
      </select></label>
      <label><span>匹配条件（JSON array）</span><textarea name="${esc(prefix)}_conditions_json" rows="6" maxlength="65536" spellcheck="false" placeholder='[{"from":"packet.events.0.type","operator":"equals","value":"manual.triggered"}]'${readonly ? " disabled" : ""}>${esc(JSON.stringify(filter.conditions || [], null, 2))}</textarea><small>字段路径以 packet. 开头，例如 packet.key、packet.message.text、packet.message.data.*、packet.events.0.type。操作符支持 equals、contains、数值比较、exists 等。</small></label>
      <label><span>输出顺序</span><select name="${esc(prefix)}_order_by"${readonly ? " disabled" : ""}>
        <option value="input"${filter.order_by === "input" ? " selected" : ""}>保持输入顺序</option>
        <option value="priority_desc"${filter.order_by === "priority_desc" ? " selected" : ""}>priority 从高到低</option>
      </select></label>
      <label><span>最多保留</span><input name="${esc(prefix)}_limit" type="number" min="1" max="24" step="1" value="${filter.limit == null ? "" : esc(filter.limit)}" placeholder="留空表示不限"${readonly ? " disabled" : ""}></label>
    </section>`;
  }

  function packetSelectionFromFields(root, prefix = "selection") {
    let conditions;
    try {
      conditions = JSON.parse(value(root, `${prefix}_conditions_json`) || "[]");
    } catch (_error) {
      return { filter: null, error: "Packet 匹配条件必须是有效 JSON array。" };
    }
    if (!Array.isArray(conditions)) {
      return { filter: null, error: "Packet 匹配条件必须是 JSON array。" };
    }
    if (conditions.some(condition => !String(condition?.from || "").trim().replace(/^\$/, "").startsWith("packet."))) {
      return { filter: null, error: "字段路径必须以 packet. 开头，例如 packet.events.0.type。" };
    }
    const limitRaw = value(root, `${prefix}_limit`).trim();
    const filter = global.PortalLineageProgram?.normalizePacketFilter?.({
      mode: field(root, `${prefix}_mode`)?.value,
      conditions,
      order_by: field(root, `${prefix}_order_by`)?.value,
      limit: limitRaw || null,
    });
    if (!filter || filter.conditions.length !== conditions.length) {
      return { filter: null, error: "Packet 匹配条件包含空路径或不支持的操作符。" };
    }
    return { filter, error: "" };
  }

  function selectorBranchesEditorHtml(rawBranches, readonly) {
    const branches = global.PortalLineageProgram?.normalizeSelectorBranches?.(rawBranches) || [];
    return i18n().html`<section class="programSelectorBranches" data-selector-branches-editor>
      <header><span><strong>Selector 输出分支</strong><small>一个 Invoke → 多个具名输出端口</small></span><button type="button" data-selector-branch-add${readonly ? " disabled" : ""}>添加分支</button></header>
      <p>最常用的配置是“Packet 路由标签 → 分支”。例如标签 <code>frontend</code> 会从对应的 <code>branch:&lt;id&gt;</code> 端口输出。</p>
      <div class="programSelectorBranchList" data-selector-branch-list>${branches.map((branch, index) => selectorBranchCardHtml(branch, index, readonly)).join("")}</div>
      <details class="programSelectorJsonProjection">
        <summary>高级 · 查看规范 JSON</summary>
        <textarea data-selector-json-projection rows="12" readonly>${esc(JSON.stringify(branches, null, 2))}</textarea>
        <small>这里是上方结构化配置的只读投影，不是第二份配置来源。</small>
      </details>
    </section>`;
  }

  function selectorBranchSimpleTag(branch) {
    const match = branch?.match || {};
    const conditions = Array.isArray(match.conditions) ? match.conditions : [];
    return (
      match.mode === "all"
      && match.order_by === "input"
      && match.limit == null
      && conditions.length === 1
      && conditions[0]?.from === "packet.key"
      && conditions[0]?.operator === "equals"
      && typeof conditions[0]?.value === "string"
    ) ? conditions[0].value : "";
  }

  function selectorBranchCardHtml(branch, index, readonly) {
    const routeTag = selectorBranchSimpleTag(branch);
    const advanced = !routeTag;
    return i18n().html`<fieldset class="programSelectorBranchCard" data-selector-branch-row>
      <legend><span>分支 ${index + 1}</span><code>branch:${esc(branch.id)}</code><button type="button" data-selector-branch-remove${readonly ? " disabled" : ""}>移除</button></legend>
      <div class="programSelectorBranchIdentity">
        <label><span>分支 ID</span><input name="selector_branch_id" maxlength="32" value="${esc(branch.id)}" pattern="[a-z][a-z0-9_-]{0,31}" required${readonly ? " disabled" : ""}><small>决定稳定输出端口 <code>branch:&lt;id&gt;</code></small></label>
        <label><span>显示名称</span><input name="selector_branch_label" maxlength="80" value="${esc(branch.label)}" required${readonly ? " disabled" : ""}></label>
      </div>
      <label><span>分支说明</span><textarea name="selector_branch_description" rows="2" maxlength="1000"${readonly ? " disabled" : ""}>${esc(branch.description || "")}</textarea></label>
      <div class="programSelectorRouteMap">
        <span>当 Packet</span>
        <strong>路由标签</strong>
        <span>等于</span>
        <input name="selector_branch_route_tag" maxlength="64" value="${esc(routeTag || branch.id)}"${advanced ? " disabled" : ""}${readonly ? " disabled" : ""}>
        <span>→ ${esc(branch.label)}</span>
      </div>
      <label class="programInspectorCheckbox"><input name="selector_branch_advanced" type="checkbox"${advanced ? " checked" : ""}${readonly ? " disabled" : ""}><span>使用高级匹配规则</span></label>
      <details class="programSelectorAdvancedRule"${advanced ? " open" : ""}${advanced ? "" : " hidden"}>
        <summary>高级匹配规则</summary>
        <textarea name="selector_branch_match_json" rows="7" maxlength="65536" spellcheck="false"${readonly ? " disabled" : ""}>${esc(JSON.stringify(branch.match, null, 2))}</textarea>
        <small>可按 key、priority、payload.* 等组合条件；高级模式下以这里的规则为准。</small>
      </details>
    </fieldset>`;
  }

  function selectorBranchesFromFields(root) {
    const rows = [...(root.querySelectorAll("[data-selector-branch-row]") || [])];
    if (rows.length < 2 || rows.length > 12) {
      return { branches: null, error: "Selector 必须配置 2–12 个输出分支。" };
    }
    const authored = [];
    for (const row of rows) {
      const id = value(row, "selector_branch_id").trim().toLowerCase();
      const label = value(row, "selector_branch_label").trim();
      const description = value(row, "selector_branch_description").trim();
      const advanced = Boolean(field(row, "selector_branch_advanced")?.checked);
      let match;
      if (advanced) {
        try {
          match = JSON.parse(value(row, "selector_branch_match_json") || "{}");
        } catch (_error) {
          return { branches: null, error: `分支 ${label || id || "—"} 的高级匹配规则不是有效 JSON。` };
        }
      } else {
        const routeTag = value(row, "selector_branch_route_tag").trim();
        if (!routeTag) {
          return { branches: null, error: `分支 ${label || id || "—"} 需要填写路由标签。` };
        }
        match = {
          mode: "all",
          conditions: [{ from: "packet.key", operator: "equals", value: routeTag }],
          order_by: "input",
          limit: null,
        };
      }
      authored.push({ id, label, description, match });
    }
    const branches = global.PortalLineageProgram?.normalizeSelectorBranches?.(authored) || [];
    if (branches.length !== authored.length) {
      return {
        branches: null,
        error: "Selector 分支 ID 必须唯一并符合 [a-z][a-z0-9_-]*，且每个分支都要有合法 match。",
      };
    }
    const invalidMatch = branches.some((branch, index) => (
      (Array.isArray(authored[index]?.match?.conditions) ? authored[index].match.conditions.length : 0)
        !== branch.match.conditions.length
    ));
    if (invalidMatch) {
      return { branches: null, error: "Selector 分支的 match 包含空路径或不支持的操作符。" };
    }
    return { branches, error: "" };
  }

  function syncSelectorBranches(root, busy = false) {
    const rows = [...(root?.querySelectorAll("[data-selector-branch-row]") || [])];
    rows.forEach((row, index) => {
      const id = value(row, "selector_branch_id").trim();
      const label = (value(row, "selector_branch_label") || id).trim();
      const legend = row.querySelector("legend span");
      const port = row.querySelector("legend code");
      if (legend) legend.textContent = i18n().text`分支 ${index + 1}`;
      if (port) port.textContent = i18n().text`branch:${id || "—"}`;
      const advanced = Boolean(field(row, "selector_branch_advanced")?.checked);
      const routeInput = field(row, "selector_branch_route_tag");
      const advancedRule = row.querySelector(".programSelectorAdvancedRule");
      if (routeInput) routeInput.disabled = advanced || busy;
      if (advancedRule) advancedRule.hidden = !advanced;
      const remove = row.querySelector("[data-selector-branch-remove]");
      if (remove) remove.disabled = busy || rows.length <= 2;
      const arrow = row.querySelector(".programSelectorRouteMap span:last-child");
      if (arrow) arrow.textContent = `→ ${label || id || i18n().t("分支")}`;
    });
    const add = root?.querySelector("[data-selector-branch-add]");
    if (add) add.disabled = busy || rows.length >= 12;
    const projection = root?.querySelector("[data-selector-json-projection]");
    const parsed = selectorBranchesFromFields(root);
    if (projection && parsed.branches) projection.value = JSON.stringify(parsed.branches, null, 2);
  }

  function bindSelectorBranches(root, readonly, options = {}) {
    const busy = () => Boolean(options.busy?.());
    const list = root?.querySelector("[data-selector-branch-list]");
    root?.querySelector("[data-selector-branch-add]")?.addEventListener("click", () => {
      if (readonly) return;
      const currentRows = [...list.querySelectorAll("[data-selector-branch-row]")];
      const used = new Set(currentRows.map(row => value(row, "selector_branch_id")));
      let index = 1;
      while (used.has(`branch_${index}`)) index += 1;
      const id = `branch_${index}`;
      const wrapper = document.createElement("div");
      wrapper.innerHTML = selectorBranchCardHtml({
        id,
        label: i18n().text`分支 ${index}`,
        description: i18n().text`接收路由标签为 ${id} 的 Packet。`,
        match: {
          mode: "all",
          conditions: [{ from: "packet.key", operator: "equals", value: id }],
          order_by: "input",
          limit: null,
        },
      }, currentRows.length, false);
      list.append(wrapper.firstElementChild);
      syncSelectorBranches(root, busy());
      options.onChange?.();
    });
    list?.addEventListener("click", event => {
      const remove = event.target.closest("[data-selector-branch-remove]");
      if (!remove || remove.disabled) return;
      remove.closest("[data-selector-branch-row]")?.remove();
      syncSelectorBranches(root, busy());
      options.onChange?.();
    });
    list?.addEventListener("input", () => syncSelectorBranches(root, busy()));
    list?.addEventListener("change", () => syncSelectorBranches(root, busy()));
    syncSelectorBranches(root, busy());
  }

  function operatorFieldsHtml(component, readonly) {
    const role = component?.config || {};
    const implementation = operatorImplementation(role, component?.kind);
    const filter = component?.kind === "filter";
    const selector = component?.kind === "selector";
    const sharedBranches = selector ? selectorBranchesEditorHtml(role?.branches, readonly) : "";
    const rules = filter
      ? packetSelectionEditorHtml(role?.selection, readonly, "selection", "Filter 规则")
      : selector
        ? ""
        : i18n().html`<section class="programPacketFilter">
          <header><strong>Reducer 规则</strong><small>输入集合 → 一个 Packet</small></header>
          <label><span>归约策略</span><select name="reduce_strategy"${readonly ? " disabled" : ""}>
            <option value="join"${String(role?.reduce_strategy || "join") === "join" ? " selected" : ""}>按输入顺序拼接</option>
            <option value="highest_priority"${role?.reduce_strategy === "highest_priority" ? " selected" : ""}>取最高 priority</option>
            <option value="first"${role?.reduce_strategy === "first" ? " selected" : ""}>取第一条</option>
          </select></label>
          <label><span>拼接分隔符</span><input name="reduce_separator" maxlength="200" value="${esc(String(role?.reduce_separator ?? "\n\n"))}"${readonly ? " disabled" : ""}></label>
          <label><span>输出 Packet key</span><input name="output_key" maxlength="80" value="${esc(String(role?.output_key || "reduced"))}" required${readonly ? " disabled" : ""}></label>
        </section>`;
    return i18n().html`<label><span>实现方式</span><select name="operator_implementation" data-operator-implementation${readonly ? " disabled" : ""}>
        <option value="rules"${implementation === "rules" ? " selected" : ""}>规则式</option>
        <option value="resident"${implementation === "resident" ? " selected" : ""}>可对话 AI Resident</option>
      </select><small>两种方式共享同一个 wait-all、输入与输出协议。</small></label>
      <div data-operator-resident-fields${implementation === "resident" ? "" : " hidden"}>
        <label><span>${filter ? i18n().t("语义过滤 Prompt") : selector ? i18n().t("语义分流 Prompt") : i18n().t("语义归约 Prompt")}</span><textarea name="prompt" maxlength="12000"${implementation === "resident" ? " required" : ""}${readonly ? " disabled" : ""}>${esc(role?.prompt || "")}</textarea></label>
      </div>
      ${sharedBranches}
      <div data-operator-rules-fields${implementation === "rules" ? "" : " hidden"}>${rules}</div>
      <p class="programInspectorNote">组件身份与执行器分离：切换规则式 / Resident 式不会改变组件 ID、分支或连线。规则式没有 Chat；Resident 式使用一个真实、可对话的执行器。</p>
      <p class="programInspectorNote">协议：同一 Event 的本轮输入集合在开始时冻结。Filter 单出并只保留候选；Selector 从一个 Invoke 入口路由到多个具名输出；Reducer 把输入归约为一个 Packet。</p>`;
  }

  function syncOperatorFields(root) {
    const implementation = String(root?.querySelector("[data-operator-implementation]")?.value || "resident");
    const residentFields = root?.querySelector("[data-operator-resident-fields]");
    const rulesFields = root?.querySelector("[data-operator-rules-fields]");
    if (residentFields) residentFields.hidden = implementation !== "resident";
    if (rulesFields) rulesFields.hidden = implementation !== "rules";
    const prompt = residentFields?.querySelector('[name="prompt"]');
    if (prompt) prompt.required = implementation === "resident";
  }

  function bindOperatorFields(root, readonly, options = {}) {
    syncOperatorFields(root);
    bindSelectorBranches(root, readonly, options);
    root?.querySelector("[data-operator-implementation]")?.addEventListener("change", () => syncOperatorFields(root));
  }

  // The operator's configuration from its fields; keys the fields do not show are kept.
  function operatorConfigFromFields(root, component) {
    const config = JSON.parse(JSON.stringify(component?.config || {}));
    config.operator_implementation = value(root, "operator_implementation", "rules") || "rules";
    config.prompt = String(value(root, "prompt") || config.prompt || "").trim();
    if (component.kind === "filter") {
      const parsed = packetSelectionFromFields(root, "selection");
      if (parsed.error) return { config: null, error: parsed.error };
      config.selection = parsed.filter;
    } else if (component.kind === "selector") {
      const parsed = selectorBranchesFromFields(root);
      if (parsed.error) return { config: null, error: parsed.error };
      config.branches = parsed.branches;
    } else {
      config.reduce_strategy = value(root, "reduce_strategy", "join") || "join";
      config.reduce_separator = field(root, "reduce_separator") ? value(root, "reduce_separator") : "\n\n";
      config.output_key = (value(root, "output_key", "reduced") || "reduced").trim().toLowerCase();
      if (!/^[a-z][a-z0-9_-]{0,63}$/.test(config.output_key)) {
        return { config: null, error: i18n().t("Reducer 输出 key 必须以小写字母开头，并只包含小写字母、数字、_ 或 -。") };
      }
    }
    return { config, error: "" };
  }

  // ---------- Invoke wires ----------

  // Delivery policy fields of an Route wire. The Lineage inspector
  // adds its waiting-queue view through options.extraHtml.
  function deliveryFieldsHtml(wire, readonly, options = {}) {
    const config = wire?.config || {};
    const policy = String(config.unavailable_policy || "drop");
    const limit = i18n().html`<label data-message-limit${options.messageDelivery === false ? " hidden" : ""}><span>发送消息次数上限</span><input name="max_messages" type="number" min="0" step="1" placeholder="无限次" value="${esc(config.max_messages ?? "")}"${readonly || options.messageDelivery === false ? " disabled" : ""}><small>留空为无限次；0 表示停止发送。仅成功投递到 Resident 的消息计数，达到上限后丢弃新的消息。</small></label>`;
    return i18n().html`${limit}<label><span>接收方不可消费时</span><select name="unavailable_policy"${readonly ? " disabled" : ""}>
          <option value="drop"${policy === "drop" ? " selected" : ""}>直接丢弃（默认）</option>
          <option value="latest"${policy === "latest" ? " selected" : ""}>只保留最新一个</option>
          <option value="queue"${policy === "queue" ? " selected" : ""}>按顺序有限积压</option>
        </select><small data-event-policy-help></small></label>
        <label data-event-queue-limit${policy === "queue" ? "" : " hidden"}><span>最多积压数量</span><input name="queue_limit" type="number" min="1" max="100" step="1" value="${esc(config.queue_limit || 10)}"${readonly ? " disabled" : ""}><small>达到上限后，新到的 Packet 会被丢弃，已有顺序保持不变。</small></label>
        <label><span>等待多久后自动消失（秒）</span><input name="expires_after_seconds" type="number" min="0" max="604800" step="1" value="${esc(config.expires_after_seconds || 0)}"${readonly ? " disabled" : ""}><small>0 表示不自动过期；只作用于这条边上尚未投递的 Packet。</small></label>
        <label class="programInspectorCheckbox"><input name="include_envelope" type="checkbox"${config.include_envelope !== false ? " checked" : ""}${readonly ? " disabled" : ""}><span>沿这条 Route 投递时附带包信封</span></label>
        ${options.extraHtml || ""}<div data-invocation-mapping-slot>${options.mappingHtml || ""}</div>`;
  }

  function syncDeliveryFields(root) {
    const policySelect = field(root, "unavailable_policy");
    if (!policySelect) return;
    const queueField = root.querySelector("[data-event-queue-limit]");
    if (queueField) queueField.hidden = policySelect.value !== "queue";
    const help = root.querySelector("[data-event-policy-help]");
    if (help) {
      help.textContent = ({
        drop: i18n().t("目标当前不能消费 Invoke 时，这次触发直接消失。"),
        latest: i18n().t("边上至多等待一个触发；新触发会替换旧触发。"),
        queue: i18n().t("按到达顺序积压；目标每次恢复可消费时投递队首。"),
      })[policySelect.value] || "";
    }
  }

  function deliveryConfigFromFields(root) {
    return {
      include_envelope: Boolean(field(root, "include_envelope")?.checked),
      max_messages: field(root, "max_messages")?.disabled || value(root, "max_messages").trim() === "" ? null : Math.max(0, Math.trunc(Number(value(root, "max_messages")))),
      unavailable_policy: value(root, "unavailable_policy", "drop") || "drop",
      queue_limit: Math.max(1, Math.min(100, Math.trunc(Number(value(root, "queue_limit")) || 10))),
      expires_after_seconds: Math.max(0, Math.min(604800, Math.trunc(Number(value(root, "expires_after_seconds")) || 0))),
    };
  }

  global.PortalLineageElementFields = Object.freeze({
    isOperatorKind,
    operatorImplementation,
    invokeProfileKey,
    loopFieldsHtml,
    loopConfigFromFields,
    ensureInstructionCatalog,
    instructionOptionsHtml,
    effectFieldsHtml,
    effectConfigFromFields,
    bindEffectFields,
    recipientList,
    recipientState,
    recipientNames,
    recipientChipsHtml,
    hydrateRecipientChips,
    setRecipientChips,
    loadRecipientUsers,
    openRecipientEditor,
    packetSelectionEditorHtml,
    packetSelectionFromFields,
    selectorBranchesEditorHtml,
    selectorBranchCardHtml,
    selectorBranchesFromFields,
    syncSelectorBranches,
    bindSelectorBranches,
    operatorFieldsHtml,
    syncOperatorFields,
    bindOperatorFields,
    operatorConfigFromFields,
    deliveryFieldsHtml,
    syncDeliveryFields,
    deliveryConfigFromFields,
  });
})(window);
