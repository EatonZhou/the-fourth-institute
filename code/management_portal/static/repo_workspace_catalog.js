(() => {
  "use strict";

  if (window.PortalRepoWorkspaceCatalog) return;

  // The Repo records behind the board: every page of the records matching
  // the query and Project filter, cancelling a superseded query, the semantic
  // search scope, the descriptions generated for cards and the automation
  // statuses of dirty checkouts. A reply to a superseded query is dropped.
  const DEPENDENCIES = Object.freeze([
    "elements", "getJson", "loadWorkspace", "postJson", "queryState", "renderProjectOptions",
    "renderRepoBoard", "renderWorkspace", "repoStateHtml", "resourceServiceRecovery", "showToast", "state",
    "updateAutomationControls", "updateModeControls", "writeQuery",
  ]);

  function createCatalog(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalRepoWorkspaceCatalog is missing: ${missing.join(", ")}`);
    const {
      elements, getJson, loadWorkspace, postJson, queryState, renderProjectOptions, renderRepoBoard,
      renderWorkspace, repoStateHtml, resourceServiceRecovery, showToast, state, updateAutomationControls,
      updateModeControls, writeQuery,
    } = dependencies;

    const REPO_QUERY_LIMIT = 500;

    function cancelRepoQuery() {
      state.requestController?.abort();
      state.requestController = null;
      state.requestSequence += 1;
      state.descriptionPending.forEach(id => state.descriptionAttempted.delete(id));
      state.descriptionPending.clear();
      state.automationStatusLoading = false;
    }

    async function queryAllRepoRecords({q = "", project = "", signal} = {}) {
      const recordsById = new Map();
      let offset = 0;
      while (true) {
        const params = new URLSearchParams({
          resource_type: "repo",
          include_attributes: "1",
          limit: String(REPO_QUERY_LIMIT),
          offset: String(offset),
        });
        if (q) params.set("q", q);
        if (project) params.set("project", project);
        const payload = await getJson(`/api/resource-index/query?${params}`, {signal});
        const page = Array.isArray(payload.resources) ? payload.resources : [];
        const previousSize = recordsById.size;
        page.forEach(record => {
          const id = String(record?.resource_id || "").trim();
          if (id) recordsById.set(id, record);
        });
        const total = Number(payload.total);
        offset += page.length;
        if (
          !page.length
          || page.length < REPO_QUERY_LIMIT
          || recordsById.size === previousSize
          || (Number.isFinite(total) && offset >= total)
        ) break;
      }
      return [...recordsById.values()];
    }

    function initializeSemanticSearch() {
      state.semanticSearch = window.PortalSemanticSearch?.create({
        input: elements.query,
        button: elements.semanticButton,
        panel: elements.semanticPanel,
        identity: elements.semanticIdentity,
        identityOptions: {
          titleId: "repoSemanticSearchTitle",
          idId: "repoSemanticSearchId",
        },
        projectionScope: "repo",
        client: { get: getJson, post: postJson },
        initialActiveId: queryState().semantic,
        urls: {
          list: "/api/resource-semantic-searches?resource_scope=repo",
          start: "/api/resource-semantic-search/start",
          refresh: "/api/resource-semantic-search/refresh",
          delete: "/api/resource-semantic-search/delete",
        },
        startPayload: () => ({ resource_scope: "repo" }),
        actionPayload: () => ({ resource_scope: "repo" }),
        onStart: () => {
          elements.query.value = "";
          state.mode = "board";
          writeQuery({ q: "", semantic: "", item: "", tab: "", path: "", file: "" });
          loadRepos();
        },
        onActiveChange: searchId => {
          state.mode = "board";
          if (searchId) {
            elements.query.value = "";
            writeQuery({ q: "", semantic: searchId, item: "", tab: "", path: "", file: "" });
          } else {
            writeQuery({ semantic: "", item: "" });
          }
          applyVisibleRecords();
        },
        onError: (_error, message) => showToast(message, "error"),
      });
    }

    function applyVisibleRecords({ render = true } = {}) {
      state.visibleRecords = state.semanticSearch
        ? state.semanticSearch.filterAndSort(
            state.records,
            record => record.resource_id,
          )
        : state.records.slice();
      elements.count.textContent = state.semanticSearch?.activeId()
        ? window.PortalI18n.text`${state.visibleRecords.length} 个语义匹配`
        : window.PortalI18n.text`${state.records.length} 个 Repo`;
      updateModeControls();
      if (!render) return;
      if (state.mode === "board") renderRepoBoard();
      else if (state.overview?.id === state.selectedId) renderWorkspace();
    }

    async function loadRepoDescriptions(records, {
      render = true,
      querySequence = state.requestSequence,
      signal,
    } = {}) {
      if (querySequence !== state.requestSequence) return null;
      const missing = (Array.isArray(records) ? records : []).filter(record => {
        const id = String(record?.resource_id || "").trim();
        return id
          && !String(record?.summary || "").trim()
          && !state.descriptionAttempted.has(id);
      });
      if (!missing.length) return null;
      const ids = missing.map(record => String(record.resource_id));
      ids.forEach(id => {
        state.descriptionAttempted.add(id);
        state.descriptionPending.add(id);
        state.descriptionFailed.delete(id);
      });
      if (render && state.mode === "board") renderRepoBoard();
      try {
        // Generation may span multiple server-limited model calls.
        const payload = await postJson("/api/repos/descriptions/generate", {
          repo_ids: ids,
        }, { signal, timeoutMs: 0 });
        if (querySequence !== state.requestSequence) return null;
        const descriptions = new Map(
          (Array.isArray(payload.repos) ? payload.repos : [])
            .map(item => [String(item?.id || ""), String(item?.description || "").trim()])
            .filter(([id, description]) => id && description),
        );
        state.records.forEach(record => {
          const description = descriptions.get(String(record.resource_id || ""));
          if (description) record.summary = description;
        });
        const failed = new Set(
          (Array.isArray(payload.errors) ? payload.errors : [])
            .map(item => String(item?.id || ""))
            .filter(Boolean),
        );
        ids.forEach(id => {
          if (!descriptions.has(id) && failed.has(id)) {
            state.descriptionFailed.add(id);
          }
        });
        return payload;
      } catch (error) {
        if (querySequence !== state.requestSequence || error?.name === "AbortError") {
          ids.forEach(id => state.descriptionAttempted.delete(id));
        } else {
          ids.forEach(id => state.descriptionFailed.add(id));
        }
        throw error;
      } finally {
        ids.forEach(id => state.descriptionPending.delete(id));
        if (querySequence === state.requestSequence) applyVisibleRecords({ render });
      }
    }

    function queryVersionIsCurrent(querySequence) {
      return querySequence == null || querySequence === state.requestSequence;
    }

    async function loadAutomationStatuses(records, {
      render = true,
      querySequence = null,
      signal,
    } = {}) {
      if (!queryVersionIsCurrent(querySequence)) return [];
      const values = Array.isArray(records) ? records : [];
      const ids = [...new Set(values.map(record => (
        typeof record === "string" ? record : record?.resource_id
      )).map(value => String(value || "").trim()).filter(Boolean))];
      const names = new Map(values
        .filter(record => record && typeof record === "object" && record.resource_id)
        .map(record => [String(record.resource_id), String(record.title || record.resource_id)]));
      if (!ids.length) {
        state.automationStatusLoaded = true;
        updateAutomationControls();
        return [];
      }
      state.automationStatusLoading = true;
      updateAutomationControls();
      if (render && state.mode === "board") renderRepoBoard();
      const output = [];
      try {
        for (let offset = 0; offset < ids.length; offset += 120) {
          const params = new URLSearchParams();
          ids.slice(offset, offset + 120).forEach(id => params.append("repo_id", id));
          const payload = await getJson(`/api/repos/automation/status?${params}`, { signal });
          if (!queryVersionIsCurrent(querySequence)) return [];
          const statuses = Array.isArray(payload.repos) ? payload.repos : [];
          output.push(...statuses);
        }
        if (!queryVersionIsCurrent(querySequence)) return [];
        names.forEach((name, id) => state.automationNames.set(id, name));
        output.forEach(item => {
          if (item?.id) state.automationStatuses.set(String(item.id), item);
        });
        state.automationStatusLoaded = true;
        return output;
      } finally {
        if (queryVersionIsCurrent(querySequence)) {
          state.automationStatusLoading = false;
          updateAutomationControls();
          if (render && state.mode === "board") renderRepoBoard();
        }
      }
    }

    async function loadRepos({
      keepWorkspace = true,
      propagateServiceUnavailable = false,
    } = {}) {
      cancelRepoQuery();
      const controller = new AbortController();
      state.requestController = controller;
      const sequence = state.requestSequence;
      const current = queryState();
      elements.query.value = current.q;
      elements.count.textContent = window.PortalI18n.t("正在加载…");
      try {
        const records = await queryAllRepoRecords({...current, signal: controller.signal});
        if (sequence !== state.requestSequence) return;
        state.records = records;
        renderProjectOptions(state.records);
        const requestedId = current.item;
        if (requestedId) {
          if (requestedId !== state.selectedId) state.overview = null;
          state.selectedId = requestedId;
          state.mode = "workspace";
        }
        applyVisibleRecords({ render: false });
        if (state.mode === "board") {
          renderRepoBoard();
        } else if (
          keepWorkspace
          && state.selectedId
          && state.overview?.id === state.selectedId
        ) {
          renderWorkspace();
        } else if (state.selectedId) {
          await loadWorkspace();
        } else {
          state.mode = "board";
          renderRepoBoard();
        }
        loadAutomationStatuses(state.records, {
          querySequence: sequence,
          signal: controller.signal,
        })
          .catch(() => {
            if (sequence !== state.requestSequence) return;
            state.automationStatusLoaded = false;
            updateAutomationControls();
          });
        loadRepoDescriptions(state.records, {
          querySequence: sequence,
          signal: controller.signal,
        }).catch(error => {
          if (sequence !== state.requestSequence || error?.name === "AbortError") return;
          if (state.mode === "board") {
            showToast(window.PortalI18n.t("部分 Repo 简介暂未生成，可刷新页面重试。"), "error");
          }
        });
      } catch (error) {
        if (sequence !== state.requestSequence) return;
        if (resourceServiceRecovery.isUnavailable(error)) {
          if (propagateServiceUnavailable) throw error;
          void resourceServiceRecovery.check();
          return;
        }
        elements.count.textContent = window.PortalI18n.t("暂不可用");
        elements.workspace.innerHTML = repoStateHtml(error.message || error, {
          state: "error",
          title: window.PortalI18n.t("Repo 资源不可用"),
        });
      }
    }

    return Object.freeze({
      cancelRepoQuery, initializeSemanticSearch, loadAutomationStatuses, loadRepos, queryAllRepoRecords,
    });
  }

  window.PortalRepoWorkspaceCatalog = Object.freeze({ createCatalog, dependencies: DEPENDENCIES });
})();
