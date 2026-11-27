"""Public ResourceService request schemas owned by Workbench."""

from management_portal.frontend_manifest import builtins


ALIAS = r"^[a-z][a-z0-9_-]{0,63}$"
SIDEBAR_ID = {"type": "string", "pattern": ALIAS, "maxLength": 64}
TRIMMED_TITLE = r"^\s*\S(?:[\s\S]{0,118}\S)?\s*$"


def _text(description, **constraints):
    return {"type": "string", "description": description, **constraints}


CHAT = {
    "description": "可选的门面对话配置。给出的绑定及门面由 Workbench 复核。",
    "type": ["object", "null"],
    "required": ["binding", "role"],
    "additionalProperties": False,
    "properties": {
        "binding": _text("对话所属的 Lineage 绑定别名。"),
        "role": {"description": "对话使用门面角色。", "const": "facade"},
    },
}

MANIFEST = {
    "description": "v2 页面清单。null 表示无清单。面板源码、绑定、应用包和清单的对应关系由 Workbench 核验。",
    "type": ["object", "null"],
    "required": ["schema", "sidebar"],
    "additionalProperties": False,
    "properties": {
        "schema": {"description": "完整清单格式标签。", "const": "tfi.projection.v2"},
        "sidebar": {
            "description": "按展示顺序列出的 1–32 个功能区。功能区 ID 不重复。",
            "type": "array", "minItems": 1, "maxItems": 32,
            "items": {
                "type": "object", "required": ["id", "title", "source"], "additionalProperties": False,
                "properties": {
                    "id": {**SIDEBAR_ID, "description": "功能区的唯一 ID，facade 与 chat- 前缀保留。",
                           "not": {"pattern": r"^(?:facade$|chat-)"}},
                    "title": _text("功能区名称，去掉首尾空白后 1–120 字。", pattern=TRIMMED_TITLE),
                    "icon": _text("可选图标。", maxLength=120),
                    "pattern": _text("可选范式。", maxLength=120),
                    "source": _text("面板的相对 HTML 路径，或 package: 后跟应用包入口名称。",
                                   pattern=r"^(?:(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.html|package:\s*[a-z][a-z0-9_-]{0,63}\s*)$"),
                    "chat": CHAT,
                },
            },
        },
        "chat": CHAT,
        "context": {
            "description": "最多 32 个情境通道，发布者及订阅者引用清单中的功能区。",
            "type": "array", "maxItems": 32,
            "items": {
                "type": "object", "required": ["channel", "publishers", "subscribers"], "additionalProperties": False,
                "properties": {
                    "channel": _text("唯一通道名。", pattern=ALIAS),
                    "publishers": {"description": "发布者功能区 ID。", "type": "array", "items": SIDEBAR_ID,
                                   "uniqueItems": True},
                    "subscribers": {"description": "订阅者功能区 ID。", "type": "array", "items": SIDEBAR_ID,
                                    "uniqueItems": True},
                },
            },
        },
        "navigation": {"description": "v2 清单使用分页导航。", "const": {"mode": "pages"}},
        "external_links": {
            "description": "最多 32 个外部应用入口，键为入口别名。",
            "type": "object", "maxProperties": 32,
            "propertyNames": {"pattern": ALIAS},
            "additionalProperties": {
                "type": "object", "required": ["title", "url"], "additionalProperties": False,
                "properties": {
                    "title": _text("入口名称，去掉首尾空白后 1–120 字。", pattern=TRIMMED_TITLE),
                    "url": _text("不含登录凭据的 HTTP(S) 完整地址。主机、端口与凭据由 Workbench 核验。",
                                 maxLength=4096, pattern=r"^[Hh][Tt][Tt][Pp][Ss]?://[^\x00-\x20\x7f\\]+$"),
                },
            },
        },
    },
}

APPLICATION = {
    "description": "固定 Git 提交中的应用包源码，null 表示无应用包。构建回执由宿主生成。",
    "type": ["object", "null"],
    "required": ["repo_id", "directory", "commit", "entries"],
    "additionalProperties": False,
    "properties": {
        "repo_id": _text("已登记的仓库 Resource ID。", pattern=r"^repo_[0-9a-f]{12}$"),
        "directory": _text("应用包在仓库内的相对目录，点号表示仓库根。",
                           pattern=r"^(?:\.|[A-Za-z0-9_-]+(?:/[A-Za-z0-9_.-]+)*)$",
                           **{"not": {"pattern": r"(?:^|/)\.\.(?:/|$)"}}),
        "commit": _text("完整的 40 位小写 Git commit SHA。", pattern=r"^[0-9a-f]{40}$", minLength=40, maxLength=40),
        "entries": {
            "description": "1–32 个具名构建入口，值为产物相对 HTML 路径。",
            "type": "object", "minProperties": 1, "maxProperties": 32,
            "propertyNames": {"pattern": ALIAS},
            "additionalProperties": {"type": "string", "pattern": r"^(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+\.html$"},
        },
        "max_bytes": {"description": "构建产物字节上限。使用 JSON 整数，缺省 8 MiB。",
                      "type": "integer", "minimum": 1, "maximum": 64 * 1024 * 1024, "default": 8 * 1024 * 1024},
    },
}

BINDINGS = {
    "description": "最多 256 个具名 Lineage 绑定。Resident 和事件源须属于各自绑定的 Lineage。",
    "type": "object", "maxProperties": 256,
    "propertyNames": {"pattern": ALIAS},
    "additionalProperties": {
        "type": "object", "required": ["lineage_id"],
        "additionalProperties": False,
        "properties": {
            "lineage_id": _text("正式 Lineage ID。", pattern=r"^lineage_[0-9a-f]{12}$"),
            "residents": {
                "description": "最多 200 个居民角色绑定，键为角色名称。",
                "type": "object", "maxProperties": 200,
                "propertyNames": {"minLength": 1, "maxLength": 240},
                "additionalProperties": {"type": "string", "pattern": r"^resident_[0-9a-f]{12}$"},
            },
            "events": {
                "description": "最多 200 个流程入口绑定，键为入口别名。",
                "type": "object", "maxProperties": 200,
                "propertyNames": {"pattern": ALIAS},
                "additionalProperties": {"type": "string", "pattern": r"^event_source_"},
            },
        },
    },
}

