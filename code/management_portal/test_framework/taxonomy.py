from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModuleNode:
    id: str
    label: str
    design_refs: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "design_refs": list(self.design_refs)}


@dataclass(frozen=True)
class ModuleGroup:
    id: str
    label: str
    modules: tuple[ModuleNode, ...]

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "modules": [item.public() for item in self.modules]}


def node(module_id: str, label: str, *design_refs: str) -> ModuleNode:
    return ModuleNode(module_id, label, tuple(design_refs))


# SPEC.md owns the current Design index. This registry groups those resources
# into test domains and is the only accepted module vocabulary.
MODULE_GROUPS = (
    ModuleGroup("spec", "1. 规范管理与总体设计", (
        node("spec.design-governance", "Design 索引、生命周期与总体架构", "decision_ef80c6d3b5ee", "decision_9c258743f91e", "decision_463fd3e76150"),
    )),
    ModuleGroup("isa", "2. HATG ISA 指令集", (
        node("isa.instructions", "指令格式、调用入口与十四类指令", "decision_898b8911a2ff", "decision_ad3e21094f14", "decision_6eac174d85bf",
             "decision_d19b9909a674", "decision_2fe6fd5367fb", "decision_2a3ff353165c", "decision_4637ca4a69e2",
             "decision_71ba0494b93e", "decision_68215b3a825b", "decision_5b5653c3eaa5", "decision_3e063aac15b3",
             "decision_6591e7793116", "decision_22227d0ad075", "decision_4527448ad07f", "decision_aa4fdbac8364",
             "decision_8b510cbd9c58", "decision_b04cac4188bb", "decision_c54e68a82d7a", "decision_cc96fce57ab6",
             "decision_b77fde81e7a0"),
    )),
    ModuleGroup("security", "3. 用户、权限与安全", (
        node("security.identity-access", "身份、成员关系与访问控制", "decision_3fe3cca8fd95", "decision_34158fdd4078", "decision_ae422b92c6f3", "decision_5bc3fdfc7eec", "decision_b820faf42218", "decision_600ae27b0538"),
        node("security.network-boundary", "网络边界与安全入口", "decision_fb910c43ab07", "decision_85222f2d316b"),
        node("security.device-credentials", "登录认证、设备凭据与管理控制", "decision_3bce8b455a3c", "decision_6c3d74a9bc9d", "decision_ad7bdfde49ef", "decision_626fb02b3894", "decision_30528aff7035", "decision_ea13b85216d0", "decision_d397f2d99f89", "decision_3286d4167180"),
    )),
    ModuleGroup("resource", "4. 资源注册、搜索与内容管理", (
        node("resource.identity", "统一资源身份、解析与授权", "decision_4a9ba5dfada6", "decision_a4e5f03c8b9b"),
        node("resource.service", "ResourceService、Adapter 与资源协作", "decision_cf27d39e39cd", "decision_b6e74d78f7b6", "decision_36f46b395d8e", "decision_b0997bc3a922",
             "decision_355ed742bca5"),
        node("resource.index-search", "资源索引、搜索与内容投影", "decision_8dc6b3d03ca7", "decision_eb066fbea4ed"),
    )),
    ModuleGroup("resident", "5. Resident、Session 与 Conversation", (
        node("resident.identity", "Resident 身份与发现", "decision_0f57656a2acd", "decision_44cf587c1721", "decision_8fe87d812a4c", "decision_bd5217d08c09", "decision_3e07c6d35cac", "decision_5ff0cb51ed84"),
        node("resident.conversation", "输入、Conversation 与 Channel", "decision_edc470e09f5a", "decision_725dedbe89b2", "decision_c524c775e0be", "decision_692a484e9bea", "decision_15f7df737ed5", "decision_a6bebf4ac283", "decision_0571f3741e23"),
        node("resident.session", "Session、Generation 与 Provider", "decision_c47a13e8b962", "decision_ccbb1cac24aa"),
        node("resident.workspace-memory", "Folder、Workspace 与上下文", "decision_721c64f9bdcc", "decision_ccb99a00eebd", "decision_8838269736d3", "decision_c616d197abbb"),
        node("resident.fork-handoff", "Fork 与 Handoff", "decision_5bf5bfbd52cb", "decision_7ef392b5f60d", "decision_6fc653d55c79"),
    )),
    ModuleGroup("lineage", "6. Lineage 与 Agent 协作", (
        node("lineage.service", "SocietyService 与领域状态", "decision_1b2bdf5c33d6", "decision_d918ebebc062"),
        node("lineage.hatg", "HATG 原语与程序", "decision_4adcc1ff2f2c"),
        node("lineage.team", "Agent Team 与协作图", "decision_6fc653d55c79"),
        node("lineage.family", "Family 门面与监督", "decision_44cf587c1721", "decision_b77fde81e7a0"),
    )),
    ModuleGroup("execution", "7. Execution、Provider 与 Agent Harness", (
        node("execution.scheduled-triggered", "定时与自动触发的工作", "decision_5ff0cb51ed84", "decision_86721bb4e8c8"),
        node("execution.service", "ExecutionService、调度与 Worker", "decision_25eb93d803a4", "decision_6ad0384e020d", "decision_8ac0b5a6c0e5", "decision_075f66430606", "decision_d40f59f5508f"),
        node("execution.persistence", "Execution 持久化与计量", "decision_b1ab45c4b3ec", "decision_f1331d57be89"),
        node("execution.harness", "Agent Harness、Prompt Context 与 AI Relay", "decision_5c62deb8253a", "decision_c604274e21dd", "decision_ef1d6c925955", "decision_fd760e898f9a"),
        node("execution.providers", "模型 Provider 与帐号接入", "decision_91eb3626d323", "decision_425e428410e0", "decision_3e8c2a7f91d4", "decision_e587fe1c472e", "decision_6b9e42f1c8a7", "decision_3948ce9b9443", "decision_3dd52c0692c1", "decision_6c2f91a7d84e", "decision_276105b5d67b", "decision_7956d7cb4bde"),
    )),
    ModuleGroup("workflow", "8. Paper 与业务工作流", (
        node("workflow.wall", "WALL 设计与软件演进", "decision_06defc47e960", "decision_1a2730dcd3d1"),
        node("workflow.studio", "投影工坊与反投影", "decision_4bc03b20c30f"),
        node("workflow.paper", "Paper", "decision_528bf9985705", "decision_e37f240e186d"),
        node("workflow.repository", "Repo 与 Workspace", "decision_6476c69b33da"),
        node("workflow.article", "Article", "decision_956154cd02f6"),
        node("workflow.knowledge", "Knowledge、Skill、Experiment 与 Archive", "decision_3ece26686464", "decision_f48c2514bf33", "decision_9ef033d79173", "decision_58db6282c244", "decision_4666241592d4"),
        node("workflow.project", "Project 业务状态", "decision_4648f7fa3295", "decision_46c37026e8ea", "decision_9d15ae2053c4"),
    )),
    ModuleGroup("workbench", "9. Portal 前端与工作台", (
        node("workbench.components", "前端组件系统与通用交互", "decision_741b04821171", "decision_b3022c27d22f", "decision_daba6706a788", "decision_fd9851ae01c0"),
        node("workbench.panels", "Workbench、Panel 与打开语义", "decision_4348ed990621", "decision_538e155e1102", "decision_79de71d9890d", "decision_f67ae5e70793", "decision_7a5ae487e2e9", "decision_13e1a748cf04", "decision_78134209e5c9"),
        node("workbench.chat", "Agent Console、Inbox 与共享 Chat", "decision_f560803947c6", "decision_18abbdef3da3", "decision_ce44377c3257", "decision_15f7df737ed5",
             "decision_1e2eb869bc10", "decision_8b2fa224616b"),
        node("workbench.notifications", "Home 与通知", "decision_00546e28e1e0", "decision_865ac8ebef25", "decision_5d2bf4a72f09", "decision_605810a1292b", "decision_152d5c046a23", "decision_e30897dd3dc3", "decision_4dea0db1f45a", "decision_fc26f883773d", "decision_481dc4e6292c", "decision_ed57c4ad1001"),
        node("workbench.preview", "文件、Preview、Resource Detail 与 Workspace", "decision_61e50306cc79", "decision_dca903d71575", "decision_e5f66e2f6a5e", "decision_864d4fe41934", "decision_2f67ed28db67", "decision_769a35d6b5c7", "decision_f22e45dfd382"),
        node("workbench.user-state", "页面状态、关联与恢复", "decision_6e230393221c", "decision_9f6135cbbb22", "decision_bd179998ba08", "decision_f25266e5a0e7"),
        node("workbench.shell", "Portal Shell、导航与搜索", "decision_27253e25f016", "decision_e486671af066", "decision_a144bf700017"),
    )),
    ModuleGroup("platform", "10. 数据、事件与服务通信", (
        node("platform.events", "NATS、事件通知与服务通信", "decision_abaedeefca6f", "decision_f55bac7fc199"),
        node("platform.data", "数据库、投影与共享文件状态", "decision_71aa039e3211", "decision_86aed2f0e8c7", "decision_0e043d5f81be"),
        node("platform.core", "系统、Web、RPC 与运行状态", "decision_0820081083f5", "decision_96a887d83e8a", "decision_ecf0f2b2de28", "decision_99bb5dc0f96b"),
    )),
    ModuleGroup("operations", "11. 部署、测试与运行保障", (
        node("operations.deployment", "Dev/Release、重启与部署", "decision_37f98f8fcb4b", "decision_08bb2d9141f9",
             "decision_15aab18c0ab3", "decision_ab9880dc4c9d", "decision_d88306d0bd17"),
        node("operations.runtime", "备份与运行保障", "decision_b06ae321a668"),
        node("operations.test-framework", "测试框架", "decision_8273271b67b1", "decision_c953eab654cd"),
        node("operations.test-workbench", "测试工作台", "decision_f2a6a5dd4646"),
        node("operations.test-gates", "定向验证与发布证据", "decision_6febe7bb57f9"),
    )),
)

