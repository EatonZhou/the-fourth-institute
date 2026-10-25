(() => {
  if (window.PortalChatWorkspaceSubagents) return;

  // The subagents a Resident's model starts: the finished run card, the live
  // cards with their clocks and steps, the badge title, and the detail dialog
  // with its timeline, trace pages and full result.
  const DEPENDENCIES = Object.freeze([
    "bindLinkPreview", "bindResourceChips", "bindResponseSourcePaths", "escapeHtml",
    "linkifyResourceAddresses", "requestWorkspacePreview", "runtimeStatusLabel",
  ]);

  function subagentRunDuration(part) {
    const seconds = Math.round((Date.parse(String(part.finished_at || "")) - Date.parse(String(part.started_at || ""))) / 1000);
    if (!Number.isFinite(seconds) || seconds < 0) return "";
    return seconds < 60 ? `用时 ${seconds} 秒` : `用时 ${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
  }

  // The folded card shows the result's opening words as plain text.
  function subagentRunPreview(result) {
    return String(result || "")
      .replace(/```[\s\S]*?(```|$)/g, " ")
      .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/^\s*(#{1,6}|>|[-*+]|\d+[.)])\s+/gm, "")
      .replace(/`|\*\*|__|~~/g, "")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 300);
  }

  // While the model waits on a subagent, its live card shows among the turn's
  // activity: what it does now, its last steps and how long it has run.  The
  // finished card replaces it in the conversation once the model has the result.
  const SUBAGENT_LIVE_TRAIL = 4;

  function subagentClockText(since) {
    const started = Date.parse(String(since || ""));
    if (!Number.isFinite(started)) return "";
    const seconds = Math.max(0, Math.floor((Date.now() - started) / 1000));
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  }

  function patchSubagentLive(current, next) {
    current.dataset.count = next.dataset.count;
    const currentHead = current.querySelector(":scope > .subagentLiveGroupHead");
    const nextHead = next.querySelector(":scope > .subagentLiveGroupHead");
    if (!nextHead) currentHead?.remove();
    else if (!currentHead) current.prepend(nextHead);
    else if (currentHead.dataset.sig !== nextHead.dataset.sig) currentHead.replaceWith(nextHead);
    const grid = current.querySelector(".subagentLiveGrid");
    const cards = [...next.querySelectorAll(".subagentLiveCard")];
    const keep = new Set(cards.map(card => card.dataset.subagentLive));
    [...grid.children].forEach(card => { if (!keep.has(card.dataset.subagentLive)) card.remove(); });
    cards.forEach((card, index) => {
      const existing = [...grid.children].find(node => node.dataset.subagentLive === card.dataset.subagentLive);
      if (!existing) {
        grid.insertBefore(card, grid.children[index] || null);
        return;
      }
      for (const part of card.querySelectorAll("[data-subagent-part]")) {
        const old = existing.querySelector(`[data-subagent-part="${part.dataset.subagentPart}"]`);
        if (old && old.dataset.sig !== part.dataset.sig) old.replaceWith(part);
      }
      if (grid.children[index] !== existing) grid.insertBefore(existing, grid.children[index] || null);
    });
  }

  function subagentTokens(usage) {
    const total = Number(usage?.total_tokens || 0) || Number(usage?.input_tokens || 0) + Number(usage?.output_tokens || 0);
    if (!(total > 0)) return "";
    return total >= 1000 ? `${(total / 1000).toFixed(total >= 100000 ? 0 : 1)}k tokens` : `${total} tokens`;
  }

  function subagentFileName(path) {
    return String(path || "").split("/").filter(Boolean).pop() || String(path || "");
  }

  const SUBAGENT_STATUS_LABELS = { completed: "已完成", running: "运行中", failed: "失败", cancelled: "已取消", stopped: "已停止", killed: "已停止" };

  const SUBAGENT_DETAIL_POLL_MS = 2500;
  const SUBAGENT_TRACE_PAGE = 500;

  function subagentStatusLabel(status) {
    const label = SUBAGENT_STATUS_LABELS[String(status || "")];
    return label ? window.PortalI18n.t(label) : String(status || "");
  }

  function subagentStepDuration(step) {
    const ms = Date.parse(String(step.finished_at || "")) - Date.parse(String(step.started_at || ""));
    if (!Number.isFinite(ms) || ms < 0) return "";
    if (ms < 1000) return `${ms} ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`;
    return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
  }

  function subagentStepInput(input) {
    if (input === null || input === undefined || input === "") return "";
    return typeof input === "string" ? input : JSON.stringify(input, null, 2);
  }

  function subagentStepSummary(step) {
    const input = step.input;
    const value = input && typeof input === "object"
      ? ["command", "file_path", "pattern", "path", "url", "query", "description", "prompt"]
        .map(key => input[key]).find(item => typeof item === "string" && item.trim()) || ""
      : String(input || step.text || "");
    return String(value).replace(/\s+/g, " ").trim().slice(0, 240);
  }

  // The steps of a run's own trace from ``offset`` on, every page of them.
  async function readSubagentTrace(residentId, traceRef, offset = 0) {
    let from = offset;
    const steps = [];
    for (;;) {
      const page = (await window.PortalInstructionClient.execute("GetSubagentRun", {
        resident_id: residentId, trace_ref: traceRef, offset: from, limit: SUBAGENT_TRACE_PAGE,
      }))?.run || {};
      steps.push(...(Array.isArray(page.steps) ? page.steps : []));
      const total = Number(page.total || 0);
      if (total <= from + SUBAGENT_TRACE_PAGE) return { steps, total };
      from += SUBAGENT_TRACE_PAGE;
    }
  }

  // A run's whole result: the card keeps its opening, the run's own trace
  // keeps what it last said.
  async function subagentFullResult(residentId, run) {
    const result = String(run?.result || "");
    if (!run?.result_truncated) return result;
    try {
      const head = (await window.PortalInstructionClient.execute("GetSubagentRun", {
        resident_id: residentId, trace_ref: run.trace_ref || run.tool_use_id, offset: 0, limit: 1,
      }))?.run || {};
      const { steps } = await readSubagentTrace(residentId, run.trace_ref || run.tool_use_id, Math.max(0, Number(head.total || 0) - 40));
      const said = steps.filter(step => step.kind === "message");
      const last = said.find(step => step.final) || said.at(-1);
      if (last && String(last.text || "").length > result.length) return String(last.text);
    } catch {}
    return result;
  }

  function subagentSlug(text) {
    return String(text || "").normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-+|-+$/g, "").slice(0, 40) || "subagent";
  }

  const SUBAGENT_STOP_REASONS = {
    unknown_subagent: "Provider 还没有登记这个子 agent（可能刚启动或已经结束），稍后再试",
    subagent_stop_unsupported: "这个 Provider 不能单独停止子 agent",
    subagent_finished: "子 agent 已经结束，不用再停止",
    no_active_turn: "这一轮已经结束",
    no_session: "这一轮的会话已经结束",
    session_changed: "会话已经换成新的，这个子 agent 不在其中",
  };

  function createSubagents(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceSubagents is missing: ${missing.join(", ")}`);
    const {
      bindLinkPreview, bindResourceChips, bindResponseSourcePaths, escapeHtml,
      linkifyResourceAddresses, requestWorkspacePreview, runtimeStatusLabel,
    } = dependencies;

    // A subagent the Resident's model started: its own steps stay out of the
    // Resident's context; the Chat shows the run once the model has its result.
    // The built-in kinds of the Providers (Claude Code subagent_type, Codex
    // agent role, Kimi Code subagent name) by what they do; any other kind
    // shows as its Provider named it.
    const SUBAGENT_TYPE_LABELS = {
      "general-purpose": window.PortalI18n.t("通用"), default: window.PortalI18n.t("通用"),
      explore: window.PortalI18n.t("探索"), explorer: window.PortalI18n.t("探索"),
      plan: window.PortalI18n.t("规划"),
      coder: window.PortalI18n.t("编码"), worker: window.PortalI18n.t("执行"),
    };

    function subagentTypeLabel(type) {
      const raw = String(type || "").trim();
      return SUBAGENT_TYPE_LABELS[raw.toLowerCase()] || raw;
    }

    function subagentRunTitle(part) {
      const kind = subagentTypeLabel(part.agent_type);
      return window.PortalI18n.text`子 agent${kind ? ` · ${kind}` : ""}${part.description ? `：${part.description}` : ""}`;
    }

    // The tools a run used, most used first: "Bash ×3 · Read".
    function subagentRunTools(steps) {
      const counts = new Map();
      steps.filter(step => step.kind === "tool").forEach(step => {
        const name = String(step.tool_name || "tool");
        counts.set(name, (counts.get(name) || 0) + 1);
      });
      return [...counts].sort((a, b) => b[1] - a[1])
        .map(([name, count]) => window.PortalI18n.html`<code>${escapeHtml(name)}${count > 1 ? ` ×${count}` : ""}</code>`).join("");
    }

    function subagentRunCard(item, part, context) {
      const status = { completed: "已完成", running: "进行中", failed: "失败", cancelled: "已取消", stopped: "已停止", killed: "已停止" }[part.status] || String(part.status || "");
      const steps = Array.isArray(part.steps) ? part.steps : [];
      const total = Number(part.steps_total || steps.length);
      const stepHtml = steps.map(step => step.kind === "tool"
        ? window.PortalI18n.html`<li data-step-kind="tool"${step.error ? ' data-step-error="1"' : ""}><code>${escapeHtml(step.tool_name || "tool")}</code> ${escapeHtml(step.text || "")}</li>`
        : window.PortalI18n.html`<li data-step-kind="message">${escapeHtml(step.text || "")}</li>`).join("");
      const meta = [subagentRunDuration(part), total ? `${total} 步` : "", subagentTokens(part.usage)].filter(Boolean).join(" · ");
      const tools = subagentRunTools(steps);
      // Folded by default: one line of the result and the tools it used.
      const fold = escapeHtml(part.tool_use_id || item.id);
      const preview = part.result ? subagentRunPreview(part.result) : "子 agent 没有返回文字结果";
      const files = subagentFilesHtml(part.files);
      const prompt = String(part.prompt || "").trim();
      return window.PortalI18n.html`<section class="residentQuestionCard nativeTurnCard chatSystemCard subagentRunCard" data-subagent-run="${escapeHtml(item.id)}" data-subagent-tool-use="${escapeHtml(part.tool_use_id || "")}" data-status="${escapeHtml(part.status || "")}">
      <header class="nativeTurnHeader"><div class="nativeTurnIdentity"><div class="residentQuestionTitle"><span class="subagentRunMark" aria-hidden="true"></span>${escapeHtml(subagentRunTitle(part))}</div><span class="messageSystemBadge">${escapeHtml(part.background ? window.PortalI18n.t("后台子 agent") : window.PortalI18n.t("子 agent"))}${status ? ` · ${escapeHtml(status)}` : ""}</span></div>${meta ? window.PortalI18n.html`<span class="subagentRunMeta">${escapeHtml(meta)}</span>` : ""}</header>
      <details class="subagentRunBody" data-message-disclosure="subagent-result:${fold}">
        <summary><span class="subagentRunFold" aria-hidden="true"></span><span class="subagentRunPreview">${escapeHtml(preview)}</span><span class="subagentRunLabel">结果</span>${tools ? window.PortalI18n.html`<span class="subagentRunTools">${tools}</span>` : ""}<span class="subagentRunHint"><span class="subagentRunExpand">展开</span><span class="subagentRunCollapse">收起</span></span></summary>
        <div class="subagentRunResult">${part.result ? context.renderContent(part.result) : window.PortalI18n.html`<small class="nativeTurnEmpty">子 agent 没有返回文字结果</small>`}
        ${part.result_truncated ? window.PortalI18n.html`<small class="nativeTurnEmpty">结果较长，这里只显示开头；复制或保存时取完整结果</small>` : ""}</div>
        ${files ? window.PortalI18n.html`<div class="subagentRunSection"><div class="subagentRunSectionLabel">改动的文件</div>${files}</div>` : ""}
        ${prompt ? window.PortalI18n.html`<details class="subagentRunPromptFold" data-message-disclosure="subagent-prompt:${fold}"><summary>它收到的任务</summary><div class="subagentRunPrompt">${escapeHtml(prompt)}</div>${part.prompt_truncated ? window.PortalI18n.html`<small class="nativeTurnEmpty">任务较长，这里只显示开头</small>` : ""}</details>` : ""}
        ${steps.length ? window.PortalI18n.html`<details class="subagentRunStepsFold" data-message-disclosure="subagent-steps:${fold}"><summary>查看子 agent 的步骤（${total} 步${total > steps.length ? window.PortalI18n.text`，显示最后 ${steps.length} 步` : ""}）</summary><ol class="subagentRunSteps">${stepHtml}</ol></details>` : ""}
        <div class="subagentRunActions">
          <button type="button" class="portalButton is-compact" data-subagent-action="detail" title="打开这个子 agent 的完整步骤：每次工具调用的输入和输出">全部步骤</button>
          <button type="button" class="portalButton is-compact" data-subagent-action="quote" title="把结果摘录放进输入框，接着写要父 Resident 做什么">引用到输入框</button>
          <button type="button" class="portalButton is-compact" data-subagent-action="copy">复制结果</button>
          <button type="button" class="portalButton is-compact" data-subagent-action="save" title="把任务、结果和改动的文件保存为 Resident 自己 Workspace 中的 Markdown 文件">保存为文件</button>
        </div>
      </details>
      </section>`;
    }

    let subagentClockTimer = 0;

    function startSubagentClocks() {
      if (!subagentClockTimer) subagentClockTimer = window.setInterval(tickSubagentClocks, 1000);
    }

    function tickSubagentClocks() {
      const clocks = document.querySelectorAll("[data-subagent-since]");
      if (!clocks.length) {
        window.clearInterval(subagentClockTimer);
        subagentClockTimer = 0;
        return;
      }
      clocks.forEach(clock => {
        const text = subagentClockText(clock.dataset.subagentSince);
        if (clock.textContent !== text) clock.textContent = text;
      });
    }

    function subagentLiveRuns(details) {
      return (Array.isArray(details) ? details : [])
        .filter(detail => detail?.kind === "subagent" && runtimeStatusLabel(detail.status) === "running"
          && detail.subagent && typeof detail.subagent === "object" && detail.subagent.tool_use_id)
        .map(detail => ({ ...detail.subagent, execution_id: String(detail.execution_id || "") }))
        .sort((a, b) => String(a.started_at || "").localeCompare(String(b.started_at || "")));
    }

    // Each part carries the data it shows (data-sig): an update replaces only
    // the parts that changed, so a card keeps its running animations.
    function subagentLivePart(name, data, tag, className, html) {
      return `<${tag} class="${className}" data-subagent-part="${name}" data-sig="${escapeHtml(JSON.stringify(data))}">${html}</${tag}>`;
    }

    function subagentLiveStepText(step) {
      return step.kind === "tool"
        ? window.PortalI18n.html`<code>${escapeHtml(step.tool_name || "tool")}</code><span class="subagentLiveText">${escapeHtml(step.text || "")}</span>`
        : window.PortalI18n.html`<span class="subagentLiveSay">说</span><span class="subagentLiveText">${escapeHtml(step.text || "")}</span>`;
    }

    function subagentLiveCardHtml(run) {
      const steps = Array.isArray(run.steps) ? run.steps : [];
      const now = steps.length ? steps[steps.length - 1] : null;
      const trail = steps.slice(Math.max(0, steps.length - 1 - SUBAGENT_LIVE_TRAIL), Math.max(0, steps.length - 1));
      const total = Number(run.steps_total || steps.length);
      const started = Date.parse(String(run.started_at || ""));
      const age = Number.isFinite(started) ? Math.max(0, (Date.now() - started) / 1000) : 0;
      const head = subagentLivePart("head", [run.agent_type, run.description, run.background, run.started_at], "header", "subagentLiveHead", window.PortalI18n.html`
        <span class="subagentLiveSpinner" aria-hidden="true"></span>
        <div class="subagentLiveIdentity">
          <div class="subagentLiveType" title="${escapeHtml(run.agent_type || "")}">${escapeHtml(subagentTypeLabel(run.agent_type) || window.PortalI18n.t("子 agent"))}<span class="subagentLiveMode">${run.background ? window.PortalI18n.t("后台") : window.PortalI18n.t("前台")}</span></div>
          <div class="subagentLiveTask" title="${escapeHtml(run.description || "")}">${escapeHtml(run.description || "")}</div>
        </div>
        <span class="subagentLiveClock" data-subagent-since="${escapeHtml(run.started_at || "")}">${escapeHtml(subagentClockText(run.started_at))}</span>`);
      const nowHtml = now
        ? `${now.kind === "tool" && !now.running ? window.PortalI18n.html`<span class="subagentLiveTick" data-step-error="${now.error ? "1" : ""}" aria-hidden="true"></span>` : ""}${subagentLiveStepText(now)}`
        : window.PortalI18n.html`<span class="subagentLiveText">正在启动…</span>`;
      // A call still running shows how long it has taken so far.
      const stepClock = now?.kind === "tool" && now.running && now.started_at
        ? window.PortalI18n.html`<span class="subagentLiveStepClock" data-subagent-since="${escapeHtml(now.started_at)}" title="这一步已用时">${escapeHtml(subagentClockText(now.started_at))}</span>`
        : "";
      const nowPart = subagentLivePart("now", now, "div", "subagentLiveNow", window.PortalI18n.html`${nowHtml}${stepClock}<span class="subagentLiveDots" aria-hidden="true"><i></i><i></i><i></i></span>`);
      const trailPart = subagentLivePart("trail", trail, "ol", "subagentLiveTrail", trail.map(step => window.PortalI18n.html`<li data-step-kind="${escapeHtml(step.kind || "")}"><span class="subagentLiveTick" data-step-error="${step.error ? "1" : ""}" aria-hidden="true"></span>${subagentLiveStepText(step)}</li>`).join(""));
      const tokens = subagentTokens(run.usage);
      const fileCount = Array.isArray(run.files) ? run.files.length : 0;
      const foot = subagentLivePart("foot", [total, run.model, tokens, fileCount], "footer", "subagentLiveFoot",
        window.PortalI18n.html`<span>${total ? window.PortalI18n.text`第 ${total} 步` : window.PortalI18n.t("等待第一步")}${tokens ? ` · ${escapeHtml(tokens)}` : ""}${fileCount ? window.PortalI18n.text` · 改了 ${fileCount} 个文件` : ""}</span>${run.model ? window.PortalI18n.html`<span>${escapeHtml(run.model)}</span>` : ""}`);
      // Stop shows where the Provider stops one run and the reader may stop the
      // turn, steering where the reader may send (the Chat root says which).
      const actions = subagentLivePart("actions", 0, "div", "subagentLiveActions", window.PortalI18n.html`<button type="button" class="subagentLiveAction" data-subagent-action="detail" title="打开这个子 agent 的任务、完整步骤和用量">详情</button><button type="button" class="subagentLiveAction" data-subagent-action="steer" title="在输入框写给父 Resident 的纠偏，发送后立即进入当前这一轮">纠偏</button><button type="button" class="subagentLiveAction" data-subagent-action="stop" title="只停止这个子 agent，父 Resident 的这一轮继续">停止</button>`);
      return window.PortalI18n.html`<section class="subagentLiveCard" data-subagent-live="${escapeHtml(run.tool_use_id)}"${age < 2 ? " data-fresh" : ""} style="--subagent-age: ${age.toFixed(2)}" aria-label="${escapeHtml(window.PortalI18n.text`子 agent${subagentTypeLabel(run.agent_type) ? ` ${subagentTypeLabel(run.agent_type)}` : ""} 正在运行：${run.description || ""}`)}">${head}${nowPart}${trailPart}${foot}${actions}</section>`;
    }

    function subagentLiveHtml(details) {
      const runs = subagentLiveRuns(details);
      if (!runs.length) return "";
      runs.forEach(run => subagentLiveById.set(run.tool_use_id, run));
      startSubagentClocks();
      const head = runs.length > 1
        ? subagentLivePart("group", runs.length, "div", "subagentLiveGroupHead", window.PortalI18n.html`<span class="subagentLivePulse" aria-hidden="true"></span>${runs.length} 个子 agent 正在并行工作`)
        : "";
      return window.PortalI18n.html`<div class="subagentLiveGroup" data-message-key="__subagents__" data-count="${runs.length}" role="status" aria-live="polite">${head}<div class="subagentLiveGrid">${runs.map(subagentLiveCardHtml).join("")}</div></div>`;
    }

    // Running subagents by their card's tool_use_id, as the Chat last drew
    // them: the detail dialog follows a run here, and its Stop names the run's
    // Execution from here.
    const subagentLiveById = new Map();
    // Where each running card last stood: the run's result card arrives from
    // there when it ends (decision_b1ab45c4b3ec).
    const subagentLivePlaces = new Map();

    // The files a run changed; each opens in the Workspace preview.
    function subagentFilesHtml(files) {
      const list = (Array.isArray(files) ? files : []).filter(file => String(file?.path || "").trim());
      if (!list.length) return "";
      return window.PortalI18n.html`<div class="subagentFiles">${list.map(file => window.PortalI18n.html`<button type="button" class="subagentFileChip" data-portal-source-path="${escapeHtml(file.path)}" title="${escapeHtml(window.PortalI18n.text`${file.path}（${(file.tools || []).join("、")}）`)}"><span>${escapeHtml(subagentFileName(file.path))}</span>${Number(file.count) > 1 ? `<small>×${Number(file.count)}</small>` : ""}</button>`).join("")}</div>`;
    }

    function rememberSubagentPlaces(root) {
      root?.querySelectorAll?.(".subagentLiveCard[data-subagent-live]").forEach(card => {
        const rect = card.getBoundingClientRect();
        if (rect.width && rect.height) subagentLivePlaces.set(card.dataset.subagentLive, { rect, at: Date.now() });
      });
    }

    // A run this Chat showed running has ended once its result card is drawn
    // and its running card is gone: the result card moves in from where the
    // running card stood and lights up once.
    function revealFinishedSubagents(root, onFinished) {
      if (!root || !subagentLivePlaces.size) return;
      const latest = new Map();
      root.querySelectorAll(".subagentRunCard[data-subagent-tool-use]").forEach(card => latest.set(card.dataset.subagentToolUse, card));
      const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
      latest.forEach((card, id) => {
        const place = subagentLivePlaces.get(id);
        if (!id || !place || root.querySelector(`[data-subagent-live="${CSS.escape(id)}"]`)) return;
        subagentLivePlaces.delete(id);
        const run = subagentLiveById.get(id) || {};
        subagentLiveById.delete(id);
        const to = card.getBoundingClientRect();
        const dx = place.rect.left - to.left;
        const dy = place.rect.top - to.top;
        if (!reduced && typeof card.animate === "function" && Date.now() - place.at < 5000 && Math.abs(dy) < window.innerHeight) {
          card.animate([
            { transform: `translate(${dx}px, ${dy}px)`, opacity: 0.35 },
            { transform: "none", opacity: 1 },
          ], { duration: 420, easing: "cubic-bezier(.2, .8, .2, 1)" });
        }
        card.setAttribute("data-just-finished", "");
        window.setTimeout(() => card.removeAttribute("data-just-finished"), 2600);
        onFinished?.({ ...run, status: card.dataset.status || run.status });
      });
    }

    // While the page is hidden, its title counts the subagents that ended.
    let subagentTitleCount = 0;
    let subagentTitleBase = "";
    let subagentTitleShown = "";

    function badgeTitleForSubagent() {
      if (!subagentTitleCount) {
        subagentTitleBase = document.title;
        const restore = () => {
          if (document.hidden) return;
          document.removeEventListener("visibilitychange", restore);
          if (document.title === subagentTitleShown) document.title = subagentTitleBase;
          subagentTitleCount = 0;
        };
        document.addEventListener("visibilitychange", restore);
      }
      subagentTitleCount += 1;
      subagentTitleShown = window.PortalI18n.text`✓ ${subagentTitleCount} 个子 agent 已结束 · ${subagentTitleBase}`;
      document.title = subagentTitleShown;
    }

    const SUBAGENT_RUNNING_STEPS = new Set(["running", "inProgress", "in_progress"]);

    function subagentMarkdown(text) {
      return window.MD?.render?.(String(text || "")) || escapeHtml(String(text || "")).replace(/\n/g, "<br>");
    }

    // One step of the timeline: a call opens to its whole input and output,
    // a failed one is red and open, what the subagent said reads in full.
    function subagentTimelineStepHtml(step) {
      const index = Number(step.index ?? 0);
      const kind = String(step.kind || "");
      const linkified = value => linkifyResourceAddresses(escapeHtml(String(value || "")));
      if (kind === "tool") {
        const running = SUBAGENT_RUNNING_STEPS.has(String(step.status || ""));
        const input = subagentStepInput(step.input);
        const when = running && step.started_at
          ? `<span class="subagentStepClock" data-subagent-since="${escapeHtml(step.started_at)}">${escapeHtml(subagentClockText(step.started_at))}</span>`
          : escapeHtml(subagentStepDuration(step));
        const files = subagentFilesHtml((step.files || []).map(path => ({ path, tools: [step.tool_name], count: 1 })));
        return window.PortalI18n.html`<li class="subagentStep" data-step-index="${index}" data-step-kind="tool"${step.error ? ' data-step-error="1"' : ""}${running ? " data-step-running" : ""}><details${step.error ? " open" : ""}>
        <summary><span class="subagentStepMark" aria-hidden="true"></span><code>${escapeHtml(step.tool_name || "tool")}</code><span class="subagentStepText">${escapeHtml(subagentStepSummary(step))}</span><span class="subagentStepWhen">${when}</span></summary>
        <div class="subagentStepBody">${files}
          ${input ? window.PortalI18n.html`<div class="subagentStepLabel">输入</div><pre class="subagentStepPre">${linkified(input)}</pre>` : ""}
          ${step.error_text ? window.PortalI18n.html`<div class="subagentStepLabel">错误</div><pre class="subagentStepPre" data-step-error="1">${linkified(step.error_text)}</pre>` : ""}
          ${step.output ? window.PortalI18n.html`<div class="subagentStepLabel">输出${step.output_truncated ? window.PortalI18n.t("（较长，只显示开头）") : ""}</div><pre class="subagentStepPre">${linkified(step.output)}</pre>` : running ? window.PortalI18n.html`<small class="nativeTurnEmpty">正在运行…</small>` : ""}
        </div></details></li>`;
      }
      const label = kind === "input" ? window.PortalI18n.t("任务") : kind === "thinking" ? window.PortalI18n.t("思考")
        : step.final ? window.PortalI18n.t("回答") : window.PortalI18n.t("说");
      const at = step.at ? window.PortalUI?.formatDateTime?.(step.at, { style: "compact" }) || "" : "";
      const body = kind === "message"
        ? `<div class="subagentStepMarkdown">${linkifyResourceAddresses(subagentMarkdown(step.text))}</div>`
        : `<div class="subagentStepPlain">${linkified(step.text)}</div>`;
      return window.PortalI18n.html`<li class="subagentStep" data-step-index="${index}" data-step-kind="${escapeHtml(kind)}"${step.final ? " data-step-final" : ""}><details${kind === "message" ? " open" : ""}>
      <summary><span class="subagentStepMark" aria-hidden="true"></span><span class="subagentStepKind">${escapeHtml(label)}</span><span class="subagentStepText">${escapeHtml(String(step.text || "").replace(/\s+/g, " ").trim().slice(0, 200))}</span><span class="subagentStepWhen">${escapeHtml(at)}</span></summary>
      <div class="subagentStepBody">${body}${step.text_truncated ? window.PortalI18n.html`<small class="nativeTurnEmpty">较长，只显示开头</small>` : ""}</div></details></li>`;
    }

    let subagentDetailRuntime = null;

    function ensureSubagentDetailDialog() {
      if (subagentDetailRuntime?.overlay?.isConnected) return subagentDetailRuntime;
      const overlay = document.createElement("div");
      overlay.className = "portalDetailOverlay subagentDetailOverlay";
      overlay.dataset.portalDetailOverlay = "";
      overlay.hidden = true;
      overlay.setAttribute("aria-hidden", "true");
      overlay.innerHTML = window.PortalI18n.html`<div class="portalDetailSurface subagentDetailSurface" data-portal-detail-surface role="dialog" aria-modal="true" aria-labelledby="subagentDetailTitle">
      <div class="portalDetailHeader" data-portal-detail-header>
        <div class="subagentDetailHeading"><div class="subagentDetailTitle" id="subagentDetailTitle" data-subagent-detail-title></div><div class="subagentDetailMeta" data-subagent-detail-meta></div></div>
        <button class="portalDetailClose" data-portal-detail-close data-portal-icon="close" type="button" aria-label="关闭子 agent 详情" title="关闭"></button>
      </div>
      <div class="subagentDetailBody" data-subagent-detail-body>
        <section class="subagentDetailSection" data-subagent-detail-prompt></section>
        <section class="subagentDetailSection" data-subagent-detail-files></section>
        <section class="subagentDetailSection" data-subagent-detail-result></section>
        <section class="subagentDetailSection"><h3>步骤</h3><ol class="subagentTimeline" data-subagent-detail-steps></ol><p class="subagentDetailNote" data-subagent-detail-status role="status"></p></section>
      </div>
      <div class="portalDetailFooter subagentDetailFooter" data-subagent-detail-footer></div>
    </div>`;
      document.body.appendChild(overlay);
      window.PortalDetailSurface?.mountAll?.(overlay);
      const runtime = { overlay, session: null, dialog: null };
      runtime.dialog = window.PortalUI.createDialogController({
        modal: overlay,
        backdropClose: true,
        escapeClose: true,
        requestClose: () => runtime.dialog.close(),
        onClose: () => {
          runtime.session?.stop();
          runtime.session = null;
        },
      });
      overlay.querySelector("[data-portal-detail-close]")?.addEventListener("click", () => runtime.dialog.close());
      overlay.addEventListener("click", event => {
        const button = event.target instanceof Element ? event.target.closest("[data-subagent-detail-action]") : null;
        if (!button || button.disabled) return;
        if (button.dataset.subagentDetailAction === "close") runtime.dialog.close();
        else runtime.session?.action(button.dataset.subagentDetailAction);
      });
      subagentDetailRuntime = runtime;
      return runtime;
    }

    // The whole run of one subagent: its task, the files it changed, its token
    // use and every step from its own trace (GetSubagentRun), followed while
    // it runs.  ``actions(live)`` are the footer buttons of the Chat that
    // opened it, ``onAction(name, context)`` runs them.
    function openSubagentDetail(options = {}) {
      const runtime = ensureSubagentDetailDialog();
      runtime.session?.stop();
      const { overlay } = runtime;
      const residentId = String(options.residentId || "");
      const id = String(options.run?.tool_use_id || "");
      const traceRef = String(options.run?.trace_ref || id);
      const el = name => overlay.querySelector(`[data-subagent-detail-${name}]`);
      const scroller = el("body");
      const list = el("steps");
      let run = { ...(options.run || {}) };
      let steps = [];
      let total = 0;
      let timer = 0;
      let stopped = false;
      let signature = "";
      const isLive = () => options.live === true && !!document.querySelector(`[data-subagent-live="${CSS.escape(id)}"]`);
      const openPreview = request => {
        runtime.dialog.close();
        return requestWorkspacePreview(request);
      };
      const bindLinks = root => {
        bindResponseSourcePaths(root, { onLinkPreview: openPreview });
        bindResourceChips(root, { onResourcePreview: context => openPreview({ ...context, resourceAddress: context.address, path: context.address, title: context.node?.textContent?.trim() || context.address, source: "chat" }) });
        bindLinkPreview(root, { onLinkPreview: openPreview });
      };
      const renderRun = () => {
        const live = isLive();
        if (live) run = { ...run, ...(subagentLiveById.get(id) || {}) };
        const prompt = String(run.prompt || steps.find(step => step.kind === "input")?.text || "").trim();
        const next = JSON.stringify([live, run.status, run.description, run.agent_type, run.model, run.usage, run.files, run.result, prompt, run.finished_at]);
        if (next === signature) return;
        signature = next;
        el("title").textContent = subagentRunTitle(run);
        el("meta").innerHTML = [
          `<span data-subagent-detail-state="${live ? "running" : escapeHtml(run.status || "")}">${escapeHtml(live ? window.PortalI18n.t("运行中") : subagentStatusLabel(run.status))}</span>`,
          live && run.started_at
            ? `<span data-subagent-since="${escapeHtml(run.started_at)}">${escapeHtml(subagentClockText(run.started_at))}</span>`
            : subagentRunDuration(run) ? `<span>${escapeHtml(subagentRunDuration(run))}</span>` : "",
          subagentTokens(run.usage) ? `<span>${escapeHtml(subagentTokens(run.usage))}</span>` : "",
          run.usage?.tool_uses ? `<span>${escapeHtml(window.PortalI18n.text`${run.usage.tool_uses} 次工具调用`)}</span>` : "",
          run.model ? `<span>${escapeHtml(run.model)}</span>` : "",
          run.background ? `<span>${escapeHtml(window.PortalI18n.t("后台"))}</span>` : "",
        ].filter(Boolean).join("");
        el("prompt").innerHTML = window.PortalI18n.html`<h3>它收到的任务</h3>${prompt
        ? window.PortalI18n.html`<div class="subagentDetailPrompt">${linkifyResourceAddresses(escapeHtml(prompt))}</div>${run.prompt_truncated ? window.PortalI18n.html`<small class="nativeTurnEmpty">较长，只显示开头</small>` : ""}`
        : window.PortalI18n.html`<small class="nativeTurnEmpty">这个 Provider 没有给出任务全文</small>`}`;
        const files = subagentFilesHtml(run.files);
        el("files").hidden = !files;
        el("files").innerHTML = files ? window.PortalI18n.html`<h3>改动的文件</h3>${files}` : "";
        const result = !live && String(run.result || "").trim();
        el("result").hidden = !result;
        el("result").innerHTML = result
          ? window.PortalI18n.html`<h3>结果</h3><div class="subagentStepMarkdown">${linkifyResourceAddresses(subagentMarkdown(run.result))}</div>${run.result_truncated ? window.PortalI18n.html`<small class="nativeTurnEmpty">卡片只保留开头；完整回答见步骤中的最后一条</small>` : ""}`
          : "";
        el("footer").innerHTML = (options.actions?.(live) || []).filter(action => !action.hidden)
          .map(action => `<button type="button" class="portalButton${action.danger ? " is-danger" : ""}" data-subagent-detail-action="${escapeHtml(action.action)}"${action.title ? ` title="${escapeHtml(action.title)}"` : ""}>${escapeHtml(action.label)}</button>`).join("")
          + window.PortalI18n.html`<button type="button" class="portalButton" data-subagent-detail-action="close">关闭</button>`;
        for (const name of ["prompt", "files", "result"]) bindLinks(el(name));
        if (live) startSubagentClocks();
      };
      const renderSteps = (from, fresh) => {
        const nearBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80;
        const open = new Set([...list.querySelectorAll(":scope > li > details")]
          .map(details => [details.parentElement.dataset.stepIndex, details.open]).filter(([, isOpen]) => isOpen).map(([index]) => index));
        [...list.children].forEach(item => { if (Number(item.dataset.stepIndex) >= from) item.remove(); });
        const template = document.createElement("template");
        template.innerHTML = fresh.map(subagentTimelineStepHtml).join("");
        const added = [...template.content.children];
        added.forEach(item => {
          if (open.has(item.dataset.stepIndex)) item.querySelector(":scope > details")?.setAttribute("open", "");
          list.append(item);
          bindLinks(item);
        });
        if (added.some(item => item.hasAttribute("data-step-running"))) startSubagentClocks();
        // A reader who scrolled down to the latest step keeps following it.
        if (nearBottom && scroller.scrollTop > 0 && isLive()) scroller.scrollTop = scroller.scrollHeight;
      };
      const load = async () => {
        // A call still running reads again from there; the rest of the trace only grows.
        const running = steps.find(step => step.kind === "tool" && SUBAGENT_RUNNING_STEPS.has(String(step.status || "")));
        const from = running ? Number(running.index) : total;
        try {
          const page = await readSubagentTrace(residentId, traceRef, from);
          if (stopped) return;
          total = page.total;
          steps = [...steps.filter(step => Number(step.index) < from), ...page.steps];
          renderSteps(from, page.steps);
          el("status").textContent = steps.length ? "" : window.PortalI18n.t("还没有记录的步骤");
        } catch (error) {
          if (stopped) return;
          el("status").textContent = error?.code === "instruction.not_found"
            ? window.PortalI18n.t("这个子 agent 还没有记录的步骤")
            : window.PortalI18n.text`步骤读取失败：${error?.message || String(error)}`;
        }
      };
      const tick = async () => {
        if (stopped) return;
        const live = isLive();
        renderRun();
        await load();
        if (stopped) return;
        renderRun();
        // A run that ended gets its last steps once more, then stays still.
        if (live) timer = window.setTimeout(tick, SUBAGENT_DETAIL_POLL_MS);
      };
      runtime.session = {
        stop() {
          stopped = true;
          window.clearTimeout(timer);
        },
        action(name) {
          return options.onAction?.(name, { run, steps, live: isLive(), close: () => runtime.dialog.close() });
        },
      };
      list.replaceChildren();
      el("status").textContent = window.PortalI18n.t("正在读取步骤…");
      scroller.scrollTop = 0;
      runtime.dialog.open();
      tick();
      return runtime;
    }

    function subagentResultMarkdown(run, result) {
      const lines = [`# ${subagentRunTitle(run)}`, ""];
      const facts = [
        [window.PortalI18n.t("状态"), subagentStatusLabel(run.status)],
        [window.PortalI18n.t("开始"), run.started_at],
        [window.PortalI18n.t("结束"), run.finished_at],
        [window.PortalI18n.t("模型"), run.model],
        [window.PortalI18n.t("用量"), subagentTokens(run.usage)],
        ["tool_use_id", run.tool_use_id],
      ].filter(([, value]) => String(value || "").trim());
      facts.forEach(([name, value]) => lines.push(`- ${name}：${value}`));
      if (String(run.prompt || "").trim()) lines.push("", `## ${window.PortalI18n.t("任务")}`, "", String(run.prompt).trim());
      lines.push("", `## ${window.PortalI18n.t("结果")}`, "", String(result || "").trim() || window.PortalI18n.t("子 agent 没有返回文字结果"));
      const files = Array.isArray(run.files) ? run.files : [];
      if (files.length) lines.push("", `## ${window.PortalI18n.t("改动的文件")}`, "", ...files.map(file => `- ${file.path}`));
      return `${lines.join("\n")}\n`;
    }

    return Object.freeze({
      subagentTypeLabel, subagentRunTitle, subagentRunCard, subagentLiveHtml, patchSubagentLive,
      subagentLiveById, rememberSubagentPlaces, revealFinishedSubagents, badgeTitleForSubagent,
      subagentFullResult, openSubagentDetail, subagentSlug, subagentResultMarkdown,
      SUBAGENT_STOP_REASONS,
    });
  }

  window.PortalChatWorkspaceSubagents = Object.freeze({
    createSubagents, dependencies: DEPENDENCIES, patchSubagentLive, subagentFullResult,
    subagentSlug, SUBAGENT_STOP_REASONS,
  });
})();
