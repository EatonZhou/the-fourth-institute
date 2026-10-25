(function () {
  "use strict";

  if (window.PortalResidentService) return;

  const restartRegistries = new WeakMap();
  // All clients in this page share the same Resident lifecycle, including
  // clients with their own API adapter and the graph's instruction client.
  const residentReads = new Map();
  // Retire a remotely deleted target before ordinary invalidation listeners
  // dispatch refresh reads. Identity changes alone are not deletion evidence.
  window.addEventListener("portal:change", event => {
    for (const [id, scopes] of Object.entries(event?.detail?.resident_changes || {})) {
      if (Array.isArray(scopes) && scopes.includes("deleted")) readState(id).deleted = true;
    }
  }, { capture: true });
  function readState(residentId) {
    const id = String(residentId || "").trim();
    if (!id) throw new TypeError("resident_id required");
    if (!residentReads.has(id)) residentReads.set(id, { reads: new Set(), deletion: null, deleted: false });
    return residentReads.get(id);
  }
  function deletedResidentError(residentId) {
    return Object.assign(new Error("Resident has been deleted"), {
      name: "ResidentDeletedError", code: "resident.deleted", residentId,
    });
  }
  async function readResident(residentId, operation) {
    let state = readState(residentId);
    while (state.deletion) {
      await state.settled;
      state = readState(residentId);
    }
    if (state.deleted) throw deletedResidentError(residentId);
    let finish;
    const pending = new Promise(resolve => { finish = resolve; });
    state.reads.add(pending);
    try {
      const result = await operation();
      if (state.deleted) throw deletedResidentError(residentId);
      return result;
    }
    finally {
      state.reads.delete(pending);
      finish();
      if (!state.reads.size && !state.deletion && !state.deleted) residentReads.delete(String(residentId).trim());
    }
  }
  function withResidentDeletion(residentId, operation) {
    const state = readState(residentId);
    if (state.deletion) return state.deletion;
    if (state.deleted) return Promise.reject(deletedResidentError(residentId));
    let release;
    state.settled = new Promise(resolve => { release = resolve; });
    state.deletion = (async () => {
      try {
        // Let already dispatched reads finish while the Resident still exists.
        // New reads wait for the write outcome instead of racing its commit.
        await Promise.allSettled([...state.reads]);
        const result = await operation();
        state.deleted = true;
        return result;
      } finally {
        state.deletion = null;
        release();
        if (!state.deleted && !state.reads.size) residentReads.delete(String(residentId).trim());
      }
    })();
    return state.deletion;
  }

  function createClient(options = {}) {
    const configuredApi = options.api || null;
    const registryKey = configuredApi || window;
    if (!restartRegistries.has(registryKey)) restartRegistries.set(registryKey, new Map());
    const restarts = restartRegistries.get(registryKey);
    const mergeRestart = (previous, next) => {
      if (previous?.restart_id && previous.restart_id === next?.restart_id
        && ["completed", "failed", "cancelled"].includes(previous.status)
        && ["accepted", "cancelling"].includes(next.status)) return previous;
      return next;
    };
    const storageKey = residentId => `portal:pending-session-restart:${residentId}`;
    const persistPending = entry => {
      if (configuredApi) return;
      try {
        if (entry.pending) sessionStorage.setItem(storageKey(entry.residentId), JSON.stringify(entry.pending));
        else sessionStorage.removeItem(storageKey(entry.residentId));
      } catch (_) {}
    };
    const restartView = entry => JSON.parse(JSON.stringify({
      config: entry.config,
      restart: entry.pending ? {
        status: entry.pending.phase, source: entry.config?.source || {},
        target: { provider: entry.pending.payload.target_provider, account_id: entry.pending.payload.target_account },
        submitted_at: entry.pending.submittedAt,
      } : entry.restart,
      lastSyncedAt: entry.lastSyncedAt,
      error: entry.error,
    }));
    const notifyRestartListener = (entry, listener) => {
      try { listener(restartView(entry)); }
      catch (error) { console.error(window.PortalI18n.t("Resident restart subscriber failed"), error); }
    };
    const notifyRestart = entry => {
      for (const listener of entry.listeners) notifyRestartListener(entry, listener);
    };
    const definitiveRestartError = error => {
      const status = Number(error.status);
      return status >= 400 && status < 500 && status !== 408;
    };
    const restartEntry = residentId => {
      if (restarts.has(residentId)) return restarts.get(residentId);
      const entry = {
        residentId, config: null, restart: null, pending: null, error: "", lastSyncedAt: 0,
        listeners: new Set(), timer: null, inflight: null, revision: 0,
      };
      if (!configuredApi) {
        try {
          const saved = JSON.parse(sessionStorage.getItem(storageKey(residentId)) || "null");
          if (saved?.payload?.resident_id === residentId && saved.payload.idempotency_key) {
            entry.pending = { ...saved, phase: "confirming" };
          }
        } catch (_) {}
      }
      entry.refresh = () => {
        if (entry.inflight) return entry.inflight;
        clearTimeout(entry.timer);
        const revision = entry.revision;
        entry.inflight = client.sessionRestartOptions(residentId).then(config => {
          if (revision !== entry.revision) return;
          entry.restart = mergeRestart(entry.restart, config.restart || null);
          entry.config = { ...config, restart: entry.restart };
          entry.lastSyncedAt = Date.now();
          entry.error = "";
          if (entry.pending && config.restart?.request_id === entry.pending.payload.idempotency_key) {
            entry.pending = null;
            persistPending(entry);
          }
        }).catch(error => {
          if (revision !== entry.revision) return;
          entry.error = String(error.message || error);
          if (Number(error.status) === 403 || Number(error.status) === 404) entry.denied = true;
        }).finally(() => {
          entry.inflight = null;
          notifyRestart(entry);
          if (!entry.denied && (entry.pending || ["accepted", "cancelling"].includes(entry.restart?.status))) {
            entry.timer = setTimeout(entry.refresh, 1500);
          }
        });
        return entry.inflight;
      };
      entry.onChange = event => {
        const detail = event?.detail || {};
        const scopes = detail.resident_changes?.[residentId];
        const restartChanged = Array.isArray(scopes) && scopes.includes("session_restart");
        if (restartChanged || (detail.snapshot_required && (entry.pending || entry.restart?.status === "accepted"))) {
          entry.refresh();
        }
      };
      restarts.set(residentId, entry);
      return entry;
    };
    const transport = method => {
      const api = configuredApi || window.PortalAPI;
      if (typeof api?.[method] !== "function") {
        throw new TypeError(`PortalResidentService requires PortalAPI.${method}`);
      }
      return api;
    };

    const required = (value, name) => {
      const clean = String(value || "").trim();
      if (!clean) throw new TypeError(`${name} required`);
      return clean;
    };
    // Instructions page with cursor/next_cursor; the conversation views keep calling it next_page_token.
    const pageTokenOf = result => {
      if (!result || typeof result !== "object") return result;
      const { next_cursor: nextCursor, ...rest } = result;
      return { ...rest, next_page_token: String(nextCursor || "") };
    };
    const query = values => {
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(values || {})) {
        if (value === undefined || value === null || value === "") continue;
        if (Array.isArray(value)) value.forEach(item => params.append(key, String(item)));
        else params.set(key, String(value));
      }
      return params.toString();
    };
    const get = (path, values = null) => {
      const suffix = values ? query(values) : "";
      const operation = () => transport("get").get(`${path}${suffix ? `?${suffix}` : ""}`, {
        cache: "no-store",
        // Separate simultaneous reads of the same projection in browser/server
        // diagnostics. This identifies an HTTP attempt, not a business action.
        ...(["/api/agent-workbench/residents/projection", "/api/agent-workbench/residents/navigation-target"].includes(path)
          ? { headers: { "X-Portal-Request-ID": globalThis.PortalUIPrimitives.randomUUID() } } : {}),
      });
      const residentId = path.startsWith("/api/agent-workbench/residents/") ? values?.id || values?.resident_id
        : path === "/api/agent-workbench/agent-runtime-catalog" ? values?.resident_id : "";
      return residentId ? readResident(residentId, operation) : operation();
    };
    const post = (path, payload = {}) => transport("post").post(path, payload);
    const residentPath = suffix => `/api/agent-workbench/residents${suffix || ""}`;
    // Mutations use ISA; application workflows compose the owning instructions.
    const executeInstruction = options.execute
      || ((name, args = {}, controls = {}) => window.PortalInstructionClient.execute(name, args, controls));
    const execute = (name, args = {}, controls = {}) => {
      const operation = () => executeInstruction(name, args, controls);
      if (name === "DeleteResident") return withResidentDeletion(args.resident_id, operation);
      if (args.resident_id && /^(Get|List|Search|Preview)/.test(name)) return readResident(args.resident_id, operation);
      return operation();
    };
    const watch = options.watch
      || ((name, args, onBatch, controls) => window.PortalInstructionClient.watch(name, args, onBatch, controls));
    const watchboardRuns = new Map();
    const newKey = prefix => `${prefix}:${globalThis.PortalUIPrimitives.randomUUID()}`;
    const keyed = (payload, prefix) => ({ idempotencyKey: String(payload?.idempotency_key || "").trim() || newKey(prefix) });
    const ids = values => [...new Set((values || []).map(String).map(value => value.trim()).filter(Boolean))];
    // A Resident's backend is the provider name the ISA uses; CreateResident checks it.
    const providerOf = payload => (payload?.backend ? { provider: String(payload.backend) } : {});
    const workspacePaths = payload => ids([
      ...(Array.isArray(payload?.workspace_paths) ? payload.workspace_paths : []),
    ]);
    const residentRecord = async (residentId, lineageId) => {
      const result = await execute("GetResident", { resident_id: residentId });
      return { ...(result?.resident || {}), resident_id: residentId, lineage_id: result?.resident?.lineage_id || lineageId };
    };
    // A Resident's file roots: its Resident Folder (the Resident's ID), its
    // Lineage Folder (the Lineage's ID) and its Workspaces. The file <path> of a
    // root is the address <root ID>/file/<path>, read with ReadResource.
    const FILE_PREVIEW_BYTES = 2 * 1024 * 1024;
    const EDITABLE_KINDS = new Set(["markdown", "json", "jsonl", "text"]);
    const trimSlashes = value => String(value || "").replace(/^\/+|\/+$/g, "");
    const parentPath = path => {
      const clean = trimSlashes(path);
      return clean.includes("/") ? `/${clean.slice(0, clean.lastIndexOf("/"))}` : "";
    };
    const baseName = path => trimSlashes(path).split("/").pop() || "";
    const fileAddress = (rootId, relPath = "") => `${rootId}/file${trimSlashes(relPath) ? `/${trimSlashes(relPath)}` : ""}`;
    const workspaceRoot = (directoryId, rootKind, label, path, extra = {}) => ({
      directory_id: directoryId, workspace_id: rootKind === "workspace" ? directoryId : "",
      label, source_label: label, path, root_kind: rootKind,
      is_default_cwd: false, is_lineage_workspace: rootKind === "lineage_folder",
      exists: true, is_dir: true, truncated: false, hidden_count: 0, children_loaded: false, has_children: true,
      ...extra,
    });
    const workspaceMap = resident => {
      const residentId = String(resident.resident_id || "");
      const lineageId = String(resident.lineage_id || "");
      const bound = Array.isArray(resident.workspaces) ? resident.workspaces : [];
      // The owned Workspace is <Lineage Folder>/residents/<Resident ID>/workspace.
      const residentPath = parentPath(bound.find(item => item.is_owned)?.path);
      const residentsPath = parentPath(residentPath);
      const lineagePath = baseName(residentsPath) === "residents" ? parentPath(residentsPath) : "";
      const workspaces = [...bound]
        .sort((a, b) => Number(Boolean(b.is_owned)) - Number(Boolean(a.is_owned))
          || baseName(a.path).localeCompare(baseName(b.path)))
        .map(item => workspaceRoot(String(item.workspace_id), "workspace", String(item.title || item.label || baseName(item.path) || item.workspace_id), String(item.path || ""), {
          is_default_cwd: Boolean(item.is_owned), ...(item.purpose ? { purpose: item.purpose } : {}),
        }));
      const residentFolder = workspaceRoot(residentId, "resident_folder", "Resident Folder", residentPath);
      const lineageFolder = workspaceRoot(lineageId, "lineage_folder", "Lineage Folder", lineagePath);
      const roots = [residentFolder, lineageFolder, ...workspaces];
      const defaultWorkspace = workspaces.find(item => item.is_default_cwd) || null;
      return {
        resident_id: residentId, lineage_id: lineageId,
        workspace_id: defaultWorkspace?.workspace_id || "",
        default_cwd: defaultWorkspace?.path || "", root_path: defaultWorkspace?.path || "",
        default_workspace: defaultWorkspace, workspaces, resident_folder: residentFolder, lineage_folder: lineageFolder,
        primary_directory_id: defaultWorkspace?.directory_id || "",
        roots,
        entries: roots.map(root => ({
          directory_id: root.directory_id, path: root.directory_id, rel_path: "", name: root.label, label: root.label,
          type: "directory", kind: "directory", depth: 0, size: 0, workspace_root: true, root_kind: root.root_kind,
          is_default_cwd: root.is_default_cwd, is_lineage_workspace: root.is_lineage_workspace, root_path: root.path,
          children_loaded: false, has_children: true,
        })),
        truncated: false, hidden_count: 0, listing_mode: "lazy",
      };
    };
    const workspaceEntry = (directoryId, entry) => ({
      ...entry, directory_id: directoryId, rel_path: entry.path, path: `${directoryId}/${entry.path}`,
      label: entry.name, type: entry.kind, depth: 0, mtime: entry.modified_at,
      children_loaded: false, has_children: entry.kind === "directory",
    });
    const workspaceFile = (residentId, directoryId, relPath, result, metadata) => {
      const kind = String(result.kind || "binary");
      const contentUrl = String(result.content_url || "");
      const allows = result.allows || {};
      const editable = directoryId.startsWith("workspace_") && !metadata && EDITABLE_KINDS.has(kind)
        && !result.binary && !result.truncated && allows.UpdateWorkspaceFile === true;
      return {
        resident_id: residentId, directory_id: directoryId, rel_path: relPath, path: `${directoryId}/${relPath}`,
        address: fileAddress(directoryId, relPath), kind,
        entry: { name: result.name, path: relPath, kind, type: kind, size: result.size, mtime: result.modified_at, mtime_ns: result.mtime_ns },
        ...(metadata ? { meta_only: true } : {
          binary: Boolean(result.binary), truncated: Boolean(result.truncated), content: String(result.text || ""),
        }),
        raw_url: contentUrl, download_url: contentUrl ? `${contentUrl}&download=1` : "",
        // allows carries the server's answer for UpdateWorkspaceFile on this address; editable adds the preview's own limits.
        editable, allows: { UpdateWorkspaceFile: allows.UpdateWorkspaceFile === true },
      };
    };
    const client = {
      // A question is answered by number; an approval or elicitation, or a cancel, answers with its action.
      // `action: "status"` reads whether that reply reached the waiting turn.
      respondUserInput(payload = {}) {
        const residentId = required(payload.resident_id, "resident_id");
        const messageId = required(payload.message_id, "message_id");
        const action = String(payload.action || "answer");
        if (action === "status") {
          return execute("GetResidentRequest", { resident_id: residentId, message_id: messageId });
        }
        const answers = ["accept", "decline", "cancel"].includes(action)
          ? { action, ...(payload.content === undefined || payload.content === null ? {} : { content: payload.content }) }
          : payload.answers || {};
        return execute("AnswerResidentRequest", {
          resident_id: residentId,
          message_id: messageId,
          answers,
        }, keyed(payload, "resident-request-answer"));
      },
      list(values = {}) { return get(residentPath(""), values); },
      activeAgents() {
        return get("/api/agent-status/active-agents");
      },
      navigationTarget(residentId) {
        return get(residentPath("/navigation-target"), { id: required(residentId, "resident_id") });
      },
      projection({ residentId, lineageId = "" } = {}) {
        return get(residentPath("/projection"), {
          id: required(residentId, "resident_id"),
          lineage_id: lineageId,
        });
      },
      runtimeAccounts() { return execute("ListProviderAccounts", {}); },
      runtimeSettings(residentId) {
        return execute("GetResidentSettings", { resident_id: required(residentId, "resident_id") });
      },
      runtimeCatalog({ residentId = "", backend = "", accountId = "" } = {}) {
        return get("/api/agent-workbench/agent-runtime-catalog", {
          resident_id: residentId, backend, account_id: accountId,
        });
      },
      updateRuntimeSettings(residentId, changes, idempotencyKey) {
        return execute("UpdateResidentSettings", {
          resident_id: required(residentId, "resident_id"), changes,
        }, { idempotencyKey: idempotencyKey || newKey("resident-settings") });
      },
      snapshot(residentId, { historyLimit = 200 } = {}) {
        return execute("GetConversation", {
          resident_id: required(residentId, "resident_id"),
          history_limit: Math.max(1, Number(historyLimit || 200)),
        }).then(pageTokenOf);
      },
      // A Fork's inheritance with its opening compaction's readable messages.
      inheritedContext(residentId) {
        return execute("GetConversation", {
          resident_id: required(residentId, "resident_id"),
          history_limit: 1,
          include_inherited_context: true,
        }).then(result => result?.inheritance || null);
      },
      // One read of the events after a cursor: the first batch of
      // WatchConversationEvents, which the server sends at once with a checkpoint.
      events(residentId, { afterCursor = 0, limit = 500 } = {}) {
        const id = required(residentId, "resident_id");
        const cursor = Math.max(0, Number(afterCursor || 0));
        const size = Math.max(1, Number(limit || 500));
        return new Promise((resolve, reject) => {
          let settled = false;
          const subscription = watch("WatchConversationEvents", { resident_id: id, after_cursor: cursor, limit: size }, items => {
            if (settled) return;
            settled = true;
            subscription.close("first_batch_read");
            const checkpoint = [...items].reverse().find(item => item?.high_watermark !== undefined) || {};
            resolve({
              resident_id: id,
              events: items.filter(item => item?.event).map(item => item.event),
              cursor: Number(checkpoint.cursor ?? cursor),
              high_watermark: Number(checkpoint.high_watermark ?? cursor),
            });
          }, { includeEmptyBatches: true });
          subscription.done.then(() => {
            if (!settled) { settled = true; reject(new Error("WatchConversationEvents ended before its first batch")); }
          }, error => {
            if (!settled) { settled = true; reject(error); }
          });
        });
      },
      inputState(residentId, messageId) {
        return execute("GetInputState", {
          resident_id: required(residentId, "resident_id"),
          message_id: required(messageId, "message_id"),
        }).then(result => result?.input_state || {});
      },
      conversationMessages(residentId, { pageToken = "", limit = 200 } = {}) {
        return execute("ListMessages", {
          resident_id: required(residentId, "resident_id"),
          cursor: required(pageToken, "cursor"),
          limit: Math.max(1, Number(limit || 200)),
        }).then(pageTokenOf);
      },
      inputHistory(residentId, { pageToken = "", limit = 50 } = {}) {
        return execute("ListInputHistory", {
          resident_id: required(residentId, "resident_id"),
          ...(pageToken ? { cursor: pageToken } : {}),
          limit: Math.max(1, Number(limit || 50)),
        }).then(pageTokenOf);
      },
      searchMessages(residentIds, searchQuery, { pageToken = "", limit = 120 } = {}) {
        return execute("SearchMessages", {
          resident_ids: ids(residentIds),
          query: String(searchQuery || ""),
          ...(pageToken ? { cursor: pageToken } : {}),
          limit: Math.max(1, Number(limit || 120)),
        }).then(pageTokenOf);
      },
      sendInput(payload = {}) {
        return execute("DeliverMessage", {
          resident_id: required(payload.resident_id, "resident_id"),
          message: payload.message,
          ...(payload.activation_policy ? { activation_policy: payload.activation_policy } : {}),
          ...(payload.business_ref ? { business_ref: payload.business_ref } : {}),
          ...(payload.temporary_credentials?.length ? { temporary_credentials: payload.temporary_credentials } : {}),
        }, keyed(payload, "deliver"));
      },
      injectInput(payload = {}) {
        return execute("InjectMailboxItem", {
          resident_id: required(payload.resident_id, "resident_id"),
          message_id: required(payload.message_id, "message_id"),
        }, keyed(payload, "queue-inject"));
      },
      cancelInput(payload = {}) {
        return execute("CancelInput", {
          resident_id: required(payload.resident_id, "resident_id"),
          message_id: required(payload.message_id, "message_id"),
        }, keyed(payload, "turn-cancel"));
      },
      mergeMailboxItems(payload = {}) {
        return execute("MergeMailboxItems", {
          resident_id: required(payload.resident_id, "resident_id"),
          message_ids: Array.isArray(payload.message_ids) ? payload.message_ids.map(String) : [],
        }, keyed(payload, "queue-merge"));
      },
      splitMailboxItem(payload = {}) {
        return execute("SplitMailboxItem", {
          resident_id: required(payload.resident_id, "resident_id"),
          message_id: required(payload.message_id, "message_id"),
        }, keyed(payload, "queue-split"));
      },
      reorderMailbox(payload = {}) {
        const move = payload.before_message_id
          ? { before_message_id: String(payload.before_message_id) }
          : { after_message_id: String(payload.after_message_id || "") };
        return execute("ReorderMailbox", {
          resident_id: required(payload.resident_id, "resident_id"),
          message_id: required(payload.message_id, "message_id"),
          ...move,
        }, keyed(payload, "queue-reorder"));
      },
      // A Family request given to the wrong member goes to the one named here (decision_44cf587c1721).
      reassignFamilyRequest(payload = {}) {
        return execute("RelayFamilyMessage", {
          resident_id: required(payload.resident_id, "resident_id"),
          message_id: required(payload.message_id, "message_id"),
          member_id: required(payload.member_id, "member_id"),
          reassign: true,
          ...(String(payload.reason || "").trim() ? { reason: String(payload.reason).trim() } : {}),
        }, keyed(payload, "family-reassign"));
      },
      // The Mailbox tab (decision_b5d349190743): what waits, the round in progress and the recent rounds.
      mailbox(residentId) {
        return execute("GetMailbox", { resident_id: required(residentId, "resident_id") });
      },
      requeueMessage(payload = {}) {
        return execute("RequeueMessage", {
          resident_id: required(payload.resident_id, "resident_id"),
          message_id: required(payload.message_id, "message_id"),
        }, keyed(payload, "queue-requeue"));
      },
      message(residentId, messageId) {
        return execute("GetMessage", {
          resident_id: required(residentId, "resident_id"),
          message_id: required(messageId, "message_id"),
        }).then(result => result?.message || {});
      },
      // What the Provider actually received for a delivered message, and what Portal adds under current settings.
      sentPrompt(residentId, messageId) {
        return execute("GetSentPrompt", {
          resident_id: required(residentId, "resident_id"),
          message_id: required(messageId, "message_id"),
        }).then(result => result?.prompt || {});
      },
      promptInjectionPreview(residentId, turn = "later") {
        return execute("PreviewPromptInjection", { resident_id: required(residentId, "resident_id"), turn });
      },
      clearMailbox(payload = {}) {
        return execute("ClearMailbox", { resident_id: required(payload.resident_id, "resident_id") }, keyed(payload, "queue-clear"));
      },
      loadStates(residentIds, { includeViewerState = true, readObservation } = {}) {
        return execute("ListResidentStates", {
          resident_ids: ids(residentIds),
          ...(includeViewerState ? {} : { include_viewer_state: false }),
        }, { readObservation });
      },
      setIntake(payload = {}) {
        return execute("OpenIntake", {
          resident_id: required(payload.resident_id, "resident_id"),
          open: Boolean(payload.open),
        }, keyed(payload, "intake"));
      },
      // A new Resident in its own new Lineage: CreateLineage, then CreateResident.
      async attach(payload = {}) {
        const lineageId = required(payload.lineage_id, "lineage_id");
        const created = await execute("CreateResident", {
          lineage_id: lineageId,
          title: required(payload.title, "title"),
          ...providerOf(payload),
          ...(payload.backend_route_id ? { account_id: payload.backend_route_id } : {}),
          ...(payload.runtime_settings && Object.keys(payload.runtime_settings).length ? { settings: payload.runtime_settings } : {}),
          ...(workspacePaths(payload).length ? { workspace_paths: workspacePaths(payload) } : {}),
        }, keyed(payload, "resident-attach"));
        return { resident: await residentRecord(String(created?.resident_id || ""), lineageId) };
      },
      async remove(payload = {}) {
        const residentId = required(payload.resident_id, "resident_id");
        const lineageId = payload.lineage_id || (await residentRecord(residentId, "")).lineage_id;
        return execute("DeleteResident", { lineage_id: required(lineageId, "lineage_id"), resident_id: residentId }, keyed(payload, "resident-delete"));
      },
      async update(payload = {}) {
        const residentId = required(payload.resident_id, "resident_id");
        const lineageId = payload.lineage_id || (await residentRecord(residentId, "")).lineage_id;
        let record = null;
        if (payload.title) {
          record = (await execute("UpdateResident", { lineage_id: required(lineageId, "lineage_id"), resident_id: residentId, title: payload.title }, keyed(payload, "resident-update")))?.resident;
        }
        if (payload.target_lineage_id || payload.target_lineage_resident_id) {
          let targetLineageId = String(payload.target_lineage_id || "");
          if (!targetLineageId) {
            targetLineageId = String((await execute("GetResident", { resident_id: payload.target_lineage_resident_id }))?.resident?.lineage_id || "");
          }
          const moved = await execute("MoveResident", {
            lineage_id: required(lineageId, "lineage_id"), resident_id: residentId, target_lineage_id: required(targetLineageId, "target_lineage_id"),
            ...(payload.allow_empty_source ? { allow_empty_source: true } : {}),
          }, keyed(payload, "resident-move"));
          return { ...(record || {}), ...moved, resident_id: residentId };
        }
        if (payload.project_id) {
          const current = await residentRecord(residentId, "");
          await execute("UpdateLineage", { lineage_id: current.lineage_id, changes: { project_id: payload.project_id } }, keyed(payload, "lineage-project"));
        }
        return residentRecord(residentId, "");
      },
      sessionRestartOptions(residentId) {
        return get(residentPath("/session-restart-options"), { id: required(residentId, "resident_id") });
      },
      observeSessionRestart(residentId, listener) {
        const entry = restartEntry(required(residentId, "resident_id"));
        if (!entry.listeners.size) window.addEventListener("portal:change", entry.onChange);
        entry.listeners.add(listener);
        notifyRestartListener(entry, listener);
        entry.refresh();
        return () => {
          entry.listeners.delete(listener);
          if (!entry.listeners.size) window.removeEventListener("portal:change", entry.onChange);
          if (!entry.listeners.size && !entry.pending
            && !["accepted", "cancelling"].includes(entry.restart?.status)) clearTimeout(entry.timer);
        };
      },
      refreshSessionRestart(residentId) {
        return restartEntry(residentId).refresh();
      },
      rememberSessionRestart(residentId, receipt) {
        const entry = restartEntry(residentId);
        entry.revision += 1;
        entry.restart = mergeRestart(entry.restart, receipt);
        entry.pending = null;
        entry.error = "";
        entry.lastSyncedAt = Date.now();
        persistPending(entry);
        notifyRestart(entry);
        if (["accepted", "cancelling"].includes(receipt?.status)) entry.refresh();
      },
      retrySessionRestart(residentId) {
        const pending = restartEntry(residentId).pending;
        if (!pending) throw new Error("没有待确认的迁移请求，请刷新状态");
        return client.restartSession(pending.payload);
      },
      async restartSession(payload = {}) {
        const source = payload && typeof payload === "object" ? payload : {};
        const residentId = source.resident_id;
        const options = Object.fromEntries([
          "idempotency_key", "target_provider", "target_account",
          "context_strategy", "active_execution_policy", "resume_failed_input",
          "model", "reasoning_effort", "prompt_context", "backend_options",
        ].filter(name => Object.prototype.hasOwnProperty.call(source, name))
          .map(name => [name, source[name]]));
        options.idempotency_key ||= `portal-session-restart:${globalThis.PortalUIPrimitives.randomUUID()}`;
        const entry = restartEntry(required(residentId, "resident_id"));
        if (entry.pending && entry.pending.payload.idempotency_key !== options.idempotency_key) {
          throw new Error("上次迁移的受理结果仍待确认，请先查询或重试同一请求");
        }
        entry.revision += 1;
        entry.pending = {
          payload: { resident_id: residentId, ...options }, phase: "submitting",
          submittedAt: entry.pending?.submittedAt || new Date().toISOString(),
        };
        entry.error = "";
        persistPending(entry);
        notifyRestart(entry);
        const pendingRequest = entry.pending;
        try {
          const receipt = await execute("RestartSession", {
            resident_id: residentId,
            ...(options.target_provider ? { provider: options.target_provider } : {}),
            ...(options.target_account ? { account_id: options.target_account } : {}),
            ...(options.context_strategy ? { context_strategy: options.context_strategy } : {}),
            ...(options.active_execution_policy ? { active_execution_policy: options.active_execution_policy } : {}),
            ...(options.model ? { model: options.model } : {}),
            ...(options.reasoning_effort ? { reasoning_effort: options.reasoning_effort } : {}),
            ...(options.prompt_context ? { prompt_context: options.prompt_context } : {}),
            ...(options.backend_options ? { backend_options: options.backend_options } : {}),
            ...(typeof options.resume_failed_input === "boolean" ? { resume_failed_input: options.resume_failed_input } : {}),
          }, { idempotencyKey: options.idempotency_key });
          client.rememberSessionRestart(residentId, receipt);
          return receipt;
        } catch (error) {
          if (entry.restart?.request_id === options.idempotency_key
            && ["accepted", "completed"].includes(entry.restart.status)) {
            client.rememberSessionRestart(residentId, entry.restart);
            return entry.restart;
          }
          const definitive = definitiveRestartError(error);
          if (!definitive || Number(error.status) === 409) {
            try {
              const config = await client.sessionRestartOptions(residentId);
              entry.restart = mergeRestart(entry.restart, config.restart || null);
              entry.config = { ...config, restart: entry.restart };
              if (config.restart?.request_id === options.idempotency_key) {
                client.rememberSessionRestart(residentId, config.restart);
                return config.restart;
              }
            } catch (_) {}
          }
          entry.pending = definitive ? null : { ...pendingRequest, phase: "confirming" };
          entry.error = definitive ? String(error.message || error) : "受理结果待确认，正在查询；请勿提交其他迁移。";
          error.restartPending = !definitive;
          persistPending(entry);
          notifyRestart(entry);
          if (!definitive || entry.restart?.status === "accepted") entry.refresh();
          throw error;
        }
      },
      async cancelSessionRestart(payload = {}) {
        const source = payload && typeof payload === "object" ? payload : {};
        const residentId = required(source.resident_id, "resident_id");
        const restartId = required(source.restart_id, "restart_id");
        const receipt = await execute("CancelSessionRestart", {
          resident_id: residentId,
          restart_id: restartId,
        }, keyed(source, "portal-session-restart-cancel"));
        const entry = restartEntry(residentId);
        entry.revision += 1;
        entry.restart = mergeRestart(entry.restart, receipt);
        entry.pending = null;
        entry.error = "";
        entry.lastSyncedAt = Date.now();
        persistPending(entry);
        notifyRestart(entry);
        entry.refresh();
        return receipt;
      },
      async updateWatchboard(action, payload) {
        if (!new Set(["add", "remove"]).has(action)) throw new TypeError("unsupported watchboard action");
        const residentId = required(payload.resident_id, "resident_id");
        const current = await get(residentPath("/watchboard"), { id: residentId });
        const plan = current.watchboard?.installation;
        if (!plan) throw new Error("This Watchboard is managed by its infrastructure service");
        if (action === "add") {
          for (const file of plan.files) await execute("UpdateWorkspaceFile", file, keyed({}, "watchboard-file"));
        }
        const operations = action === "add" ? plan.install : plan.remove;
        let definition = null;
        try {
          definition = operations.length ? await execute("ApplyLineageChanges", {
            lineage_id: plan.lineage_id, near_resident_id: residentId, operations,
          }, { ...keyed(payload, "watchboard-definition"), expectedVersion: plan.revision }) : null;
        } catch (error) {
          if (["instruction.conflict", "instruction.version_conflict"].includes(error.code)) {
            // Keep the failed write and its receipt. A fresh definition is for
            // review only: never replay the older installation/removal plan.
            error.watchboardConflict = {};
            try {
              error.watchboardConflict.lineage = (await execute("GetLineage", { lineage_id: plan.lineage_id })).lineage;
            } catch (refreshError) {
              error.watchboardConflict.refreshError = refreshError;
            }
          }
          throw error;
        }
        // ApplyLineageChanges returns affected IDs, not a Lineage snapshot.
        // Read the committed definition before measuring the newly added cards.
        // A read failure must not turn an accepted installation into a retry.
        let lineage = null;
        let lineageRefreshError = null;
        if (definition) {
          try {
            lineage = (await execute("GetLineage", { lineage_id: plan.lineage_id })).lineage;
            if (lineage?.lineage_id !== plan.lineage_id) throw new Error("Watchboard graph identity mismatch");
          } catch (error) {
            lineage = null;
            lineageRefreshError = error;
          }
        }
        return { ...await client.watchboard(residentId),
          lineage, lineageRefreshError,
          created_component_ids: action === "add" ? operations
            .filter(operation => operation.op === "create" && operation.kind === "component")
            .map(operation => operation.value.id) : [],
        };
      },
      forwardTargets({ scopeId = "", lineageId = "", residentId = "", preferredLineageId = "" } = {}) {
        return get(residentPath("/forward-targets"), {
          scope_id: String(scopeId || "").trim(),
          lineage_id: String(lineageId || "").trim(),
          resident_id: String(residentId || "").trim(),
          preferred_lineage_id: String(preferredLineageId || "").trim(),
        });
      },
      forwardChatrooms() {
        return window.PortalIdentity.userId().then(userId => execute("ListChatrooms", { user_id: userId }));
      },
      // Selected Chat messages carry their own ID; their Resident is the Chat's
      // (source_resident_id) unless an item names another one.
      forward(payload = {}) {
        const messages = Array.isArray(payload.messages) ? payload.messages : [];
        return execute("ForwardMessages", {
          ...(payload.target_chatroom_id
            ? { target_chatroom_id: required(payload.target_chatroom_id, "target_chatroom_id") }
            : { target_resident_id: required(payload.target_resident_id, "target_resident_id") }),
          messages: messages.map(item => ({
            ...(item.chatroom_id ? { chatroom_id: required(item.chatroom_id, "chatroom_id") }
              : { resident_id: required(item.resident_id || payload.source_resident_id, "resident_id") }),
            message_id: required(item.message_id || item.id, "message_id"),
          })),
          ...(payload.note || payload.message ? { note: String(payload.note || payload.message) } : {}),
          ...(payload.activation_policy ? { activation_policy: payload.activation_policy } : {}),
        }, keyed(payload, "forward"));
      },
      resetContext(payload = {}) {
        return execute("ClearContext", { resident_id: required(payload.resident_id, "resident_id") }, keyed(payload, "context-clear"));
      },
      async workspaces(residentId) {
        const result = await execute("GetResident", { resident_id: required(residentId, "resident_id") });
        return workspaceMap({ ...(result?.resident || {}), resident_id: residentId });
      },
      async listDirectory(directoryId, relPath = "") {
        const rootId = required(directoryId, "directory_id");
        const listing = await execute("ReadResource", { address: fileAddress(rootId, relPath) });
        return {
          directory_id: rootId, path: trimSlashes(relPath) ? `${rootId}/${trimSlashes(relPath)}` : rootId,
          entries: (listing.entries || []).map(entry => workspaceEntry(rootId, entry)),
          truncated: Boolean(listing.truncated), hidden_count: Number(listing.hidden_count || 0),
        };
      },
      async folder(residentId) {
        const workspace = await client.workspaces(residentId);
        const roots = await Promise.all(workspace.roots.map(async root => {
          try {
            const listing = await client.listDirectory(root.directory_id);
            return { ...root, entries: listing.entries, truncated: listing.truncated, hidden_count: listing.hidden_count, children_loaded: true };
          } catch (_) {
            // A root whose folder is missing or unreadable stays listed, empty.
            return { ...root, exists: false, has_children: false, entries: [] };
          }
        }));
        return { ...workspace, roots, entries: roots.flatMap(root => root.entries || []) };
      },
      async file({ residentId = "", directoryId, path, metadata = false } = {}) {
        const rootId = required(directoryId, "directory_id");
        const relPath = trimSlashes(required(path, "path"));
        const result = await execute("ReadResource", {
          address: fileAddress(rootId, relPath),
          ...(metadata ? { meta_only: true } : { preview: true, max_bytes: FILE_PREVIEW_BYTES }),
        });
        return workspaceFile(String(residentId || ""), rootId, relPath, result, metadata);
      },
      saveFile(payload = {}) {
        return execute("UpdateWorkspaceFile", {
          address: required(payload.address, "address"),
          content: String(payload.content ?? ""),
        }, {
          ...keyed(payload, "workspace-file"),
          ...(payload.expected_version ? { expectedVersion: payload.expected_version } : {}),
        });
      },
      async watchboard(residentId) {
        const response = await get(residentPath("/watchboard"), { id: required(residentId, "resident_id") });
        const board = response.watchboard;
        if (board?.execution_model !== "lineage" || !board.enabled) return response;
        const lineageId = board.lineage_id;
        const batches = await Promise.all((board.commands || []).map(async command => {
          const list = await execute("ListScriptRuns", {lineage_id: lineageId, effect_id: command.effect_id, limit: 5});
          return (list.runs || []).map(run => ({...run, run_id: run.script_run_id, command: command.id}));
        }));
        const runs = batches.flat();
        const pending = watchboardRuns.get(residentId);
        if (pending) {
          const detail = await execute("GetTriggerPath", {lineage_id: lineageId, trigger_path_id: pending.trigger_path_id});
          const calls = window.PortalScriptRuns.effectCalls(detail).items;
          const call = calls.find(item => item.script_run_id);
          if (call) {
            if (!runs.some(item => item.script_run_id === call.script_run_id)) {
              const run = await execute("GetScriptRun", {lineage_id: lineageId, script_run_id: call.script_run_id});
              runs.push({...run, run_id: run.script_run_id, command: pending.command});
            }
            watchboardRuns.delete(residentId);
          } else {
            const failed = calls.find(item => item.status === "failed")
              || detail.steps?.items?.find(item => item.status === "failed");
            runs.push({...pending, status: failed || detail.path?.status === "failed" ? "failed" : "queued",
              error: failed?.error?.message || failed?.error || detail.path?.error || ""});
          }
        }
        runs.sort((a, b) => String(b.started_at || b.created_at || "").localeCompare(String(a.started_at || a.created_at || "")));
        board.recent_runs = runs;
        board.signature += JSON.stringify(runs.map(run => [run.run_id, run.status, run.finished_at]));
        return response;
      },
      async saveWatchboardConfig(payload) {
        await execute("UpdateWorkspaceFile", {
          address: required(payload.address, "address"), content: String(payload.content ?? ""),
        }, { ...keyed(payload, "watchboard-config"), expectedVersion: required(payload.expected_version, "expected_version") });
        return client.watchboard(payload.resident_id);
      },
      watchboardApplication(payload) {
        return post(residentPath("/watchboard-application"), payload);
      },
      async runWatchboard(payload) {
        const residentId = required(payload.resident_id, "resident_id");
        const current = await get(residentPath("/watchboard"), {id: residentId});
        const board = current.watchboard;
        const command = board?.commands?.find(item => item.id === payload.command_id);
        if (!command?.event_source_id) throw new Error("Watchboard command is not installed");
        const receipt = await execute("TriggerEvent", {
          lineage_id: board.lineage_id, event_source_id: command.event_source_id,
          prompt: JSON.stringify(payload.args || {}),
        }, keyed(payload, "watchboard-trigger"));
        watchboardRuns.set(residentId, {trigger_path_id: receipt.trigger_path_id, command: command.id,
          status: "queued", created_at: new Date().toISOString()});
        return client.watchboard(residentId);
      },
      watchboardOutput({lineage_id, script_run_id, stream = "stdout", offset = 0}) {
        return execute("ReadScriptRunOutput", {lineage_id, script_run_id, stream, offset, limit: 16384});
      },
      async cancelWatchboardRun({resident_id, lineage_id, script_run_id}) {
        await execute("CancelScriptRun", {lineage_id, script_run_id}, keyed({}, "watchboard-cancel"));
        return client.watchboard(resident_id);
      },
    };
    return Object.freeze(client);
  }

  const defaultClient = createClient();
  window.PortalResidentService = Object.freeze({ ...defaultClient, createClient, withResidentDeletion });
})();
