"""Telegram presentation of shared routing actions and question sessions."""
from __future__ import annotations
from typing import Any
from .channel import ChannelCapabilities

CREDENTIAL_DECLINE = "不交出"

HELP_TEXT = "\n".join([
    "**Telepus 使用说明**",
    "",
    "**发消息**",
    "• 直接发文字或图片，就交给你在 Portal 的个人助手；助手安排合适的成员处理，回复第一行是承接成员的名字。",
    "• 想直接找某个 Resident：发送它的 ID（如 `resident_0123456789ab`）或 `/route <ID>`，之后的消息都发给它；`/home` 回到个人助手。",
    "• 收到“已收到。”表示消息已进入 Portal，回复完成后自动推送过来。",
    "",
    "**引用**",
    "在 Telegram 里长按一条回复选“回复”，再写内容，就等于在网页上引用它：个人助手把这条消息直接交给写那条回复的成员。",
    "",
    "**问答卡片**",
    "成员提问时会出现“❓ 问题 1/N”：点按钮选答案，或“回复”提问消息写自己的答案；可以“« 上一题”，也可以“不再回答”。答完自动提交。",
    "需要你交出权限的问题，在个人助手里只能选“不交出”；要授权请用 `/route` 直接联系那个成员，或到网页上作答。",
    "",
    "**网页上的对话**",
    "你在网页上和助手的往来不会推送到手机。需要时发 `/history` 查看最近几轮，点“展开”看全文、“更早”往前翻；回复其中一条即可在手机上接着聊。",
    "",
    "**命令**",
    "/help 本说明",
    "/home 回到个人助手",
    "/route <resident_id> 直接联系一个 Resident",
    "/where 查看当前目标",
    "/history [n] 查看最近 n 轮对话（默认 3，最多 10），含网页端",
    "/queue 查看未完成的消息",
    "/stop 停止未完成的消息；精准路由时 `/stop current` 只停当前任务",
    "/now <内容> 精准路由时插入目标正在进行的工作",
    "/refresh 立即拉取回复",
    "",
    "回复中的文件会显示下载按钮，点按后发送到这里。",
])


# The command menu Telegram shows after typing "/" (setMyCommands); /help explains the rest.
BOT_COMMANDS = (
    ("help", "使用说明"),
    ("home", "回到个人助手"),
    ("route", "按 Resident ID 直接联系"),
    ("where", "查看当前目标"),
    ("history", "查看最近几轮对话（含网页端）"),
    ("queue", "查看未完成的消息"),
    ("stop", "停止未完成的消息"),
    ("now", "精准路由时插入当前工作"),
    ("refresh", "立即拉取回复"),
)


class TelegramPresentation:
    capabilities = ChannelCapabilities(buttons=True, edit_messages=True, unlimited_sends=True, command_menu=True, text_limit=3000)
    title = "Telegram"
    receipts = True
    help_text = HELP_TEXT

    def incoming(self, core, state, message):
        return None

    def update_prompt(self, message_id, prompt):
        if self.capabilities.edit_messages:
            return {"messages": [], "edit": {"telegram_message_id": message_id, **prompt}}
        return {"messages": [prompt]}

    def _attachment_keyboard(self, core, state: dict[str, Any], resident_id: str, message: dict[str, Any]) -> dict[str, Any] | None:
        try:
            files = core.portal.attachments(state["portal_actor"], resident_id, message)
        except Exception:  # noqa: BLE001 - the text still goes out
            return None
        rows = []
        for item in files:
            token = core.store.create_attachment_delivery(
                chat_id=state["chat_id"], telegram_user_id=state["telegram_user_id"],
                portal_actor=state["portal_actor"], source_message_id=str(message.get("message_id") or ""),
                attachment=item)
            rows.append([{"text": f"⬇ {item.get('filename') or 'attachment'}"[:64], "callback_data": f"att:{token}"}])
        return {"inline_keyboard": rows} if rows else None


    def _question_prompt(self, core, session: dict[str, Any]) -> dict[str, Any]:
        step = int(session["step"])
        question = session["questions"][step]
        total = len(session["questions"])
        lines = [f"❓ **问题 {step + 1}/{total}**：{question['title']}"]
        options = [str(item) for item in question.get("options") or [] if str(item).strip()]
        credentials = question.get("credentials") or []
        if credentials:
            lines.append("请求交出的权限：" + "、".join(f"`{item.get('a')} · {item.get('resource_id')}`" for item in credentials))
            lines.append("凭证只在对方处理这条回答期间有效。")
            if not session["lendable"]:
                options = [CREDENTIAL_DECLINE]
                lines.append("门面中成员的权限请求不能在这里同意，需要时请在 Portal 网页中处理。")
        else:
            lines.append("点选项作答，或“回复”这条消息写下其他回答。" if options else "请“回复”这条消息写下回答。")
        rows = [[core._button(session["chat_id"], option[:60], {"kind": "answer", "session": session["session_id"],
                                                                 "step": step, "answer": option})]
                for option in options]
        controls = []
        if step:
            controls.append(core._button(session["chat_id"], "« 上一题", {"kind": "question_back", "session": session["session_id"], "step": step}))
        controls.append(core._button(session["chat_id"], "不再回答", {"kind": "question_close", "session": session["session_id"]}))
        rows.append(controls)
        return {"text": "\n".join(lines), "reply_markup": {"inline_keyboard": rows},
                "question_session": session["session_id"]}

    def _button(self, core, chat_id: str, text: str, payload: dict[str, Any]) -> dict[str, str]:
        return {"text": text, "callback_data": "cb:" + core.store.create_callback_token(chat_id, payload)}

