"""New projection outputs use TFI without updating existing Workbenches.

:module: workbench.panels
:importance: P1
:design: decision_7a5ae487e2e9
"""
from copy import deepcopy
import json

import pytest

from management_portal.workbench_projections.development_projection import application_definition
from management_portal.workbench_projections.research_apps import definition as research
from management_portal.workbench_projections.store import ProjectionError, validate_manifest
from tests.cases.test_tfi_projection_format_compatibility import _read_only


COMMIT = "421e133b5ab4ba64a39f1da530fd149405b3bf53"


@pytest.mark.parametrize("example", [False, True])
def test_new_page_output_and_legacy_validation_preserve_payload(example, monkeypatch, tmp_path):
    """新投影输出使用规范格式，旧格式验证被拒绝且载荷与生成器模板不变"""
    from management_common.site_config import read_site_config
    config = read_site_config()
    config.setdefault('deployment', {})['development_projection'] = {
        'repo_id': 'repo_123456789abc', 'lineage_id': 'lineage_123456789abc',
        'facade_resident_id': 'resident_123456789abc',
        'packaging_lineage_id': 'lineage_abcdef123456',
        'packaging_facade_resident_id': 'resident_abcdef123456',
    }
    site = tmp_path / 'site.json'
    site.write_text(json.dumps(config))
    monkeypatch.setenv('PORTAL_SITE_CONFIG', str(site))
    with _read_only():
        value = application_definition(COMMIT, example=example)
        assert value["manifest"]["schema"] == "tfi.projection.v2"
        assert value["application"]["commit"] == COMMIT
        assert set(value["application"]["entries"]) == (
            {"example"} if example else {"architecture", "progress", "evolution", "packaging"}
        )
        assert ("chat" in value["manifest"]) is not example
        original = deepcopy(value)
        assert validate_manifest(value) == original == value
        legacy = deepcopy(value)
        legacy["manifest"]["schema"] = "archive.projection.v2"
        before = deepcopy(legacy)
        with pytest.raises(ProjectionError, match='不支持的投影页面清单版本') as error:
            validate_manifest(legacy)
        assert error.value.code == 'projection.manifest'
        assert legacy == before
        value["bindings"].setdefault("main", {}).setdefault("events", {})["test"] = "local"
        assert application_definition(COMMIT, example=example) == original


def test_research_outputs_preserve_historical_source_and_revision():
    source = research.ROOT / "data/legacy-definitions.json"
    before = (source.read_bytes(), source.stat().st_mtime_ns)
    old = json.loads(before[0])["applications"]
    with _read_only():
        outputs = research.definitions(COMMIT)
        for (entry, previous), row in zip(old.items(), outputs, strict=True):
            value = row["fields"]
            assert value["manifest"]["schema"] == "tfi.projection.v2"
            assert row["resource_id"] == previous["workbench_id"]
            assert row["previous_published_revision"] == previous["published_revision"]
            assert value["expected_revision"] == previous["revision"]
            assert value["application"]["commit"] == COMMIT
            assert value["application"]["entries"] == {entry: entry + ".html"}
            bindings = deepcopy(previous["bindings"])
            bindings["main"]["residents"]["facade"] = bindings["main"]["residents"]["coordinator"]
            assert value["bindings"] == bindings
            assert not set(value) & {"title", "owner_ids", "project_ids", "navigation"}
        expected = deepcopy(outputs)
        outputs[0]["fields"]["bindings"]["main"]["residents"]["facade"] = "local"
        assert research.definitions(COMMIT) == expected
        with pytest.raises(ValueError):
            research.definitions("short")
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
