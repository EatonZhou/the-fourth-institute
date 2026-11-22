(function () {
  "use strict";
  const root = document.getElementById("residentResults");
  if (!root) return;
  // This page owns its copy; no shared translation registry or resource HTML.
  const words = {
    title: ["项目成果", "Project results"], project: ["项目", "Project"], author: ["作者", "Author"],
    filter: ["筛选", "Filter"], create: ["新建成果", "New result"], empty: ["没有可见成果。", "No visible results."],
    loading: ["正在读取…", "Loading…"], back: ["返回列表", "Back to results"], prev: ["上一页", "Previous"], next: ["下一页", "Next"],
    draft: ["草稿", "Draft"], published: ["已发布", "Published"], withdrawn: ["已撤回", "Withdrawn"],
    visibility: ["草稿仅本人可见；发布后在项目内共享。", "Drafts are private to the author; published results are shared in the project."],
    status: ["发布状态", "Publication status"], validation: ["作者验证声明", "Author’s validation statement"],
    validationHint: ["发布状态不表示验证通过。以下内容由作者提供。", "Publication status does not certify validation. The following statement is supplied by the author."],
    missing: ["未提供", "Not provided"], summary: ["摘要", "Summary"], body: ["正文", "Body"], limitations: ["限制", "Limitations"],
    name: ["标题", "Title"], documents: ["文档原文", "Source documents"], references: ["来源引用", "References"],
    raw: ["查看本版本原文", "View this version’s source"], revision: ["版本", "Version"], latest: ["查看最新版本", "View latest version"],
    open: ["打开版本", "Open version"], fixed: ["固定版本链接", "Permanent version link"], edit: ["编辑为新版本", "Edit as a new version"],
    historic: ["正在查看历史版本；编辑请先打开最新版本。", "You are viewing a historical version. Open the latest version to edit."],
    withdrawWarning: ["此成果已撤回。历史内容仅供追溯，请勿作为当前结论使用。", "This result has been withdrawn. Historical content is retained for reference, not as a current conclusion."],
    cancel: ["取消", "Cancel"], saveDraft: ["保存草稿", "Save draft"], publish: ["发布到项目", "Publish to project"],
    save: ["保存新版本", "Save new version"], withdraw: ["撤回成果", "Withdraw result"], reason: ["撤回原因", "Reason for withdrawal"],
    withdrawHint: ["撤回后会显示警示，已保存的历史版本仍会保留。", "Withdrawal displays a warning and preserves saved historical versions."],
    confirmWithdraw: ["确认撤回", "Confirm withdrawal"], contact: ["联系作者", "Contact author"], message: ["留言", "Message"],
    send: ["发送给原作者", "Send to original author"], sent: ["已发送给原作者。", "Sent to the original author."],
    contactHint: ["提交后向原作者发送留言，并附上当前固定版本引用。", "Submitting sends your message to the original author with a reference to this fixed version."],
    addDocument: ["添加文档", "Add document"], addReference: ["添加引用", "Add reference"], remove: ["移除", "Remove"],
    fileName: ["文件名", "File name"], content: ["原文", "Source content"], address: ["资源地址", "Resource address"],
    sourceVersion: ["来源版本（可选）", "Source version (optional)"], hash: ["SHA-256（可选）", "SHA-256 (optional)"],
    projectRequired: ["请先填写项目，再新建成果。", "Enter a project before creating a result."],
    conflict: ["已有更新版本。你的输入已保留，请打开最新版本后核对合并。", "A newer version exists. Your input is preserved; open the latest version to review and merge."],
    unavailable: ["成果不存在或无权访问。", "The result does not exist or is not accessible."],
    invalidVersion: ["版本必须为正整数。", "Version must be a positive integer."],
    invalidReceipt: ["保存已返回，但缺少成果编号或版本，请返回列表核对。", "Save returned without a result ID or version. Check the results list."],
    retry: ["重试", "Retry"], saving: ["正在提交…", "Submitting…"], total: ["条成果", "results"],
  };
  const t = key => words[key]?.[window.PortalI18n.locale().startsWith("en") ? 1 : 0] || key;
  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  }
  function labelText(node, key) { node.dataset.resultsText = key; node.textContent = t(key); return node; }
  const textEl = (tag, key, css) => labelText(el(tag, undefined, css), key);
  function button(key, action, primary = false) {
    const node = textEl("button", key, `portalButton${primary ? " is-primary" : ""}`);
    node.type = "button";
    if (action) node.addEventListener("click", action);
    return node;
  }
  function link(key, url) { const node = textEl("a", key, "portalButton is-compact"); node.href = url; return node; }
  function field(parent, key, name, value = "", type = "input") {
    const label = el("label"); label.append(textEl("span", key));
    const input = el(type); input.name = name; input.value = value ?? "";
    label.append(input); parent.append(label); return input;
  }
  const params = () => new URLSearchParams(location.search);
  function href(values) {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(values)) if (value !== undefined && value !== null && value !== "") query.set(key, value);
    return `/results?${query}`;
  }
  function detailHref(record, revision) { return href({project_id: record.project_id, id: record.resource_id, revision}); }
  function openSaved(result, project, expectedId) {
    // RegisterResource and UpdateResource return ISA receipts, not GET details.
    const resource = result.resource?.record || result.resource;
    const id = result.resource_id || resource?.resource_id || expectedId;
    if (!id || (expectedId && id !== expectedId)) throw new Error(t("invalidReceipt"));
    const revision = resource?.revision ?? resource?.attributes?.revision ?? result.version;
    // An opaque version is not a revision number. GET resolves and pins the
    // current snapshot before rendering, as it does for any unversioned link.
    navigate(detailHref({resource_id: id, project_id: project}, isRevision(revision) ? revision : undefined));
  }
  function navigate(url, replace = false) { history[replace ? "replaceState" : "pushState"]({}, "", url); load(); }
  function navLink(key, url) {
    const node = link(key, url);
    node.addEventListener("click", event => {
      if (event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault(); navigate(url);
    });
    return node;
  }
  function showError(target, error) {
    target.className = "resultsError";
    target.setAttribute("role", "alert");
    target.textContent = error.status === 409 ? t("conflict") : [403, 404].includes(error.status) ? t("unavailable") : error.message;
  }
  let generation = 0, controller;
  const api = window.PortalAPI;
  const isRevision = value => /^[1-9]\d*$/.test(String(value)) && Number.isSafeInteger(Number(value));

  async function load() {
    const ticket = ++generation;
    controller?.abort(); controller = new AbortController();
    const query = params();
    root.replaceChildren(textEl("h1", "title"));
    document.title = t("title");
    const state = textEl("p", "loading", "resultsHint"); state.setAttribute("role", "status"); root.append(state);
    try {
      if (query.get("id")) {
        const id = query.get("id"), revision = query.get("revision");
        if (revision !== null && !isRevision(revision)) throw new Error(t("invalidVersion"));
        const data = await api.get(`/api/results/${encodeURIComponent(id)}${revision ? `?revision=${revision}` : ""}`, {signal: controller.signal});
        if (ticket !== generation) return;
        if (!isRevision(data.revision)) throw new Error(t("invalidVersion"));
        // Pin even an unversioned entry before exposing contact or source links.
        query.set("revision", data.revision);
        history.replaceState({}, "", `/results?${query}`);
        state.remove(); renderDetail(data, ticket);
      } else {
        renderFilters(query);
        const search = new URLSearchParams({project_id: query.get("project_id") || "", author_id: query.get("author_id") || "", offset: String(Math.max(0, parseInt(query.get("offset"), 10) || 0))});
        const data = await api.get(`/api/results?${search}`, {signal: controller.signal});
        if (ticket !== generation) return;
        state.remove(); renderList(data, query);
      }
    } catch (error) {
      if (ticket !== generation) return;
      delete state.dataset.resultsText; showError(state, error);
      root.append(button("retry", load));
    }
  }
  function renderFilters(query) {
    const form = el("form", undefined, "resultsToolbar");
    const project = field(form, "project", "project_id", query.get("project_id"));
    const author = field(form, "author", "author_id", query.get("author_id"));
    const submit = button("filter"); submit.type = "submit";
    const notice = el("p", "", "resultsHint"); notice.setAttribute("role", "status");
    form.append(submit, button("create", () => {
      if (!project.value.trim()) { notice.textContent = t("projectRequired"); project.focus(); return; }
      // New results use the entered project, never the author filter.
      ++generation; controller?.abort(); root.replaceChildren(textEl("h1", "create"));
      renderEditor(null, project.value.trim(), generation);
    }, true));
    form.addEventListener("submit", event => { event.preventDefault(); navigate(href({project_id: project.value.trim(), author_id: author.value.trim()})); });
    root.append(form, textEl("p", "visibility", "resultsHint"), notice);
  }
  function renderList(data, query) {
    root.append(el("p", `${data.total} ${t("total")}`, "resultsHint"));
    if (!data.items.length) root.append(textEl("p", "empty"));
    for (const record of data.items) {
      const card = el("article", undefined, "resultsCard");
      const title = el("h2"), anchor = navLink("name", detailHref(record, record.revision));
      delete anchor.dataset.resultsText; anchor.textContent = record.title; title.append(anchor);
      card.append(title, el("p", record.summary), textEl("span", record.status, "resultsStatus"),
        el("p", `${record.author_id} · ${record.project_id} · v${record.revision} · ${record.updated_at}`, "resultsMeta"));
      root.append(card);
    }
    const pager = el("nav", undefined, "resultsActions");
    const offset = Number(data.offset), limit = Number(data.limit);
    if (limit > 0 && offset > 0) pager.append(navLink("prev", href({project_id: query.get("project_id"), author_id: query.get("author_id"), offset: Math.max(0, offset - limit)})));
    if (limit > 0 && offset + limit < data.total) pager.append(navLink("next", href({project_id: query.get("project_id"), author_id: query.get("author_id"), offset: offset + limit})));
    root.append(pager);
  }
  function renderDetail(data, ticket) {
    const {record, snapshot, revision, latest_revision: latest} = data;
    root.append(navLink("back", href({project_id: record.project_id, author_id: params().get("author_id")})));
    const panel = el("article", undefined, "resultsPanel");
    panel.append(el("h2", snapshot.title), el("p", `${record.author_id} · ${record.project_id} · ${record.resource_id}`, "resultsMeta"));
    const status = el("p"); status.append(textEl("span", "status"), el("span", ": "), textEl("span", record.status, "resultsStatus")); panel.append(status);
    if (record.status === "withdrawn") {
      panel.append(textEl("p", "withdrawWarning", "resultsWarning"));
      if (record.withdrawal_reason) panel.append(el("p", record.withdrawal_reason));
    }
    const versions = el("form", undefined, "resultsActions resultsVersion");
    const input = field(versions, "revision", "revision", revision); input.type = "number"; input.min = "1"; input.max = String(latest); input.required = true;
    const open = button("open"); open.type = "submit";
    versions.append(open, navLink("latest", detailHref(record)), link("fixed", detailHref(record, revision)));
    versions.addEventListener("submit", event => { event.preventDefault(); if (isRevision(input.value)) navigate(detailHref(record, input.value)); });
    panel.append(versions);
    if (Number(revision) !== Number(latest)) panel.append(textEl("p", "historic", "resultsHint"));
    for (const key of ["summary", "body", "validation", "limitations"]) {
      const section = el("section"); section.append(textEl("h3", key));
      if (key === "validation") section.append(textEl("p", "validationHint", "resultsHint"));
      section.append(snapshot[key] ? el("pre", snapshot[key]) : textEl("p", "missing", "resultsHint")); panel.append(section);
    }
    const docs = el("section"); docs.append(textEl("h3", "documents"));
    for (const doc of snapshot.documents || []) {
      const details = el("details"); details.append(el("summary", doc.name), el("p", `SHA-256: ${doc.sha256 || "—"}`, "resultsMeta"), el("pre", doc.content)); docs.append(details);
    }
    panel.append(docs);
    const refs = el("section"); refs.append(textEl("h3", "references"));
    for (const ref of snapshot.references || []) refs.append(el("pre", [ref.address, ref.version === undefined ? "" : `version: ${ref.version}`, ref.sha256 ? `SHA-256: ${ref.sha256}` : ""].filter(Boolean).join("\n")));
    panel.append(refs);
    const raw = el("details"); raw.append(textEl("summary", "raw"), el("pre", JSON.stringify({revision, snapshot}, null, 2))); panel.append(raw);
    root.append(panel);
    if (record.can_edit && Number(revision) === Number(latest)) {
      root.append(button("edit", () => { ++generation; root.replaceChildren(textEl("h1", "edit")); renderEditor(data, record.project_id, generation); }));
      if (record.status !== "withdrawn") renderWithdrawal(data, ticket);
    }
    renderContact(data, ticket);
  }
  // One operation key per exact payload. An uncertain transport failure can be
  // retried without creating another result or sending the same message twice.
  function operation() {
    let previous, id;
    return payload => {
      const next = JSON.stringify(payload);
      if (next !== previous) {
        previous = next;
        id = Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, "0")).join("");
      }
      return id;
    };
  }
  function mutationForm(parent, ticket, action) {
    const form = el("form", undefined, "resultsPanel"), fields = el("fieldset"), error = el("p");
    error.setAttribute("aria-live", "polite");
    form.append(fields, error); parent.append(form);
    let busy = false;
    form.addEventListener("submit", async event => {
      event.preventDefault(); if (busy) return;
      busy = true; fields.disabled = true; error.textContent = t("saving"); error.className = "resultsHint";
      try { await action(event.submitter, fields, error); }
      catch (failure) { if (ticket === generation && form.isConnected) showError(error, failure); }
      finally { busy = false; fields.disabled = false; }
    });
    return {form, fields, error};
  }
  function renderEditor(data, project, ticket) {
    const snapshot = data?.snapshot || {};
    const record = data?.record;
    const operationId = operation();
    const inputs = {};
    const {fields} = mutationForm(root, ticket, async submitter => {
      const payload = Object.fromEntries(Object.entries(inputs).map(([key, input]) => [key, input.value]));
      payload.documents = [...documents.children].map(row => ({name: row.querySelector('[name="name"]').value, content: row.querySelector('[name="content"]').value}));
      payload.references = [...references.children].map(row => {
        const value = {address: row.querySelector('[name="address"]').value};
        for (const key of ["version", "sha256"]) { const content = row.querySelector(`[name="${key}"]`).value; if (content) value[key] = content; }
        return value;
      });
      payload.status = record ? status.value : submitter?.value || "draft";
      if (!record) payload.project_id = project;
      const result = await api.request(record ? `/api/results/${encodeURIComponent(record.resource_id)}/update` : "/api/results", {
        method: "POST",
        body: record ? {expected_revision: data.revision, payload} : {operation_id: operationId(payload), payload},
      });
      if (ticket !== generation) return;
      openSaved(result, project, record?.resource_id);
    });
    fields.append(el("p", `${t("project")}: ${project}`, "resultsMeta"), textEl("p", "visibility", "resultsHint"));
    for (const key of ["title", "summary", "body", "validation", "limitations"]) inputs[key] = field(fields, key === "title" ? "name" : key, key, snapshot[key], key === "title" ? "input" : "textarea");
    inputs.title.required = true;
    fields.append(textEl("h3", "documents"));
    const documents = el("div"); fields.append(documents);
    function addDocument(doc = {}) {
      const row = el("div", undefined, "resultsDocument");
      field(row, "fileName", "name", doc.name).required = true;
      field(row, "content", "content", doc.content, "textarea");
      row.append(button("remove", () => row.remove())); documents.append(row);
    }
    (snapshot.documents || []).forEach(addDocument); fields.append(button("addDocument", () => addDocument()));
    fields.append(textEl("h3", "references"));
    const references = el("div"); fields.append(references);
    function addReference(ref = {}) {
      const row = el("div", undefined, "resultsReference");
      field(row, "address", "address", ref.address).required = true;
      field(row, "sourceVersion", "version", ref.version);
      const hash = field(row, "hash", "sha256", ref.sha256); hash.pattern = "[a-fA-F0-9]{64}";
      row.append(button("remove", () => row.remove())); references.append(row);
    }
    (snapshot.references || []).forEach(addReference); fields.append(button("addReference", () => addReference()));
    let status;
    if (record) {
      status = field(fields, "status", "status", "", "select");
      // Withdrawal has its own reason/confirmation form.
      for (const key of record.status === "withdrawn" ? ["withdrawn", "draft", "published"] : ["draft", "published"]) {
        const option = textEl("option", key); option.value = key; status.append(option);
      }
      status.value = record.status;
    }
    const actions = el("div", undefined, "resultsActions");
    for (const [key, value] of record ? [["save", ""]] : [["saveDraft", "draft"], ["publish", "published"]]) {
      const submit = button(key, null, true); submit.type = "submit"; submit.value = value; actions.append(submit);
    }
    if (record) {
      const latest = link("latest", detailHref(record)); latest.target = "_blank"; latest.rel = "noopener";
      actions.append(latest);
    }
    actions.append(button("cancel", load)); fields.append(actions);
  }
  function renderWithdrawal(data, ticket) {
    const details = el("details"); details.append(textEl("summary", "withdraw")); root.append(details);
    const {fields} = mutationForm(details, ticket, async () => {
      if (!reason.value.trim()) { reason.focus(); throw new Error(t("reason")); }
      const result = await api.request(`/api/results/${encodeURIComponent(data.record.resource_id)}/update`, {method: "POST", body: {
        expected_revision: data.revision, payload: {status: "withdrawn", withdrawal_reason: reason.value.trim()},
      }});
      if (ticket === generation) openSaved(result, data.record.project_id, data.record.resource_id);
    });
    fields.append(textEl("p", "withdrawHint", "resultsWarning"));
    const reason = field(fields, "reason", "withdrawal_reason", "", "textarea"); reason.required = true;
    const submit = button("confirmWithdraw"); submit.type = "submit"; fields.append(submit);
  }
  function renderContact(data, ticket) {
    const details = el("details"); details.append(textEl("summary", "contact")); root.append(details);
    const operationId = operation();
    const {fields, form} = mutationForm(details, ticket, async (_submitter, _fields, notice) => {
      const payload = {revision: data.revision, message: message.value.trim()};
      if (!payload.message) { message.focus(); throw new Error(t("message")); }
      await api.post(`/api/results/${encodeURIComponent(data.record.resource_id)}/contact`, {...payload, operation_id: operationId(payload)});
      if (ticket !== generation || !form.isConnected) return;
      notice.textContent = t("sent"); fields.remove();
    });
    fields.append(textEl("p", "contactHint", "resultsHint"), el("p", `${data.record.author_id} · ${data.record.resource_id} · v${data.revision}`, "resultsMeta"));
    const message = field(fields, "message", "message", "", "textarea"); message.required = true; message.maxLength = 8000;
    const submit = button("send", null, true); submit.type = "submit"; fields.append(submit);
  }
  window.addEventListener("popstate", load);
  window.addEventListener("portal:language-changed", () => {
    // Translate labels in place so switching language cannot discard a draft.
    root.querySelectorAll("[data-results-text]").forEach(node => { node.textContent = t(node.dataset.resultsText); });
    document.title = t("title");
  });
  load();
})();
