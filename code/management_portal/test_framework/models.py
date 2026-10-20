from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from management_portal.test_framework.taxonomy import canonical_feature, canonical_module, module_path


IMPORTANCE = ("P0", "P1", "P2", "P3")
ASPECTS = frozenset(
    {"functional", "ui", "behavior", "performance", "architecture-conformance"}
)
LIFECYCLES = frozenset({"active", "needs-review", "obsolete", "retired"})
IMPLEMENTATION_RISKS = frozenset({
    "unspecified",
    "no-external-side-effects",
    "external-side-effects",
})
TEST_RESOURCE_ID_RE = re.compile(r"^test_[0-9a-f]{12}$")


class CatalogError(ValueError):
    pass


def clean_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def design_point_ref(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise CatalogError("design_point_ref 必须是单个 WALL source#point 完整 ID")
    value = value.strip()
    source, separator, point = value.partition("#")
    if not separator or not source or not point or "#" in point or any(char.isspace() for char in value):
        raise CatalogError("design_point_ref 必须是单个 WALL source#point 完整 ID")
    return value


def parse_live(value: Any, case_id: str) -> dict[str, Any] | None:
    """Normalize the one public contract for a playable Live definition."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise CatalogError(f"{case_id}：Live 定义必须是对象")
    live = dict(value)
    mode = str(live.get("mode") or "").strip()
    target = str(live.get("target") or "").strip()
    if not target.startswith("/"):
        raise CatalogError(f"{case_id}：Live 必须提供站内 target")
    if mode == "sandbox":
        steps = live.get("steps")
        if not isinstance(steps, list) or not steps:
            raise CatalogError(f"{case_id}：沙盒 Live 必须包含可执行 steps")
        if any(not isinstance(step, dict) or not str(step.get("action") or "").strip() for step in steps):
            raise CatalogError(f"{case_id}：沙盒 Live 的每一步都必须包含 action")
        return live
    if mode == "direct":
        if str(live.get("risk") or "").strip() != "read-only":
            raise CatalogError(f"{case_id}：真实页面 Live 必须是 read-only")
        if not str(live.get("adapter") or "").strip():
            raise CatalogError(f"{case_id}：真实页面 Live 必须指定 adapter")
        try:
            adapter_version = int(live.get("adapter_version") or 1)
        except (TypeError, ValueError) as exc:
            raise CatalogError(f"{case_id}：Live adapter_version 无效") from exc
        if adapter_version != 1:
            raise CatalogError(f"{case_id}：不支持 Live adapter_version {adapter_version}")
        steps = live.get("direct_steps")
        if not isinstance(steps, list) or not steps:
            raise CatalogError(f"{case_id}：真实页面 Live 必须包含 direct_steps")
        if any(not isinstance(step, dict) or not str(step.get("capability") or "").strip() for step in steps):
            raise CatalogError(f"{case_id}：真实页面 Live 的每一步都必须包含 capability")
        live["adapter_version"] = adapter_version
        return live
    raise CatalogError(f"{case_id}：Live mode 必须是 direct 或 sandbox")


@dataclass(frozen=True)
class Implementation:
    id: str
    runner: str
    entrypoint: str
    source: str
    profiles: tuple[str, ...]
    task_prompt: str = ""
    risk: str = "unspecified"
    lineage_fixture: str = ""

    @classmethod
    def parse(cls, raw: Any, case_id: str) -> "Implementation":
        if not isinstance(raw, dict):
            raise CatalogError(f"{case_id}：测试实现必须是对象")
        values = {
            "id": str(raw.get("id") or "").strip(),
            "runner": str(raw.get("runner") or "").strip(),
            "entrypoint": str(raw.get("entrypoint") or "").strip(),
            "source": str(raw.get("source") or "").strip(),
        }
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise CatalogError(f"{case_id}：测试实现缺少 {', '.join(missing)}")
        risk = str(raw.get("risk") or "unspecified").strip()
        if risk not in IMPLEMENTATION_RISKS:
            raise CatalogError(
                f"{case_id}：测试实现 risk 必须是 {sorted(IMPLEMENTATION_RISKS)} 之一"
            )
        fixture = raw.get("lineage_fixture", "")
        if not isinstance(fixture, str) or ("lineage_fixture" in raw and not fixture.strip()):
            raise CatalogError(f"{case_id}：lineage_fixture 必须说明独立测试团队的必要性")
        if fixture and (values["runner"] != "tau" or risk != "external-side-effects"):
            raise CatalogError(f"{case_id}：独立测试团队仅用于声明外部副作用的 TAU 实现")
        return cls(
            **values,
            profiles=clean_list(raw.get("profiles")),
            task_prompt=str(raw.get("task_prompt") or "").strip(),
            risk=risk,
            lineage_fixture=fixture.strip(),
        )

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "runner": self.runner,
            "entrypoint": self.entrypoint,
            "source": self.source,
            "profiles": list(self.profiles),
            "risk": self.risk,
            **({"task_prompt": self.task_prompt} if self.task_prompt else {}),
            **({"lineage_fixture": self.lineage_fixture} if self.lineage_fixture else {}),
        }


@dataclass(frozen=True)
class TestCase:
    id: str
    key: str
    title: str
    description: str
    importance: str
    module: str
    aspects: tuple[str, ...]
    is_design_point: bool
    design_refs: tuple[str, ...]
    lifecycle: str
    implementations: tuple[Implementation, ...]
    live: dict[str, Any] | None = None
    design_point_ref: str | None = None
    # The features (portal_features.py) the case needs; none means core.
    features: tuple[str, ...] = ()

    @classmethod
    def parse(cls, raw: Any) -> "TestCase":
        if not isinstance(raw, dict):
            raise CatalogError("测试用例必须是对象")
        case_id = str(raw.get("id") or "").strip()
        if not TEST_RESOURCE_ID_RE.fullmatch(case_id):
            raise CatalogError(
                f"测试用例 ID 必须是 test_<12位小写十六进制码>：{case_id or '<空>'}"
            )
        required = {
            "id": case_id,
            "key": str(raw.get("key") or "").strip(),
            "title": str(raw.get("title") or "").strip(),
            "module": canonical_module(str(raw.get("module") or "").strip()),
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise CatalogError(f"{case_id or '<未知>'}：缺少 {', '.join(missing)}")
        # A test without a docstring has its name as the title and no description.
        required["description"] = str(raw.get("description") or "").strip()
        importance = str(raw.get("importance") or "").upper()
        if importance not in IMPORTANCE:
            raise CatalogError(f"{case_id}：重要等级无效 {importance!r}")
        aspects = clean_list(raw.get("aspects"))
        if not aspects or not set(aspects) <= ASPECTS:
            raise CatalogError(f"{case_id}：测试方面无效 {aspects!r}")
        design_refs = clean_list(raw.get("design_refs"))
        is_design_point = bool(raw.get("is_design_point"))
        point_ref = design_point_ref(raw.get("design_point_ref"))
        if point_ref and (not is_design_point or point_ref.partition("#")[0] not in design_refs):
            raise CatalogError(f"{case_id}：点绑定须标记 is_design_point 并在 design_refs 保留其来源文档")
        if is_design_point and not design_refs:
            raise CatalogError(f"{case_id}：设计点必须关联 design_refs")
        if "architecture-conformance" in aspects and not design_refs:
            raise CatalogError(f"{case_id}：架构约束测试必须关联 design_refs")
        lifecycle = str(raw.get("lifecycle") or "active").strip()
        if lifecycle not in LIFECYCLES:
            raise CatalogError(f"{case_id}：生命周期无效 {lifecycle!r}")
        implementations = tuple(
            Implementation.parse(item, case_id) for item in (raw.get("implementations") or [])
        )
        live = parse_live(raw.get("live"), case_id)
        return cls(
            **required,
            importance=importance,
            aspects=aspects,
            is_design_point=is_design_point,
            design_refs=design_refs,
            lifecycle=lifecycle,
            implementations=implementations,
            live=live,
            design_point_ref=point_ref,
            features=tuple(dict.fromkeys(canonical_feature(item) for item in clean_list(raw.get("features")))),
        )

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "title": self.title,
            "description": self.description,
            "importance": self.importance,
            "module": self.module,
            "module_path": module_path(self.module),
            "features": list(self.features),
            "aspects": list(self.aspects),
            "is_design_point": self.is_design_point,
            "design_refs": list(self.design_refs),
            "design_point_ref": self.design_point_ref,
            "lifecycle": self.lifecycle,
            "implementations": [item.public() for item in self.implementations],
            **({"live": self.live} if self.live else {}),
        }

    def workspace_summary(self) -> dict[str, Any]:
        """Project only fields needed to browse the Test catalog.

        Implementation definitions and design documents belong to the selected
        case detail.  Sending those fields for every case made the Test
        workspace bootstrap grow linearly with the full implementation corpus.
        """
        return {
            "id": self.id,
            "key": self.key,
            "title": self.title,
            "importance": self.importance,
            "module": self.module,
            "features": list(self.features),
            "aspects": list(self.aspects),
            "is_design_point": self.is_design_point,
            "design_point_ref": self.design_point_ref,
            "lifecycle": self.lifecycle,
            # Keep runner classification in the bounded projection.  Panels
            # need this summary to classify TAU coverage without downloading
            # every implementation definition.
            "runners": sorted({item.runner for item in self.implementations}),
            "detail_loaded": False,
            **({"live": self.live} if self.live else {}),
        }
