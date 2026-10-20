"""The slash commands of a Chat session: its menu, Portal's own commands and their replies.

A session offers what its Provider declares (``AgentProvider.native_commands``)
and Portal's private commands; the Provider's session answers its own
(``NativeOptions.handle_native_command``), Portal answers the rest here.
"""
from __future__ import annotations
from management_common.portal_paths import path as portal_path

from copy import deepcopy
from typing import Any
from .provider_catalog import AGENT_PROVIDERS
from .resident_runtime_settings import normalize_settings


PORTAL_PRIVATE_COMMANDS = (
    "portal-help",
    "portal-tokens",
)


SLASH_COMMAND_DESCRIPTIONS = {
    "status": ("Status", "查看当前会话配置、session 与 token 摘要。"),
    "goal": ("Goal", "查看或更新当前会话的原生 goal。"),
    "plan": ("Plan", "切换 Plan mode。"),
    "model": ("Model", "查看或切换当前会话模型。"),
    "fast": ("Fast", "切换 fast service tier。"),
    "permissions": ("Permissions", "查看或切换当前会话权限预设。"),
    "compact": ("Compact", "压缩当前会话上下文。"),
    "portal-help": ("Portal Help", "查看 Portal 聊天输入与命令说明。"),
    "portal-tokens": ("Portal Tokens", "查看当前会话 token 使用统计。"),
}


SLASH_PATH_ROOTS = (
    str(portal_path("share_root")),
    "/home",
    "/srv",
    "/tmp",
    "/var",
    "/etc",
    "/mnt",
    "/nas_ro",
    "/staging",
    "/workspace",
)


def slash_command_catalog(*, backend: str, include_portal: bool = True) -> list[dict[str, str]]:
    """The commands a session of ``backend`` answers: what its Provider declares, then Portal's own."""

    definition = AGENT_PROVIDERS.get(str(backend or "").strip())
    commands = list(definition.native_commands() if definition else ())
    if include_portal:
        commands.extend(PORTAL_PRIVATE_COMMANDS)
    catalog: list[dict[str, str]] = []
    seen: set[str] = set()
    for command in commands:
        if command in seen:
            continue
        seen.add(command)
        title, description = SLASH_COMMAND_DESCRIPTIONS.get(command, (command, ""))
        catalog.append({
            "command": f"/{command}",
            "title": title,
            "description": description,
        })
    return catalog


def handle_slash_command(
    content: str,
    settings: object,
    *,
    scope_label: str = "session",
    token_usage: object = None,
    backend: str = "",
) -> dict[str, Any]:
    """Portal's own commands; the Provider's session answered the commands it declares before this."""

    text = str(content or "").strip()
    if not text.startswith("/"):
        return {"handled": False}
    if _slash_text_looks_like_filesystem_path(text):
        return {"handled": False}
    command, args = _split_command(text)
    current = normalize_settings(settings)
    if command == "portal-help":
        return _result(current, _portal_help_content(scope_label, backend), title="Portal slash commands")
    if command == "portal-tokens":
        content = _token_usage_content(token_usage)
        return _result(
            current,
            content if content else f"当前会话：`{scope_label}`\n\n当前没有可用 token 统计。",
            title="Portal token usage",
        )
    available = "、".join(f"`{item['command']}`" for item in slash_command_catalog(backend=backend))
    return _result(current, f"未知 slash command：`/{command}`。\n\n当前会话支持 {available}。", title="Unknown slash command")


def _result(settings: dict[str, Any], content: str, *, title: str, updates: dict[str, Any] | None = None, command_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "handled": True,
        "title": title,
        "content": content,
        "settings": deepcopy(settings),
        "updates": deepcopy(updates) if updates else {},
        "command_payload": deepcopy(command_payload) if command_payload else {},
    }


