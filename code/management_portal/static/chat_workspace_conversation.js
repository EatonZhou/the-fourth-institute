(() => {
  if (window.PortalChatWorkspaceConversation) return;

  // A Resident's conversation as the Chat shows it: protocol messages as Chat
  // messages, infos folded into the request they came with, a Family facade's
  // request progress, and the Chat contract built from the snapshot.
  const DEPENDENCIES = Object.freeze([
    "DEFAULT_RESIDENT_HISTORY_PAGE_SIZE", "mediaOnlyCaption", "mediaPartPath",
    "normalizeResidentTarget", "subagentRunTitle",
  ]);

  function turnArtifactDisplay(part) {
    const hasPlan = !!(String(part.plan_text || "").trim() || String(part.explanation || "").trim() || part.plan?.length);
    const hasChanges = !!(String(part.diff || "").trim() || part.changes?.length);
    return {
      hasContent: hasPlan || hasChanges,
      title: hasPlan ? (hasChanges ? window.PortalI18n.t("本轮计划与修改") : window.PortalI18n.t("本轮计划")) : (hasChanges ? window.PortalI18n.t("本轮修改") : window.PortalI18n.t("本轮未记录到可展示的计划或文件差异")),
    };
  }

  const FAMILY_REQUEST_ACTIONS = Object.freeze({
    // CancelInput withdraws a waiting request and stops a running one.
    "family-request-withdraw": "cancelInput",
    "family-request-stop": "cancelInput",
    "family-request-inject": "injectInput",
  });

  function noticeQueueCleared(result) {
    const count = Number(result.cancelled);
    window.PortalUI?.toast?.(window.PortalI18n.text`已清除 ${count} 条排队消息，正在执行的消息不受影响。`, "success");
  }

  // Infos delivered with a request were read with it and show inside it (concept_43c793b4f3ab).
  // Every read folds them on the server, but an info enters the trace when its request
  // starts, so it arrives as an Event after that request: fold it here the same way.
  // An info waiting again (held_info) stays a message of its own.
  function foldDeliveredInfos(messages, { handedToSteward = false } = {}) {
    const present = new Set(messages.map(message => String(message?.message_id || "")));
    const infos = new Map();
    messages.forEach(message => {
      const request = String(message?.released_with || "");
      if (!request || message?.held_info === true || !present.has(request)) return;
      if (!infos.has(request)) infos.set(request, []);
      infos.get(request).push(message);
    });
    if (!infos.size) return messages;
    const folded = new Set([...infos.values()].flat());
    const view = info => Object.fromEntries(["message_id", "author_id", "origin", "created_at", "content_parts"]
      .filter(key => key in info).map(key => [key, info[key]]));
    return messages.filter(message => !folded.has(message)).map(message => {
      const attached = infos.get(String(message?.message_id || ""));
      if (!attached) return message;
      const shown = Array.isArray(message.attached_infos) ? message.attached_infos : [];
      const known = new Set(shown.map(info => String(info?.message_id || "")));
      const added = attached.filter(info => !known.has(String(info.message_id || ""))).map(view);
      return {
        ...message,
        attached_infos: [...shown, ...added],
        ...(handedToSteward ? { attached_infos_steward: true } : {}),
      };
    });
  }

  function createConversation(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceConversation is missing: ${missing.join(", ")}`);
    const {
      DEFAULT_RESIDENT_HISTORY_PAGE_SIZE, mediaOnlyCaption, mediaPartPath,
      normalizeResidentTarget, subagentRunTitle,
    } = dependencies;

    function residentProtocolMessageToWorkspace(message = {}) {
      const parts = Array.isArray(message.content_parts) ? message.content_parts : [];
      const media = parts.filter(part => part?.type === "image" || part?.type === "file");
      const text = parts.filter(part => part?.type === "text").map(part => String(part.text || "")).join("\n");
      const legacyMediaCaption = media.map(part => `[${part.type}: ${part.name || part.type}]`).join(" ");
      // Media-only delivery has a technical text part for Resident execution.
      // The thumbnail already labels the attachment in the conversation.
      const captionParts = media.length && [legacyMediaCaption, mediaOnlyCaption(media)].includes(text.trim())
        ? parts.filter(part => part.type !== "text")
        : parts;
      let previousType = "";
      const orderedContent = captionParts.map(part => {
        let value = "";
        if (part?.type === "text") {
          value = `${previousType === "text" ? "\n" : ""}${String(part.text || "")}`;
        } else if (part?.type === "image" || part?.type === "file") {
          const path = mediaPartPath(part);
          if (path && !text.includes(path)) value = `[${part.type}: ${path}]`;
        } else return "";
        previousType = part.type;
        return value;
      }).join("");
      // “审查这些修改” sends only the reviewed turn; any text beside it is Portal's wording for the agent.
      const content = (parts.some(part => part?.type === "turn_review") ? window.PortalI18n.t("请审查这一轮的修改") : "")
        || orderedContent
        || parts.filter(part => part?.type === "async_questions").flatMap(part => part.questions || []).map(question => String(question.title || "")).join("\n")
        || parts.filter(part => ["native_turn", "resource_changes"].includes(part.type)).map(part => turnArtifactDisplay(part).title).join("\n")
        || parts.filter(part => part?.type === "subagent_run").map(subagentRunTitle).join("\n")
        || parts.filter(part => nativeInteractionNames.has(part.tool_name)).map(part => part.type === "tool_call"
          ? part.interaction ? window.PortalI18n.t("原生交互请求") : (part.questions || []).map(question => question.question).join("\n")
          : ({ answered: window.PortalI18n.t("回答已送达"), cancelled: window.PortalI18n.t("提问已取消"), expired: window.PortalI18n.t("提问已失效") }[part.status] || window.PortalI18n.t("提问已结束"))).join("\n");
      return {
        ...message,
        ...(message.local_delivery ? { pending: true } : {}),
        id: String(message.message_id || ""),
        role: message.role === "agent" ? "assistant" : String(message.role || "system"),
        author: String(message.origin?.sender?.id || message.author_id || ""),
        content,
        input_media: media,
        token_usage: message.usage || {},
      };
    }

    // A Family facade shows each unfinished request's progress after
    // the request and the outputs written for it so far — routing, its place in
    // the member's queue, the member's live work — not one block below all
    // messages.  The block belongs to the request's last shown message, so a new
    // output moves it and re-renders both.
    const FAMILY_OPEN_REQUEST_STATES = new Set(["waiting_members", "delivering", "routing", "scope_review", "quota_review", "queued", "running", "recovery"]);

    // A withdrawn message never reaches the Resident. A Family facade, which
    // already displayed the request, keeps only a withdrawal annotation.
    // A message that started meanwhile is stopped instead (decision_2a3ff353165c).
    const QUEUE_WITHDRAWN_NOTICE = window.PortalI18n.t("已撤回，这条消息不会被处理");
    function noticeQueueWithdrawn(result) {
      if (result?.cancelled === true || String(result?.input_state?.state || "") === "cancelled") {
        window.PortalUI?.toast?.(QUEUE_WITHDRAWN_NOTICE, "success");
      }
    }

    function withFamilyRequestStatus(messages) {
      const status = new Map();
      const live = new Set();
      messages.forEach((item, index) => {
        const relay = item?.relay && typeof item.relay === "object" ? item.relay : null;
        if (item?.role !== "user" || !relay || !FAMILY_OPEN_REQUEST_STATES.has(String(relay.state || ""))) return;
        let end = index;
        while (end + 1 < messages.length
          && String(messages[end + 1]?.family_reply?.request_message_id || "") === item.id) end += 1;
        status.set(end, { ...relay, request_message_id: item.id });
        // Outputs of a request its member is running stay open, like a running turn's.
        if (relay.state === "running") for (let output = index + 1; output <= end; output += 1) live.add(output);
      });
      if (!status.size) return messages;
      return messages.map((item, index) => (status.has(index) || live.has(index) ? {
        ...item,
        ...(live.has(index) ? { live_output: true } : {}),
        ...(status.has(index) ? { family_status: status.get(index) } : {}),
      } : item));
    }

    function projectResidentConversation(metadata = {}, snapshot = {}) {
      const base = metadata && typeof metadata === "object" ? metadata : {};
      const facade = !!(snapshot.family_facade && typeof snapshot.family_facade === "object");
      const familyMembers = Array.isArray(snapshot.family_facade?.members) ? snapshot.family_facade.members : null;
      const roleIssues = (snapshot.family_facade?.nodes || []).filter(node =>
        node.availability?.status && node.availability.status !== "available");
      const familyIssueLabel = [
        ...roleIssues.map(node => `${node.role === "router" ? "Router" : "Steward"}：${node.availability.reason}`),
        snapshot.family_facade?.alert_contact?.status === "missing" ? window.PortalI18n.t("未登记真人告警联系人") : "",
      ].filter(Boolean).join("；");
      const projected = foldDeliveredInfos(Array.isArray(snapshot.messages) ? snapshot.messages : [],
        { handedToSteward: facade }).map(residentProtocolMessageToWorkspace);
      const known = new Set(projected.map(message => message.id));
      for (const message of snapshot.local_submissions || []) {
        if (!known.has(message.message_id)) projected.push(residentProtocolMessageToWorkspace(message));
      }
      const messages = facade ? withFamilyRequestStatus(projected) : projected;
      const activities = !facade && Array.isArray(snapshot.activities) ? snapshot.activities : [];
      const state = window.PortalResidentProtocol.stateView(snapshot.state);
      const controls = snapshot.controls || {};
      // A facade's work shows under each request instead.
      const live = facade ? false : state.working;
      const phase = facade ? "" : (state.work_state === "stopping" ? "stopping" : state.work_phase);
      const queue = {
        ...(snapshot.queue && typeof snapshot.queue === "object"
          ? snapshot.queue
          : { resident_id: String(snapshot.resident?.resident_id || ""), items: [] }),
        active_turn_input_supported: controls.active_turn_input === true,
      };
      const runtimeSettings = snapshot.runtime_settings || {};
      const contract = base.chat_workspace && typeof base.chat_workspace === "object"
        ? {
          ...base.chat_workspace,
          messages,
          ...(facade && familyMembers ? { rendering: {
            ...(base.chat_workspace.rendering || {}),
            assistant_name: snapshot.family_facade.title || base.chat_workspace.rendering?.assistant_name || "Family",
            empty_text: window.PortalI18n.text`这是 Family「${snapshot.family_facade.title || base.chat_workspace.rendering?.assistant_name || "Family"}」的门面。发到这里的消息由 Family Router 交给最合适的成员处理，`
              + window.PortalI18n.t("成员的回复显示在这里。还没有经过门面的对话；直接发给成员的对话在各成员自己的对话里。")
              + (familyMembers.length ? window.PortalI18n.text`成员：${familyMembers.map(member => member.title || member.resident_id || "").join("、")}。` : ""),
          } } : {}),
          ...(facade && (familyMembers || state.family_activity || familyIssueLabel) ? { header: {
            ...(base.chat_workspace.header || {}),
            subtitle_parts: (base.chat_workspace.header?.subtitle_parts || []).map(part => {
              if (part.identity !== "family") return part;
              let text = familyMembers
                ? (familyMembers.length ? window.PortalI18n.text`Family 门面（${familyMembers.length} 位成员）` : window.PortalI18n.t("Family 门面"))
                : window.PortalI18n.message(part.text);
              if (state.family_activity) text += ` · ${{ running: window.PortalI18n.t("运行中"), queued: window.PortalI18n.t("排队中"), idle: window.PortalI18n.t("空闲") }[state.display_work_state]}${state.family_activity.running_count > 1 ? window.PortalI18n.text`（${state.family_activity.running_count} 个节点）` : ""}`;
              return { ...part, text };
            }).concat(familyIssueLabel ? [{ text: familyIssueLabel, identity: "family_availability" }] : []),
          } } : {}),
          runtime: { live, details: activities, phase },
          composer: {
            ...(base.chat_workspace.composer || {}),
            can_send: controls.can_send === true,
            disabled_reason: controls.disabled_reason || "",
            send_hint: controls.send_hint ? window.PortalI18n.t(controls.send_hint) : "",
            execution_gate: state.execution_gate,
            intake_state: state.intake_state,
            can_stop: controls.can_stop === true,
            queue,
            runtime_settings: runtimeSettings,
          },
          history: {
            can_load_older: Boolean(String(snapshot.next_page_token || "").trim()),
          },
          inheritance: snapshot.inheritance && typeof snapshot.inheritance === "object" ? snapshot.inheritance : null,
        }
        : null;
      return {
        ...base,
        ...(Object.prototype.hasOwnProperty.call(snapshot, "family_facade") ? { family_facade: snapshot.family_facade } : {}),
        messages,
        runtime_activity: { live, details: activities, phase },
        resident_state: state,
        queue,
        input_states: Array.isArray(snapshot.input_states) ? snapshot.input_states : [],
        runtime_settings: runtimeSettings,
        ...(contract ? { chat_workspace: contract } : {}),
      };
    }

    async function loadResidentConversation(target, options = {}) {
      const normalized = normalizeResidentTarget(target);
      if (!normalized.resident_id) throw new TypeError("resident_id required");
      const [projection, snapshot] = await Promise.all([
        window.PortalResidentProjection.load(normalized.resident_id, {
          force: options.force === true,
        }),
        window.PortalResidentProtocol.loadSnapshot(normalized.resident_id, {
          force: options.force === true,
          historyLimit: Math.max(
            1,
            Number(options.historyLimit || DEFAULT_RESIDENT_HISTORY_PAGE_SIZE),
          ),
        }),
      ]);
      return projectResidentConversation(projection, snapshot);
    }

    const nativeInteractionNames = new Set(["request_user_input", "request_approval", "mcp_elicitation"]);

    return Object.freeze({
      turnArtifactDisplay, residentProtocolMessageToWorkspace, FAMILY_REQUEST_ACTIONS,
      noticeQueueWithdrawn, noticeQueueCleared, projectResidentConversation,
      loadResidentConversation, nativeInteractionNames,
    });
  }

  window.PortalChatWorkspaceConversation = Object.freeze({
    createConversation, dependencies: DEPENDENCIES, turnArtifactDisplay, FAMILY_REQUEST_ACTIONS,
    noticeQueueCleared,
  });
})();
