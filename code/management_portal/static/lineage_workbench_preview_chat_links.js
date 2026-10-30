(() => {
  "use strict";

  if (window.PortalLineageWorkbenchPreviewChatLinks) return;

  // Links in Chat messages and Markdown previews: opening them in the
  // Preview, the Preview host other panels open documents through, and the
  // Chat view's workspace messages.

  const DEPENDENCIES = Object.freeze([
    "currentAgentChatPanel", "currentLineageWorkbench", "ensurePreviewVisible", "fileBaseName",
    "linkLooksLikeRelativeLocalPath", "loadLocalLinkPreview", "loadResidentFile", "localFilePathFromLink",
    "openAgentPreviewDocument", "openResourcePreview", "previewRelativeTargetFromLink", "previewState",
    "residentFilePartsForService", "residentService", "residentVirtualPathFromLink", "safeDecodeLink", "state",
    "withInitialPreviewLine",
  ]);

  function createPreviewChatLinks(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalLineageWorkbenchPreviewChatLinks is missing: ${missing.join(", ")}`);
    const {
      currentAgentChatPanel, currentLineageWorkbench, ensurePreviewVisible, fileBaseName,
      linkLooksLikeRelativeLocalPath, loadLocalLinkPreview, loadResidentFile, localFilePathFromLink,
      openAgentPreviewDocument, openResourcePreview, previewRelativeTargetFromLink, previewState,
      residentFilePartsForService, residentService, residentVirtualPathFromLink, safeDecodeLink, state,
      withInitialPreviewLine,
    } = dependencies;

    async function renderChatLinkPreview(href, label = "", options = {}) {
      const displayHref = safeDecodeLink(href);
      const displayLabel = String(label || "").trim();
      return openAgentPreviewDocument({
        key: `link-card:${href}`,
        mode: options.mode,
        path: displayHref || href,
        title: displayLabel || window.PortalI18n.t("链接"),
        sourceType: "link-card",
        href,
        load: async () => window.PortalPreviewContract.normalizeDocument({
          source: "remote_link_card",
          path: displayHref || href,
          href,
          title: displayLabel || window.PortalI18n.t("链接"),
          kind: "remote-link",
          binary: true,
        }),
      });
    }

    async function openChatMarkdownLink(href, label = "", options = {}) {
      const hrefLocator = String(href || "").trim().match(/(#L\d+(?:-L\d+)?|:\d+(?::\d+)?)$/i)?.[1] || "";
      const previewOptions = {
        ...options,
        locator: options.locator || hrefLocator,
      };
      const resourceAddress = String(
        window.PortalResourceChips?.resourceAddressFromPreviewRequest?.({
          resourceAddress: options.resourceAddress,
          href,
        })
        || "",
      ).trim();
      if (resourceAddress) {
        return openResourcePreview({
          mode: options.mode,
          address: resourceAddress,
          load: () => window.PortalResourceChips.loadPreview(resourceAddress),
          navigate: () => window.location.assign(
            href || `/r/${encodeURIComponent(resourceAddress)}`,
          ),
        }, { navigateOnUnsupported: true });
      }
      const relativeTarget = options.previewPayload
        ? previewRelativeTargetFromLink(href, options.previewPayload)
        : null;
      if (relativeTarget?.type === "local") {
        return loadLocalLinkPreview(relativeTarget.path, label || fileBaseName(relativeTarget.path), previewOptions);
      }
      if (relativeTarget?.type === "resident") {
        return loadResidentFile(relativeTarget.path, previewOptions);
      }
      const targetFile = residentVirtualPathFromLink(href);
      if (targetFile) {
        return loadResidentFile(targetFile, previewOptions);
      }
      const localPath = await localFilePathFromLink(href);
      if (localPath) {
        return loadLocalLinkPreview(localPath, label, previewOptions);
      }
      return renderChatLinkPreview(href, label, previewOptions);
    }

    function bindChatMarkdownLinks(root, previewDocument = null) {
      root?.querySelectorAll(".messageContent a[href], .previewBox.markdownBody a[href]").forEach(anchor => {
        if (anchor.matches(".portalResourceChip")) return;
        if (anchor.dataset.portalResourceLinkBound === "1") return;
        if (anchor.dataset.portalFileLinkBound === "1") return;
        if (anchor.dataset.portalPreviewLinkBound === "1") return;
        if (anchor.dataset.chatLinkPreviewBound === "1") return;
        anchor.dataset.chatLinkPreviewBound = "1";
        anchor.addEventListener("contextmenu", event => {
          const href = anchor.getAttribute("href") || "";
          if (!window.PortalFileLinks.describe(href, { allowAnyLocalPath: true })
            && !linkLooksLikeRelativeLocalPath(href)) return;
          window.PortalPreview.openFileMenu({
            event, trigger: anchor, request: { href, title: anchor.textContent || "" },
            onOpen: request => openChatMarkdownLink(request.href, request.title, {
              ...request, previewPayload: previewDocument,
            }),
          });
        });
        anchor.addEventListener("click", event => {
          if (
            event.button !== 0
            || event.metaKey
            || event.ctrlKey
            || event.shiftKey
            || event.altKey
          ) return;
          event.preventDefault();
          event.stopPropagation();
          const href = anchor.getAttribute("href") || "";
          const descriptor = window.PortalFileLinks?.describe?.(href, { allowAnyLocalPath: true });
          openChatMarkdownLink(href, anchor.textContent || "", {
            previewPayload: previewDocument,
            locator: descriptor?.locator || "",
          });
        });
      });
    }

    function bindWorkspacePreviewHost() {
      currentLineageWorkbench()?.getPanel("preview")?.registerPreviewHost("lineage", async request => {
        if (String(request.locatorKind || "") === "lineage-workspace") {
          const lineageId = String(request.lineageId || "").trim();
          const residentId = String(request.residentId || "").trim();
          const path = String(request.path || "").trim();
          if (!lineageId || !residentId || !path) return false;
          ensurePreviewVisible();
          await openAgentPreviewDocument({
            key: `lineage:${lineageId}:${residentId}:${path}`,
            mode: request.mode,
            path,
            title: request.title || fileBaseName(path),
            sourceType: "lineage-workspace",
            lineageId,
            residentId,
            locator: request.locator || "",
            line: request.line,
            lineStart: request.lineStart,
            reload: request.reload === true,
            load: async () => withInitialPreviewLine(previewState.agentPreviewSource.normalize(
              await residentService.file({ residentId, ...residentFilePartsForService(path) }),
              {
                path,
                lineage_id: lineageId,
                residentId,
                source: "lineage-workspace",
              },
            ), request),
          });
          return true;
        }
        await openChatMarkdownLink(
          request.href || request.path || "",
          request.title || "",
          {
            resourceAddress: request.resourceAddress || "",
            mode: request.mode,
            locator: request.locator || "",
            line: request.line,
            lineStart: request.lineStart,
          },
        );
        return true;
      }, { priority: 100 });
    }

    function syncLineageChatWorkspaceProjection(detail) {
      const workspaceItems = Array.isArray(detail?.chat_workspace?.messages)
        ? detail.chat_workspace.messages
        : [];
      state.workspaceMessageItems = workspaceItems.map((item, idx) => ({
        key: window.PortalChatWorkspace.messageIdentity(item),
        item,
        idx,
      }));
    }

    function refreshLineageChatView() {
      const detail = currentAgentChatPanel()?.refreshView?.();
      if (!detail) return null;
      state.detail = detail;
      syncLineageChatWorkspaceProjection(detail);
      return detail;
    }

    return {
      bindChatMarkdownLinks, bindWorkspacePreviewHost, refreshLineageChatView, syncLineageChatWorkspaceProjection,
    };
  }

  window.PortalLineageWorkbenchPreviewChatLinks = Object.freeze({ createPreviewChatLinks, dependencies: DEPENDENCIES });
})();
