"""Shared channel ↔ Portal routing (decision_66f5be947b67).

A chat sends to the User's personal assistant facade, or to one Resident named
by ID. Replies to the chat's own messages and messages the target addresses to
the User come back; everything else stays in Portal until ``/history``.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import time
from typing import Any

from .portal_client import PortalClient, RESIDENT_ID_RE, is_terminal, message_text
from .router_store import RouterStore
from .waiting import WaitingNotices


HISTORY_DEFAULT_ROUNDS = 3
HISTORY_MAX_ROUNDS = 10
HISTORY_INPUT_CHARS = 200
HISTORY_REPLY_CHARS = 1000
# Open inputs are re-read at least this often even when no event arrives.
INPUT_RECHECK_SECONDS = 20.0
CREDENTIAL_APPROVE = "同意交出这些权限"
CREDENTIAL_DECLINE = "不交出"

from .telegram_presentation import BOT_COMMANDS, HELP_TEXT, TelegramPresentation


def _failure_text(exc: Exception) -> str:
    code = str(getattr(exc, "code", "") or "").strip()
    message = str(exc or "").strip()[:240]
    if code and message:
        return f"{code}: {message}"
    return message or type(exc).__name__


def _not_found(exc: Exception) -> bool:
    return isinstance(exc, KeyError) or str(getattr(exc, "code", "") or "") in {
        "resident.not_found", "instruction.not_found", "resource.not_found"}


def _clip(text: str, limit: int) -> tuple[str, bool]:
    clean = str(text or "").strip()
    return (clean, False) if len(clean) <= limit else (clean[:limit].rstrip() + "…", True)


def _recipient_is(message: dict[str, Any], actor: str) -> bool:
    wanted = str(actor or "").strip().casefold()
    return any(
        isinstance(item, dict) and item.get("kind") == "user" and str(item.get("id") or "").casefold() == wanted
        for item in message.get("recipients") or []
    )


def question_part(message: dict[str, Any]) -> dict[str, Any] | None:
    if message.get("role") != "agent":
        return None
    return next((part for part in message.get("content_parts") or []
                 if isinstance(part, dict) and part.get("type") == "async_questions"), None)


class RouterCore:
    def __init__(self, schema: str | None = None, *, store: RouterStore | None = None,
                 portal: PortalClient | None = None, presentation=None):
        self.store = store or RouterStore(schema)
        self.portal = portal or PortalClient()
        self.presentation = presentation or TelegramPresentation()
        self.waiting = WaitingNotices(self.store)
        self._last_input_check: dict[str, float] = {}

    def init(self) -> dict[str, Any]:
        self.store.init_schema()
        return {"schema": "telepus.router.init.v2", "counts": self.store.counts()}

    def status(self) -> dict[str, Any]:
        return {"schema": "telepus.router.status.v2", "counts": self.store.counts()}

    # -- attachments (the adapter sends the bytes) -------------------------------

    def start_attachment_delivery(self, token: str) -> bool:
        return self.store.mark_attachment_sending(token)

    def ack_attachment_delivery(self, token: str, telegram_result: dict[str, Any]) -> None:
        result = telegram_result.get("result") if isinstance(telegram_result.get("result"), dict) else {}
        self.store.mark_attachment_sent(token, str(result.get("message_id") or ""))

    def fail_attachment_delivery(self, token: str, error: str) -> None:
        self.store.mark_attachment_failed(token, error)

    # -- incoming Telegram messages -------------------------------------------------

    def dispatch_message(
        self,
        *,
        chat_id: str,
        telegram_user_id: str,
        text: str,
        portal_actor: str,
        chat_type: str = "private",
        telegram_message_id: int = 0,
        reply_to_telegram_message_id: int = 0,
    ) -> dict[str, Any]:
        message = str(text or "").strip()
        if not message:
            raise ValueError("text required")
        self.waiting.received(chat_id, f"message:{telegram_message_id}" if telegram_message_id else secrets.token_hex(8))
        state = self.store.upsert_chat_state(chat_id, chat_type=chat_type, telegram_user_id=telegram_user_id,
                                             portal_actor=portal_actor)
        self.store.append_audit("message_received", chat_id=chat_id, telegram_user_id=telegram_user_id,
                                portal_actor=portal_actor, payload={"text": message[:500], "chat_type": chat_type})
        handled = self.presentation.incoming(self, state, message)
        if handled is not None:
            return handled
        if reply_to_telegram_message_id:
            session = self.store.question_session_for_message(chat_id, reply_to_telegram_message_id)
            if session:
                return self._answer_question(state, session, int(session["step"]), message, custom=True)
        if message.startswith("/"):
            return self._command(state, message)
        if RESIDENT_ID_RE.fullmatch(message):
            return self._route(state, message)
        quoted = self.store.linked_message(chat_id, reply_to_telegram_message_id) if reply_to_telegram_message_id else None
        return self._deliver(state, message, telegram_message_id=telegram_message_id, quoted=quoted)

    def _command(self, state: dict[str, Any], message: str) -> dict[str, Any]:
        command, _, rest = message.partition(" ")
        command = command.split("@", 1)[0].strip().lower()
        arg = rest.strip()
        chat_id = state["chat_id"]
        if command in {"/start", "/help"}:
            return self._reply(self.presentation.help_messages(self._where_text(state)))
        if command == "/home":
            next_state = self.store.upsert_chat_state(chat_id, mode="assistant", exact_resident_id="")
            self._audit(next_state, "mode_changed", {"mode": "assistant"})
            return self._reply([self._where_text(next_state)])
        if command == "/route":
            if not RESIDENT_ID_RE.fullmatch(arg):
                return self._reply(["请使用 `/route <resident_id>`，Resident ID 是 `resident_` 加 12 位十六进制字符。"])
            return self._route(state, arg)
        if command == "/where":
            return self._reply([self._where_text(state)])
        if command == "/history":
            if arg and (not arg.isdigit() or not 1 <= int(arg) <= HISTORY_MAX_ROUNDS):
                return self._reply([f"请使用 `/history`，或用 `/history 1` 到 `/history {HISTORY_MAX_ROUNDS}` 指定轮数。"])
            return self._history(state, int(arg) if arg else HISTORY_DEFAULT_ROUNDS, shown=0)
        if command == "/refresh":
            outgoing = self.refresh_chat(state, force=True)
            return {"messages": outgoing or [{"text": "当前没有新回复。"}]}
        if command == "/queue":
            return self._queue(state)
        if command in {"/stop", "/cancel"}:
            if arg not in {"", "all", "current"}:
                return self._reply(["请使用 `/stop` 停止全部未完成的消息；精准路由时 `/stop current` 只停当前任务。"])
            return self._stop(state, current_only=arg == "current")
        if command in {"/now", "/interject"}:
            if not arg:
                return self._reply(["请使用 `/now <补充内容>`。"])
            if state["mode"] != "exact":
                return self._reply(["个人助手模式下直接发送补充内容即可；`/now` 只用于精准路由。"])
            return self._deliver(state, arg, telegram_message_id=0, quoted=None, inject=True)
        return self._reply(["未知命令。发送 /help 查看可用命令。"])

    # -- targets --------------------------------------------------------------------

    def _route(self, state: dict[str, Any], resident_id: str) -> dict[str, Any]:
        actor = state["portal_actor"]
        try:
            resident = self.portal.resident(actor, resident_id)
        except Exception as exc:  # noqa: BLE001 - the user picks another target
            return self._reply([f"无法联系 Resident `{resident_id}`：{_failure_text(exc)}"])
        next_state = self.store.upsert_chat_state(state["chat_id"], mode="exact", exact_resident_id=resident_id)
        self._audit(next_state, "route_set", {"resident_id": resident_id})
        title = str(resident.get("title") or resident_id)
        return self._reply([f"已切换到 **{title}**（`{resident_id}`）。之后的消息直接发给它，`/home` 回到个人助手。"])

    def _target(self, state: dict[str, Any]) -> tuple[str, str, str]:
        """(resident_id, title, error) of where this chat's messages go now."""
        actor = state["portal_actor"]
        if state["mode"] == "exact" and state["exact_resident_id"]:
            resident_id = state["exact_resident_id"]
            try:
                resident = self.portal.resident(actor, resident_id)
            except Exception as exc:  # noqa: BLE001
                if not _not_found(exc) and not isinstance(exc, PermissionError):
                    return "", "", f"暂时无法读取 `{resident_id}`：{_failure_text(exc)}"
                self.store.upsert_chat_state(state["chat_id"], mode="assistant", exact_resident_id="")
                return "", "", f"Resident `{resident_id}` 已不可用，已回到个人助手。请重新发送。"
            return resident_id, str(resident.get("title") or resident_id), ""
        assistant = self.portal.personal_assistant(actor)
        if not assistant.get("resident_id"):
            return "", "", "个人助手尚未就绪，请稍后再试；也可以用 `/route <resident_id>` 直接联系某个 Resident。"
        return assistant["resident_id"], str(assistant.get("title") or "个人助手"), ""

    def _where_text(self, state: dict[str, Any]) -> str:
        if state['mode'] != 'exact':
            return '当前目标：个人助手 · 手机助手'
        return f"当前目标：精准路由 → `{state['exact_resident_id']}`"

    # -- sending ------------------------------------------------------------------------

    @staticmethod
    def _message_id(chat_id: str, telegram_message_id: int) -> str:
        if not telegram_message_id:
            return f"msg_{secrets.token_hex(16)}"
        return "msg_" + hashlib.sha256(f"telepus:{chat_id}:{telegram_message_id}".encode()).hexdigest()[:32]

    def _deliver(self, state: dict[str, Any], text: str, *, telegram_message_id: int,
                 quoted: dict[str, Any] | None, inject: bool = False) -> dict[str, Any]:
        chat_id, actor = state["chat_id"], state["portal_actor"]
        if quoted:
            # A Telegram reply goes to the Conversation the quoted message is in.
            resident_id, title = str(quoted["resident_id"]), ""
        else:
            resident_id, title, error = self._target(state)
            if error:
                return self._reply([error])
        self._watch(state, resident_id)
        message_id = self._message_id(chat_id, telegram_message_id)
        message: dict[str, Any] = {"message_id": message_id, "role": "user",
                                   "content_parts": [{"type": "text", "text": text}]}
        if quoted:
            message["quote"] = {"message_id": str(quoted["message_id"])}
        try:
            self.portal.deliver(actor, resident_id, message,
                                activation_policy="inject_active_turn" if inject else "enqueue_and_trigger")
        except Exception as exc:  # noqa: BLE001 - reported to the user, never retried as someone else
            self._audit(state, "deliver_failed", {"resident_id": resident_id, "error": _failure_text(exc)})
            if quoted and _not_found(exc):
                return self._reply(["被引用的消息所在的对话已不可用，请直接发送。"])
            return self._reply([f"消息未能送达：{_failure_text(exc)}"])
        self.store.record_input(chat_id, resident_id, message_id, telegram_message_id, excerpt=text)
        self.store.link_message(chat_id, telegram_message_id, resident_id, message_id)
        self._audit(state, "message_delivered", {"resident_id": resident_id, "message_id": message_id,
                                                 "quoted": bool(quoted), "inject": inject})
        personal = state['mode'] != 'exact' and (not quoted or
            self.portal.personal_assistant(actor).get('resident_id') == resident_id)
        notices = self._solo_notice(state, resident_id) if personal else []
        if not self.presentation.receipts:
            return {"messages": notices, "typing": True}
        if inject:
            return self._reply(["已插入当前工作。"])
        if state["mode"] == "exact" and not quoted:
            return self._reply([f"已发送到 {title}。"])
        return {'messages': notices + [{'text': '已收到。'}]}

    def _solo_notice(self, state, resident_id):
        policy = self.portal.family_chat_preferences(state['portal_actor'], resident_id)
        solo = policy.get('solo_member_id') or ''
        token = str(policy.get('solo_notice_token') or solo)
        key = 'phone-solo-notice:' + state['chat_id'] + ':' + resident_id
        if not solo:
            self.store.set_meta(key, '')
            return []
        if self.store.get_meta(key, '') == token:
            return []
        member = self.portal.resident(state['portal_actor'], solo)
        title = member.get('title') or solo
        self.store.set_meta(key, token)
        return [{'text': f'网页上你对 {title} 开了 Solo；手机消息仍由手机助手处理。'
                         f'想直接和 {title} 对话，发送 /route {solo}，/home 回到个人助手。',
                 'solo_notice_key': key}]

    def _watch(self, state: dict[str, Any], resident_id: str) -> None:
        """The refresh reads this Resident's events from now on."""
        if state.get("watch_resident_id") == resident_id:
            return
        cursor = int(self.portal.conversation(state["portal_actor"], resident_id, history_limit=1).get("cursor") or 0)
        state.update(self.store.upsert_chat_state(state["chat_id"], watch_resident_id=resident_id, watch_cursor=cursor))

    # -- replies ---------------------------------------------------------------------------

    def refresh_chat(self, state: dict[str, Any], *, force: bool = False) -> list[dict[str, Any]]:
        """Outgoing Telegram messages for what arrived since the last refresh."""
        chat_id, actor = state["chat_id"], state["portal_actor"]
        watched = state.get("watch_resident_id") or ""
        outgoing: list[dict[str, Any]] = []
        changed = False
        if watched:
            events, cursor = self.portal.events(actor, watched, int(state.get("watch_cursor") or 0))
            changed = bool(events)
            for item in events:
                event = item["event"]
                if event.get("event_type") != "conversation.message.appended":
                    continue
                message = (event.get("payload") or {}).get("message") or {}
                # A message the target addresses to this User, outside any request.
                if message.get("role") == "agent" and _recipient_is(message, actor):
                    outgoing.extend(self._reply_messages(state, watched, message, proactive=True))
        now = time.monotonic()
        recheck = force or now - self._last_input_check.get(chat_id, 0.0) >= INPUT_RECHECK_SECONDS
        for entry in self.store.open_inputs(chat_id):
            if not (recheck or (changed and entry["resident_id"] == watched)):
                continue
            outgoing.extend(self._finished_input(state, entry))
        if recheck:
            self._last_input_check[chat_id] = now
        if watched:
            self.store.watch_cursor(chat_id, watched, cursor)
            for item in outgoing:
                item["watch_from"] = (watched, int(state.get("watch_cursor") or 0))
        return outgoing

    def _finished_input(self, state: dict[str, Any], entry: dict[str, Any]) -> list[dict[str, Any]]:
        actor, resident_id, message_id = state["portal_actor"], entry["resident_id"], entry["message_id"]
        try:
            input_state = self.portal.input_state(actor, resident_id, message_id)
        except Exception as exc:  # noqa: BLE001
            if _not_found(exc):
                self.store.close_input(message_id)
                return [{"text": "这条消息所在的对话已不可用。", "reply_to_telegram_message_id": entry["telegram_message_id"]}]
            raise
        status = str(input_state.get("state") or "")
        if not is_terminal(status):
            return []
        work = input_state.get("work") or {}
        work_state = str(work.get("state") or "unknown")
        if status == "completed":
            # Delivery to an active turn is completed before the work finishes.
            # Keep the input tracked until the execution has an outcome.
            if work_state in {"queued", "running"}:
                return []
            if work_state in {"failed", "cancelled"}:
                status = work_state
        outgoing: list[dict[str, Any]] = []
        reply_ids = [str(item) for item in input_state.get("reply_message_ids") or []]
        if status == "completed" or reply_ids:
            shown = self.portal.conversation(actor, resident_id, input_message_id=message_id).get("messages") or []
            by_id = {str(item.get("message_id") or ""): item for item in shown if isinstance(item, dict)}
            if any(reply_id not in by_id for reply_id in reply_ids):
                # Input state and conversation are separate reads. A reply whose
                # body is not visible yet must not be dropped or partly claimed.
                return []
            replies = [by_id[reply_id] for reply_id in reply_ids if reply_id in by_id]
            if not replies:
                # The projection names the reply differently; its last text answer to this input stands in.
                found = next((item["reply"] for item in conversation_rounds(shown)
                              if item["input"].get("message_id") == message_id and item["reply"]), None)
                replies = [found] if found else []
            if not replies and work and work_state == "unknown":
                # Missing execution evidence is not evidence of completion.
                return []
            for reply in replies:
                outgoing.extend(self._reply_messages(state, resident_id, reply, reply_to=entry["telegram_message_id"]))
        if status != "completed" and not outgoing:
            reason = str(input_state.get("error") or input_state.get("failure_reason") or "").strip()
            label = {"cancelled": "已停止", "failed": "处理失败"}.get(status, f"已结束（{status}）")
            outgoing.append({"text": f"这条消息{label}" + (f"：{reason[:300]}" if reason else "。"),
                             "reply_to_telegram_message_id": entry["telegram_message_id"]})
        self.store.close_input(message_id)
        for item in outgoing:
            item["input"] = message_id
        return outgoing

    def restore_unsent(self, state: dict[str, Any], items: list[dict[str, Any]]) -> None:
        """Undo the bookkeeping of messages a failed send never delivered; the next refresh sends them."""
        chat_id = state["chat_id"]
        for item in items:
            if item.get('solo_notice_key'):
                self.store.set_meta(item['solo_notice_key'], '')
            if item.get("delivery"):
                self.store.release_delivery(chat_id, str(item["delivery"]))
            if item.get("input"):
                self.store.reopen_input(str(item["input"]))
            if item.get("question_session"):
                self.store.drop_question_session(str(item["question_session"]))
        marks = [item["watch_from"] for item in items if isinstance(item.get("watch_from"), tuple)]
        for resident_id in {mark[0] for mark in marks}:
            self.store.rewind_watch_cursor(chat_id, resident_id, min(mark[1] for mark in marks if mark[0] == resident_id))

    def _reply_messages(self, state: dict[str, Any], resident_id: str, message: dict[str, Any], *,
                        reply_to: int = 0, proactive: bool = False) -> list[dict[str, Any]]:
        """A Portal message as Telegram messages; each Portal message is sent once per chat."""
        message_id = str(message.get("message_id") or "")
        if not message_id or not self.store.claim_delivery(state["chat_id"], message_id):
            return []
        text = message_text(message)
        family = message.get("family_reply") if isinstance(message.get("family_reply"), dict) else {}
        member = str(family.get("title") or "").strip()
        if proactive:
            sender = str(message.get("sender_label") or member or "").strip()
            text = (f"📨 **{sender}**\n" if sender else "📨 ") + text
        elif member:
            text = f"**{member}**\n{text}"
        outgoing: list[dict[str, Any]] = []
        markup = self._attachment_keyboard(state, resident_id, message)
        if text.strip() or markup:
            outgoing.append({"text": text if text.strip() else "（附件）", "reply_markup": markup,
                             "link": {"resident_id": resident_id, "message_id": message_id},
                             "reply_to_telegram_message_id": reply_to})
        questions = question_part(message)
        if questions and str(questions.get("state") or "open") == "open":
            outgoing.extend(self._open_questions(state, resident_id, message, questions, member_owned=bool(family)))
        for item in outgoing:
            item["delivery"] = message_id
        return outgoing

    def _attachment_keyboard(self, state: dict[str, Any], resident_id: str, message: dict[str, Any]) -> dict[str, Any] | None:
        return self.presentation._attachment_keyboard(self, state, resident_id, message)

    def _open_questions(self, state: dict[str, Any], resident_id: str, message: dict[str, Any],
                        part: dict[str, Any], *, member_owned: bool) -> list[dict[str, Any]]:
        questions = [item for item in part.get("questions") or [] if isinstance(item, dict) and item.get("title")]
        answer_id = str(part.get("answer_message_id") or "")
        if not questions or not answer_id:
            return []
        session = self.store.open_question_session(
            chat_id=state["chat_id"], resident_id=resident_id, source_message_id=str(message["message_id"]),
            answer_message_id=answer_id, questions=questions, lendable=not member_owned)
        if not session:
            return []
        return [self._question_prompt(session)]

    def _question_prompt(self, session: dict[str, Any]) -> dict[str, Any]:
        return {**self.presentation._question_prompt(self, session),
                'link': {'resident_id': session['resident_id'], 'message_id': session['source_message_id']}}

    def _answer_question(self, state: dict[str, Any], session: dict[str, Any], step: int, answer: str, *,
                         custom: bool = False) -> dict[str, Any]:
        if session["status"] != "open" or step != int(session["step"]):
            return self._reply(["这组问题已经回答或关闭。"])
        question = session["questions"][step]
        if question.get("credentials") and (custom or (answer == CREDENTIAL_APPROVE and not session["lendable"])):
            return self._reply(["这道权限问题只能选择卡片上的选项。"])
        answers = {**session["answers"], str(step): answer}
        if step + 1 < len(session["questions"]):
            self.store.update_question_session(session["session_id"], answers=answers, step=step + 1)
            prompt = self._question_prompt({**session, "answers": answers, "step": step + 1})
            return self.presentation.update_prompt(session["telegram_message_id"], prompt)
        return self._submit_answers(state, {**session, "answers": answers})

    def _submit_answers(self, state: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
        entries = [{"question_index": question.get("question_index", index), "answer": session["answers"][str(index)]}
                   for index, question in enumerate(session["questions"])]
        lent: list[dict[str, str]] = []
        for index, question in enumerate(session["questions"]):
            if question.get("credentials") and session["answers"][str(index)] == CREDENTIAL_APPROVE:
                lent.extend(item for item in question["credentials"] if item not in lent)
        message = {"message_id": session["answer_message_id"], "role": "user", "content_parts": [
            {"type": "text", "text": "\n".join(entry["answer"] for entry in entries)},
            {"type": "async_question_answer", "source_message_id": session["source_message_id"], "answers": entries},
        ]}
        try:
            self.portal.deliver(state["portal_actor"], session["resident_id"], message, temporary_credentials=lent or None)
        except Exception as exc:  # noqa: BLE001 - the answers stay; the user may retry the last choice
            self.store.update_question_session(session["session_id"], answers={
                key: value for key, value in session["answers"].items() if key != str(len(session["questions"]) - 1)})
            return self._reply([f"回答未能送达：{_failure_text(exc)}"])
        self.store.update_question_session(session["session_id"], answers=session["answers"], status="answered")
        self.store.record_input(state["chat_id"], session["resident_id"], session["answer_message_id"],
                                session["telegram_message_id"], excerpt="（回答问题）")
        summary = "\n".join(f"{index + 1}. {question['title']}：{session['answers'][str(index)]}"
                            for index, question in enumerate(session["questions"]))
        response = self.presentation.update_prompt(session["telegram_message_id"],
                                                    {"text": "✅ 已提交回答\n" + summary, "reply_markup": None})
        if (state['mode'] != 'exact' and self.portal.personal_assistant(state['portal_actor']).get('resident_id')
                == session['resident_id']):
            notices = self._solo_notice(state, session['resident_id'])
            if notices:
                response['messages'] = notices + response.get('messages', [])
        return response

    # -- callbacks ------------------------------------------------------------------------------

    def _button(self, chat_id: str, text: str, payload: dict[str, Any]) -> dict[str, str]:
        return self.presentation._button(self, chat_id, text, payload)

    def dispatch_callback(self, *, chat_id: str, telegram_user_id: str, data: str, portal_actor: str,
                          chat_type: str = "private", telegram_message_id: int = 0) -> dict[str, Any]:
        state = self.store.upsert_chat_state(chat_id, chat_type=chat_type, telegram_user_id=telegram_user_id,
                                             portal_actor=portal_actor)
        kind, _, token = str(data or "").partition(":")
        if kind == "att":
            return self._attachment(state, token)
        payload = self.store.get_callback_payload(chat_id, token) if kind == "cb" else None
        if not payload:
            return self._reply(["这个按钮已失效。"])
        action = payload.get("kind")
        if action in {"answer", "question_back", "question_close"}:
            session = self.store.question_session(str(payload.get("session") or ""), chat_id)
            if not session:
                return self._reply(["这组问题已失效。"])
            if telegram_message_id and session["telegram_message_id"] != telegram_message_id:
                self.store.update_question_session(session["session_id"], telegram_message_id=telegram_message_id)
                session["telegram_message_id"] = telegram_message_id
            if action == "answer":
                return self._answer_question(state, session, int(payload.get("step") or 0), str(payload.get("answer") or ""))
            if session["status"] != "open":
                return self._reply(["这组问题已经回答或关闭。"])
            if action == "question_back":
                step = max(0, int(payload.get("step") or 0) - 1)
                self.store.update_question_session(session["session_id"], step=step)
                return self.presentation.update_prompt(session["telegram_message_id"], self._question_prompt({**session, "step": step}))
            try:
                self.portal.close_questions(portal_actor, session["resident_id"], session["source_message_id"])
            except Exception as exc:  # noqa: BLE001
                return self._reply([f"关闭失败：{_failure_text(exc)}"])
            self.store.update_question_session(session["session_id"], status="closed")
            return self.presentation.update_prompt(session["telegram_message_id"], {"text": "这组问题已关闭，不再回答。", "reply_markup": None})
        if action == "expand":
            return self._expand(state, str(payload.get("resident_id") or ""), str(payload.get("input_id") or ""))
        if action == "history_more":
            return self._history(state, int(payload.get("rounds") or HISTORY_DEFAULT_ROUNDS),
                                 shown=int(payload.get("shown") or 0), resident_id=str(payload.get("resident_id") or ""))
        if action in {"queue_delete", "queue_top", "queue_inject"}:
            return self._queue_action(state, action, str(payload.get("resident_id") or ""), str(payload.get("message_id") or ""))
        return self._reply(["这个按钮已失效。"])

    def _attachment(self, state: dict[str, Any], token: str) -> dict[str, Any]:
        delivery = self.store.get_attachment_delivery(chat_id=state["chat_id"], telegram_user_id=state["telegram_user_id"],
                                                      portal_actor=state["portal_actor"], token=token)
        if not delivery:
            return self._reply(["这个附件按钮无效，或不属于当前联系人。"])
        status = delivery["status"]
        if status in {"expired", "sent", "sending"}:
            return self._reply([{"expired": "这个附件按钮已过期。", "sent": "这个文件已经发送过。",
                                 "sending": "文件正在发送，请稍候。"}[status]])
        attachment = delivery["attachment"]
        try:
            loaded = self.portal.read_attachment(state["portal_actor"], attachment)
        except Exception as exc:  # noqa: BLE001
            error = _failure_text(exc)
            self.store.mark_attachment_failed(token, error)
            self._audit(state, "attachment_delivery_failed", {"token": token, "error": error})
            return self._reply([f"文件暂时无法发送：{error}"])
        return {"messages": [], "attachment_delivery": {**loaded, "token": token}}

    # -- history ---------------------------------------------------------------------------------

    def _history(self, state: dict[str, Any], rounds: int, *, shown: int, resident_id: str = "") -> dict[str, Any]:
        actor, chat_id = state["portal_actor"], state["chat_id"]
        if not resident_id:
            resident_id, _title, error = self._target(state)
            if error:
                return self._reply([error])
        limit = min(1000, max(60, (shown + rounds) * 40))
        try:
            snapshot = self.portal.conversation(actor, resident_id, history_limit=limit)
        except Exception as exc:  # noqa: BLE001
            return self._reply([f"无法读取对话：{_failure_text(exc)}"])
        all_rounds = conversation_rounds(snapshot.get("messages") or [])
        end = len(all_rounds) - shown
        selected = all_rounds[max(0, end - rounds):max(0, end)]
        if not selected:
            return self._reply(["没有更早的对话。" if shown else "当前目标还没有对话。"])
        telegram_ids = self.store.telegram_input_ids(chat_id, [item["input"]["message_id"] for item in selected])
        outgoing = []
        for item in selected:
            request = item["input"]
            reply = item["reply"]
            source = self.presentation.title if request["message_id"] in telegram_ids else "网页"
            asked, asked_cut = _clip(message_text(request), HISTORY_INPUT_CHARS)
            answer, answer_cut = _clip(message_text(reply) if reply else "", HISTORY_REPLY_CHARS)
            family = (reply or {}).get("family_reply") if isinstance((reply or {}).get("family_reply"), dict) else {}
            responder = str(family.get("title") or "").strip() or "回复"
            quote = request.get("quote") if isinstance(request.get("quote"), dict) else {}
            lines = [f"🕘 {str(request.get('created_at') or '')[:16].replace('T', ' ')} · {source}"]
            if quote.get("text"):
                lines.append("> " + _clip(str(quote["text"]), 80)[0].replace("\n", " "))
            lines.append(f"**你**：{asked or '（无文字）'}")
            lines.append(f"**{responder}**：{answer}" if reply else "（还没有回复）")
            markup = None
            if asked_cut or answer_cut:
                markup = {"inline_keyboard": [[self._button(chat_id, "展开", {
                    "kind": "expand", "resident_id": resident_id, "input_id": request["message_id"]})]]}
            link_id = str((reply or request).get("message_id") or "")
            outgoing.append({"text": "\n".join(lines), "reply_markup": markup,
                             "link": {"resident_id": resident_id, "message_id": link_id}})
        if end - rounds > 0 or snapshot.get("next_cursor"):
            more = self._button(chat_id, "更早", {"kind": "history_more", "resident_id": resident_id,
                                                  "rounds": rounds, "shown": shown + len(selected)})
            last = outgoing[-1]
            rows = (last.get("reply_markup") or {}).get("inline_keyboard") or []
            last["reply_markup"] = {"inline_keyboard": [*rows, [more]]}
        return {"messages": outgoing}

    def _expand(self, state: dict[str, Any], resident_id: str, input_id: str) -> dict[str, Any]:
        try:
            messages = self.portal.conversation(state["portal_actor"], resident_id, input_message_id=input_id).get("messages") or []
        except Exception as exc:  # noqa: BLE001
            return self._reply([f"无法读取这一轮：{_failure_text(exc)}"])
        rounds = [item for item in conversation_rounds(messages) if item["input"]["message_id"] == input_id]
        if not rounds:
            return self._reply(["这一轮已不可用。"])
        request, reply = rounds[0]["input"], rounds[0]["reply"]
        outgoing = [{"text": "**你**：" + (message_text(request) or "（无文字）"),
                     "link": {"resident_id": resident_id, "message_id": input_id}}]
        if reply:
            family = reply.get("family_reply") if isinstance(reply.get("family_reply"), dict) else {}
            responder = str(family.get("title") or "").strip() or "回复"
            outgoing.append({"text": f"**{responder}**：" + message_text(reply),
                             "reply_markup": self._attachment_keyboard(state, resident_id, reply),
                             "link": {"resident_id": resident_id, "message_id": str(reply.get("message_id") or "")}})
        return {"messages": outgoing}

    # -- queue and stop ------------------------------------------------------------------------------

    def _queue(self, state: dict[str, Any]) -> dict[str, Any]:
        actor, chat_id = state["portal_actor"], state["chat_id"]
        if state["mode"] == "exact" and state["exact_resident_id"]:
            resident_id = state["exact_resident_id"]
            try:
                current = self.portal.resident_state(actor, resident_id)
                pending = self.portal.mailbox(actor, resident_id)
            except Exception as exc:  # noqa: BLE001
                return self._reply([f"无法读取队列：{_failure_text(exc)}"])
            lines = [f"目标状态：{current.get('work_state') or 'idle'}；排队 {len(pending)} 条。"]
            outgoing = [{"text": lines[0]}]
            for item in pending[:10]:
                message_id = str(item.get("message_id") or "")
                preview, _ = _clip(str(item.get("preview") or ""), 120)
                outgoing.append({"text": f"• {preview or message_id}", "reply_markup": {"inline_keyboard": [[
                    self._button(chat_id, "删除", {"kind": "queue_delete", "resident_id": resident_id, "message_id": message_id}),
                    self._button(chat_id, "置顶", {"kind": "queue_top", "resident_id": resident_id, "message_id": message_id}),
                    self._button(chat_id, "插入当前", {"kind": "queue_inject", "resident_id": resident_id, "message_id": message_id}),
                ]]}})
            return {"messages": outgoing}
        entries = self.store.open_inputs(chat_id)
        if not entries:
            return self._reply(["本聊天没有未完成的消息。"])
        lines = ["本聊天未完成的消息："]
        for entry in entries:
            try:
                status = str(self.portal.input_state(actor, entry["resident_id"], entry["message_id"]).get("state") or "")
            except Exception as exc:  # noqa: BLE001
                status = _failure_text(exc)
            lines.append(f"• {entry['excerpt'] or entry['message_id']} — {status}")
        return self._reply(["\n".join(lines)])

    def _queue_action(self, state: dict[str, Any], action: str, resident_id: str, message_id: str) -> dict[str, Any]:
        actor = state["portal_actor"]
        try:
            if action == "queue_delete":
                self.portal.cancel_input(actor, resident_id, message_id)
                return self._reply(["已从队列删除。"])
            if action == "queue_top":
                self.portal.prioritize(actor, resident_id, message_id)
                return self._reply(["已置顶。"])
            self.portal.inject(actor, resident_id, message_id)
            return self._reply(["已插入当前工作。"])
        except Exception as exc:  # noqa: BLE001
            return self._reply([f"操作失败：{_failure_text(exc)}"])

    def _stop(self, state: dict[str, Any], *, current_only: bool) -> dict[str, Any]:
        actor, chat_id = state["portal_actor"], state["chat_id"]
        if state["mode"] == "exact" and state["exact_resident_id"]:
            resident_id = state["exact_resident_id"]
            try:
                active = str(self.portal.resident_state(actor, resident_id).get("active_message_id") or "")
                targets = [active] if active else []
                if not current_only:
                    targets += [str(item.get("message_id") or "") for item in self.portal.mailbox(actor, resident_id)]
                for message_id in dict.fromkeys(item for item in targets if item):
                    self.portal.cancel_input(actor, resident_id, message_id)
            except Exception as exc:  # noqa: BLE001
                return self._reply([f"停止失败：{_failure_text(exc)}"])
            if not targets:
                return self._reply(["目标当前没有任务。"])
            return self._reply(["已停止当前任务。" if current_only else f"已停止当前任务并清空队列（{len(targets)} 条）。"])
        if current_only:
            return self._reply(["`/stop current` 只用于精准路由；个人助手模式下 `/stop` 停止本聊天未完成的消息。"])
        stopped, failures = 0, []
        for entry in self.store.open_inputs(chat_id):
            try:
                self.portal.cancel_input(actor, entry["resident_id"], entry["message_id"])
                stopped += 1
            except Exception as exc:  # noqa: BLE001
                failures.append(_failure_text(exc))
        if not stopped and not failures:
            return self._reply(["本聊天没有未完成的消息。"])
        lines = [f"已请求停止 {stopped} 条消息。"]
        if failures:
            lines.append("未能停止：" + "；".join(failures[:3]))
        return self._reply(lines)

    # -- helpers ----------------------------------------------------------------------------------------

    def _audit(self, state: dict[str, Any], event: str, payload: dict[str, Any]) -> None:
        self.store.append_audit(event, chat_id=state["chat_id"], telegram_user_id=state.get("telegram_user_id", ""),
                                portal_actor=state.get("portal_actor", ""), payload=payload)

    @staticmethod
    def _reply(lines: list[str]) -> dict[str, Any]:
        return {"messages": [{"text": line} for line in lines if line]}


def conversation_rounds(messages: list[Any]) -> list[dict[str, Any]]:
    """Each user request with its last text reply, oldest first."""
    rounds: list[dict[str, Any]] = []
    by_request: dict[str, dict[str, Any]] = {}
    for message in messages:
        if not isinstance(message, dict):
            continue
        message_id = str(message.get("message_id") or "")
        if message.get("role") == "user" and message_id:
            entry = {"input": message, "reply": None}
            rounds.append(entry)
            by_request[message_id] = entry
            continue
        if message.get("role") != "agent" or not message_text(message):
            continue
        request_id = str(message.get("display_reply_to_message_id") or message.get("reply_to_message_id") or "")
        entry = by_request.get(request_id)
        if entry is not None:
            entry["reply"] = message
    return rounds
