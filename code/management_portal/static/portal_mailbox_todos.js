(function () {
  "use strict";
  if (window.PortalMailboxTodos) return;

  function create(container, { onChange = () => {} } = {}) {
    const controller = new AbortController();
    const { signal } = controller;
    const t = source => window.PortalI18n.t(source);
    const state = { items: [], loaded: false, visible: false, active: false, busy: false, editing: null, saving: null, drag: null, revision: 0, request: null, closed: new Set(), pendingCompletion: new Map() };
    const today = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Hong_Kong", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
    container.innerHTML = window.PortalI18n.markup('<form class="portalMailboxTodoComposer" data-mailbox-todo-form>' +
      '<textarea rows="2" data-mailbox-todo-text aria-label="待办内容" placeholder="新增待办（最多 100 字）" required></textarea>' +
      '<div class="portalMailboxTodoFormActions"><input type="date" data-mailbox-todo-date aria-label="待办日期" required>' +
      '<span data-mailbox-todo-length aria-live="polite">0/100</span><button type="submit" data-mailbox-todo-save>添加</button></div>' +
      '</form><div class="portalMailboxTodoError" data-mailbox-todo-error role="alert" hidden></div>' +
      '<div class="portalMailboxTodoGroups" data-mailbox-todo-groups></div>');
    const form = container.querySelector("form");
    const text = container.querySelector("[data-mailbox-todo-text]");
    const date = container.querySelector("[data-mailbox-todo-date]");
    const save = container.querySelector("[data-mailbox-todo-save]");
    const groups = container.querySelector("[data-mailbox-todo-groups]");
    const errorBox = container.querySelector("[data-mailbox-todo-error]");
    date.value = today();

    function error(value = "") { errorBox.textContent = value; errorBox.hidden = !value; }
    function validate() {
      const length = Array.from(text.value.trim()).length;
      container.querySelector("[data-mailbox-todo-length]").textContent = length + "/100";
      text.setCustomValidity(length > 100 ? t("待办最多 100 字") : "");
      save.disabled = state.busy || length === 0 || length > 100;
    }
    function resetForm() { text.value = ""; date.value = today(); validate(); }
    function snapshot() {
      return { count: state.items.length, pendingCount: state.items.filter(item => !item.completed).length, loaded: state.loaded };
    }
    const sorted = day => state.items.filter(item => item.date === day).sort((a, b) => a.position - b.position || a.todo_id.localeCompare(b.todo_id));
    function render() {
      // Keep the editor and pointer-capture node intact during background reads.
      if (state.editing || state.drag) { onChange(snapshot()); return; }
      const oldGroups = new Map([...groups.querySelectorAll("[data-mailbox-todo-day]")].map(node => [node.dataset.mailboxTodoDay, node]));
      const oldRows = new Map([...groups.querySelectorAll("[data-mailbox-todo-id]")].map(node => [node.dataset.mailboxTodoId, node]));
      const dates = [...new Set(state.items.map(item => item.date))].sort().reverse();
      groups.querySelector(".portalAgentInboxEmpty")?.remove();
      dates.forEach((day, dayIndex) => {
        let group = oldGroups.get(day);
        if (!group) {
          group = document.createElement("details");
          group.className = "portalMailboxTodoGroup";
          group.dataset.mailboxTodoDay = day;
          group.innerHTML = "<summary><time></time><small></small></summary>";
          group.open = !state.closed.has(day);
        }
        if (groups.children[dayIndex] !== group) groups.insertBefore(group, groups.children[dayIndex] || null);
        group.querySelector("time").textContent = day.slice(2);
        group.querySelector("time").dateTime = day;
        const items = sorted(day);
        group.querySelector("small").textContent = items.filter(item => !item.completed).length + "/" + items.length;
        items.forEach((item, index) => {
          let row = oldRows.get(item.todo_id);
          if (!row) {
            row = document.createElement("div");
            row.className = "portalMailboxTodoRow";
            row.dataset.mailboxTodoId = item.todo_id;
            row.innerHTML = '<span class="portalMailboxTodoGrip" data-mailbox-todo-grip tabindex="0" role="button">⠿</span>' +
              '<input type="checkbox" data-mailbox-todo-check><div class="portalMailboxTodoBody" data-mailbox-todo-body></div>' +
              '<div class="portalMailboxTodoActions"><button type="button" data-mailbox-todo-action="delete"><span data-portal-icon="trash" aria-hidden="true"></span></button></div>';
            const grip = row.querySelector("[data-mailbox-todo-grip]");
            grip.setAttribute("aria-label", t("拖拽排序；Alt+↑/↓ 调序"));
            grip.title = t("拖拽排序；Alt+↑/↓ 调序");
            row.querySelector("[data-mailbox-todo-action]").setAttribute("aria-label", t("删除待办"));
            row.querySelector("[data-mailbox-todo-action]").title = t("删除待办");
          }
          row.classList.toggle("is-completed", item.completed);
          row.style.setProperty("--todo-indent", Number(item.indent) || 0);
          row.querySelector("input").checked = state.pendingCompletion.get(item.todo_id) ?? item.completed;
          row.querySelector("input").disabled = state.busy || state.pendingCompletion.has(item.todo_id);
          row.querySelector("input").setAttribute("aria-label", t("完成待办") + ": " + item.text);
          row.querySelector("[data-mailbox-todo-action]").disabled = state.busy;
          const body = row.querySelector("[data-mailbox-todo-body]");
          if (!body.querySelector("[data-mailbox-todo-content]")) body.innerHTML = '<button type="button" class="portalMailboxTodoContent" data-mailbox-todo-content></button>';
          const content = body.querySelector("button");
          content.textContent = item.text;
          content.title = t("点击编辑；Tab 缩进，Shift+Tab 取消缩进；点击别处保存");
          content.disabled = state.busy;
          if (group.children[index + 1] !== row) group.insertBefore(row, group.children[index + 1] || null);
        });
      });
      oldRows.forEach((row, id) => { if (!state.items.some(item => item.todo_id === id)) row.remove(); });
      oldGroups.forEach((group, day) => { if (!dates.includes(day)) group.remove(); });
      if (!dates.length && errorBox.hidden) {
        const empty = document.createElement("div");
        empty.className = "portalAgentInboxEmpty";
        empty.textContent = t(state.loaded ? "暂无待办，在上方添加" : "正在读取待办…");
        groups.appendChild(empty);
      }
      window.PortalUIPrimitives.mountIcons(groups);
      onChange(snapshot());
    }
    async function refresh(force = false) {
      if (signal.aborted || state.busy || (!force && (state.editing || state.drag))) return;
      if (state.request) return state.request;
      const revision = state.revision;
      const request = window.PortalIdentity.userId().then(userId => window.PortalInstructionClient.execute("ListMailboxTodos", {
        user_id: userId,
      })).then(payload => {
        if (signal.aborted || revision !== state.revision) return;
        state.items = payload.items || [];
        state.loaded = true;
        error(); render();
      }).catch(reason => {
        if (!signal.aborted && revision === state.revision) { error(reason.message || t("读取待办失败")); render(); }
      }).finally(() => { if (state.request === request) state.request = null; });
      state.request = request;
      return request;
    }
    async function mutate(instruction, args) {
      if (state.busy) return false;
      state.busy = true;
      const revision = ++state.revision;
      error(); validate(); render();
      try {
        const result = await window.PortalInstructionClient.execute(instruction, args, {
          idempotencyKey: "mailbox-todo:" + window.PortalUIPrimitives.randomUUID(),
        });
        if (signal.aborted || revision !== state.revision) return false;
        if (result.item?.text) state.items = state.items.filter(item => item.todo_id !== result.item.todo_id).concat(result.item);
        else if (instruction === "DeleteMailboxTodo") state.items = state.items.filter(item => item.todo_id !== args.todo_id);
        state.request = null;
        state.busy = false;
        await refresh(true);
        return !signal.aborted && revision === state.revision;
      } catch (reason) {
        if (!signal.aborted && revision === state.revision) error(reason.message || t("保存待办失败"));
        return false;
      } finally {
        if (!signal.aborted && revision === state.revision) { state.busy = false; validate(); render(); }
      }
    }
    function resizeEditor(editor) { editor.style.height = "auto"; editor.style.height = editor.scrollHeight + "px"; }
    async function beginEdit(id) {
      if (state.editing?.id === id || !(await finishEdit()) || state.busy) return;
      const item = state.items.find(value => value.todo_id === id);
      const row = [...groups.querySelectorAll("[data-mailbox-todo-id]")].find(node => node.dataset.mailboxTodoId === id);
      if (!item || !row) return;
      state.revision += 1;
      state.request = null;
      const original = { text: item.text, date: item.date, indent: item.indent || 0 };
      state.editing = { id, row, original, ...original };
      const body = row.querySelector("[data-mailbox-todo-body]");
      body.innerHTML = window.PortalI18n.markup('<textarea class="portalMailboxTodoEditor" data-mailbox-todo-editor aria-label="编辑待办" rows="1"></textarea>' +
        '<input type="date" data-mailbox-todo-edit-date aria-label="待办日期" required><small class="portalMailboxTodoEditHint">Tab / Shift+Tab 缩进 · 点击别处保存</small>');
      const editor = body.querySelector("textarea");
      editor.value = item.text;
      body.querySelector("input").value = item.date;
      editor.focus(); editor.setSelectionRange(editor.value.length, editor.value.length); resizeEditor(editor);
    }
    function finishEdit() {
      if (state.saving) return state.saving;
      const draft = state.editing;
      if (!draft) return Promise.resolve(true);
      const length = Array.from(draft.text.trim()).length;
      if (!length || length > 100 || !draft.row.querySelector("[data-mailbox-todo-edit-date]").checkValidity()) {
        error(t(!length ? "待办不能为空" : length > 100 ? "待办最多 100 字" : "请选择有效日期"));
        return Promise.resolve(false);
      }
      const changes = Object.fromEntries(Object.entries({ text: draft.text.trim(), date: draft.date, indent: draft.indent }).filter(([key, value]) => value !== draft.original[key]));
      if (!Object.keys(changes).length) { state.editing = null; render(); return Promise.resolve(true); }
      draft.row.querySelector("textarea").readOnly = true;
      draft.row.querySelector("[data-mailbox-todo-edit-date]").readOnly = true;
      const pending = mutate("UpdateMailboxTodo", { todo_id: draft.id, changes }).then(saved => {
        if (state.editing !== draft) return false;
        if (saved) state.editing = null;
        else { draft.row.querySelector("textarea").readOnly = false; draft.row.querySelector("[data-mailbox-todo-edit-date]").readOnly = false; }
        render(); return saved;
      }).finally(() => { if (state.saving === pending) state.saving = null; });
      state.saving = pending;
      return pending;
    }
    text.addEventListener("input", validate, { signal });
    form.addEventListener("submit", async event => {
      event.preventDefault(); validate();
      if (save.disabled || !form.reportValidity() || !(await finishEdit())) return;
      // A User adds to their own list, named explicitly (user_id is required).
      const { username = "" } = await window.PortalIdentity.load().catch(() => ({}));
      const draft = { user_id: username, text: text.value.trim(), date: date.value };
      if (await mutate("CreateMailboxTodo", draft) && text.value.trim() === draft.text && date.value === draft.date) resetForm();
    }, { signal });
    groups.addEventListener("toggle", event => {
      const group = event.target.closest("[data-mailbox-todo-day]");
      if (!group || !group.isConnected) return;
      if (group.open) state.closed.delete(group.dataset.mailboxTodoDay);
      else state.closed.add(group.dataset.mailboxTodoDay);
    }, { signal, capture: true });
    groups.addEventListener("input", event => {
      if (!state.editing || state.saving) return;
      if (event.target.matches("[data-mailbox-todo-editor]")) { state.editing.text = event.target.value; resizeEditor(event.target); }
      if (event.target.matches("[data-mailbox-todo-edit-date]")) state.editing.date = event.target.value;
    }, { signal });
    groups.addEventListener("keydown", event => {
      const draft = state.editing;
      if (event.target.matches("[data-mailbox-todo-editor]") && draft && !state.saving && !event.isComposing) {
        if (event.key === "Tab") {
          event.preventDefault();
          draft.indent = Math.max(0, Math.min(4, draft.indent + (event.shiftKey ? -1 : 1)));
          draft.row.style.setProperty("--todo-indent", draft.indent);
        } else if (event.key === "Escape") {
          event.preventDefault(); event.stopPropagation(); state.editing = null; error(); render();
        } else if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); void finishEdit(); }
      } else if (event.target.matches("[data-mailbox-todo-grip]") && event.altKey && ["ArrowUp", "ArrowDown"].includes(event.key)) {
        event.preventDefault();
        const id = event.target.closest("[data-mailbox-todo-id]").dataset.mailboxTodoId;
        void finishEdit().then(saved => saved && mutate("MoveMailboxTodo", { todo_id: id, direction: event.key === "ArrowUp" ? "up" : "down" }));
      }
    }, { signal });
    let pointerFocus = false;
    document.addEventListener("pointerdown", () => { pointerFocus = true; }, { signal, capture: true });
    document.addEventListener("pointerup", () => { pointerFocus = false; }, { signal, capture: true });
    document.addEventListener("pointercancel", () => {
      pointerFocus = false;
      if (state.editing && !state.editing.row.contains(document.activeElement)) void finishEdit();
    }, { signal, capture: true });
    groups.addEventListener("focusout", event => {
      // For pointer focus changes, save on click, after its target has been activated.
      // Otherwise collapsing an editor can move another checkbox before pointerup.
      if (pointerFocus) return;
      // activeElement can still be <body> between blur and the next focus event.
      if (state.editing?.row.contains(event.relatedTarget)) return;
      queueMicrotask(() => { if (state.editing && !state.editing.row.contains(document.activeElement)) void finishEdit(); });
    }, { signal });
    document.addEventListener("click", event => {
      // The clicked text button has just been replaced by its inline editor.
      if (state.editing && !event.composedPath().includes(state.editing.row)) void finishEdit();
    }, { signal });
    groups.addEventListener("change", async event => {
      if (!event.target.matches("[data-mailbox-todo-check]")) return;
      const todo_id = event.target.closest("[data-mailbox-todo-id]").dataset.mailboxTodoId;
      const completed = event.target.checked;
      const pending = state.pendingCompletion;
      pending.set(todo_id, completed);
      event.target.disabled = true;
      try {
        if (await finishEdit() && state.pendingCompletion === pending) await mutate("UpdateMailboxTodo", { todo_id, changes: { completed } });
      } finally {
        pending.delete(todo_id);
        if (state.pendingCompletion === pending && !signal.aborted) {
          // render() preserves an unsaved editor, so also restore its checkbox here.
          event.target.checked = Boolean(state.items.find(item => item.todo_id === todo_id)?.completed);
          event.target.disabled = state.busy;
          render();
        }
      }
    }, { signal });
    groups.addEventListener("click", async event => {
      const row = event.target.closest("[data-mailbox-todo-id]");
      if (!row) return;
      const id = row.dataset.mailboxTodoId;
      if (event.target.closest("[data-mailbox-todo-content]")) return void beginEdit(id);
      if (event.target.closest("[data-mailbox-todo-action=delete]") && await finishEdit()) await mutate("DeleteMailboxTodo", { todo_id: id });
    }, { signal });
    function clearDrop() { groups.querySelectorAll(".is-drop-before,.is-drop-after").forEach(row => row.classList.remove("is-drop-before", "is-drop-after")); }
    groups.addEventListener("pointerdown", event => {
      const grip = event.target.closest("[data-mailbox-todo-grip]");
      if (!grip || event.button !== 0 || state.busy) return;
      event.preventDefault();
      state.drag = { id: grip.closest("[data-mailbox-todo-id]").dataset.mailboxTodoId, pointer: event.pointerId, x: event.clientX, y: event.clientY, grip, active: false, placement: null };
      grip.setPointerCapture(event.pointerId);
    }, { signal });
    groups.addEventListener("pointermove", event => {
      const drag = state.drag;
      if (!drag || drag.pointer !== event.pointerId) return;
      if (!drag.active && Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 5) return;
      drag.active = true;
      drag.grip.closest("[data-mailbox-todo-id]").classList.add("is-dragging");
      event.preventDefault(); clearDrop(); drag.placement = null;
      const hit = document.elementFromPoint(event.clientX, event.clientY);
      if (!groups.contains(hit)) return;
      let target = hit.closest("[data-mailbox-todo-id]");
      let after;
      if (target) { const rect = target.getBoundingClientRect(); after = event.clientY > rect.top + rect.height / 2; }
      else { target = hit.closest("[data-mailbox-todo-day]")?.querySelector("[data-mailbox-todo-id]:last-child"); after = true; }
      if (!target || target.dataset.mailboxTodoId === drag.id) return;
      drag.placement = { [after ? "after_todo_id" : "before_todo_id"]: target.dataset.mailboxTodoId };
      target.classList.add(after ? "is-drop-after" : "is-drop-before");
      const rect = groups.getBoundingClientRect();
      if (event.clientY < rect.top + 28) groups.scrollTop -= 14;
      if (event.clientY > rect.bottom - 28) groups.scrollTop += 14;
    }, { signal });
    async function endDrag(event) {
      const drag = state.drag;
      if (!drag || drag.pointer !== event.pointerId) return;
      state.drag = null; clearDrop();
      drag.grip.closest("[data-mailbox-todo-id]").classList.remove("is-dragging");
      if (drag.grip.hasPointerCapture(event.pointerId)) drag.grip.releasePointerCapture(event.pointerId);
      if (event.type === "pointerup" && drag.active && drag.placement && await finishEdit()) await mutate("MoveMailboxTodo", { todo_id: drag.id, ...drag.placement });
      render();
    }
    groups.addEventListener("pointerup", endDrag, { signal });
    groups.addEventListener("pointercancel", endDrag, { signal });
    groups.addEventListener("lostpointercapture", endDrag, { signal });
    // The tab count is visible whenever Mailbox is open, even on the messages tab.
    const refreshVisible = () => { if (state.visible && !document.hidden) void refresh(); };
    window.addEventListener("focus", refreshVisible, { signal });
    document.addEventListener("visibilitychange", refreshVisible, { signal });
    window.addEventListener("portal:identity-changed", () => {
      state.revision += 1;
      Object.assign(state, { request: null, busy: false, items: [], loaded: false, editing: null, saving: null, drag: null, pendingCompletion: new Map() });
      state.closed.clear(); resetForm(); error(); render(); refreshVisible();
    }, { signal });
    const timer = window.setInterval(refreshVisible, 15000);
    validate(); render();
    return Object.freeze({
      refresh, snapshot,
      setView({ visible, active }) {
        const refreshNeeded = (visible && !state.visible) || (active && !state.active);
        const finish = state.active && !active;
        state.visible = visible;
        state.active = active;
        if (refreshNeeded) refreshVisible();
        if (finish) void finishEdit();
      },
      destroy() { controller.abort(); window.clearInterval(timer); container.replaceChildren(); },
    });
  }
  window.PortalMailboxTodos = Object.freeze({ create });
}());
