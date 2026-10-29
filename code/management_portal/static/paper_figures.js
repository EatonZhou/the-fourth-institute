(() => {
  "use strict";

  if (window.PortalPaperFigures) return;

  // Figures: the list and a figure's detail, extracting and explaining
  // figures, the inputs for a generated figure, drafts and their variants,
  // the lightbox, and deleting a generated figure.
  const DEPENDENCIES = Object.freeze([
    "$", "alertError", "assetsJobRunning", "assetsJobStatusText", "chip", "currentAssetsJob",
    "detailSectionLoadingHtml", "detailSectionsLoaded", "esc", "loadDetailSections", "md", "needPaperHtml",
    "paperApi", "paperStateHtml", "renderWorkspace", "state",
  ]);

  function createFigures(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPaperFigures is missing: ${missing.join(", ")}`);
    const {
      $, alertError, assetsJobRunning, assetsJobStatusText, chip, currentAssetsJob, detailSectionLoadingHtml,
      detailSectionsLoaded, esc, loadDetailSections, md, needPaperHtml, paperApi, paperStateHtml,
      renderWorkspace, state,
    } = dependencies;

    function renderFiguresMode() {
      const detail = state.detail;
      if (!detail?.paper) {
        $("workspaceMain").innerHTML = needPaperHtml();
        return;
      }
      if (!detailSectionsLoaded(["figures"])) {
        $("workspaceMain").innerHTML = detailSectionLoadingHtml("Figures");
        return;
      }
      const previousPane = document.querySelector(".figureDetailPane");
      const paperId = String(detail.paper.id || state.selectedId || "");
      const previousList = document.querySelector(".figureList");
      const previousListScrollTop = previousList && String(previousList.dataset.figureListPaperId || "") === paperId ? previousList.scrollTop : 0;
      const previousFigureId = state.selectedFigureId;
      const previousScrollTop = previousPane ? previousPane.scrollTop : 0;
      const figures = detail.figures || [];
      const assetsJob = currentAssetsJob();
      const assetsRunning = assetsJobRunning(assetsJob);
      const assetsText = assetsJobStatusText(assetsJob);
      const extracting = state.figureExtractInFlight === paperId || assetsRunning;
      const statusText = extracting
        ? (assetsText || window.PortalI18n.t("Extracting figures from PDF..."))
        : (figures.length ? `${figures.length} figures extracted from PDF.` : window.PortalI18n.t("Extract figures from this paper PDF."));
      const selected = figures.find(item => String(item.figure_id || "") === state.selectedFigureId) || figures[0] || null;
      state.selectedFigureId = selected ? String(selected.figure_id || "") : "";
      $("workspaceMain").innerHTML = window.PortalI18n.html`
          <div class="portalToolbar paperToolbar" style="justify-content:space-between;margin-bottom:10px;">
            <div class="paneNote">${esc(statusText)}</div>
            <button class="portalButton is-compact is-primary" type="button" id="extractFiguresBtn" ${detail.paper.has_pdf && !extracting ? "" : "disabled"}>
              ${assetsRunning ? window.PortalI18n.t("Auto extracting...") : (extracting ? "Extracting..." : (figures.length ? window.PortalI18n.t("Re-extract Figures") : window.PortalI18n.t("Extract Figures")))}
            </button>
          </div>
          ${figures.length
          ? window.PortalI18n.html`<div class="figureWorkspace">
                <div class="figureList" aria-label="Figures" data-figure-list-paper-id="${esc(paperId)}">${figures.map(item => renderFigureListItem(item, state.selectedFigureId)).join("")}</div>
                <div class="figureDetailPane">${renderFigureDetail(selected)}</div>
              </div>`
          : paperStateHtml(
            detail.paper.has_pdf
              ? (assetsRunning ? window.PortalI18n.t("Automatic figure extraction is running.") : window.PortalI18n.t("No figures extracted yet."))
              : window.PortalI18n.t("No PDF registered for this paper."),
            { state: assetsRunning ? "loading" : "empty" },
          )}
        `;
      const list = document.querySelector(".figureList");
      if (list) {
        const restoreList = () => { list.scrollTop = previousListScrollTop; };
        requestAnimationFrame(restoreList);
        setTimeout(restoreList, 80);
      }
      if (selected && previousFigureId === String(selected.figure_id || "")) {
        const pane = document.querySelector(".figureDetailPane");
        if (pane) {
          const restore = () => { pane.scrollTop = previousScrollTop; };
          requestAnimationFrame(restore);
          setTimeout(restore, 80);
        }
      }
    }

    function renderFigureListItem(figure, selectedId) {
      const figureId = String(figure.figure_id || "");
      const active = figureId && figureId === selectedId;
      const caption = String(figure.caption || "").replace(/^\s*(?:Fig\.?|Figure)\s*[0-9A-Za-z]+\s*[:.\-]?\s*/i, "").trim();
      return window.PortalI18n.html`
          <button class="figureListItem ${active ? "active" : ""}" type="button" data-select-figure="${esc(figureId)}">
            ${figure.image_url ? window.PortalI18n.html`<img class="figureListThumb" src="${esc(figure.image_url)}" alt="" loading="lazy" />` : window.PortalI18n.html`<span class="figureListThumb"></span>`}
            <span class="figureListText">
              <span class="figureListTitle">${esc(figure.label || figureId || "Figure")}</span>
              <span class="figureListMeta">${esc([figure.page ? `page ${figure.page}` : "", caption].filter(Boolean).join(" · "))}</span>
            </span>
          </button>
        `;
    }

    function figureInputKey(figureId, sourceGeneratedId = "") {
      return `${state.selectedId || ""}::${String(figureId || "")}::${String(sourceGeneratedId || "original")}`;
    }

    function figureInputItems(key) {
      const items = state.figureInputImages[key];
      return Array.isArray(items) ? items : [];
    }

    function renderFigureInputTrayHtml(key) {
      const items = figureInputItems(key);
      if (!items.length) return "";
      return items.map(item => window.PortalI18n.html`
          <div class="figureInputChip" data-figure-input-id="${esc(item.id)}">
            <button class="figureInputThumb" type="button" data-figure-input-open="${esc(key)}" data-input-id="${esc(item.id)}" title="Open image">
              ${item.previewUrl ? window.PortalI18n.html`<img src="${esc(item.previewUrl)}" alt="" />` : ""}
            </button>
            <div class="figureInputMeta">
              <div class="figureInputName">${esc(item.name || "image")}</div>
              <div class="figureInputStatus ${item.status === "error" ? "error" : ""}">${esc(item.status === "done" ? "attached" : (item.error || item.status || ""))}</div>
            </div>
            <button class="figureInputRemove" type="button" data-figure-input-remove="${esc(key)}" data-input-id="${esc(item.id)}" aria-label="Remove image" data-portal-icon="close"></button>
          </div>
        `).join("");
    }

    function renderFigureInputTray(key) {
      return window.PortalI18n.html`<div class="figureInputTray" data-figure-input-tray="${esc(key)}">${renderFigureInputTrayHtml(key)}</div>`;
    }

    function refreshFigureInputTray(key) {
      document.querySelectorAll(`[data-figure-input-tray="${CSS.escape(key)}"]`).forEach(node => {
        node.innerHTML = renderFigureInputTrayHtml(key);
      });
    }

    function renderFigureDetail(figure) {
      if (!figure) return paperStateHtml("Select a Figure.", { density: "compact" });
      const figureId = String(figure.figure_id || "");
      const explaining = state.figureExplainInFlight === figureId;
      const generating = state.figureGenerateInFlight === figureId;
      const generated = Array.isArray(figure.generated) ? figure.generated : [];
      const explanationStatus = String(figure.explanation_status || "");
      const originalInputKey = figureInputKey(figureId);
      return window.PortalI18n.html`
          <article class="apiCard figureCard" data-figure-id="${esc(figureId)}">
            <div class="portalToolbar paperToolbar" style="justify-content:space-between;">
              <h3>${esc(figure.label || figureId || "Figure")}</h3>
              <div class="chips">
                ${figure.page ? chip(`page ${figure.page}`) : ""}
                ${figure.width && figure.height ? chip(`${figure.width} x ${figure.height}`) : ""}
              </div>
            </div>
            ${figure.image_url ? window.PortalI18n.html`<img class="figureImage" src="${esc(figure.image_url)}" alt="${esc(figure.label || figureId || "Figure")}" loading="lazy" data-open-figure-image data-lightbox-figure-id="${esc(figureId)}" data-lightbox-title="${esc(figure.label || figureId || "Figure")}" />` : ""}
            ${figure.caption ? window.PortalI18n.html`<div class="refRaw">${esc(figure.caption)}</div>` : ""}
            <div class="portalToolbar paperToolbar" style="justify-content:flex-start;">
              <button class="portalButton is-compact is-quiet" type="button" data-explain-figure="${esc(figureId)}" ${explaining ? "disabled" : ""}>${explaining ? "Explaining..." : "Explain"}</button>
              ${explanationStatus && explanationStatus !== "done" ? window.PortalI18n.html`<span class="paneNote">${esc(explanationStatus)}</span>` : ""}
            </div>
            ${figure.explanation ? window.PortalI18n.html`<div class="markdownBody">${md(figure.explanation)}</div>` : window.PortalI18n.html`<div class="paneNote">No explanation yet.</div>`}
            ${figure.explanation_error ? window.PortalI18n.html`<div class="paneNote" role="alert">${esc(figure.explanation_error)}</div>` : ""}
            <textarea class="portalControl figurePromptBox" data-figure-request="${esc(figureId)}" data-figure-id="${esc(figureId)}" placeholder="Describe how to redraw or modify this figure... Ctrl/⌘ + Enter to generate"></textarea>
            ${renderFigureInputTray(originalInputKey)}
            <div class="portalToolbar paperToolbar" style="justify-content:flex-end;">
              <button class="portalButton is-compact is-primary" type="button" data-generate-figure="${esc(figureId)}" ${generating ? "disabled" : ""}>${generating ? "Generating..." : window.PortalI18n.t("Generate Image")}</button>
            </div>
            ${generated.length ? window.PortalI18n.html`<div class="figureGenerated">
              ${generated.map(item => window.PortalI18n.html`
                <div class="apiCard" style="padding:8px;">
                  <div class="portalToolbar paperToolbar" style="justify-content:space-between;margin-bottom:6px;">
                    <span class="paneNote">${esc(new Date((item.created_at || 0) * 1000).toLocaleString())} · ${esc(item.kind || "image")}${item.status && item.status !== "done" ? ` · ${esc(item.status)}` : ""}</span>
                    <button class="portalButton is-compact is-quiet" type="button" data-delete-generated-figure="${esc(figureId)}" data-generated-id="${esc(item.generated_id || "")}">删除</button>
                  </div>
                  ${item.image_url ? window.PortalI18n.html`<img class="figureImage" src="${esc(item.image_url)}" alt="Generated Figure" loading="lazy" data-open-figure-image data-lightbox-figure-id="${esc(figureId)}" data-source-generated-id="${esc(item.generated_id || "")}" data-lightbox-title="${esc(item.generated_id || window.PortalI18n.t("Generated Figure"))}" />` : ""}
                  ${item.error ? paperStateHtml(item.error, { state: "error", density: "compact" }) : ""}
                  ${item.prompt ? window.PortalI18n.html`<details class="paperDisclosure"><summary><span>Codex note</span><span data-portal-icon="chevron-down" data-portal-icon-class="paperDisclosureIcon" class="paperDisclosureIcon" aria-hidden="true"></span></summary><div class="markdownBody">${md(item.prompt || "")}</div></details>` : (!item.image_url && !item.error ? window.PortalI18n.html`<div class="paneNote">Generating image in the background.</div>` : "")}
                  ${item.image_url && String(item.status || "done") === "done" ? window.PortalI18n.html`
                    <textarea class="portalControl figurePromptBox" data-generated-variant-request="${esc(item.generated_id || "")}" data-figure-id="${esc(figureId)}" placeholder="Describe how to modify this generated image..."></textarea>
                    ${renderFigureInputTray(figureInputKey(figureId, item.generated_id || ""))}
                    <div class="portalToolbar paperToolbar" style="justify-content:flex-end;">
                      <button class="portalButton is-compact is-quiet" type="button" data-generate-figure-variant="${esc(figureId)}" data-source-generated-id="${esc(item.generated_id || "")}">Generate Variant</button>
                    </div>
                  ` : ""}
                </div>
              `).join("")}
            </div>` : ""}
          </article>
        `;
    }

    async function extractFigures() {
      const paperId = String(state.selectedId || "").trim();
      if (!paperId || state.figureExtractInFlight) return;
      state.figureExtractInFlight = paperId;
      renderWorkspace();
      try {
        const result = await paperApi.request("/paper/api/figures/extract", {
          method: "POST",
          body: JSON.stringify({ paper_id: paperId }),
        });
        if (String(state.selectedId || "") === paperId && state.detail?.paper) {
          state.detail.figures = result.figures || [];
        }
      } finally {
        if (state.figureExtractInFlight === paperId) state.figureExtractInFlight = "";
        if (state.mode === "figures") renderWorkspace();
      }
    }

    async function explainFigure(figureId) {
      const id = String(figureId || "").trim();
      if (!state.selectedId || !id || state.figureExplainInFlight) return;
      state.figureExplainInFlight = id;
      renderWorkspace();
      try {
        const result = await paperApi.request("/paper/api/figures/explain", {
          method: "POST",
          body: JSON.stringify({ paper_id: state.selectedId, figure_id: id }),
        });
        if (result.figure && state.detail?.figures) {
          state.detail.figures = state.detail.figures.map(item => String(item.figure_id || "") === id ? result.figure : item);
        }
      } catch (error) {
        state.figureExplainInFlight = "";
        throw error;
      } finally {
        if (state.mode === "figures") renderWorkspace();
      }
    }

    function figureExplainDone(figureId) {
      const figure = (state.detail?.figures || []).find(item => String(item.figure_id || "") === figureId);
      const status = String(figure?.explanation_status || "");
      return !!figure && status !== "queued" && status !== "running";
    }

    async function generateFigureDraft(figureId, sourceGeneratedId = "", requestOverride = "") {
      const id = String(figureId || "").trim();
      if (!state.selectedId || !id || state.figureGenerateInFlight) return;
      const sourceId = String(sourceGeneratedId || "").trim();
      const inputKey = figureInputKey(id, sourceId);
      const inputImages = figureInputItems(inputKey);
      if (inputImages.some(item => item.status === "uploading")) {
        alertError(new Error(window.PortalI18n.t("Please wait for pasted images to finish uploading.")));
        return;
      }
      if (inputImages.some(item => item.status === "error")) {
        alertError(new Error(window.PortalI18n.t("Remove failed image uploads before generating.")));
        return;
      }
      const input = sourceId
        ? document.querySelector(`[data-generated-variant-request="${CSS.escape(sourceId)}"]`)
        : document.querySelector(`[data-figure-request="${CSS.escape(id)}"]`);
      const request = String(requestOverride || input?.value || "").trim();
      if (!request) {
        alertError(new Error(window.PortalI18n.t("Please describe how to modify this figure.")));
        return;
      }
      state.figureGenerateInFlight = id;
      renderWorkspace();
      try {
        const result = await paperApi.request("/paper/api/figures/generate", {
          method: "POST",
          body: JSON.stringify({
            paper_id: state.selectedId,
            figure_id: id,
            request,
            source_generated_id: sourceId,
            extra_image_paths: inputImages.map(item => item.path).filter(Boolean),
          }),
        });
        if (result.figure && state.detail?.figures) {
          state.detail.figures = state.detail.figures.map(item => String(item.figure_id || "") === id ? result.figure : item);
        }
        const generatedId = String(result.generated?.generated_id || "");
        state.figureDraftGeneratedId = generatedId;
        if (input && !requestOverride) input.value = "";
        state.figureInputImages[inputKey] = [];
        refreshFigureInputTray(inputKey);
      } catch (error) {
        state.figureGenerateInFlight = "";
        state.figureDraftGeneratedId = "";
        throw error;
      } finally {
        if (state.mode === "figures") renderWorkspace();
      }
    }

    function figureContextFromInput(input) {
      const figureId = String(input?.dataset.figureId || input?.dataset.figureRequest || "").trim();
      const sourceGeneratedId = String(input?.dataset.generatedVariantRequest || "").trim();
      return { figureId, sourceGeneratedId, key: figureInputKey(figureId, sourceGeneratedId) };
    }

    function figureContextForVisibleFigure(figureId, sourceGeneratedId = "") {
      return { figureId, sourceGeneratedId, key: figureInputKey(figureId, sourceGeneratedId) };
    }

    function figureContextFromKey(key) {
      const parts = String(key || "").split("::");
      const source = parts[2] === "original" ? "" : (parts[2] || "");
      return { key, figureId: parts[1] || "", sourceGeneratedId: source };
    }

    async function uploadFigureInputFile(file, context, existingId = "", previewUrlOverride = "") {
      if (!file || !state.selectedId || !context?.key) return null;
      const id = existingId || `input-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      const previewUrl = previewUrlOverride || URL.createObjectURL(file);
      const item = {
        id,
        name: file.name || "image.png",
        previewUrl,
        path: "",
        status: "uploading",
        error: "",
      };
      const current = figureInputItems(context.key).filter(existing => existing.id !== id);
      state.figureInputImages[context.key] = existingId ? [...current, item] : [...current, item];
      refreshFigureInputTray(context.key);
      try {
        const form = new FormData();
        form.append("paper_id", state.selectedId);
        form.append("file", file, item.name);
        const result = await paperApi.postForm("/paper/api/figures/input-image", form);
        item.path = result.path || "";
        item.status = "done";
        state.figureInputImages[context.key] = figureInputItems(context.key).map(existing => existing.id === id ? item : existing);
      } catch (error) {
        item.status = "error";
        item.error = error?.message || String(error);
        state.figureInputImages[context.key] = figureInputItems(context.key).map(existing => existing.id === id ? item : existing);
      }
      refreshFigureInputTray(context.key);
      return item;
    }

    function pastedImageFiles(event) {
      const files = Array.from(event.clipboardData?.files || []).filter(file => String(file.type || "").startsWith("image/"));
      if (files.length) return files;
      return Array.from(event.clipboardData?.items || [])
        .filter(item => String(item.type || "").startsWith("image/"))
        .map(item => item.getAsFile())
        .filter(Boolean);
    }

    async function openFigureLightbox(image, figureId, sourceGeneratedId = "", title = "", attachmentId = "") {
      const src = String(image?.src || image?.getAttribute?.("src") || "").trim();
      if (!src) return;
      const context = figureContextForVisibleFigure(figureId, sourceGeneratedId);
      if (!window.PortalImageEditor?.open) throw new Error("PortalImageEditor is unavailable");
      window.PortalImageEditor.open(src, title || "Figure image", {
        editable: true,
        name: title || "figure-image",
        meta: { figureId: context.figureId, sourceGeneratedId: context.sourceGeneratedId, key: context.key, attachmentId: String(attachmentId || "") },
        onSave: async ({ file, previewUrl }) => {
          await uploadFigureInputFile(file, { key: context.key }, String(attachmentId || ""), previewUrl);
        },
      });
    }

    function figureDraftDone(figureId, generatedId) {
      const figure = (state.detail?.figures || []).find(item => String(item.figure_id || "") === figureId);
      const generated = (figure?.generated || []).find(item => String(item.generated_id || "") === generatedId);
      return !!generated && String(generated.status || "done") !== "queued" && String(generated.status || "done") !== "running";
    }

    async function refreshFigureProjection() {
      if (!state.selectedId) return;
      await loadDetailSections(state.selectedId, ["figures"], { render: false, force: true });
      if (state.figureExplainInFlight && figureExplainDone(state.figureExplainInFlight)) {
        state.figureExplainInFlight = "";
      }
      if (
        state.figureGenerateInFlight
        && state.figureDraftGeneratedId
        && figureDraftDone(state.figureGenerateInFlight, state.figureDraftGeneratedId)
      ) {
        state.figureGenerateInFlight = "";
        state.figureDraftGeneratedId = "";
      }
      if (state.mode === "figures") renderWorkspace();
    }

    async function deleteGeneratedFigure(figureId, generatedId) {
      const fid = String(figureId || "").trim();
      const gid = String(generatedId || "").trim();
      if (!state.selectedId || !fid || !gid) return;
      const result = await paperApi.request("/paper/api/figures/generated/delete", {
        method: "POST",
        body: JSON.stringify({ paper_id: state.selectedId, figure_id: fid, generated_id: gid }),
      });
      if (result.figure && state.detail?.figures) {
        state.detail.figures = state.detail.figures.map(item => String(item.figure_id || "") === fid ? result.figure : item);
      }
      if (state.mode === "figures") renderWorkspace();
    }

    return Object.freeze({
      deleteGeneratedFigure, explainFigure, extractFigures, figureContextFromInput, figureContextFromKey,
      figureInputItems, generateFigureDraft, openFigureLightbox, pastedImageFiles, refreshFigureInputTray,
      refreshFigureProjection, renderFiguresMode, uploadFigureInputFile,
    });
  }

  window.PortalPaperFigures = Object.freeze({ createFigures, dependencies: DEPENDENCIES });
})();
