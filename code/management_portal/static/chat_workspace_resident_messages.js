(() => {
  if (window.PortalChatWorkspaceResidentMessages) return;

  // How a Resident's messages show: the body by kind, the footers for delivery,
  // infos, Mailbox groups, Family replies, recipients and token use, and a
  // request's progress after its last shown message.
  const DEPENDENCIES = Object.freeze([
    "escapeHtml", "footerTime", "messageQuotable", "messageRole", "messageSubmenuHtml",
    "nativeInteractionNames", "outwardToResidents", "parseForwardReference",
    "PRE_PROVIDER_RUNTIME_PHASES", "resourceChips", "RUNTIME_PHASE_STATUS", "runtimeDetailsHtml",
    "subagentRunCard", "turnArtifactDisplay",
  ]);

  // A Family facade Conversation reads like any Resident's (decision_44cf587c1721):
  // requests keep their sender, and member replies are the facade's replies.
  // Replies are projected from the member's Conversation, never copied.
  function familyRelayRoute(value) {
    const names = items => (Array.isArray(items) ? items : [])
      .map(item => String(item?.title || item?.resident_id || "")).filter(Boolean);
    const via = names(value?.via), moved = names(value?.reassigned_from);
    return (via.length ? `（经 ${via.join(" → ")}）` : "") + (moved.length ? `（改派自 ${moved[moved.length - 1]}）` : "");
  }

  // What a facade's Stop stops: every request the Family is executing - run by a
  // member or still being routed - and, for the confirmation, what stays queued.
  function familyExecution(snapshot) {
    const requests = (Array.isArray(snapshot?.messages) ? snapshot.messages : [])
      .filter(message => message?.role === "user" && message.relay && typeof message.relay === "object" && message.message_id)
      .map(message => ({ message_id: String(message.message_id), state: String(message.relay.state || ""),
        stopping: message.relay.work_state === "stopping",
        member: String(message.relay.title || message.relay.resident_id || "成员") }));
    return {
      active: requests.filter(request => request.state === "routing" || (request.state === "running" && !request.stopping)),
      waiting: requests.filter(request => ["waiting_members", "scope_review", "quota_review", "delivering", "queued"].includes(request.state)).length,
    };
  }

  function heldInfoFooter(item) {
    const released = Boolean(item?.released_with);
    if (!released && item?.held_info !== true) return "";
    // A Family facade hands its infos to the steward at once (decision_44cf587c1721); a delivered
    // info whose request is not shown here was read with that request, not on its own.
    const status = item.held_info_steward
      ? "背景更新 · 已交给 steward"
      : released ? "背景更新 · 已作为背景随一条请求送达，没有单独打断工作" : "背景更新 · 随下一条任务送达";
    return window.PortalI18n.html`<div class="familyRelay heldInfo" data-held-info="${released ? "released" : "true"}"><div class="familyRelayStatus">${status}</div></div>`;
  }

  function memberText(message) {
    return (Array.isArray(message?.content_parts) ? message.content_parts : [])
      .filter(part => part && part.type === "text").map(part => String(part.text || "")).join("\n").trim();
  }

  // A Mailbox group shows its members in its body; a member points at its group.
  function mailboxFooter(item) {
    if (item?.grouped_into_message_id) {
      return window.PortalI18n.markup('<div class="familyRelay heldInfo" data-mailbox-member="true"><div class="familyRelayStatus">已并入 Mailbox 群体消息</div></div>');
    }
    return "";
  }

  function formatTokenCount(value) {
    const count = Number(value || 0);
    if (!Number.isFinite(count) || count <= 0) return "0";
    if (count >= 1000000) return `${(count / 1000000).toFixed(1)}M`;
    if (count >= 1000) return `${(count / 1000).toFixed(1)}K`;
    return String(Math.round(count));
  }

  function createResidentMessages(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceResidentMessages is missing: ${missing.join(", ")}`);
    const {
      escapeHtml, footerTime, messageQuotable, messageRole, messageSubmenuHtml,
      nativeInteractionNames, outwardToResidents, parseForwardReference,
      PRE_PROVIDER_RUNTIME_PHASES, resourceChips, RUNTIME_PHASE_STATUS, runtimeDetailsHtml,
      subagentRunCard, turnArtifactDisplay,
    } = dependencies;

    function nativePlanLiveHtml(details, messages = []) {
      const finalIds = new Set(messages.filter(item => item.content_parts?.some(part => part.type === "native_turn"))
        .map(item => String(item.id)));
      const cards = details.flatMap(detail => {
        const part = detail.native_plan;
        if (detail.kind !== "plan" || detail.status !== "running" || part?.type !== "native_turn"
          || !detail.execution_id || part.execution_id !== detail.execution_id
          || !part.thread_id || !part.turn_id || !part.message_id || part.diff
          || finalIds.has(String(part.message_id))) return [];
        const item = { id: part.message_id, role: "agent", content_parts: [part] };
        return [`<article class="messageItem agent nativePlanLive" data-message-key="${escapeHtml(item.id)}" aria-live="polite">${residentMessageBody(item, { livePlan: true })}</article>`];
      });
      return cards.length ? `<div class="nativePlansLive" data-message-key="__plans__">${cards.join("")}</div>` : "";
    }

    function residentMessageBody(item, context = {}) {
      const subagent = item.content_parts?.find(part => part.type === "subagent_run");
      if (subagent && messageRole(item) !== "human") return subagentRunCard(item, subagent, context);
      const turn = item.content_parts?.find(part => ["native_turn", "resource_changes"].includes(part.type));
      if (turn && messageRole(item) !== "human") {
        const display = turnArtifactDisplay(turn);
        if (!display.hasContent) return window.PortalI18n.html`<small class="nativeTurnEmpty">${escapeHtml(display.title)}</small>`;
        const labels = { pending: window.PortalI18n.t("待处理"), inProgress: window.PortalI18n.t("进行中"), completed: window.PortalI18n.t("已完成") };
        return window.PortalI18n.html`<section class="residentQuestionCard nativeTurnCard chatSystemCard" data-native-turn="${escapeHtml(item.id)}">
        <header class="nativeTurnHeader"><div class="nativeTurnIdentity"><div class="residentQuestionTitle">${escapeHtml(display.title)}</div><span class="messageSystemBadge">${context.livePlan ? window.PortalI18n.t("当前计划") : window.PortalI18n.t("执行记录")}</span></div>${turn.diff ? window.PortalI18n.html`<button type="button" class="portalButton" data-turn-review title="审查本轮全部修改">审查这些修改</button>` : ""}</header>
        ${turn.type === "resource_changes" ? window.PortalI18n.html`<ul class="resourceTurnChanges">${(turn.changes || []).map(change => {
          const version = snapshot => snapshot?.number ? `v${snapshot.number}` : "";
          const before = version(change.before_version) || String(change.before_sha256 || "").slice(0, 8);
          const after = version(change.after_version) || String(change.after_sha256 || "").slice(0, 8);
          return window.PortalI18n.html`<li>${escapeHtml(change.address)} <small>${escapeHtml(before)} → ${escapeHtml(after)}</small></li>`;
        }).join("")}</ul>` : ""}
        ${turn.plan_text ? context.renderContent(turn.plan_text) : ""}
        ${turn.explanation ? window.PortalI18n.html`<p>${escapeHtml(turn.explanation)}</p>` : ""}
        ${turn.plan?.length ? window.PortalI18n.html`<ol>${turn.plan.map(step => window.PortalI18n.html`<li data-step-status="${escapeHtml(step.status)}"><span>${escapeHtml(labels[step.status] || step.status)}</span> ${escapeHtml(step.step)}</li>`).join("")}</ol>` : ""}
        ${turn.diff ? window.PortalI18n.html`<details><summary>查看本轮修改${turn.diff_truncated ? window.PortalI18n.t("（已截断）") : ""}</summary><div class="nativeTurnDiffToolbar" hidden><select data-turn-diff-file aria-label="修改文件" disabled></select><button type="button" class="portalButton is-icon is-quiet" data-turn-file-preview aria-label="在 Preview 中打开当前文件" title="请先选择一个文件" disabled>${window.PortalUIPrimitives.iconHtml("external-link")}</button></div><div data-turn-diff>${escapeHtml(turn.diff)}</div></details>` : ""}
        </section>`;
      }
      if (messageRole(item) !== "human" && item.content_parts?.some(part => nativeInteractionNames.has(part.tool_name))) {
        return item.content_parts.map(part => {
          if (!nativeInteractionNames.has(part.tool_name)) return "";
          if (part.type === "tool_call" && part.interaction) return window.PortalI18n.html`<div class="residentQuestionHost" data-resident-question="${escapeHtml(part.request_id)}"></div>`;
          if (part.type === "tool_call") return window.PortalI18n.html`<div class="residentQuestionHost" data-resident-question="${escapeHtml(part.request_id)}"></div>`;
          const label = { answered: window.PortalI18n.t("回答已送达"), cancelled: window.PortalI18n.t("请求已取消"), expired: window.PortalI18n.t("请求已失效") }[part.status] || window.PortalI18n.t("请求已结束");
          const answers = Object.values(part.answers || {}).flatMap(answer => answer.answers || []).join("；");
          return window.PortalI18n.html`<div class="residentQuestionStatus">${escapeHtml(label)}${answers ? `：${escapeHtml(answers)}` : ""}</div>`;
        }).join("");
      }
      if (messageRole(item) !== "human" && item.content_parts?.some(part => part.type === "async_questions")) {
        return item.content_parts.map(part => {
          if (part.type === "text") return context.renderContent(part.text || "");
          if (part.type !== "async_questions") return "";
          return window.PortalI18n.html`<div class="residentQuestionHost" data-resident-question="${escapeHtml(part.answer_message_id || "")}"></div>`;
        }).join("");
      }
      if (outwardToResidents(item)) return outwardRecordHtml(item, context.bodyHtml || "");
      if (item.expanded_members?.length) return mailboxGroupBody(item.expanded_members, context);
      const parsed = messageRole(item) === "human" ? parseForwardReference(context.content || "") : null;
      if (!parsed) return context.bodyHtml || "";
      const render = context.renderContent;
      const reference = parsed.entries?.length
        ? parsed.entries.map(entry => window.PortalI18n.html`<section class="forwardReferenceEntry"><div class="forwardReferenceSpeaker">${escapeHtml(entry.speaker)}</div><div class="forwardReferenceEntryContent markdownBody">${render(entry.content)}</div></section>`).join("")
        : render(parsed.reference);
      return window.PortalI18n.html`<div class="forwardReferenceMessage"><details class="forwardReferenceDetails" data-message-disclosure="forward-reference"><summary><span>转发参考</span><span class="forwardReferenceHint"><span class="forwardReferenceExpand">展开</span><span class="forwardReferenceCollapse">收起</span></span></summary><div class="forwardReferenceBody markdownBody">${reference}</div></details>${parsed.note ? window.PortalI18n.html`<div class="forwardReferenceNote">${render(parsed.note)}</div>` : ""}</div>`;
    }

    // A facade request given to a member can be reassigned to another (decision_44cf587c1721);
    // the members open from one 改派 item, beside it, like a bookmark folder's.
    function familyReassignMenuItems(item, family) {
      const current = String(item?.relay?.resident_id || "");
      if (messageRole(item) !== "human" || !current) return "";
      const members = (Array.isArray(family?.members) ? family.members : [])
        .filter(member => member?.resident_id && member.resident_id !== current)
        .map(member => window.PortalI18n.html`<button type="button" role="menuitem" data-portal-message-action="family-reassign" data-family-member="${escapeHtml(member.resident_id)}">${escapeHtml(member.title || member.resident_id)}</button>`)
        .join("");
      return members ? messageSubmenuHtml("family-reassign", window.PortalI18n.t("改派"), members) : "";
    }

    // Each reply follows the request it answers; the request's progress shows
    // where any Resident shows its work, below the messages.  The Conversation
    // answers one request at a time, so the member is named once, on the last
    // reply to a request, not on each intermediate output; while the request is
    // open its status block names the member.
    function familyReplyFooter(item, index = -1, items = []) {
      const reply = item && typeof item.family_reply === "object" ? item.family_reply : null;
      if (!reply || item.family_status) return "";
      const request = String(reply.request_message_id || "");
      const next = index >= 0 && Array.isArray(items) ? items[index + 1] : null;
      if (request && String(next?.family_reply?.request_message_id || "") === request) return "";
      const title = String(reply.title || reply.resident_id || window.PortalI18n.t("成员"));
      const source = messageRole(item) === "agent" ? `由 ${title} 回复` : `来自 ${title}`;
      const residentId = String(reply.resident_id || "");
      const messageId = String(reply.message_id || item.message_id || item.id || "");
      const label = escapeHtml(`${source}${familyRelayRoute(reply)}`);
      const href = `/lineage?${new URLSearchParams({ resident: residentId, message: messageId, open_chat: "1" })}`;
      const sourceLink = residentId && messageId
        ? window.PortalI18n.html`<a class="messageMetaLink" href="${escapeHtml(href)}" data-family-reply-source title="在成员对话中定位这条回复">${label}</a>`
        : label;
      const chain = [...(reply.via || []).map(member => member.resident_id), residentId];
      return window.PortalI18n.html`<div class="familyRelay familyReply" data-family-reply-member="${escapeHtml(residentId)}"><div class="familyRelayStatus">${sourceLink}<span data-family-reply-controls data-family-reply-chain="${escapeHtml(JSON.stringify(chain))}" hidden></span></div></div>`;
    }

    // Infos delivered with a request were read as one input with it: shown inside it, collapsed (concept_43c793b4f3ab).
    function attachedInfosFooter(item) {
      const infos = Array.isArray(item?.attached_infos) ? item.attached_infos : [];
      if (!infos.length) return "";
      const rows = infos.map(info => {
        const text = (Array.isArray(info?.content_parts) ? info.content_parts : [])
          .filter(part => part && part.type === "text").map(part => String(part.text || "")).join("\n").trim();
        const authorId = String(info?.origin?.sender?.id || info?.author_id || "").trim();
        const author = /^resident_[0-9a-f]{12}$/.test(authorId) ? resourceChips.resourceLabelHtml(authorId) : escapeHtml(authorId);
        const time = footerTime(info?.created_at);
        return window.PortalI18n.html`<div class="attachedInfo" data-attached-info="${escapeHtml(String(info?.message_id || ""))}"><div class="attachedInfoMeta">${author}${time ? ` · ${escapeHtml(time)}` : ""}</div><div class="attachedInfoText">${escapeHtml(text.slice(0, 4000))}</div></div>`;
      }).join("");
      const note = item.attached_infos_steward ? " · 已交给 steward" : "";
      return window.PortalI18n.html`<details class="familyRelay heldInfo attachedInfos" data-message-disclosure="attached-infos"><summary class="familyRelayStatus">附带背景更新 ${infos.length} 条${note}</summary>${rows}</details>`;
    }

    // A Mailbox group's body is its members (decision_15f7df737ed5), each with
    // its own sender, time and text; a long one opens from a one-line preview.
    function mailboxGroupBody(members, context) {
      const rows = members.map(member => {
        const id = escapeHtml(String(member?.message_id || ""));
        const text = memberText(member);
        const authorId = String(member?.origin?.sender?.id || member?.author_id || "").trim();
        const author = /^resident_[0-9a-f]{12}$/.test(authorId) ? resourceChips.resourceLabelHtml(authorId) : escapeHtml(authorId);
        const time = footerTime(member?.created_at);
        const meta = window.PortalI18n.html`<span class="mailboxMemberMeta">${author}${time ? ` · ${escapeHtml(time)}` : ""}</span>`;
        const body = window.PortalI18n.html`<div class="mailboxMemberText markdownBody">${context.renderContent(text)}</div>`;
        if (text.length <= 160 && !text.includes("\n")) {
          return window.PortalI18n.html`<section class="mailboxMember" data-mailbox-member="${id}">${meta}${body}</section>`;
        }
        const preview = text.split("\n").find(line => line.trim()) || "";
        return window.PortalI18n.html`<details class="mailboxMember" data-mailbox-member="${id}" data-message-disclosure="mailbox-member:${id}"><summary>${meta}<span class="mailboxMemberPreview">${escapeHtml(preview.slice(0, 200))}</span></summary>${body}</details>`;
      }).join("");
      return window.PortalI18n.html`<div class="mailboxGroupBody" data-mailbox-group="true"><div class="mailboxGroupNote">合并了 ${members.length} 条消息，作为一次输入处理</div>${rows}</div>`;
    }

    // A message a Resident sends to a user, or a Family sends out as its facade, names
    // its recipients; a Family's message also names the member that sent it (decision_c524c775e0be).
    function recipientsStatus(item) {
      const recipients = (Array.isArray(item?.recipients) ? item.recipients : []).filter(recipient => recipient?.id);
      if (!recipients.length) return null;
      const label = id => (/^resident_[0-9a-f]{12}$/.test(id) ? resourceChips.resourceLabelHtml(id) : escapeHtml(id));
      const names = recipients.map(recipient => {
        const display = item?.recipient_labels?.[recipient.kind]?.[recipient.id];
        return typeof display === "string" && display ? escapeHtml(display) : label(String(recipient.id));
      }).join("、");
      const sender = String(item?.origin?.sender?.id || "");
      const initiator = String(item?.origin?.initiated_by?.id || "");
      const by = /^resident_[0-9a-f]{12}$/.test(initiator) && initiator !== sender ? ` · 由 ${label(initiator)} 发出` : "";
      return { ids: recipients.map(recipient => recipient.id).join(","), html: `发给 ${names}${by}` };
    }

    // A message a Family sent another Resident in its name (outwardToResidents)
    // stays one line in the facade's Conversation and opens on demand.
    function outwardRecordHtml(item, bodyHtml) {
      const status = recipientsStatus(item);
      return window.PortalI18n.html`<details class="familyRelay outwardMessage outwardRecord" data-message-disclosure="outward-record" data-message-recipients="${escapeHtml(status.ids)}"><summary class="familyRelayStatus">${status.html}</summary><div class="outwardRecordBody markdownBody">${bodyHtml}</div></details>`;
    }

    // A message sent to a user - a member's question, say - is answered from the message
    // itself; the reply quotes it and goes straight to the member that wrote it.
    function recipientsFooter(item) {
      const status = recipientsStatus(item);
      if (!status || outwardToResidents(item)) return "";
      const toUser = item.recipients.some(recipient => recipient?.kind === "user");
      const reply = toUser && messageQuotable(item)
        ? window.PortalI18n.markup('<button class="portalButton is-compact outwardReply" type="button" data-portal-message-action="quote" title="引用这条消息回复">回复</button>')
        : "";
      return window.PortalI18n.html`<div class="familyRelay outwardMessage${reply ? " canReply" : ""}" data-message-recipients="${escapeHtml(status.ids)}"><div class="familyRelayStatus">${status.html}</div>${reply}</div>`;
    }

    function residentMessageFooter(item, index, items) {
      const delivery = item.local_delivery;
      const failed = delivery?.state === "failed";
      const confirming = delivery?.state === "confirming";
      const localStatus = delivery && delivery.state !== "accepted"
        ? window.PortalI18n.html`<div class="messageDeliveryStatus${failed ? " is-failed" : ""}" data-message-delivery="${delivery.state}" role="status">
          <span>${escapeHtml(failed ? window.PortalI18n.text`发送失败：${delivery.error}`
            : confirming ? window.PortalI18n.t("正在确认发送状态") : window.PortalI18n.t("发送中…"))}</span>
          ${failed ? window.PortalI18n.html`<button type="button" class="portalButton is-compact" data-portal-message-action="submission-retry">${window.PortalI18n.t("重试")}</button><button type="button" class="portalButton is-compact" data-portal-message-action="submission-dismiss">${window.PortalI18n.t("移除")}</button>`
            : confirming ? window.PortalI18n.html`<button type="button" class="portalButton is-compact" data-portal-message-action="submission-check">${window.PortalI18n.t("检查发送状态")}</button>` : ""}
        </div>` : "";
      return localStatus + attachedInfosFooter(item) + mailboxFooter(item) + heldInfoFooter(item) + familyReplyFooter(item, index, items)
        + recipientsFooter(item) + residentTokenFooter(item);
    }

    // The progress of a Family facade request, after its last shown message:
    // which member has it, its place in that member's queue, the live activity.
    // The sender (or the owner) withdraws it while queued and stops it while it runs.
    function residentMessageAfter(item, index, items, context = {}) {
      const status = item && typeof item.family_status === "object" ? item.family_status : null;
      return status ? requestProgressHtml(status, context) : "";
    }

    // How far one Resident has got with one request: being handed over, its
    // place in the Resident's queue, or the Resident's live work on it
    // (decision_44cf587c1721).  A Chatroom shows the same block, without the
    // withdraw and stop actions, for each Resident a message called
    // (decision_a6bebf4ac283).
    function requestProgressHtml(status, context = {}, { actions: withActions = true } = {}) {
      const state = String(status?.state || "");
      const member = `${String(status.title || status.resident_id || "成员")}${familyRelayRoute(status)}`;
      const phase = String(status.work_phase || "");
      const phaseLabel = PRE_PROVIDER_RUNTIME_PHASES.has(phase) ? RUNTIME_PHASE_STATUS[phase]?.label || "" : "";
      const stopping = status.work_state === "stopping";
      const button = (action, label, unavailable = "", title = "") => window.PortalI18n.html`<button class="portalButton is-compact familyRequestAction" type="button" data-portal-message-action="${action}"${unavailable ? ' disabled data-action-unavailable="true"' : ""}${unavailable || title ? ` title="${escapeHtml(unavailable || title)}"` : ""}>${escapeHtml(label)}</button>`;
      let label = "";
      let actions = "";
      let body = "";
      if (state === "recovery") {
        const labels = {
          selecting: "正在查找原请求", selected: "正在恢复原请求", routing: "正在恢复原请求的转交",
          queued: "原请求已在排队，无需重复启动", running: "原请求正在运行，无需重复启动",
          completed: "原请求已经完成", failed: "恢复未完成", cancelled: "原请求已停止",
          missing: "原请求的执行记录不可用", unavailable: "无法恢复原请求",
          choose: "选择要恢复的原请求，引用后发送 continue。",
        };
        label = window.PortalI18n.t(labels[status.status] || "正在恢复原请求");
        if (status.resident_id) label += ` · ${member}`;
        if (status.error) body += `<div class="paneNote">${escapeHtml(typeof status.error === "string" ? status.error : status.error.message || JSON.stringify(status.error))}</div>`;
        if (status.original_message_id) actions = button("family-recovery-view", "查看原请求");
        body += (status.candidates || []).map(candidate => `<div class="paneNote"><button type="button" class="portalButton is-compact" data-portal-message-action="family-recovery-choose" data-recovery-request="${escapeHtml(candidate.message_id)}">${escapeHtml(candidate.text || candidate.message_id)}</button></div>`).join("");
      } else if (state === "routing") {
        label = RUNTIME_PHASE_STATUS.family_routing.label;
        // Stopped while being routed, the request is withdrawn before any member gets it.
        actions = button("family-request-withdraw", "停止");
      } else if (state === "waiting_members") {
        label = "等待 Family 恢复成员";
        actions = button("family-request-withdraw", "撤回");
      } else if (state === "scope_review") {
        // No member's duties cover it: the steward decides who takes it, maybe a new member.
        label = "不在现有成员职责内，steward 正在安排成员";
        actions = button("family-request-withdraw", "撤回");
      } else if (state === "quota_review") {
        label = "steward 正在评估成员额度与任务强度";
      } else if (state === "delivering") {
        label = `正在交给 ${member}`;
      } else if (state === "queued") {
        const ahead = Number(status.ahead);
        const place = status.ahead === undefined || !Number.isFinite(ahead) ? "" : ahead > 0 ? `（前面还有 ${ahead} 条）` : "（下一条处理）";
        label = phaseLabel ? `已交给 ${member} · ${phaseLabel}` : `已交给 ${member} · 排队中${place}`;
        // Disputing the Router's choice names the member that should take it, and why.
        actions = button("family-request-inject", "立即注入",
          status.resident_id && status.message_id ? "" : "等待确认承接成员的排队消息",
          "当前工作可接收时立即注入，否则排到下一轮；暂停接单时仍会等待")
          + button("family-request-correct", "纠正 Router") + button("family-request-withdraw", "撤回");
      } else if (state === "running") {
        label = stopping ? `${member} · ${RUNTIME_PHASE_STATUS.stopping.label}` : phaseLabel ? `${member} · ${phaseLabel}` : `${member} 运行中`;
        actions = stopping ? "" : button("family-request-stop", "停止");
        body = phaseLabel ? "" : runtimeDetailsHtml(Array.isArray(status.activities) ? status.activities : [], {
          live: !stopping,
          formatTime: value => window.PortalUI?.formatDateTime?.(value, { style: "compact" }) || String(value || ""),
          assistantName: String(status.title || "成员"),
          emptyLiveHtml: window.PortalI18n.html`<div class="paneNote">${escapeHtml(member)} 已开始处理，等待活动事件。</div>`,
        });
      } else return "";
      const keyAttrs = context.key ? ` data-message-key="${escapeHtml(context.key)}" data-message-after-of="${escapeHtml(context.anchorKey || "")}"` : "";
      const residentAttr = status.resident_id ? ` data-request-resident="${escapeHtml(String(status.resident_id))}"` : "";
      const labelHtml = status.resident_id
        ? `<a class="messageMetaLink" data-family-request-source href="/lineage?${escapeHtml(new URLSearchParams({ resident: String(status.resident_id), open_chat: "1" }).toString())}" title="${escapeHtml(window.PortalI18n.t("打开执行成员的对话"))}">${escapeHtml(label)}</a>`
        : escapeHtml(label);
      return window.PortalI18n.html`<article class="messageItem agent liveRuntime familyRequestStatus"${keyAttrs}${residentAttr} data-family-request="${escapeHtml(String(status.request_message_id || ""))}" data-family-request-state="${escapeHtml(stopping ? "stopping" : state)}">
      <div class="messageHead"><span class="messageTitle familyRequestLabel">${labelHtml}</span><span class="messageHeadRight">${withActions ? actions : ""}</span></div>
      ${body ? `<div class="familyRequestActivity"${context.workCollapsed ? " hidden" : ""}>${body}</div>` : ""}
    </article>`;
    }

    function residentTokenFooter(item) {
      return [
        item.token_usage,
      ].map(usage => {
        if (!usage) return "";
        const parts = [["total_tokens", ""], ["input_tokens", "in "], ["output_tokens", "out "], ["reasoning_output_tokens", "reason "]]
          .filter(([key]) => Number(usage[key]) > 0).map(([key, label]) => `${label}${formatTokenCount(usage[key])}`);
        return parts.length ? window.PortalI18n.html`<div class="messageTokenNote">${escapeHtml(`tokens ${parts.join(" · ")}`)}</div>` : "";
      }).join("");
    }

    return Object.freeze({
      residentMessageBody, nativePlanLiveHtml, familyExecution, familyReassignMenuItems, residentMessageFooter,
      residentMessageAfter, requestProgressHtml, formatTokenCount,
    });
  }

  window.PortalChatWorkspaceResidentMessages = Object.freeze({
    createResidentMessages, dependencies: DEPENDENCIES, familyExecution, formatTokenCount,
  });
})();