MODULES = {module.id: (group, module) for group in MODULE_GROUPS for module in group.modules}

def canonical_module(value: str) -> str:
    candidate = str(value or "").strip()
    if candidate not in MODULES:
        raise ValueError(f"未知测试框架功能模块：{value!r}")
    return candidate


def module_path(module_id: str) -> dict[str, str]:
    group, module = MODULES[canonical_module(module_id)]
    return {"group_id": group.id, "group_label": group.label, "module_id": module.id, "module_label": module.label}


def public_taxonomy() -> list[dict[str, Any]]:
    return [group.public() for group in MODULE_GROUPS]


# The second dimension: the features a Portal package may leave out
# (portal_features.py).  A case belongs to the features it needs; a package
# without one of them cannot run it.  A case that needs none is core.
CORE_FEATURE = "core"


def features() -> dict[str, str]:
    """``{feature key: title}`` in ``portal_features.FEATURES`` order."""
    from management_portal.portal_features import FEATURES
    return {feature.key: feature.title for feature in FEATURES}


def canonical_feature(value: str) -> str:
    candidate = str(value or "").strip()
    if candidate not in features():
        raise ValueError(f"未知功能：{value!r}")
    return candidate


def public_features() -> list[dict[str, str]]:
    return [{"id": CORE_FEATURE, "label": "核心"}, *({"id": key, "label": title} for key, title in features().items())]
