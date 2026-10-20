"""Versioned v2 page packages; validation is shared by every save/publish path."""
from __future__ import annotations

import json
import re

from management_common.format_tags import schema_matches

from .content import ContentTemplateError, validate_content


def validate_application(application):
    """Validate the authored source reference, never a caller-supplied build receipt."""
    if not isinstance(application, dict) or set(application) - {"repo_id", "directory", "commit", "entries", "max_bytes"}:
        raise ContentTemplateError("应用包需要仓库、目录、提交、入口及可选体积上限")
    if not re.fullmatch(r"repo_[0-9a-f]{12}", str(application.get("repo_id", ""))):
        raise ContentTemplateError("应用包需要已登记的仓库 Resource ID")
    directory = application.get("directory")
    if not isinstance(directory, str) or not (directory == "." or re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_.-]+)*", directory)) or ".." in directory.split("/"):
        raise ContentTemplateError("应用包目录必须是仓库内相对目录")
    if not re.fullmatch(r"[0-9a-f]{40}", str(application.get("commit", ""))):
        raise ContentTemplateError("应用包提交必须是完整 Git commit SHA")
    entries = application.get("entries")
    if not isinstance(entries, dict) or not 1 <= len(entries) <= 32:
        raise ContentTemplateError("应用包需要 1–32 个构建入口")
    for name, path in entries.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name) or not isinstance(path, str) or not re.fullmatch(r"(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+\.html", path):
            raise ContentTemplateError("应用包入口名称或产物相对 HTML 路径无效")
    limit = application.get("max_bytes", 8 * 1024 * 1024)
    if type(limit) is not int or not 1 <= limit <= 64 * 1024 * 1024:
        raise ContentTemplateError("应用包体积上限须为 1–64 MB 范围内的字节数")


def validate_page(manifest, panels, bindings, application=None):
    def require(condition, message):
        if not condition:
            raise ContentTemplateError(message)

    require(isinstance(manifest, dict), "页面清单必须是对象")
    require(schema_matches(manifest.get("schema"), expected="tfi.projection.v2"), "不支持的投影页面清单版本")
    require(not set(manifest) - {"schema", "sidebar", "chat", "context"}, "页面清单包含未知字段")
    require(isinstance(panels, dict), "面板源码必须按 source 路径保存")
    sidebar = manifest.get("sidebar")
    require(isinstance(sidebar, list) and 1 <= len(sidebar) <= 32, "页面必须包含 1–32 个功能区")
    ids, sources, package_entries = set(), set(), set()
    for item in sidebar:
        require(isinstance(item, dict), "功能区必须是对象")
        require(not set(item) - {"id", "title", "icon", "pattern", "source", "chat"}, "功能区包含未知字段")
        identity = item.get("id")
        require(isinstance(identity, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", identity), "功能区 ID 格式无效")
        require(identity not in ids and identity != "facade" and not identity.startswith("chat-"), "功能区 ID 重复或占用对话保留名称")
        ids.add(identity)
        require(isinstance(item.get("title"), str) and 0 < len(item["title"].strip()) <= 120, "功能区需要名称")
        for key in ("icon", "pattern"):
            require(key not in item or isinstance(item[key], str) and len(item[key]) <= 120, "功能区图标或范式无效")
        path = item.get("source")
        if isinstance(path, str) and path.startswith("package:"):
            entry = path.removeprefix("package:").strip()
            require(application is not None and entry in application["entries"], "功能区缺少已登记的应用包入口")
            package_entries.add(entry)
            continue
        require(isinstance(path, str) and re.fullmatch(r"(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.html", path), "面板 source 必须是相对 HTML 路径")
        require(path in panels and isinstance(panels[path], str) and panels[path].strip(), "功能区缺少非空源码")
        sources.add(path)
    require(set(panels) == sources, "面板源码必须与清单 source 一一对应")
    require(application is None or bool(package_entries), "应用包必须被至少一个功能区使用")
    require(len(json.dumps([manifest, panels], ensure_ascii=False).encode()) <= 400_000, "页面清单与面板源码合计须小于 400 KB")
    for source in panels.values():
        validate_content(source)
    for chat in [manifest.get("chat"), *(item.get("chat") for item in sidebar)]:
        if chat is None:
            continue
        require(isinstance(chat, dict) and set(chat) == {"binding", "role"}, "对话区需要 binding 和 facade 角色")
        require(chat.get("role") == "facade" and isinstance(chat.get("binding"), str), "对话区必须使用 facade 角色")
        require(bindings.get(chat["binding"], {}).get("residents", {}).get("facade"), "对话区 facade 未绑定")
    context = manifest.get("context", [])
    require(isinstance(context, list) and len(context) <= 32, "情境通道格式无效")
    channels = set()
    for item in context:
        require(isinstance(item, dict) and set(item) == {"channel", "publishers", "subscribers"}, "情境通道需要 channel、publishers、subscribers")
        channel = item["channel"]
        require(isinstance(channel, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", channel) and channel not in channels, "情境通道名称无效或重复")
        channels.add(channel)
        for key in ("publishers", "subscribers"):
            names = item[key]
            require(isinstance(names, list) and all(isinstance(name, str) and name in ids for name in names), "情境通道引用了不存在的功能区")
            require(len(names) == len(set(names)), "情境通道成员不能重复")
