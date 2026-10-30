(() => {
  "use strict";

  if (window.PortalLineageWorkbenchPreviewFiles) return;

  // The selected Resident's files: reading its folder, previewing its first
  // file, and reading and opening a file of it.

  const DEPENDENCIES = Object.freeze([
    "$id", "activeAgentPreviewDocument", "activePreviewPayload", "currentPersonalWorkspaces", "currentResident",
    "ensurePreviewVisible", "fileBaseName", "lineageIdForResident", "openAgentPreviewDocument", "previewState",
    "resetAgentPreviewToEmpty", "residentFileParts", "residentService", "selectedResidentId", "setPreviewTitle",
    "state", "stopPreviewRefresh", "topLevelResidentFilePath", "unavailableResidentBindings",
    "withInitialPreviewLine",
  ]);

  function createPreviewFiles(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchPreviewFiles is missing: ${missing.join(", ")}`);
    const {
      $id, activeAgentPreviewDocument, activePreviewPayload, currentPersonalWorkspaces, currentResident,
      ensurePreviewVisible, fileBaseName, lineageIdForResident, openAgentPreviewDocument, previewState,
      resetAgentPreviewToEmpty, residentFileParts, residentService, selectedResidentId, setPreviewTitle, state,
      stopPreviewRefresh, topLevelResidentFilePath, unavailableResidentBindings, withInitialPreviewLine,
    } = dependencies;

    async function loadFolder() {
      if (currentResident()) {
        const residentId = selectedResidentId();
        if (!residentId || unavailableResidentBindings.has(residentId)) return;
        if (
          state.lineageDeleteSaving
          && lineageIdForResident(currentResident()) === state.lineageDeleteSaving
        ) return;
        const requestSequence = ++state.folderRequestSequence;
        try {
          const folder = await residentService.folder(residentId);
          if (requestSequence !== state.folderRequestSequence || residentId !== selectedResidentId()) return;
          state.folder = folder;
          currentPersonalWorkspaces()?.controller.setResident({ id: residentId, roots: folder.roots || [] });
          const first = topLevelResidentFilePath(state.folder, state.selectedFile);
          if (!activePreviewPayload() && !activeAgentPreviewDocument()) {
            if (first) {
              await loadResidentFile(first, {
                activatePreview: false,
                revealPanel: false,
              });
            } else {
              state.selectedFile = "";
              resetAgentPreviewToEmpty();
            }
          }
        } catch (err) {
          if (requestSequence !== state.folderRequestSequence || residentId !== selectedResidentId()) return;
          console.warn("Resident workspace metadata unavailable", err);
          state.folder = { resident_id: residentId, roots: [], error: String(err.message || err) };
          currentPersonalWorkspaces()?.controller.setResident({ id: residentId, error: state.folder.error });
        }
        return;
      }
    }

    async function loadResidentFileDocument(path, residentId) {
      const { directoryId, relPath } = residentFileParts(path);
      if (!residentId || !directoryId || !path) return null;
      const payload = await residentService.file({
        residentId,
        directoryId,
        path: relPath,
      });
      if (String(payload?.source || "") === "workspace_link") {
        return previewState.agentPreviewSource.normalize(payload, {
          path: String(payload?.path || path),
          source: "workspace_link",
        });
      }
      return previewState.agentPreviewSource.normalize(payload, {
        path: String(payload?.path || path),
        residentId,
        directoryId,
        relPath,
        source: "resident",
      });
    }

    async function loadResidentFile(path, options = {}) {
      const residentId = selectedResidentId();
      if (!residentId || !path) return;
      if (options.revealPanel !== false) ensurePreviewVisible();
      const { directoryId, relPath } = residentFileParts(path);
      if (!directoryId) return;
      stopPreviewRefresh();
      await openAgentPreviewDocument({
        key: `resident:${residentId}:${directoryId}:${relPath}`,
        mode: options.mode,
        path,
        title: fileBaseName(path),
        sourceType: "resident",
        residentId,
        directoryId,
        relPath,
        locator: options.locator || "",
        line: options.line,
        lineStart: options.lineStart,
        onLoading: () => {
          state.selectedFile = path;
          setPreviewTitle(fileBaseName(path));
          $id("previewPath").textContent = path;
          $id("previewBox").className = "previewBox";
          $id("previewBox").textContent = window.PortalI18n.t("加载中…");
        },
        load: async () => withInitialPreviewLine(
          await loadResidentFileDocument(path, residentId),
          options,
        ),
        onError: err => {
          $id("previewBox").textContent = window.PortalI18n.text`预览失败：${err.message || err}`;
        },
      });
    }

    return {
      loadFolder, loadResidentFile, loadResidentFileDocument,
    };
  }

  window.PortalLineageWorkbenchPreviewFiles = Object.freeze({ createPreviewFiles, dependencies: DEPENDENCIES });
})();