def _portal_help_content(scope_label: str, backend: str = "") -> str:
    """The help of a session of ``backend``: the commands it answers (``slash_command_catalog``) and the keys."""

    definition = AGENT_PROVIDERS.get(str(backend or "").strip())
    sections = []
    native = [f"- `/{name}`：{SLASH_COMMAND_DESCRIPTIONS[name][1]}" for name in (definition.native_commands() if definition else ())]
    if native:
        # The list is this session's Provider's; the heading names no Provider.
        sections.append("当前会话命令：\n" + "\n".join(native))
    sections.append("Portal 私有命令：\n" + "\n".join(
        f"- `/{name}`：{SLASH_COMMAND_DESCRIPTIONS[name][1]}" for name in PORTAL_PRIVATE_COMMANDS))
    return (
        f"当前会话：`{scope_label}`\n\n"
        + "\n\n".join(sections) + "\n\n"
        "通用快捷键：\n"
        "- `Ctrl/⌘ + K`：打开全局搜索。\n"
        "- `Ctrl` + 反引号键（Esc 下方、数字 1 左边的键；macOS 上也按 Control 键）：回到上一个 Portal 页面或上一个 Chat Workspace。"
        "`Ctrl/⌘ + Tab` 和 `⌘` + 反引号键会被浏览器或系统用来切换标签页、窗口，网页收不到。\n\n"
        "Chat 快捷键：\n"
        "- `Ctrl/⌘ + Enter`：发送当前输入框内容。\n"
        "- 输入框在第一行按 `↑`、在最后一行按 `↓`：切换你在当前会话发过的输入（多人共用的会话不含其他人发的）。\n"
        "- `Ctrl/⌘ + Z`：撤销输入框里的上一步修改，包括粘贴的文本和插入的图片、文件、引用。\n"
        "- 输入 `/` 打开命令菜单，在 Chatroom 输入 `@` 打开提及列表：`↑`/`↓` 选择，`Enter` 或 `Tab` 填入，`Esc` 关闭。\n"
        "- 提问卡片：按数字 `1`–`9` 选择对应选项；在回答输入框中按 `Enter` 确认。\n"
        "- 在输入框中点击右键：粘贴剪贴板内容。\n"
        "- 右键一条消息：在消息操作菜单中选择引用回复，或选择 Forward 后多选消息并转发（按当前会话提供的功能）。\n"
        "- 在页面右侧的“我的 Mailbox”中，右键一条对话，可以置顶或取消置顶；只用键盘时，按 `Tab` 选中这条对话，再按 `Shift + F10` 打开同一个菜单。\n"
        "- Resident 详情的 Mailbox 标签：按 `Tab` 选中一条等待中的消息，再按 `Alt + ↑`/`Alt + ↓` 调整处理顺序。\n\n"
        "Lineage 画布快捷键（先点击一下画布空白处）：\n"
        "- `+` 或 `=` 放大，`-` 缩小，`0` 恢复 100%，`F` 显示全部节点。\n"
        "- `Shift`、`Ctrl` 或 `⌘` + 点击节点：加入或移出多选；按住 `Shift` 在空白处拖动：框选多个节点。\n"
        "\n"
        "第一次使用 Portal：阅读 [新用户指引](/guide)（人类与 agent 各一份）。"
    )


def _token_usage_content(token_usage: object) -> str:
    if not isinstance(token_usage, dict):
        return ""
    summary = token_usage.get("summary") if isinstance(token_usage.get("summary"), dict) else token_usage
    count = _nonnegative_int(summary.get("entry_count"))
    total = summary.get("total_turn_usage") if isinstance(summary.get("total_turn_usage"), dict) else {}
    last = summary.get("last_turn_usage") if isinstance(summary.get("last_turn_usage"), dict) else {}
    if not last and isinstance(summary.get("last_entry"), dict):
        last = summary["last_entry"].get("turn_usage") if isinstance(summary["last_entry"].get("turn_usage"), dict) else {}
    total_tokens = _nonnegative_int(total.get("total_tokens"))
    last_tokens = _nonnegative_int(last.get("total_tokens"))
    if not any((count, total_tokens, last_tokens)):
        return ""
    lines = ["当前 token 统计：", ""]
    if total_tokens:
        lines.append(f"- Total: `{total_tokens}`")
    if last_tokens:
        lines.append(f"- Last turn: `{last_tokens}`")
    if count:
        lines.append(f"- Turns: `{count}`")
    return "\n".join(lines)


def _nonnegative_int(value: object) -> int:
    try:
        n = int(value)
    except Exception:
        return 0
    return n if n > 0 else 0


def _split_command(text: str) -> tuple[str, list[str]]:
    parts = text.strip().split()
    command = parts[0].lstrip("/").strip().lower()
    return command, parts[1:]


def parse_goal_command(content: object) -> dict[str, Any]:
    """Parse the public /goal grammar without interpreting objectives."""

    text = str(content or "").strip()
    parts = text.split(maxsplit=1)
    if not parts or parts[0].casefold() != "/goal":
        return {}
    remainder = parts[1].strip() if len(parts) > 1 else ""
    if not remainder:
        return {"action": "get"}
    command, separator, rest = remainder.partition(" ")
    action = command.casefold()
    if action in {"pause", "resume", "clear"} and not rest.strip():
        return {"action": action}
    return {"action": "set", "objective": remainder}


def _slash_text_looks_like_filesystem_path(text: str) -> bool:
    first = str(text or "").strip().split(maxsplit=1)[0] if str(text or "").strip() else ""
    if not first.startswith("/"):
        return False
    lower = first.lower().rstrip("/")
    if any(lower == root or lower.startswith(f"{root}/") for root in SLASH_PATH_ROOTS):
        return True
    return "/" in first[1:]
