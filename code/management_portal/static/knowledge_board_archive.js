// Knowledge board: the archive workspace: jobs, candidates, drafts and applying them.

// ── Archive workspace ────────────────────────────────────────────────
function archiveCurrent() {
  return state.archive.current || null;
}

function bindArchiveResident(payload) {
  const residentId = String(payload?.resident_id || "").trim();
  if (!residentId) {
    archiveChatPanel?.closeSurface?.();
    return;
  }
  archiveChatPanel?.setResident(residentId, { load: true }).catch(alertError);
}

function setArchiveCurrent(payload) {
  state.archive.current = payload || null;
  const archiveId = String(payload?.job?.archive_id || "").trim();
  if (archiveId) state.archive.currentId = archiveId;
  bindArchiveResident(payload);
}

function archiveResidentState() {
  return archiveChatPanel?.snapshot?.()?.state || {};
}

function archiveIsActive() {
  return ["queued", "running", "stopping"].includes(
    String(archiveResidentState().work_state || ""),
  );
}

function openArchiveModal() {
  state.archive.open = true;
  archiveDialogController.open();
  loadArchiveJobs().catch(alertError);
}

function finalizeArchiveClose() {
  if (state.archive.candidateDirty) {
    clearTimeout(state.archive.candidateSaveTimer);
    state.archive.candidateSaveTimer = null;
    saveArchiveCandidates({ silent: true }).catch(alertError);
  }
  state.archive.open = false;
  archiveChatRefresh?.stop?.();
  archiveChatPanel?.closeSurface?.();
}

function closeArchiveModal() {
  archiveDialogController.close();
}

async function loadArchiveJobs(options = {}) {
  const selectFirst = options.selectFirst !== false;
  let payload;
  try {
    payload = await knowledgeApi.request("/knowledge/api/archive/jobs?limit=30", { cache: "no-store" });
  } catch (error) {
    state.archive.jobs = [];
    setArchiveCurrent(null);
    state.archive.currentId = "";
    state.archive.serviceError = String(error?.message || error || "KnowledgeArchiveAgent 未运行，归档功能暂不可用。");
    renderArchiveJobs();
    renderArchiveWorkspace();
    return;
  }
  state.archive.serviceError = "";
  state.archive.jobs = Array.isArray(payload.jobs) ? payload.jobs : [];
  if (!state.archive.currentId && selectFirst && state.archive.jobs[0]) state.archive.currentId = state.archive.jobs[0].archive_id || "";
  renderArchiveJobs();
  if (state.archive.currentId) await loadArchiveJob(state.archive.currentId);
  else renderArchiveWorkspace();
}

async function loadArchiveJob(archiveId) {
  if (!archiveId) return;
  clearTimeout(state.archive.candidateSaveTimer);
  state.archive.candidateSaveTimer = null;
  state.archive.candidateDirty = false;
  state.archive.candidateSaving = false;
  const payload = await knowledgeApi.request(`/knowledge/api/archive/job?id=${encodeURIComponent(archiveId)}`, { cache: "no-store" });
  state.archive.currentId = payload.job?.archive_id || archiveId;
  setArchiveCurrent(payload);
  renderArchiveJobs();
  renderArchiveWorkspace();
  scheduleArchiveRefresh();
}

function scheduleArchiveRefresh() {
  if (!archiveChatRefresh || !state.archive.open || !state.archive.currentId) {
    archiveChatRefresh?.stop?.();
    return;
  }
  archiveChatRefresh.start();
}

function renderArchiveJobs() {
  if (state.archive.serviceError) {
    el.archiveJobCount.textContent = "unavailable";
    el.archiveJobList.innerHTML = knowledgeStateHtml(state.archive.serviceError, {
      state: "error",
      density: "inline",
    });
    return;
  }
  const jobs = state.archive.jobs || [];
  el.archiveJobCount.textContent = `${jobs.length} jobs`;
  if (!jobs.length) {
    el.archiveJobList.innerHTML = knowledgeStateHtml("暂无归档任务。", {
      density: "inline",
      icon: false,
    });
    return;
  }
  el.archiveJobList.innerHTML = jobs.map(job => {
    const active = job.archive_id === state.archive.currentId ? " active" : "";
    const title = job.title || job.archive_id;
    const meta = `${job.status || "idle"} · ${job.source_kind || ""} · ${job.updated_at || ""}`;
    return window.PortalI18n.html`
            <button class="archiveJobItem${active}" type="button" data-archive-job="${escapeHtml(job.archive_id)}">
              <div class="archiveJobTitle">${escapeHtml(title)}</div>
              <div class="archiveJobId">Job ID: ${escapeHtml(job.archive_id || "")}</div>
              <div class="archiveJobMeta">${escapeHtml(meta)}</div>
            </button>
          `;
  }).join("");
}

