(() => {
  "use strict";

  if (window.PortalPaperRegistration) return;

  // Registering a Paper: the dialog, uploading its PDF as a draft whose
  // metadata fills the form, and submitting it.
  const DEPENDENCIES = Object.freeze([
    "$", "loadLibrary", "mergeDetailPayload", "paperApi", "paperDialog", "setMode", "state",
    "syncPaperLocation",
  ]);

  function createRegistration(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPaperRegistration is missing: ${missing.join(", ")}`);
    const {
      $, loadLibrary, mergeDetailPayload, paperApi, paperDialog, setMode, state, syncPaperLocation,
    } = dependencies;

    function openPaperModal() {
      $("paperForm").reset();
      state.registrationEditedFields.clear();
      state.registrationUploadId = "";
      state.registrationDraftInFlight = false;
      state.registrationSubmitInFlight = false;
      if (state.registrationDraftPollTimer) clearTimeout(state.registrationDraftPollTimer);
      state.registrationDraftPollTimer = 0;
      setPaperRegistrationUploadStatus("No PDF selected");
      setPaperRegistrationControlsBusy(false);
      paperDialog.open({ focusTarget: "#paperForm [name='title']" });
    }

    function closePaperModal() {
      if (state.registrationDraftPollTimer) clearTimeout(state.registrationDraftPollTimer);
      state.registrationDraftPollTimer = 0;
      state.registrationDraftInFlight = false;
      state.registrationUploadId = "";
      paperDialog.close();
    }

    function setPaperRegistrationUploadStatus(message, { error = false } = {}) {
      const status = $("paperRegisterUploadStatus");
      if (!status) return;
      status.textContent = String(message || "");
      status.classList.toggle("is-error", !!error);
    }

    function setPaperRegistrationControlsBusy(busy) {
      const uploadButton = $("paperRegisterUploadBtn");
      const submitButton = $("submitPaperBtn");
      if (uploadButton) uploadButton.disabled = !!busy || state.registrationSubmitInFlight;
      if (submitButton) submitButton.disabled = (!!busy && !state.registrationUploadId) || state.registrationSubmitInFlight;
    }

    function setPaperFormValue(name, value, { preserveEdits = false } = {}) {
      const field = $("paperForm")?.elements?.namedItem(name);
      if (!field) return;
      const text = Array.isArray(value) ? value.join(", ") : String(value ?? "");
      if (preserveEdits && (state.registrationEditedFields.has(name) || !text.trim())) return;
      field.value = text;
    }

    function applyPaperRegistrationMetadata(metadata, { preserveEdits = false } = {}) {
      if (!metadata || typeof metadata !== "object") return;
      [
        "title", "authors", "venue", "year", "session", "abstract", "tags", "workload",
        "platform", "architecture", "optimization", "doi", "arxiv",
      ].forEach(name => {
        if (metadata[name] !== undefined && metadata[name] !== null) {
          setPaperFormValue(name, metadata[name], { preserveEdits });
        }
      });
    }

    function schedulePaperRegistrationDraftPoll(uploadId) {
      if (state.registrationDraftPollTimer) clearTimeout(state.registrationDraftPollTimer);
      state.registrationDraftPollTimer = setTimeout(() => {
        pollPaperRegistrationDraft(uploadId).catch(error => {
          if (state.registrationUploadId !== uploadId) return;
          setPaperRegistrationUploadStatus(`Metadata status unavailable: ${error.message || error}`, { error: true });
          schedulePaperRegistrationDraftPoll(uploadId);
        });
      }, 1500);
    }

    async function pollPaperRegistrationDraft(uploadId) {
      if (!uploadId || state.registrationUploadId !== uploadId || $("paperModal")?.hidden) return;
      const draft = await paperApi.get(`/paper/api/papers/registration-draft?upload_id=${encodeURIComponent(uploadId)}`);
      if (state.registrationUploadId !== uploadId) return;
      const status = String(draft.status || "");
      applyPaperRegistrationMetadata(draft.metadata || {}, { preserveEdits: true });
      if (["done", "partial", "error"].includes(status)) {
        state.registrationDraftInFlight = false;
        state.registrationDraftPollTimer = 0;
        const hidden = $("paperForm")?.elements?.namedItem("registration_upload_id");
        if (hidden) hidden.value = uploadId;
        setPaperRegistrationControlsBusy(false);
        if (status === "done") {
          setPaperRegistrationUploadStatus(`${draft.filename || "PDF"} · metadata ready`);
        } else if (status === "partial") {
          const warning = Array.isArray(draft.warnings) ? draft.warnings[0] : "";
          setPaperRegistrationUploadStatus(`${draft.filename || "PDF"} · ${warning || "partial metadata"}`, { error: true });
        } else {
          setPaperRegistrationUploadStatus(`${draft.filename || "PDF"} · ${draft.error || "metadata extraction failed"}`, { error: true });
        }
        return;
      }
      setPaperRegistrationUploadStatus(`${draft.filename || "PDF"} · ${draft.step || "extracting metadata"} · AI 尚未完成，可核对填写后直接登记，无需等待。登记采用当前表单，不会被后续提取结果覆盖。`);
      schedulePaperRegistrationDraftPoll(uploadId);
    }

    async function uploadPaperRegistrationPdf(file) {
      if (!file || state.registrationDraftInFlight || state.registrationSubmitInFlight) return;
      if (!String(file.name || "").toLowerCase().endsWith(".pdf")) {
        window.PortalUI.toast(window.PortalI18n.t("请选择 PDF 文件。"), "warning");
        return;
      }
      state.registrationDraftInFlight = true;
      state.registrationUploadId = "";
      setPaperRegistrationControlsBusy(true);
      setPaperRegistrationUploadStatus(`${file.name || "PDF"} · uploading`);
      try {
        const form = new FormData();
        form.append("file", file, file.name || "paper.pdf");
        const draft = await paperApi.postForm("/paper/api/papers/registration-draft", form);
        const uploadId = String(draft.upload_id || "").trim();
        if (!uploadId) throw new Error("Paper registration upload did not return an id");
        state.registrationUploadId = uploadId;
        setPaperRegistrationControlsBusy(true);
        const hidden = $("paperForm")?.elements?.namedItem("registration_upload_id");
        if (hidden) hidden.value = uploadId;
        setPaperRegistrationUploadStatus(`${draft.filename || file.name || "PDF"} · ${draft.step || "queued"}`);
        schedulePaperRegistrationDraftPoll(uploadId);
      } catch (error) {
        state.registrationDraftInFlight = false;
        setPaperRegistrationControlsBusy(false);
        setPaperRegistrationUploadStatus(error.message || String(error), { error: true });
        throw error;
      }
    }

    async function submitPaper(event) {
      event.preventDefault();
      if ((state.registrationDraftInFlight && !state.registrationUploadId) || state.registrationSubmitInFlight) return;
      state.registrationSubmitInFlight = true;
      setPaperRegistrationControlsBusy(true);
      const form = new FormData(event.currentTarget);
      const payload = Object.fromEntries(form.entries());
      const relatedProject = String(payload.session || "").trim();
      payload.project_ids = relatedProject ? [relatedProject] : [];
      try {
        const detail = await paperApi.request("/paper/api/papers/register", { method: "POST", body: JSON.stringify(payload) });
        closePaperModal();
        mergeDetailPayload(detail, { replace: true });
        state.selectedId = detail.paper.id;
        syncPaperLocation(state.selectedId);
        setMode("reader");
        await loadLibrary({ keepDetail: true });
      } finally {
        state.registrationSubmitInFlight = false;
        setPaperRegistrationControlsBusy(false);
      }
    }

    return Object.freeze({
      closePaperModal, openPaperModal, submitPaper, uploadPaperRegistrationPdf,
    });
  }

  window.PortalPaperRegistration = Object.freeze({ createRegistration, dependencies: DEPENDENCIES });
})();