PROJECT_IDS = {
    "description": "项目 ID 数组或兼容的逗号分隔文本。公开内容单独用 @public。公开登记必须给出位置，只有宿主自动登记可省略。",
    "anyOf": [
        {"type": "array", "items": {"type": "string"},
         "if": {"contains": {"pattern": r"^\s*@public\s*$"}},
         "then": {"items": {"pattern": r"^\s*(?:@public)?\s*$"}, "contains": {"pattern": r"^\s*@public\s*$"},
                  "maxContains": 1}},
        {"type": "string",
         "if": {"pattern": r"(?:^|,)\s*@public\s*(?:,|$)"},
         "then": {"pattern": r"^(?:\s*,)*\s*@public\s*(?:,\s*)*$"}},
    ],
}

DEFINITION_PROPERTIES = {
    "title": _text("工作台名称，1–120 字，不能全为空白。", pattern=r"\S", maxLength=120),
    "description": _text("工作台说明，可为空。"),
    "html": _text("旧式页面 HTML，UTF-8 至多 400,000 字节。与 v2 清单一起使用时必须为空串。", maxLength=400_000),
    "bindings": BINDINGS,
    "navigation": {"description": "导航展示位置。", "enum": ["menu", "top", "hidden"], "default": "menu"},
    "manifest": MANIFEST,
    "panels": {"description": "按清单 source 路径提供面板 HTML。含 v2 清单时须为对象；清单和源码合计至多 400,000 字节。",
               "type": ["object", "null"], "propertyNames": {"pattern": r"^(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.html$"},
               "additionalProperties": {"type": "string", "pattern": r"\S"}},
    "application": APPLICATION,
    "project_ids": PROJECT_IDS,
}

# The update carries a patch, so compare only values explicitly supplied
# together here. Workbench still validates the final merged page and its size.
PAGE_COMBINATIONS = [
    {
        "if": {"required": ["manifest"], "properties": {"manifest": {"type": "object"}}},
        "then": {"properties": {"html": {"const": ""}, "panels": {"type": "object"}}},
    },
    {
        "if": {"required": ["manifest"], "properties": {"manifest": {"type": "null"}}},
        "then": {"properties": {"application": {"type": "null"}, "panels": {"maxProperties": 0}}},
    },
]

REVISION = {
    "description": "当前正修订。使用 JSON 整数，兼容十进制整数字符串。",
    "oneOf": [{"type": "integer", "minimum": 1}, {"type": "string", "pattern": r"^\s*\+?\d(?:_?\d)*\s*$"}],
}


def declaration():
    """Snapshot the same installed builtin names used by the Workbench store."""
    names = sorted(builtins())
    builtin = {"description": "已安装的内置工作台入口名称。", "type": "string", "enum": names}
    register = {
        "description": "登记工作台定义。保留旧登记的页面路径与回读元数据兼容。宿主生成构建回执。",
        "type": "object", "required": ["title"], "additionalProperties": False,
        "properties": {
            **DEFINITION_PROPERTIES,
            "path": _text("兼容旧登记携带的页面路径。内置入口仍由 Workbench 的已安装注册表选择。"),
            "builtin": {**builtin, "type": ["string", "null"], "enum": [*names, "", None]},
        },
        "patternProperties": {
            "^(?:workbench_id|created_by|created_at|updated_at)$": {
                "description": "兼容整份旧定义回传的元数据。服务使用正式分配的身份、真实调用者和本次时间覆盖这些值。",
            },
        },
        "allOf": [*PAGE_COMBINATIONS, {
            "if": {"anyOf": [
                {"required": ["application"], "properties": {"application": {"type": "object"}}},
                {"required": ["panels"], "properties": {"panels": {"type": "object", "minProperties": 1}}},
            ]},
            "then": {"required": ["manifest"], "properties": {"manifest": {"type": "object"}}},
        }],
    }
    update = {
        "description": "独立提交定义修改、发布当前修订或启用内置入口。每个分支都核对当前修订。",
        "oneOf": [
            {"description": "修改工作台定义。合并后的完整页面仍按 Workbench 领域规则校验。",
             "type": "object", "required": ["expected_revision"], "additionalProperties": False,
             "properties": {"expected_revision": REVISION, **DEFINITION_PROPERTIES}, "allOf": PAGE_COMBINATIONS},
            {"description": "独立发布已保存的当前修订。",
             "type": "object", "required": ["expected_revision", "published_revision"], "additionalProperties": False,
             "properties": {"expected_revision": REVISION,
                            "published_revision": {"description": "待发布修订，使用 JSON 整数，须等于当前修订。",
                                                   "type": "integer", "minimum": 1}}},
            {"description": "独立启用已发布的内置入口，核对当前入口身份。",
             "type": "object", "required": ["expected_revision", "activate_builtin", "previous_app_id"],
             "additionalProperties": False,
             "properties": {
                 "expected_revision": REVISION,
                 "activate_builtin": {"description": "须为 true，启用已发布的内置入口。", "const": True},
                 "previous_app_id": _text("原入口工作台 ID；无原入口时明确传空串。", pattern=r"^(?:workbench_[0-9a-f]{12})?$"),
                 "builtin": builtin,
             }},
        ],
    }
    return register, update