function renderArchivePolicy() {
  el.archivePolicy.querySelectorAll("[data-archive-policy]").forEach(button => {
    button.classList.toggle("active", button.dataset.archivePolicy === state.archive.policy);
  });
}

function renderArchiveInputCollapse() {
  const collapsed = !!state.archive.inputCollapsed;
  el.archiveBody.classList.toggle("inputCollapsed", collapsed);
  el.archiveInputToggle.innerHTML = horizontalDisclosureIcon(collapsed);
  const label = collapsed ? window.PortalI18n.t("展开输入区") : window.PortalI18n.t("折叠输入区");
  el.archiveInputToggle.title = label;
  el.archiveInputToggle.setAttribute("aria-label", label);
}

function renderArchiveWorkspace() {
  const data = archiveCurrent();
  const job = data?.job || {};
  const residentState = archiveResidentState();
  const active = archiveIsActive();
  const serviceUnavailable = !!state.archive.serviceError;
  const loadingAttachments = archiveHasLoadingAttachments();
  const archiveStatus = active
    ? String(residentState.work_state || "running")
    : (job.status || "idle");
  renderArchiveSourceTray();
  renderArchivePolicy();
  renderArchiveInputCollapse();
  el.archiveTitle.textContent = job.title ? window.PortalI18n.text`知识库归档 · ${job.title}` : window.PortalI18n.t("知识库归档");
  el.archiveSub.textContent = serviceUnavailable
    ? window.PortalI18n.t("KnowledgeArchiveAgent 未运行，归档功能暂不可用。")
    : job.archive_id
    ? window.PortalI18n.text`Job ID: ${job.archive_id} · ${archiveStatus}`
    : window.PortalI18n.t("给材料 -> 选候选 -> 确认入库");

  el.archiveCreate.disabled = serviceUnavailable || state.archive.creating || loadingAttachments;
  const saveMaterial = archiveUsesMaterialSave();
  el.archiveCreate.textContent = state.archive.creating
    ? window.PortalI18n.t("生成中…")
    : (loadingAttachments ? window.PortalI18n.t("读取文件中…") : (saveMaterial ? window.PortalI18n.t("判断项目") : window.PortalI18n.t("生成方案")));
  el.archiveDrafts.hidden = saveMaterial;
  el.archiveDrafts.textContent = archivePlanCompletedWithoutCandidates() ? window.PortalI18n.t("重新生成候选") : window.PortalI18n.t("生成/更新草稿");
  el.archiveApply.textContent = window.PortalI18n.t("Apply 到知识库");
  el.archiveDrafts.disabled = saveMaterial || serviceUnavailable || !job.archive_id || archiveIsActive() || (!approvedArchiveCandidates().length && !archivePlanCompletedWithoutCandidates());
  el.archiveApply.disabled = serviceUnavailable || !job.archive_id || archiveIsActive() || (saveMaterial ? !archiveCandidates().length : (!unappliedArchiveDrafts().length && !archivePlanCompletedWithoutCandidates()));
  archiveChatPanel?.updateControls?.();
  renderArchiveCandidates();
  renderArchiveDrafts();
}

function archiveCandidates() {
  return (archiveCurrent()?.plan?.candidates || []).filter(item => item && typeof item === "object");
}

function archivePlanCompletedWithoutCandidates() {
  const data = archiveCurrent();
  return !!data?.job?.archive_id && !archiveIsActive() && !archiveCandidates().length;
}

function approvedArchiveCandidates() {
  return archiveCandidates().filter(item => (item.decision || "approve") === "approve");
}

function archiveUsesMaterialSave() {
  const jobPolicy = archiveCurrent()?.job?.archive_policy || "";
  return (jobPolicy || state.archive.policy || "") === "save_material";
}

function appliedArchiveDraftIds() {
  return new Set((archiveCurrent()?.applied || [])
    .map(item => String(item?.draft_id || ""))
    .filter(Boolean));
}

function unappliedArchiveDrafts() {
  const applied = appliedArchiveDraftIds();
  return (archiveCurrent()?.drafts || []).filter(item => {
    const draftId = String(item?.draft_id || "");
    return draftId && !applied.has(draftId);
  });
}

