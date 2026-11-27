"""Knowledge-owned public mutation fields, shared with actual validation."""
from copy import deepcopy


REGISTER_CONTRACT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "description": "登记 Markdown 知识条目；项目、路径、标题与正文语义仍由知识领域检查。整数不接受布尔、浮点或数字字符串。",
    "required": ["project_id", "title"],
    "additionalProperties": False,
    "properties": {
        "project_id": {"type": "string", "minLength": 1, "maxLength": 120,
                       "pattern": r"\S", "description": "已登记且可写的所属 Project ID。"},
        "title": {"type": "string", "minLength": 1, "maxLength": 240,
                  "pattern": r"\S", "description": "非空标题；正文若有 H1 必须与它相同。"},
        "body": {"type": "string", "maxLength": 2_000_000, "default": "",
                 "description": "Markdown 正文，不含 YAML front matter；领域生成或核对 H1 后总正文仍须在长度限制内。"},
        "importance": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5,
                       "description": "重要程度，严格整数 1–10。"},
        "metaness": {"type": "integer", "minimum": 1, "maximum": 10,
                     "description": "抽象层次，严格整数 1–10；未知时省略，不能填 null。"},
        "ai_generated": {"type": "boolean", "default": False,
                         "description": "正文是否由 AI 生成。"},
        "status": {"type": "string", "description": "可选状态文本；未规定统一枚举。"},
        "links": {
            "type": "object", "description": "按关系名给出资源引用数组；领域去除空白与重复引用。",
            "additionalProperties": False,
            "properties": {
                name: {"type": "array", "description": label,
                       "items": {"type": "string", "description": "资源引用。"}}
                for name, label in {
                    "source": "来源资源。", "related": "相关资源。",
                    "supersedes": "被替代的资源。", "derived_from": "推导来源资源。",
                }.items()
            },
        },
    },
}

_VERSION_NUMBER = {"description": "读取所得版本号；兼容整数或十进制数字字符串。",
                   "anyOf": [{"type": "integer", "minimum": 0},
                             {"type": "string", "pattern": r"^[0-9]+$"}]}
UPDATE_CONTRACT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object", "additionalProperties": False,
    "description": "更新知识条目的正文或完整文本；权限、文件归属与锁内版本核验由知识领域负责。",
    "properties": {
        "address": {"type": "string", "minLength": 1,
                    "description": "可选内部文件地址；根必须等于被更新的资源 ID。"},
        "text": {"type": "string", "description": "文件完整文本，主 Markdown 包含原 front matter；不能改变所属项目。"},
        "body": {"type": "string", "description": "仅替换知识主 Markdown 正文，保留现有元数据；不能与 text 同给。"},
        "expected_version": {
            "description": "读取所得知识版本号或含 number 的版本记录；省略或 null 沿原不指定版本前提的调用方式。",
            "anyOf": [deepcopy(_VERSION_NUMBER), {"type": "null"},
                      {"type": "object", "required": ["number"],
                       "properties": {"number": deepcopy(_VERSION_NUMBER)},
                       "additionalProperties": True}],
        },
        "version_recovery_change_ids": {
            "type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True,
            "items": {"type": "string", "minLength": 1, "description": "原内容变更审计 ID。"},
            "description": "按原链顺序提供版本修复证据；必须同给完整 text 与非空版本，领域验证原链、身份及正文不变。",
        },
    },
    "oneOf": [{"required": ["text"]}, {"required": ["body"]}],
    "allOf": [{"if": {"required": ["version_recovery_change_ids"]},
               "then": {"required": ["text", "expected_version"],
                        "properties": {"expected_version": {"not": {"type": "null"}}}}}],
}

SKILL_UPDATE_CONTRACT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object", "additionalProperties": False, "required": ["text"],
    "description": "修改 Skill 文本文件；沿 Skill 文件版本和当前权限核验，不支持知识正文替换或版本修复。",
    "properties": {
        "address": deepcopy(UPDATE_CONTRACT["properties"]["address"]),
        "text": {"type": "string", "description": "Skill 文件的完整文本。"},
        "expected_version": {"type": ["string", "null"],
                             "description": "读取所得文件 SHA-256 版本；省略或 null 保留原不指定版本前提的调用方式。"},
    },
}


def update_contract(resource_type: str):
    return SKILL_UPDATE_CONTRACT if resource_type == "skill" else UPDATE_CONTRACT
