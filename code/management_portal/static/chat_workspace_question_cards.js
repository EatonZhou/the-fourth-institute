(() => {
  if (window.PortalChatWorkspaceQuestionCards) return;

  // The questions a Resident asks in the Chat: asynchronous question groups
  // and their answers saved per conversation, native approval and MCP cards,
  // and the plan and changes card of a turn.
  const DEPENDENCIES = Object.freeze([
    "escapeHtml", "nativeInteractionNames", "requestWorkspacePreview", "residentSessionScope",
  ]);

  function createQuestionCards(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalChatWorkspaceQuestionCards is missing: ${missing.join(", ")}`);
    const {
      escapeHtml, nativeInteractionNames, requestWorkspacePreview, residentSessionScope,
    } = dependencies;

    class ResidentQuestionCards {
      constructor(panel) {
        this.panel = panel;
        // Native approval and MCP cards, by request.
        this.cards = new Map();
        this.turnEditors = new Map();
        this.nativeOutcomes = new Map();
        this.sending = new Set();
        // Each open question group's chosen answers and current question, by
        // group key; kept per actor and conversation until the group closes.
        this.progress = new Map();
        // The card of the one open question group being answered, shown where
        // it was asked; ``chosen`` names an earlier open group picked for it.
        this.active = { key: "", element: null, error: "" };
        this.chosen = "";
        // Groups closed here, until the conversation shows their closed slot.
        this.closed = new Set();
        this.scope = "";
      }

      reset() {
        // Reloading the same conversation keeps its message nodes and their open
        // diffs, so only editors whose card has left the page are released here.
        this.releaseTurnEditors();
        this.scope = "";
        for (const card of this.cards.values()) card.form.remove();
        this.cards.clear();
        this.nativeOutcomes.clear();
        this.progress.clear();
        this.chosen = "";
        this.closed.clear();
        this.closeActive();
      }

      progressKey(scope = this.scope) {
        return ["portal.residentQuestionProgress.v1", this.panel.options.draftOwner || "anonymous", scope]
          .map(part => encodeURIComponent(part)).join(":");
      }

      saveProgress() {
        if (!this.scope) return;
        if (this.progress.size) window.PortalState.storage.writeJson(this.progressKey(), Object.fromEntries(this.progress));
        else window.PortalState.storage.remove(this.progressKey());
      }

      // A question group is the questions one Agent message asks (its
      // ``async_questions`` part) or one native ``request_user_input`` request.
      // A message's group is answered by one answer message and stays open
      // until then, whatever later messages and groups come, unless a person
      // closes it (its answer slot is then cancelled as question_set_closed);
      // the server applies the same rule.  A member reply shown on a Family
      // facade carries the state the member's own Conversation gives its group.
      questionGroups(snapshot) {
        const messages = snapshot.messages || [];
        const queued = new Set((snapshot.queue?.items || []).map(item => item.message_id));
        const states = new Map((snapshot.input_states || []).map(input => [input.message_id, input.state]));
        const closed = new Set([...this.closed, ...(snapshot.input_states || [])
          .filter(input => input.error?.code === "question_set_closed").map(input => input.message_id)]);
        const answers = new Map();
        for (const message of messages) {
          if (message.role !== "user") continue;
          for (const part of message.content_parts || []) {
            if (part.type !== "async_question_answer") continue;
            const merged = answers.get(part.source_message_id) || {};
            for (const entry of part.answers || []) merged[entry.question_index] = entry.answer;
            answers.set(part.source_message_id, merged);
          }
        }
        const nativeResults = new Map(messages.flatMap(message => (message.content_parts || [])
          .filter(part => part.type === "tool_result" && part.tool_name === "request_user_input")
          .map(part => [part.request_id, part])));
        const groups = [];
        for (const message of messages) {
          if (message.role !== "agent") continue;
          for (const part of message.content_parts || []) {
            if (part.type === "async_questions") {
              const answered = answers.has(message.message_id)
                || (!closed.has(part.answer_message_id) && (states.has(part.answer_message_id) || queued.has(part.answer_message_id)));
              groups.push({
                kind: "async", key: part.answer_message_id, source_message_id: message.message_id,
                // A member's set shown on a facade is closed in the member's own conversation.
                closable: !message.family_reply,
                // Permissions are lent to the Resident this conversation belongs to, never through a facade.
                lendable: !message.family_reply,
                questions: (part.questions || []).map(question => ({ id: question.question_index, title: question.title, options: question.options || [],
                  credentials: Array.isArray(question.credentials) ? question.credentials : [] })),
                state: answered ? "answered" : closed.has(part.answer_message_id) ? "closed" : part.state || "open",
                answers: answers.get(message.message_id) || {}, delivery: states.get(part.answer_message_id) || "",
              });
            } else if (part.type === "tool_call" && part.tool_name === "request_user_input" && !part.interaction) {
              const result = nativeResults.get(part.request_id)
                || (part.status !== "pending" ? part : this.nativeOutcomes.get(part.request_id) || part);
              groups.push({
                kind: "native", key: part.request_id, source_message_id: message.message_id, native: part,
                questions: (part.questions || []).map(question => ({ id: question.id, title: question.question, options: (question.options || []).map(option => option.label) })),
                state: result.status === "pending" ? "open" : result.status,
                answers: Object.fromEntries(Object.entries(result.answers || {}).map(([id, answer]) => [id, (answer.answers || []).join("、")])),
                delivery: "",
              });
            }
          }
        }
        return groups;
      }

      render(snapshot) {
        const target = this.panel.workspace.target();
        const scope = residentSessionScope(target);
        if (this.scope !== scope) {
          this.reset();
          this.scope = scope;
          this.progress = new Map(Object.entries(window.PortalState.storage.readJson(this.progressKey(), {}) || {}));
        }
        const messages = new Map((snapshot.messages || []).map(message => [message.message_id, message]));
        this.bindTurnArtifacts(messages, target, snapshot.controls?.can_send === true);
        const hosts = new Map([...this.panel.workspace.els.messages.querySelectorAll("[data-resident-question]")]
          .map(element => [element.dataset.residentQuestion, element]));
        const canSend = snapshot.controls?.can_send === true;
        const groups = this.questionGroups(snapshot);
        // One open group at a time is answered where it was asked: the one
        // picked from its summary or already being edited, else the newest;
        // the others wait in summary.
        const answerable = canSend ? groups.filter(group => group.state === "open" && hosts.has(group.key)) : [];
        const open = answerable.find(group => group.key === this.chosen) || answerable.at(-1) || null;
        for (const group of groups) {
          const host = hosts.get(group.key);
          if (host && group !== open) this.renderSummary(host, group, canSend);
        }
        const progressCount = this.progress.size;
        for (const group of groups) {
          // Discard the choices of a group that has closed; a group off this
          // page keeps them until it is seen again.
          if (group.state !== "open") this.progress.delete(group.key);
        }
        if (this.progress.size !== progressCount) this.saveProgress();
        if (open) this.renderActive(open, hosts.get(open.key), target, scope);
        else this.closeActive();
        this.renderInteractions(messages, hosts, target, scope, canSend);
      }

      renderSummary(host, group, canSend) {
        const labels = {
          open: canSend ? window.PortalI18n.t("等待回答") : window.PortalI18n.t("只有拥有发送权限的用户可以回答。"),
          expired: group.kind === "native" ? window.PortalI18n.t("请求已失效") : window.PortalI18n.t("已失效：原对话已清空"),
          closed: window.PortalI18n.t("已关闭，不再回答"),
          cancelled: window.PortalI18n.t("请求已取消"),
          answered: ({
            queued: window.PortalI18n.t("回答已排队，将在可接收时送达"),
            failed: window.PortalI18n.t("回答已接收，但执行失败；请查看对话错误"),
            cancelled: window.PortalI18n.t("回答对应的执行已取消"),
          })[group.delivery] || window.PortalI18n.t("回答已送达"),
        };
        const status = labels[group.state] || window.PortalI18n.t("请求已结束");
        const pick = canSend && group.state === "open";
        const closing = this.sending.has(`${this.scope}:${group.key}`);
        const signature = JSON.stringify([group.state, status, group.answers, pick, closing]);
        if (host.dataset.questionSummary === signature) return;
        host.dataset.questionSummary = signature;
        host.innerHTML = window.PortalI18n.html`<div class="residentQuestionSummary" data-state="${escapeHtml(group.state)}">
        <div class="residentQuestionSummaryHead"><span class="residentQuestionTitle">${escapeHtml(window.PortalI18n.text`Agent 的问题 · ${group.questions.length}`)}</span><span class="residentQuestionStatus" role="status">${escapeHtml(status)}</span>${pick ? window.PortalI18n.html`<span class="residentQuestionSummaryActions"><button type="button" class="portalButton is-compact" data-question-pick ${closing ? "disabled" : ""}>回答</button>${group.closable ? window.PortalI18n.html`<button type="button" class="portalButton is-compact is-quiet" data-question-close ${closing ? "disabled" : ""}>不再回答</button>` : ""}</span>` : ""}</div>
        <ol>${group.questions.map(question => {
          const answer = group.answers[question.id];
          return window.PortalI18n.html`<li><span>${escapeHtml(question.title)}</span>${answer ? window.PortalI18n.html`<strong>${escapeHtml(answer)}</strong>` : ""}</li>`;
        }).join("")}</ol>
      </div>`;
        host.querySelector("[data-question-pick]")?.addEventListener("click", () => {
          this.chosen = group.key;
          this.render(this.panel.snapshot() || {});
          this.active.element?.querySelector(".residentQuestionChoice, [data-question-custom]")?.focus();
        });
        host.querySelector("[data-question-close]")?.addEventListener("click", () => void this.closeGroup(group));
      }

      closeActive() {
        this.active.element?.remove();
        this.active = { key: "", element: null, error: "" };
      }

      renderActive(group, host, target, scope) {
        if (this.active.key !== group.key) {
          this.closeActive();
          this.active = { key: group.key, element: document.createElement("div"), error: "" };
          this.active.element.className = "residentQuestionCard residentQuestionStepper";
          this.active.element.setAttribute("role", "group");
          this.active.element.setAttribute("aria-label", window.PortalI18n.t("Agent 的问题"));
          this.active.element.addEventListener("keydown", event => this.activeKey(event));
          this.active.element.addEventListener("focusin", () => { this.chosen = group.key; });
        }
        this.active.group = group;
        this.active.target = target;
        this.active.scope = scope;
        if (this.active.element.parentElement !== host) {
          delete host.dataset.questionSummary;
          host.replaceChildren(this.active.element);
        }
        const sending = this.sending.has(`${scope}:${group.key}`);
        const signature = JSON.stringify([this.progressOf(group), sending, this.active.error]);
        if (this.active.signature !== signature) this.drawActive();
      }

      progressOf(group) {
        if (!this.progress.has(group.key)) this.progress.set(group.key, { answers: {}, step: 0 });
        return this.progress.get(group.key);
      }

      drawActive() {
        const { group, element, scope } = this.active;
        const progress = this.progressOf(group);
        const sending = this.sending.has(`${scope}:${group.key}`);
        this.active.signature = JSON.stringify([progress, sending, this.active.error]);
        const step = Math.min(progress.step, group.questions.length - 1);
        const question = group.questions[step];
        const chosen = progress.answers[question.id] || "";
        const custom = chosen && !question.options.includes(chosen) ? chosen : "";
        // A permission request: its fixed options are the server's own words, the
        // first approves, and only the exact permissions it lists can be lent.
        const lending = question.credentials?.length > 0;
        const credentials = lending ? window.PortalI18n.html`<div class="residentQuestionCredentials">
          <span>${escapeHtml(group.lendable ? window.PortalI18n.t("同意后随这条回答交给 Resident 的权限：") : window.PortalI18n.t("成员在 Family 内提出的权限请求不能经门面交出"))}</span>
          <ul>${question.credentials.map(item => window.PortalI18n.html`<li><code>${escapeHtml(item.a)}</code> · <code>${escapeHtml(item.resource_id)}</code></li>`).join("")}</ul>
          <span class="residentQuestionCredentialScope">${escapeHtml(window.PortalI18n.t("只在 Resident 处理这条回答期间有效，处理完即失效。"))}</span>
        </div>` : "";
        const hint = this.active.error || (sending ? window.PortalI18n.t("正在提交…")
          : group.kind === "native" ? (group.native.is_blocking ? window.PortalI18n.t("Agent 正在等待你的回答") : window.PortalI18n.t("Agent 继续工作；请求结束后将不可回答。"))
          : window.PortalI18n.t("这组问题在回答前一直保留"));
        element.classList.toggle("has-error", Boolean(this.active.error));
        element.setAttribute("aria-busy", String(sending));
        element.innerHTML = window.PortalI18n.html`<div class="residentQuestionStepHead">
          <span class="residentQuestionStepProgress">${escapeHtml(window.PortalI18n.text`问题 ${step + 1}/${group.questions.length}`)}</span>
          <span class="residentQuestionStatus" role="status">${escapeHtml(hint)}</span>
          ${step > 0 ? window.PortalI18n.html`<button type="button" class="portalButton is-compact" data-question-back>上一题</button>` : ""}
          ${group.kind === "native" ? window.PortalI18n.html`<button type="button" class="portalButton is-compact" data-question-cancel>取消请求</button>` : ""}
          ${group.kind === "async" && group.closable ? window.PortalI18n.html`<button type="button" class="portalButton is-compact is-quiet" data-question-close>不再回答</button>` : ""}
        </div>
        <div class="residentQuestionTitle">${escapeHtml(question.title)}</div>
        ${credentials}
        <div class="residentQuestionChoices">${question.options.map((option, index) => window.PortalI18n.html`<button type="button" class="portalButton residentQuestionChoice" data-question-option="${index}" aria-pressed="${String(option === chosen)}">${index < 9 ? window.PortalI18n.html`<kbd>${index + 1}</kbd>` : ""}<span>${escapeHtml(lending ? window.PortalI18n.t(option) : option)}</span></button>`).join("")}</div>
        ${lending ? "" : window.PortalI18n.html`<div class="residentQuestionCustom"><input type="text" data-question-custom value="${escapeHtml(custom)}" placeholder="${escapeHtml(question.options.length ? window.PortalI18n.t("其他回答，按 Enter 确认") : window.PortalI18n.t("输入回答，按 Enter 确认"))}" aria-label="${escapeHtml(window.PortalI18n.text`回答：${question.title}`)}" /><button type="button" class="portalButton is-compact is-primary" data-question-confirm>${escapeHtml(step === group.questions.length - 1 ? window.PortalI18n.t("提交回答") : window.PortalI18n.t("下一题"))}</button></div>`}`;
        for (const control of element.querySelectorAll("button, input")) control.disabled = sending;
        if (lending && !group.lendable) element.querySelector('[data-question-option="0"]').disabled = true;
        element.querySelectorAll("[data-question-option]").forEach(button => button.addEventListener("click", () => {
          this.answer(question.options[Number(button.dataset.questionOption)]);
        }));
        const input = element.querySelector("[data-question-custom]");
        element.querySelector("[data-question-confirm]")?.addEventListener("click", () => this.answer(input.value.trim() || chosen));
        element.querySelector("[data-question-back]")?.addEventListener("click", () => {
          progress.step = step - 1;
          this.saveProgress();
          this.active.error = "";
          this.drawActive();
          element.querySelector(".residentQuestionChoice, [data-question-custom]")?.focus();
        });
        element.querySelector("[data-question-cancel]")?.addEventListener("click", () => {
          void this.sendNativeQuestions(group, "cancel");
        });
        element.querySelector("[data-question-close]")?.addEventListener("click", () => void this.closeGroup(group));
      }

      activeKey(event) {
        // Keys typed in the card answer the question; the conversation's own shortcuts do not see them.
        event.stopPropagation();
        if (event.isComposing) return;
        const input = event.target.closest?.("[data-question-custom]");
        if (input) {
          if (event.key === "Enter" && input.value.trim()) {
            event.preventDefault();
            this.answer(input.value.trim());
          }
          return;
        }
        const option = /^[1-9]$/.test(event.key) ? this.active.element.querySelector(`[data-question-option="${Number(event.key) - 1}"]`) : null;
        if (option && !option.disabled && !event.ctrlKey && !event.metaKey && !event.altKey) {
          event.preventDefault();
          option.click();
        }
      }

      answer(value) {
        const { group } = this.active;
        if (!group || !value || this.sending.has(`${this.active.scope}:${group.key}`)) return;
        const progress = this.progressOf(group);
        const step = Math.min(progress.step, group.questions.length - 1);
        progress.answers[group.questions[step].id] = value;
        this.active.error = "";
        // Go on to the next open question; a revised earlier answer walks the
        // following questions again, and the last answer submits the group.
        const unanswered = group.questions.map((question, index) => index).filter(index => !progress.answers[group.questions[index].id]);
        const next = unanswered.find(index => index > step) ?? unanswered[0] ?? (step + 1 < group.questions.length ? step + 1 : -1);
        if (next < 0) {
          progress.step = step;
          this.saveProgress();
          void (group.kind === "native" ? this.sendNativeQuestions(group, "answer") : this.sendAnswers(group));
          return;
        }
        progress.step = next;
        this.saveProgress();
        this.drawActive();
        this.active.element.querySelector(".residentQuestionChoice, [data-question-custom]")?.focus();
      }

      async sendAnswers(group) {
        const { target, scope } = this.active;
        const key = `${scope}:${group.key}`;
        if (this.sending.has(key)) return;
        const progress = this.progressOf(group);
        const answers = group.questions.map(question => ({ question_index: question.id, answer: progress.answers[question.id] }));
        // The user lends, as themself, exactly what each approved request lists.
        const lent = new Map();
        if (group.lendable) {
          for (const question of group.questions) {
            if (!question.credentials?.length || progress.answers[question.id] !== question.options[0]) continue;
            for (const item of question.credentials) lent.set(`${item.a}\n${item.resource_id}`, { a: item.a, resource_id: item.resource_id });
          }
        }
        this.sending.add(key);
        this.render(this.panel.snapshot() || {});
        try {
          const result = await window.PortalResidentProtocol.sendInput({
            resident_id: target.resident_id,
            lineage_id: target.lineage_id,
            ...(lent.size ? { temporary_credentials: [...lent.values()] } : {}),
            message: {
              message_id: group.key,
              role: "user",
              content_parts: [
                { type: "text", text: answers.map(entry => entry.answer).join("\n") },
                { type: "async_question_answer", source_message_id: group.source_message_id, answers },
              ],
            },
          });
          if (this.scope === scope) {
            this.progress.delete(group.key);
            this.saveProgress();
            this.panel.options.onAcknowledgement?.(result, { messageIdentity: group.key }, this.panel);
          }
        } catch (error) {
          if (this.scope === scope && this.active.key === group.key) this.active.error = error?.message || window.PortalI18n.t("回答发送失败，请重试");
        } finally {
          this.sending.delete(key);
          if (this.scope === scope) this.render(this.panel.snapshot() || {});
        }
      }

      // Closing a group leaves it unanswered and tells the Resident nothing; the
      // server records it on the group's answer slot (AnswerResidentRequest cancel).
      async closeGroup(group) {
        const target = this.panel.workspace.target();
        const scope = this.scope;
        const key = `${scope}:${group.key}`;
        if (this.sending.has(key)) return;
        this.sending.add(key);
        this.render(this.panel.snapshot() || {});
        try {
          await window.PortalResidentProtocol.respondUserInput({ resident_id: target.resident_id, message_id: group.source_message_id, action: "cancel" });
          if (this.scope === scope) this.closed.add(group.key);
        } catch (error) {
          if (this.scope === scope) window.PortalUI?.toast?.(error?.message || window.PortalI18n.t("关闭失败，请重试"), "error");
        } finally {
          this.sending.delete(key);
          if (this.scope === scope) this.render(this.panel.snapshot() || {});
        }
      }

      // A native request takes its answer through the Execution's control channel;
      // ``status`` reads whether the Provider has taken it.
      async respondNative(target, scope, sourceMessageId, payload) {
        let result = await window.PortalResidentProtocol.respondUserInput({ resident_id: target.resident_id, message_id: sourceMessageId, ...payload });
        for (let attempt = 0; result.status === "pending" && attempt < 60; attempt += 1) {
          await new Promise(resolve => setTimeout(resolve, 500));
          if (this.scope !== scope) return null;
          result = await window.PortalResidentProtocol.respondUserInput({ resident_id: target.resident_id, message_id: sourceMessageId, action: "status" });
        }
        return result;
      }

      async sendNativeQuestions(group, action) {
        const { target, scope } = this.active;
        const key = `${scope}:${group.key}`;
        if (this.sending.has(key)) return;
        const progress = this.progressOf(group);
        const answers = action === "cancel" ? {} : Object.fromEntries(group.questions.map(question => [question.id, { answers: [progress.answers[question.id]] }]));
        this.sending.add(key);
        this.render(this.panel.snapshot() || {});
        try {
          const result = await this.respondNative(target, scope, group.source_message_id, { action, answers });
          if (!result) return;
          if (result.status === "expired" || (result.status === "cancelled" && action !== "cancel")) {
            this.nativeOutcomes.set(group.key, { status: "expired", answers: {} });
          }
          if (!(result.status === "answered" || (result.status === "cancelled" && action === "cancel"))) throw new Error(result.error || "提交尚未确认；可重试查询，请勿改选后重复提交。");
          this.nativeOutcomes.set(group.key, { status: action === "cancel" ? "cancelled" : "answered", answers });
          this.progress.delete(group.key);
          this.saveProgress();
          this.panel.protocol?.load?.({ force: true });
        } catch (error) {
          if (this.active.key === group.key) this.active.error = error?.message || window.PortalI18n.t("回答发送失败，请重试");
        } finally {
          this.sending.delete(key);
          if (this.scope === scope) this.render(this.panel.snapshot() || {});
        }
      }

      renderInteractions(messages, hosts, target, scope, canSend) {
        const questions = [];
        const nativeResults = new Map([...messages.values()].flatMap(message => (message.content_parts || [])
          .filter(part => part.type === "tool_result" && nativeInteractionNames.has(part.tool_name))
          .map(part => [part.request_id, part])));
        for (const message of messages.values()) {
          if (message.role !== "agent") continue;
          for (const part of message.content_parts || []) {
            if (part.type !== "tool_call" || !part.interaction || !nativeInteractionNames.has(part.tool_name)) continue;
            const result = nativeResults.get(part.request_id)
              || (part.status !== "pending" ? part : this.nativeOutcomes.get(part.request_id) || part);
            const titles = { command: "运行命令审批", file_change: window.PortalI18n.t("文件修改审批"), permissions: window.PortalI18n.t("额外权限审批"), mcp: window.PortalI18n.t("工具需要补充信息") };
            questions.push({ title: titles[part.interaction.kind], native: part, key: part.request_id,
              source_message_id: message.message_id, accepted: result.status !== "pending", state: result.status });
          }
        }
        const visible = questions.filter(question => hosts.has(question.key));
        const visibleIds = new Set(visible.map(question => question.key));
        for (const [key, card] of this.cards) {
          if (visibleIds.has(key)) continue;
          card.form.remove();
          this.cards.delete(key);
        }
        for (const question of visible) {
          let card = this.cards.get(question.key);
          if (!card) {
            card = this.createInteractionCard(question, target, scope);
            this.cards.set(question.key, card);
          }
          const host = hosts.get(question.key);
          if (card.form.parentElement !== host) host.append(card.form);
          card.question = question;
          card.canSend = canSend;
          // A later authoritative terminal result supersedes a delivery/check
          // error from the original response; it must not invite another answer.
          if (question.accepted) card.error = "";
          const sending = this.sending.has(`${scope}:${question.key}`);
          card.fields.hidden = question.accepted;
          for (const field of card.form.querySelectorAll("input, select, textarea, button")) field.disabled = !canSend || sending || question.accepted;
          card.form.setAttribute("aria-busy", String(sending));
          card.form.classList.toggle("answered", question.accepted);
          card.status.textContent = card.error || ({ answered: window.PortalI18n.t("已送达原请求"), declined: window.PortalI18n.t("已拒绝"), cancelled: window.PortalI18n.t("已取消"), expired: window.PortalI18n.t("请求已失效，不能再提交") }[question.state])
            || (!canSend ? window.PortalI18n.t("只有拥有发送权限的用户可以回答。") : !question.native.is_blocking ? window.PortalI18n.t("Agent 继续工作；请求结束后将不可回答。") : question.native.interaction.kind === "mcp" ? window.PortalI18n.t("等待你的回答") : window.PortalI18n.t("等待你的审批"));
        }
      }

      createInteractionCard(question, target, scope) {
        const interaction = question.native.interaction;
        const request = interaction.request;
        const form = document.createElement("form");
        form.className = "residentQuestionCard";
        form.dataset.questionId = question.key;
        const title = document.createElement("div");
        title.className = "residentQuestionTitle";
        title.textContent = question.title;
        const description = document.createElement("p");
        description.textContent = interaction.kind === "mcp" ? `${request.serverName}：${request.message}` : request.reason || window.PortalI18n.t("请核对操作范围。允许仅针对本次操作；额外权限仅授予本轮。");
        const fields = document.createElement("div");
        const inputs = [];
        if (interaction.kind !== "mcp") {
          const details = document.createElement("pre");
          details.className = "nativeApprovalDetails";
          details.textContent = JSON.stringify(Object.fromEntries(Object.entries(request).filter(([key]) =>
            ["command", "cwd", "permissions", "grantRoot", "networkApprovalContext", "additionalPermissions", "reason"].includes(key))), null, 2);
          fields.append(details);
          if (interaction.kind === "file_change") {
            const scope = interaction.file_change;
            const changes = scope && scope.thread_id === request.threadId && scope.turn_id === request.turnId
              && scope.item_id === request.itemId && Array.isArray(scope.changes) ? scope.changes : [];
            if (!changes.length) {
              const unavailable = document.createElement("p");
              unavailable.className = "nativeApprovalScopeMissing";
              unavailable.textContent = window.PortalI18n.t("Provider 未提供可核对的文件改动详情。");
              fields.append(unavailable);
            }
            for (const change of changes) {
              const file = document.createElement("section");
              file.className = "nativeApprovalFile";
              const heading = document.createElement("div");
              heading.className = "nativeApprovalFilePath";
              const kind = change.kind?.type;
              const operation = { add: "新增", delete: "删除", update: "修改" }[kind];
              heading.textContent = `${operation ? window.PortalI18n.t(operation) + " · " : ""}${change.path}`;
              if (change.kind?.move_path) heading.textContent += ` → ${change.kind.move_path}`;
              const diff = document.createElement("pre");
              diff.className = "nativeApprovalDetails";
              diff.textContent = typeof change.diff === "string" ? change.diff : window.PortalI18n.t("Provider 未提供此文件的差异内容。");
              file.append(heading, diff);
              fields.append(file);
            }
          }
        } else if (request.mode === "url") {
          const link = document.createElement("a");
          link.textContent = window.PortalI18n.t("在新窗口完成工具请求");
          try {
            const url = new URL(request.url);
            if (url.protocol === "https:" && !url.username && !url.password) link.href = url.href;
          } catch (_) { link.removeAttribute("href"); }
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          fields.append(link);
        } else {
          for (const [name, schema] of Object.entries(request.requestedSchema?.properties || {})) {
            const label = document.createElement("label");
            label.className = "residentMcpField";
            const heading = document.createElement("span");
            heading.textContent = schema.title || name;
            const alternatives = schema.oneOf || schema.anyOf || (schema.enum || []).map(value => ({ const: value, title: String(value) }));
            const options = schema.type === "array" ? (schema.items?.anyOf || schema.items?.oneOf || (schema.items?.enum || []).map(value => ({ const: value, title: String(value) }))) : alternatives;
            const field = document.createElement(options.length ? "select" : "input");
            field.setAttribute("aria-label", schema.title || name);
            field.name = name;
            field.required = (request.requestedSchema.required || []).includes(name);
            if (options.length) {
              field.multiple = schema.type === "array";
              if (!field.multiple) field.append(new Option("请选择…", ""));
              for (const option of options) field.append(new Option(option.title || String(option.const), JSON.stringify(option.const)));
            } else {
              field.type = schema.type === "boolean" ? "checkbox" : ["number", "integer"].includes(schema.type) ? "number" : "text";
              if (field.type === "number") field.step = schema.type === "integer" ? "1" : "any";
              if (schema.minimum != null) field.min = schema.minimum;
              if (schema.maximum != null) field.max = schema.maximum;
              if (schema.minLength != null) field.minLength = schema.minLength;
              if (schema.maxLength != null) field.maxLength = schema.maxLength;
              if (schema.type === "boolean") field.required = false;
            }
            if (schema.default != null) {
              if (schema.type === "boolean") field.checked = schema.default;
              else if (!field.multiple) field.value = options.length ? JSON.stringify(schema.default) : String(schema.default);
            }
            label.append(heading, field);
            if (schema.description) { const hint = document.createElement("small"); hint.textContent = schema.description; label.append(hint); }
            fields.append(label);
            inputs.push({ name, schema, field, options });
          }
        }
        const footer = document.createElement("div");
        footer.className = "residentQuestionFooter";
        const status = document.createElement("div");
        status.className = "residentQuestionStatus";
        status.setAttribute("role", "status");
        footer.append(status);
        const card = { form, fields, status, question, canSend: false, error: "" };
        card.content = () => {
          if (interaction.kind !== "mcp" || request.mode === "url") return null;
          return Object.fromEntries(inputs.filter(({ field, schema }) => schema.type === "boolean" || field.value !== "").map(({ name, schema, field, options }) => [name,
            field.multiple ? [...field.selectedOptions].map(option => JSON.parse(option.value)) : options.length ? JSON.parse(field.value) :
            schema.type === "boolean" ? field.checked : ["number", "integer"].includes(schema.type) ? Number(field.value) : field.value]));
        };
        for (const [action, text] of [["decline", "拒绝"], ["cancel", "取消请求"], ["accept", interaction.kind === "mcp" ? "提交" : "允许本次"]]) {
          if (Array.isArray(request.availableDecisions) && !request.availableDecisions.includes(action)) continue;
          const button = document.createElement("button");
          button.type = "button";
          button.className = `portalButton${action === "accept" ? " is-primary" : ""}`;
          button.textContent = text;
          button.addEventListener("click", () => {
            if (action === "accept" && !form.reportValidity()) return;
            void this.sendNativeInteraction(card, target, scope, action);
          });
          footer.append(button);
        }
        form.addEventListener("submit", event => event.preventDefault());
        form.append(title, description, fields, footer);
        return card;
      }

      async sendNativeInteraction(card, target, scope, action) {
        const key = `${scope}:${card.question.key}`;
        if (!card.canSend || card.question.accepted || this.sending.has(key)) return;
        this.sending.add(key);
        card.error = "";
        this.render(this.panel.snapshot() || {});
        try {
          const result = await this.respondNative(target, scope, card.question.source_message_id, {
            action, content: action === "accept" ? card.content() : null,
          });
          if (!result) return;
          if (!["answered", "declined", "cancelled", "expired"].includes(result.status)) throw new Error(result.error || "提交尚未确认；可重试查询，请勿改选后重复提交。");
          const outcome = result.status === "answered"
            ? (action === "cancel" ? "cancelled" : action === "decline" ? "declined" : "answered")
            : result.status;
          this.nativeOutcomes.set(card.question.key, { status: outcome, answers: {} });
          card.question.accepted = true;
          this.panel.protocol?.load?.({ force: true });
        } catch (error) {
          card.error = error?.message || "回答发送失败，请重试";
        } finally {
          this.sending.delete(key);
          if (this.scope === scope) this.render(this.panel.snapshot() || {});
        }
      }

      destroy() {
        this.reset();
        this.releaseTurnEditors(true);
      }

      releaseTurnEditors(all = false) {
        for (const [root, editor] of this.turnEditors) {
          if (all || !root.isConnected) { editor?.destroy?.(); this.turnEditors.delete(root); }
        }
      }

      bindTurnArtifacts(messages, target, canSend) {
        this.releaseTurnEditors();
        for (const host of this.panel.workspace.els.messages.querySelectorAll("[data-native-turn]")) {
          const source = messages.get(host.dataset.nativeTurn);
          const part = source?.content_parts?.find(candidate => ["native_turn", "resource_changes"].includes(candidate.type));
          if (!part) continue;
          const review = host.querySelector("[data-turn-review]");
          if (review) review.disabled = !canSend || Boolean(part.diff_truncated) || review.dataset.sending === "1";
          if (host.dataset.bound) continue;
          host.dataset.bound = "1";
          const details = host.querySelector("details");
          const root = host.querySelector("[data-turn-diff]");
          const fileSelect = host.querySelector("[data-turn-diff-file]");
          const preview = host.querySelector("[data-turn-file-preview]");
          let files = [];
          let activeEditor = null;
          const selectedFile = () => fileSelect?.value !== "" ? files[Number(fileSelect?.value)] : null;
          const selectedDiff = () => fileSelect?.value ? files[Number(fileSelect.value)]?.diff || part.diff : part.diff;
          const showDiff = () => {
            const value = selectedDiff();
            root.style.height = `${Math.min(380, Math.max(96, value.split("\n").length * 19 + 20))}px`;
            fileSelect.title = fileSelect.selectedOptions[0]?.textContent || window.PortalI18n.t("本轮全部修改");
            const file = selectedFile();
            preview.disabled = !file?.path || file.deleted;
            preview.title = file?.deleted ? window.PortalI18n.t("文件已删除，仅可查看 Diff") : file?.path ? window.PortalI18n.t("在 Preview 中打开当前文件") : window.PortalI18n.t("请先选择一个文件");
            if (activeEditor) activeEditor.setValue(value);
            else root.textContent = value;
          };
          fileSelect?.addEventListener("change", showDiff);
          preview?.addEventListener("click", async () => {
            const file = selectedFile();
            if (!file?.path || file.deleted || preview.disabled) return;
            preview.disabled = true;
            try {
              const open = this.panel.workspace.options.onLinkPreview || requestWorkspacePreview;
              const opened = await open({
                href: file.path, path: file.path, title: file.path, node: preview,
                resourceAddress: file.resourceAddress || "",
                source: "chat", target: this.panel.workspace.els.messages,
              });
              if (opened === false) throw new Error("当前页面没有可用的 Preview");
            } catch (error) { this.panel.reportError(error); }
            finally { if (preview.isConnected) showDiff(); }
          });
          details?.addEventListener("toggle", async () => {
            if (!details.open || this.turnEditors.has(root)) return;
            this.turnEditors.set(root, null);
            try {
              if (!window.PortalCodeEditor) {
                window.portalTurnEditorLoading ||= new Promise((resolve, reject) => {
                  const script = document.createElement("script");
                  script.src = "/static/portal_code_editor.js";
                  script.charset = "utf-8";
                  script.onload = resolve; script.onerror = reject; document.head.append(script);
                });
                await window.portalTurnEditorLoading;
              }
              if (!root.isConnected) return;
              files = window.PortalCodeEditor.splitDiffFiles(part.diff);
              if (part.type === "resource_changes") {
                // Resource diffs have one block per write, in changes order. Their
                // address@change_id labels identify snapshots, not openable files.
                files = files.map((file, index) => {
                  const change = part.changes?.[index];
                  return { ...file, label: change?.path || change?.address || file.label,
                    path: change?.address || "", resourceAddress: change?.address || "" };
                });
              }
              fileSelect.replaceChildren(...(files.length > 1 ? [new Option(`全部修改（${files.length} 个文件）`, "")] : []), ...files.map((file, index) => new Option(file.label, String(index))));
              fileSelect.parentElement.hidden = !files.length;
              showDiff();
              root.textContent = "";
              const editor = await window.PortalCodeEditor.mount({ root, value: selectedDiff(), language: "diff", readOnly: true, ariaLabel: window.PortalI18n.t("本轮修改 Diff") });
              if (!root.isConnected) { editor.destroy(); return; }
              activeEditor = editor;
              this.turnEditors.set(root, editor);
            } catch (_) { root.textContent = selectedDiff(); }
            finally { fileSelect.disabled = false; }
          });
          review?.addEventListener("click", async () => {
            if (review.disabled) return;
            review.dataset.sending = "1"; review.disabled = true;
            try {
              host.dataset.reviewMessage ||= `msg_${globalThis.PortalUIPrimitives.randomUUID().replaceAll("-", "")}`;
              await window.PortalResidentProtocol.sendInput({ resident_id: target.resident_id, message: {
                message_id: host.dataset.reviewMessage, role: "user", content_parts: [{ type: "turn_review", segment_id: part.segment_id }],
              } });
              review.textContent = window.PortalI18n.t("审查已排队");
            } catch (error) { review.dataset.sending = ""; review.disabled = !canSend; review.textContent = window.PortalI18n.t("重试审查"); review.title = error.message; }
          });
        }
      }
    }

    return Object.freeze({
      ResidentQuestionCards,
    });
  }

  window.PortalChatWorkspaceQuestionCards = Object.freeze({
    createQuestionCards, dependencies: DEPENDENCIES,
  });
})();