function renderArchiveCandidates() {
  const candidates = archiveCandidates();
  if (!archiveCurrent()?.job?.archive_id) {
    el.archiveCandidates.innerHTML = knowledgeStateHtml(
      "创建归档任务后，Codex 会在这里生成候选方案。",
    );
    return;
  }
  if (!candidates.length) {
    if (archivePlanCompletedWithoutCandidates()) {
      el.archiveCandidates.innerHTML = knowledgeStateHtml(
        "Codex 已完成筛选，但没有发现值得归档的候选条目。可以在右侧追加要求，让 Codex 重新生成 archive_plan.json，或清空本次任务。",
      );
      return;
    }
    el.archiveCandidates.innerHTML = knowledgeStateHtml(
      "还没有候选。可以等待 Codex 完成，或在右侧继续要求它生成 archive_plan.json。",
    );
    return;
  }
  el.archiveCandidates.innerHTML = candidates.map((item, idx) => window.PortalI18n.html`
          <div class="archiveCandidate" data-candidate-index="${idx}">
            <div class="archiveCandidateTop">
              <input class="archiveInput archiveCandidateTitle" data-candidate-field="title" value="${escapeHtml(item.title || "")}" placeholder="标题" />
              <select class="archiveSelect" data-candidate-field="decision">
                ${["approve", "needs_edit", "reject"].map(value => window.PortalI18n.html`<option value="${value}" ${(item.decision || "approve") === value ? "selected" : ""}>${value}</option>`).join("")}
              </select>
            </div>
            <div class="archiveCandidateGrid">
              <input class="archiveInput" data-candidate-field="project" value="${escapeHtml(item.project || "")}" placeholder="project" />
              <select class="archiveSelect" data-candidate-field="type">
                ${["concept", "idea", "insight", "question", "decision", "plan", "thinking", "information", "material"].map(value => window.PortalI18n.html`<option value="${value}" ${(item.type || "") === value ? "selected" : ""}>${value}</option>`).join("")}
              </select>
              <input class="archiveInput" data-candidate-field="importance" type="number" min="1" max="10" value="${escapeHtml(item.importance || 5)}" />
            </div>
            <textarea class="archiveTextarea archiveCandidateInstruction" data-candidate-field="instruction" placeholder="给这条候选的补充指示，可留空">${escapeHtml(item.instruction || "")}</textarea>
          </div>
        `).join("");
}

function renderArchiveDrafts() {
  const activeDraftInput = document.activeElement?.closest?.("[data-draft-content-id]");
  if (activeDraftInput && activeDraftInput.value !== (activeDraftInput.dataset.originalContent || "")) return;
  const drafts = archiveCurrent()?.drafts || [];
  if (archiveUsesMaterialSave()) {
    el.archiveDraftList.innerHTML = knowledgeStateHtml(
      "点击 Apply 到知识库后，会保存 approve 的文件；全部 reject 时保存 0 份并清空当前流程。",
    );
    return;
  }
  if (!drafts.length) {
    if (archivePlanCompletedWithoutCandidates()) {
      el.archiveDraftList.innerHTML = knowledgeStateHtml(
        "没有候选条目，因此不会生成 Markdown 草稿。",
      );
      return;
    }
    el.archiveDraftList.innerHTML = knowledgeStateHtml("通过候选后生成 Markdown 草稿。");
    return;
  }
  const applied = appliedArchiveDraftIds();
  el.archiveDraftList.innerHTML = drafts.map(draft => {
    const draftId = String(draft.draft_id || "");
    const isApplied = applied.has(draftId);
    const isMaterial = (draft.type || "") === "material";
    return window.PortalI18n.html`
          <div class="archiveDraft${isApplied ? " applied" : ""}">
            <div class="archiveDraftTop">
              <div>
                <div class="archiveDraftTitle">${escapeHtml(draft.title || draft.filename || draft.draft_id)}</div>
                <div class="archiveSmallText">${escapeHtml(`${draft.project || ""} · ${draft.type || ""} · ${draft.filename || ""}`)}</div>
                ${isApplied ? window.PortalI18n.html`<div class="archiveDraftStatus">applied</div>` : ""}
              </div>
              <input type="checkbox" data-draft-id="${escapeHtml(draftId)}" ${isApplied ? "disabled" : "checked"} />
            </div>
            ${draft.summary ? window.PortalI18n.html`<div class="archiveSmallText">${escapeHtml(draft.summary)}</div>` : ""}
            ${isMaterial ? window.PortalI18n.html`<div class="archiveSmallText">${escapeHtml(`source: ${draft.source_path || draft.path || ""}`)}</div>` : window.PortalI18n.html`<textarea class="archiveDraftPreview" data-draft-content-id="${escapeHtml(draftId)}" ${(isApplied || draft.content_truncated) ? "disabled" : ""}>${escapeHtml(draft.content || "")}</textarea>`}
            ${draft.content_truncated ? window.PortalI18n.html`<div class="archiveSmallText">内容过长，当前只加载了前段预览，暂不支持在这里直接编辑。</div>` : ""}
          </div>
        `;
  }).join("");
}

