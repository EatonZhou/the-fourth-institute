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
    const FAMILY_VISIBLE_REQUEST_STATES = new Set(["waiting_members", "delivering", "routing", "scope_review", "quota_review", "queued", "running", "recovery", "failed"]);

    // A withdrawn message never reaches the Resident. A Family facade, which
    // already displayed the request, keeps only a withdrawal annotation.
    // A message that started meanwhile is stopped instead (decision_2a3ff353165c).
    const QUEUE_WITHDRAWN_NOTICE = window.PortalI18n.t("已撤回，这条消息不会被处理");
    function noticeQueueWithdrawn(result) {
      if (result?.cancelled === true || String(result?.input_state?.state || "") === "cancelled") {
        window.PortalUI?.toast?.(QUEUE_WITHDRAWN_NOTICE, "success");
      }
    }

    function withFamilyRequestStatus(messages, inputStates) {
      const status = new Map();
      const live = new Set();
      const outputs = new Map();
      messages.forEach((item, index) => {
        const reply = item?.family_reply;
        const requests = new Set([reply?.request_message_id, ...(reply?.request_message_ids || [])].filter(Boolean));
        requests.forEach(request => {
          if (!outputs.has(request)) outputs.set(request, []);
          outputs.get(request).push(index);
        });
      });
      const groups = new Map();
      messages.forEach((item, index) => {
        const stored = item?.relay && typeof item.relay === "object" ? item.relay : null;
        const work = inputStates.get(item.id)?.work || stored?.work;
        // Delivery may complete as soon as an appended input reaches the turn.
        // The execution fact owns whether that turn is still producing replies.
        const relay = stored && work ? { ...stored, work, state: work.state } : stored;
        if (item?.role !== "user" || !relay || !FAMILY_VISIBLE_REQUEST_STATES.has(String(relay.state || ""))) return;
        const input = work?.execution_id || relay.grouped_into_message_id || relay.message_id || item.id;
        const key = JSON.stringify([relay.resident_id || "", input]);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push({ item, index, relay });
      });
      groups.forEach(requests => {
        // Prefer the actual group input for controls when it is visible; a
        // grouped sibling still carries its facade identity, never a worker ID.
        const owner = requests.find(({ relay }) => relay.work?.kind === 'primary') || requests.find(({ relay }) => !relay.grouped_into_message_id
          || relay.grouped_into_message_id === relay.message_id) || requests[0];
        const replies = [...new Set(requests.flatMap(({ item, relay }) => (outputs.get(item.id) || [])
          .filter(index => !messages[index].family_reply.resident_id || !relay.resident_id
            || messages[index].family_reply.resident_id === relay.resident_id)))];
        const end = replies.length ? Math.max(...replies) : owner.index;
        status.set(end, { ...owner.relay, request_message_id: owner.item.id });
        // Outputs of a request its member is running stay open, like a running turn's.
        if (owner.relay.state === "running") replies.forEach(output => live.add(output));
      });
      if (!status.size) return messages;
      return messages.map((item, index) => (status.has(index) || live.has(index) ? {
        ...item,
        ...(live.has(index) ? { live_output: true } : {}),
        ...(status.has(index) ? { family_status: status.get(index) } : {}),
      } : item));
    }

    function orderRequestBlocks(messages) {
      // A command input event can precede its receipt. The receipt explicitly
      // places that exchange; apply it to the already loaded input as well.
      const exchanges = new Map(messages.filter(item => item.role === 'system'
        && item.reply_to_message_id && item.conversation_context)
        .map(item => [item.reply_to_message_id, item.conversation_context]));
      messages = messages.map(item => !item.conversation_context && exchanges.has(item.id)
        ? { ...item, conversation_context: exchanges.get(item.id) } : item);
      // All Chat hosts use explicit reply placement. Inputs keep their order;
      // late replies return to their request, including after history merges.
      const inputs = new Map(messages.filter(item => item.role === 'user' && !item.conversation_context).map((item, index) => [item.id, index]));
      const blocks = new Map();
      const placed = new Set();
      const shared = new Map();
      messages.forEach((item, index) => {
        if ((item.role === 'user' && !item.conversation_context) || item.recipients?.length) return;
        const family = item.family_reply || item.family_context;
        const associations = family ? [...new Set([family.request_message_id, ...(family.request_message_ids || [])])]
          : [item.conversation_context?.request_message_id || item.display_reply_to_message_id || item.reply_to_message_id];
        const request = associations.filter(id => inputs.has(id)).sort((a, b) => inputs.get(a) - inputs.get(b))[0];
        if (!request) return;
        const shown = item.family_reply ? { ...item, family_reply: { ...family, request_message_id: request },
          display_reply_to_message_id: request } : item;
        if (!blocks.has(request)) blocks.set(request, []);
        blocks.get(request).push(shown);
        associations.filter(id => id !== request && inputs.has(id)).forEach(id => {
          if (!shared.has(id)) shared.set(id, shown.id);
        });
        placed.add(index);
      });
      // Only reorder occurrences of the same member within their own request.
      // Trace ordinals never compare across Residents or move request blocks.
      blocks.forEach(items => {
        const members = new Map();
        items.forEach((item, index) => {
          const position = item.conversation_position || item.family_position;
          if (!position?.resident_id || !Number.isFinite(position.ordinal)) return;
          if (!members.has(position.resident_id)) members.set(position.resident_id, []);
          members.get(position.resident_id).push({ item, index, ordinal: position.ordinal });
        });
        members.forEach(entries => {
          const ordered = [...entries].sort((a, b) => a.ordinal - b.ordinal);
          entries.forEach((entry, index) => { items[entry.index] = ordered[index].item; });
        });
      });
      const result = [];
      messages.forEach((item, index) => {
        if (placed.has(index)) return;
        result.push(shared.has(item.id) ? { ...item, shared_reply_message_id: shared.get(item.id) } : item);
        const replies = blocks.get(item.id);
        if (replies) result.push(...replies);
      });
      return result;
    }

    function projectResidentConversation(metadata = {}, snapshot = {}) {
      const base = metadata && typeof metadata === "object" ? metadata : {};
      const facade = !!(snapshot.family_facade && typeof snapshot.family_facade === "object");
      const familyMembers = Array.isArray(snapshot.family_facade?.members) ? snapshot.family_facade.members : null;
      const roleIssues = (snapshot.family_facade?.nodes || []).filter(node =>
        node.availability?.status && node.availability.status !== "available");
      const familyIssueLabel = roleIssues
        .map(node => `${node.role === "router" ? "Router" : "Steward"}：${node.availability.reason}`)
        .join("；");
      const policy = snapshot.family_facade?.chat_preferences;
      const solo = policy?.solo_member_id || '';
      const muted = new Set(policy?.muted_member_ids || []);
      const memberIds = new Set((familyMembers || []).map(m => m.resident_id));
      const visible = message => {
        const relay = message.family_reply || message.family_context || message.relay || message.family_history || {};
        if (['weixin', 'telegram'].includes(relay.request_channel?.kind)) return true;
        const chain = [...(relay.via || []).map(m => m.resident_id), relay.resident_id, message.origin?.initiated_by?.id];
        const owner = chain.find(id => memberIds.has(id) || id === solo);
        return !owner || (solo ? owner === solo : !muted.has(owner));
      };
      const unique = [];
      const positions = new Map();
      for (const message of Array.isArray(snapshot.messages) ? snapshot.messages : []) {
        const id = String(message?.message_id || "");
        if (id && positions.has(id)) {
          const index = positions.get(id);
          unique[index] = window.PortalResidentProtocol.mergeMessageAssociations(unique[index], message);
        } else {
          if (id) positions.set(id, unique.length);
          unique.push(message);
        }
      }
      const projected = foldDeliveredInfos(unique.filter(visible),
        { handedToSteward: facade }).map(residentProtocolMessageToWorkspace);
      const known = new Set(projected.map(message => message.id));
      for (const message of snapshot.local_submissions || []) {
        if (!known.has(message.message_id) && visible(message)) {
          projected.push(residentProtocolMessageToWorkspace(message));
          known.add(message.message_id);
        }
      }
      const ordered = orderRequestBlocks(projected);
      const receiving = new Set((snapshot.queue?.receiving || []).map(item => item.message_id));
      const inputStates = new Map((snapshot.input_states || []).map(item => [item.message_id, item]));
      for (const message of ordered) {
        const input = inputStates.get(message.id);
        if (receiving.has(message.id)) message.input_delivery = "pending";
        else if (input?.work?.delivery === "delivered") message.input_delivery = "delivered";
      }
      const messages = facade ? withFamilyRequestStatus(ordered, inputStates) : ordered;
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
              + window.PortalI18n.t("成员的回复显示在这里。当前没有可显示的门面对话；Solo/Mute 会影响这里显示的往来，直接发给成员的对话在各成员自己的对话里。")
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
          request_progress: {
            cursor: snapshot.cursor,
            input_states: Array.isArray(snapshot.input_states) ? snapshot.input_states : [],
            relays: (snapshot.messages || []).filter(item => item.role === 'user' && item.relay)
              .map(item => ({ ...item.relay, message_id: item.message_id })),
          },
          composer: {
            ...(base.chat_workspace.composer || {}),
            can_send: controls.can_send === true,
            input_read_only: controls.input_read_only === true,
            disabled_reason: controls.disabled_reason || "",
            send_hint: controls.send_hint ? window.PortalI18n.t(controls.send_hint) : "",
            execution_gate: state.execution_gate,
            quota_switch: controls.quota_switch,
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
