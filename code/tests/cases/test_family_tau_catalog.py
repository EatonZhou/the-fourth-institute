"""Family TAU definitions remain selectable without starting a live Run.

:module: lineage.family
:design: decision_c953eab654cd
"""

import json
from pathlib import Path

import pytest

from management_portal.test_framework.catalog import CatalogRepository


SOURCE = Path(__file__).with_name("tau_cases.json")
FAMILY_CASES = [
    ("tau.resident.family-core", "test_d416f4e8cf65", "resident.identity"),
    ("tau.resident.family-solo-mute", "test_96c710a2b8f4", "resident.identity"),
    (
        "tests/cases/ui/family_recovery.spec.js::Family recovery shows the chosen request and lets an ambiguous continuation quote its target",
        "test_c90a0303999d",
        "lineage.hatg",
    ),
]


def test_tau_catalog_has_unique_case_and_implementation_identities():
    """目录身份与入口无重复，新增 Family 用例不遮蔽已有条目。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    cases = json.loads(SOURCE.read_text(encoding="utf-8"))["cases"]
    for field in ("id", "key"):
        values = [case[field] for case in cases]
        assert len(values) == len(set(values)), field
    implementations = [impl for case in cases for impl in case["implementations"]]
    for field in ("id", "entrypoint"):
        values = [impl[field] for impl in implementations]
        assert len(values) == len(set(values)), field


@pytest.mark.parametrize("key,case_id,module", FAMILY_CASES)
def test_family_tau_is_selectable_with_its_own_fixture(tmp_path, key, case_id, module):
    """真实目录解析与选择保留稳定身份、TAU入口和隔离准备说明，不启动业务。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    destination = tmp_path / "tests" / "cases" / "tau_cases.json"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(SOURCE.read_bytes())
    repository = CatalogRepository(root=tmp_path)
    cases, errors = repository.projection()
    assert errors == []
    selected, = repository.query({"case_ids": [case_id]})
    assert selected in cases
    assert (selected.key, selected.module, selected.lifecycle) == (key, module, "active")
    implementation, = selected.implementations
    assert implementation.runner == "tau"
    assert implementation.entrypoint.startswith("tau:")
    assert implementation.profiles == ("live.controlled",)
    assert implementation.risk == "external-side-effects"
    assert implementation.lineage_fixture
    assert implementation.task_prompt
