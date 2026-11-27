"""Public ResourceService request schemas owned by the Report domain."""

from .home_report_quality import QUALITIES


QUALITY = {
    "description": "汇报质量。更新时省略或传 null 沿用当前质量。",
    "enum": [*QUALITIES, None],
}

REGISTER_CONTRACT = {
    "description": "登记已编写的固定解释包，或按原请求重放已提交的历史登记。",
    "oneOf": [
        {
            "type": "object",
            "description": "登记由 Workspace 中的固定文件和完整内容指纹定义的解释包。",
            "required": ["explanation_target"],
            "properties": {
                "explanation_target": {
                    "description": "解释包清单的固定文件引用。文件可读性、路径安全及内容指纹由汇报领域复核。",
                    "type": "object",
                    "required": ["address", "sha256"],
                    "additionalProperties": False,
                    "properties": {
                        "address": {"description": "正式 Workspace 文件地址。", "type": "string",
                                    "pattern": r"^workspace_[0-9a-f]{12}/file/.+$", "maxLength": 2048},
                        "sha256": {"description": "清单字节的完整小写 SHA-256。", "type": "string",
                                   "pattern": "^[0-9a-f]{64}$", "minLength": 64, "maxLength": 64},
                    },
                },
                "origin_key": {"description": "可省略。给出时须等于服务按解释包引用派生的来源键。",
                               "type": "string", "pattern": "^home-message/v1:[0-9a-f]{64}$", "maxLength": 80},
            },
            "additionalProperties": False,
        },
        {
            "type": "object",
            "description": "仅以原操作 ID、原调用者和原始请求重放已提交的历史登记。此分支不会创建汇报。",
            "x-resource-mode": "legacy-replay-only",
            "properties": {
                "target": {"description": "原需求的任务、Lineage 或 Project 目标。", "type": ["object", "null"]},
                "version": {"description": "原请求的来源版本。", "type": ["string", "null"]},
                "import_job": {"description": "原请求的媒体导入任务 ID。", "type": ["string", "null"]},
                "origin_key": {"description": "原请求的来源键。", "type": ["string", "null"]},
                "quality": {**QUALITY, "description": "历史请求的原质量，重放不得修改。"},
                "origin_context": {"description": "历史来源上下文，按原 JSON 参与请求指纹。",
                                   "type": ["object", "array", "string", "number", "boolean", "null"]},
            },
            "additionalProperties": False,
        },
    ],
}


def _prose(description, limit=8000):
    return {"description": description, "type": "string", "pattern": r"\S", "maxLength": limit}


def _coordinate(axis):
    return {"description": f"固定图中的归一化{axis}坐标。", "type": "number", "minimum": 0, "maximum": 1}


PRESENTATION = {
    "description": "修改固定来源的交互图。节点和边必须存在于当前来源，分镜必须保留全部步骤。",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "question": _prose("交互问题。"),
        "assumption": _prose("推理假设。"),
        "rule": _prose("推理规则。"),
        "limit": _prose("推理限制。"),
        "nodes": {
            "description": "对固定来源节点的修改，同一节点至多改一次。",
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id"],
                "additionalProperties": False,
                "properties": {
                    "id": {"description": "固定来源节点 ID。", "type": "string"},
                    "title": _prose("节点标题。"),
                    "x": _coordinate("横"),
                    "y": _coordinate("纵"),
                },
            },
        },
        "steps": {
            "description": "按当前分镜顺序给出完整步骤。空对象保留对应步骤。",
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": _prose("分镜标题。"),
                    "speech": _prose("分镜讲稿。若同时给出节拍，以节拍合成讲稿。"),
                    "beats": {
                        "description": "分镜同步节拍，顺序与讲稿一致。",
                        "type": "array", "minItems": 1, "maxItems": 30,
                        "items": {
                            "type": "object", "required": ["speech"], "additionalProperties": False,
                            "properties": {
                                "speech": _prose("节拍讲稿。"),
                                "edge": {"description": "可选，节拍高亮的固定来源边 ID。", "type": "string"},
                            },
                        },
                    },
                },
            },
        },
    },
}

UPDATE_CONTRACT = {
    "description": "按当前修订刷新历史汇报或修改正文。固定解释包使用专门的创作和审核入口。",
    "type": "object",
    "required": ["expected_revision"],
    "additionalProperties": False,
    "properties": {
        "expected_revision": {
            "description": "当前正整数修订。整数须用 JSON 整数表示，兼容 1–9 位无前导零的数字字符串。",
            "oneOf": [
                {"type": "integer", "minimum": 1, "maximum": 999_999_999},
                {"type": "string", "pattern": "^[1-9][0-9]{0,8}$", "maxLength": 9},
            ],
        },
        "quality": QUALITY,
        "content": {
            "description": "省略或 null 表示按来源刷新。修改正文时至少给出一项正文或交互内容。",
            "oneOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "anyOf": [{"required": [field]} for field in ("title", "goal", "markdown", "presentation")],
                    "properties": {
                        "title": _prose("汇报标题。", 4000),
                        "goal": _prose("汇报目标。", 4000),
                        "markdown": _prose("汇报 Markdown 正文。", 100_000),
                        "presentation": PRESENTATION,
                        "authoring_id": {"description": "用于本次修订的创作任务 ID，空值表示未关联。",
                                         "type": ["string", "null"]},
                    },
                },
            ],
        },
    },
}