async function createArchiveJob() {
  if (archiveHasLoadingAttachments()) {
    state.archive.localStatus = "文件仍在读取，请稍后再生成方案。";
    renderArchiveWorkspace();
    return;
  }
  const payload = {
    source_kind: "auto",
    title: el.archiveTitleInput.value.trim(),
    project_hint: el.archiveProjectHint.value.trim(),
    archive_policy: state.archive.policy || "strict",
    user_hint: "",
    source_text: archiveSourceRawValue(),
    source_items: (state.archive.attachments || []).filter(item => !item.loading && item.data_url).map(item => ({
      kind: "file",
      name: item.name,
      mime: item.mime,
      size_bytes: item.size_bytes,
      data_url: item.data_url,
    })),
  };
  if (!payload.source_text.trim() && !payload.source_items.length) {
    state.archive.localStatus = "请先输入材料或添加附件。";
    renderArchiveWorkspace();
    window.PortalUI.toast(window.PortalI18n.t("请先输入材料或添加附件。"), "warning");
    return;
  }
  state.archive.creating = true;
  state.archive.localStatus = "正在创建归档任务，等待 KnowledgeArchiveAgent 返回…";
  renderArchiveWorkspace();
  try {
    const data = await knowledgeApi.request("/knowledge/api/archive/create", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    setArchiveCurrent(data);
    state.archive.currentId = data.job?.archive_id || "";
    state.archive.inputCollapsed = false;
    state.archive.localStatus = "";
    el.archiveSourceText.innerHTML = "";
    state.archive.attachments = [];
    renderArchiveSourceTray();
    renderArchiveWorkspace();
    scheduleArchiveRefresh();
    await loadArchiveJobs();
  } catch (error) {
    state.archive.localStatus = `创建归档任务失败：${String(error?.message || error || "unknown error")}`;
    renderArchiveWorkspace();
    throw error;
  } finally {
    state.archive.creating = false;
    renderArchiveWorkspace();
  }
}

function readArchiveCandidateEdits() {
  const candidates = archiveCandidates().map(item => ({ ...item }));
  el.archiveCandidates.querySelectorAll("[data-candidate-index]").forEach(card => {
    const idx = Number(card.dataset.candidateIndex || -1);
    if (!Number.isInteger(idx) || idx < 0 || !candidates[idx]) return;
    card.querySelectorAll("[data-candidate-field]").forEach(input => {
      const field = input.dataset.candidateField;
      if (!field) return;
      candidates[idx][field] = field === "importance" ? Number(input.value || 5) : input.value;
    });
  });
  return candidates;
}

function updateArchiveCandidatesFromDom() {
  const data = archiveCurrent();
  if (!data?.job?.archive_id) return [];
  const candidates = readArchiveCandidateEdits();
  if (!data.plan || typeof data.plan !== "object") data.plan = { candidates: [] };
  data.plan.candidates = candidates;
  state.archive.candidateDirty = true;
  el.archiveDrafts.disabled = archiveIsActive() || !approvedArchiveCandidates().length;
  return candidates;
}

function scheduleArchiveCandidateAutosave() {
  updateArchiveCandidatesFromDom();
  clearTimeout(state.archive.candidateSaveTimer);
  state.archive.candidateSaveTimer = setTimeout(() => {
    saveArchiveCandidates({ silent: true }).catch(alertError);
  }, 600);
}

async function saveArchiveCandidates(options = {}) {
  const data = archiveCurrent();
  if (!data?.job?.archive_id) return;
  const candidates = readArchiveCandidateEdits();
  if (!data.plan || typeof data.plan !== "object") data.plan = { candidates: [] };
  data.plan.candidates = candidates;
  state.archive.candidateSaving = true;
  if (!options.silent) renderArchiveWorkspace();
  try {
    const updated = await knowledgeApi.request("/knowledge/api/archive/candidates", {
      method: "POST",
      body: JSON.stringify({ archive_id: data.job.archive_id, candidates }),
    });
    setArchiveCurrent(updated);
    state.archive.candidateDirty = false;
  } finally {
    state.archive.candidateSaving = false;
    if (!options.silent) renderArchiveWorkspace();
    else {
      el.archiveDrafts.disabled = archiveIsActive() || !approvedArchiveCandidates().length;
    }
  }
}

function resetArchiveWorkspaceLocal() {
  clearTimeout(state.archive.candidateSaveTimer);
  state.archive.candidateSaveTimer = null;
  archiveChatRefresh?.stop?.();
  setArchiveCurrent(null);
  state.archive.currentId = "";
  state.archive.candidateDirty = false;
  state.archive.candidateSaving = false;
  state.archive.attachments = [];
  state.archive.policy = "strict";
  state.archive.inputCollapsed = false;
  state.archive.localStatus = "";
  el.archiveTitleInput.value = "";
  el.archiveProjectHint.value = "";
  el.archiveSourceText.innerHTML = "";
  renderArchiveSourceTray();
}

async function clearArchiveWorkspace() {
  const data = archiveCurrent();
  if (archiveIsActive()) {
    window.PortalUI.toast(window.PortalI18n.t("归档任务正在运行，完成后再清空。"), "warning");
    return;
  }
  if (!await window.PortalUI.confirm({
    title: window.PortalI18n.t("清空归档页面？"),
    message: window.PortalI18n.t("当前归档页面和后台暂存文件都会被删除。"),
    confirmLabel: window.PortalI18n.t("清空"),
    tone: "danger",
  })) return;
  const archiveId = data?.job?.archive_id || state.archive.currentId || "";
  if (archiveId) {
    await knowledgeApi.request("/knowledge/api/archive/delete", {
      method: "POST",
      body: JSON.stringify({ archive_id: archiveId }),
    });
  }
  resetArchiveWorkspaceLocal();
  await loadArchiveJobs({ selectFirst: false });
}

async function generateArchiveDrafts() {
  const data = archiveCurrent();
  if (!data?.job?.archive_id) return;
  if (archivePlanCompletedWithoutCandidates()) {
    const updated = await knowledgeApi.request("/knowledge/api/archive/chat", {
      method: "POST",
      body: JSON.stringify({
        archive_id: data.job.archive_id,
        content: "请重新检查原始材料，重新生成 archive_plan.json。优先提取真正有长期价值、与项目推进相关、且不重复已有知识库内容的候选条目；如果仍然没有值得归档的内容，请明确说明原因。",
      }),
    });
    setArchiveCurrent(updated);
    renderArchiveWorkspace();
    scheduleArchiveRefresh();
    return;
  }
  await saveArchiveCandidates();
  const updated = await knowledgeApi.request("/knowledge/api/archive/drafts", {
    method: "POST",
    body: JSON.stringify({ archive_id: data.job.archive_id }),
  });
  setArchiveCurrent(updated);
  state.archive.inputCollapsed = true;
  renderArchiveWorkspace();
  scheduleArchiveRefresh();
}

async function saveArchiveDraftContent(draftId, content) {
  const data = archiveCurrent();
  if (!data?.job?.archive_id || !draftId) return;
  const updated = await knowledgeApi.request("/knowledge/api/archive/draft/save", {
    method: "POST",
    body: JSON.stringify({
      archive_id: data.job.archive_id,
      draft_id: draftId,
      content,
    }),
  });
  setArchiveCurrent(updated);
  renderArchiveWorkspace();
  scheduleArchiveRefresh();
}

async function applyArchiveDrafts() {
  const data = archiveCurrent();
  if (!data?.job?.archive_id) return;
  const saveMaterial = archiveUsesMaterialSave();
  if (state.archive.candidateDirty || saveMaterial) {
    await saveArchiveCandidates({ silent: true });
  }
  let draftIds = [];
  if (!saveMaterial) {
    draftIds = Array.from(el.archiveDraftList.querySelectorAll("[data-draft-id]:checked:not(:disabled)"))
      .map(input => input.dataset.draftId)
      .filter(Boolean);
    if (!draftIds.length) {
      if (archivePlanCompletedWithoutCandidates()) {
        await knowledgeApi.request("/knowledge/api/archive/delete", {
          method: "POST",
          body: JSON.stringify({ archive_id: data.job.archive_id }),
        });
        resetArchiveWorkspaceLocal();
        await loadArchiveJobs({ selectFirst: false });
        return;
      }
      window.PortalUI.toast(window.PortalI18n.t("请选择要写入的草稿。"), "warning");
      return;
    }
  }
  await knowledgeApi.request("/knowledge/api/archive/apply", {
    method: "POST",
    body: JSON.stringify({ archive_id: data.job.archive_id, draft_ids: draftIds }),
  });
  await loadIndex({ refresh: true });
  await knowledgeApi.request("/knowledge/api/archive/delete", {
    method: "POST",
    body: JSON.stringify({ archive_id: data.job.archive_id }),
  });
  resetArchiveWorkspaceLocal();
  await loadArchiveJobs({ selectFirst: false });
}
