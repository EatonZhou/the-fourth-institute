"""测试框架只接受规范格式标签，保留原始摘要、身份与写入格式。

:module: operations.test-framework
:risk: no-external-side-effects
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from management_portal.test_framework.catalog import source_digests, planned_source_changed
from management_portal.test_framework.catalog_source import explicit_cases, read_case_ids
from management_portal.test_framework.models import CatalogError, Implementation
from management_portal.test_framework.records import RunRecords


READERS = (
    "test-run-index",
    "test-case-ids",
    "portal-design-live-cases",
    "portal-tau-cases",
    "portal-spec-design-cases",
)
CASE_ID = "test_0123456789ab"
RUN = {"run_id": "testrun_0123456789abcdef", "status": "failed",
       "summary": "original failure", "idempotency_key": "unchanged-key"}


def declaration(source):
    return {
        "id": CASE_ID, "key": "format-reader", "title": "Local declaration",
        "module": "operations.test-framework", "importance": "P2", "aspects": ["functional"],
        "is_design_point": True, "design_refs": ["decision_format_fixture"],
        "design_point_ref": "decision_format_fixture#reader",
        "live": {"mode": "sandbox", "target": "/", "steps": [{"action": "inspect"}]},
        "implementations": [{"id": "impl-format-reader", "runner": "tau",
            "entrypoint": "fixture:format-reader", "source": source, "profiles": [],
            "risk": "no-external-side-effects", "task_prompt": "Do not execute this fixture.",
            "line": 12}],
    }


@pytest.fixture(params=READERS)
def reader(request, tmp_path):
    tag = request.param
    if tag == "test-run-index":
        path = tmp_path / "run-index.json"
        document = {"runs": [deepcopy(RUN)]}
        read = RunRecords(path)._read_index
        expected = [RUN]
    elif tag == "test-case-ids":
        path = tmp_path / "tests/cases/case_ids.json"
        expected = {"tests/cases/test_fixture.py::test_read": CASE_ID}
        document = {"ids": expected.copy()}
        read = lambda: read_case_ids(tmp_path)
    else:
        filename = {"portal-design-live-cases": "design_live/format.json",
                    "portal-tau-cases": "tau_cases.json",
                    "portal-spec-design-cases": "spec_design_cases.json"}[tag]
        path = tmp_path / "tests/cases" / filename
        source = path.relative_to(tmp_path).as_posix()
        case = declaration(source)
        document = {"cases": [case]}
        if tag == "portal-design-live-cases":
            read = lambda: explicit_cases(tmp_path)
            expected = [case]
        else:
            implementation = Implementation.parse(case["implementations"][0], CASE_ID).public()
            def read():
                # Catalog loading and source fingerprinting enforce the same tag.
                explicit_cases(tmp_path)
                return source_digests(CASE_ID, implementation, tmp_path)
            expected = implementation
    path.parent.mkdir(parents=True, exist_ok=True)
    document.update(schema=f"tfi.{tag}.v1", summary="原文", identity="fixture-only")
    return tag, path, document, read, expected


def write_document(path, document):
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path.read_bytes()


def test_framework_reads_exact_namespace_without_rewriting(reader, tmp_path):
    """仅规范标签可读，原始文件、用例身份和摘要语义保持。"""
    tag, path, document, read, expected = reader
    document["schema"] = f"tfi.{tag}.v1"
    original = write_document(path, document)
    result = read()
    assert path.read_bytes() == original
    if tag in {"portal-tau-cases", "portal-spec-design-cases"}:
        snapshot = deepcopy(document)
        del snapshot["cases"][0]["implementations"][0]["line"]
        encoded = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        assert result == {"source_sha256": hashlib.sha256(original).hexdigest(),
                          "source_case_sha256": hashlib.sha256(encoded).hexdigest()}
        plan = {"case_id": CASE_ID, "implementation": expected, **result}
        assert not planned_source_changed(plan, tmp_path)
        document["cases"][0]["implementations"][0]["task_prompt"] += " Changed."
        write_document(path, document)
        assert planned_source_changed(plan, tmp_path)
    else:
        assert result == expected


@pytest.mark.parametrize("bad", ["archive.{tag}.v1", "archive.{tag}.v2", "tfi.{tag}.v2", "tfi.{tag}.v01",
                                  "tfi.unrelated.v1", "other.{tag}.v1", "tfi.{tag}.v1 ", None, ["tfi"]])
def test_framework_rejects_other_tags_and_versions_without_rewriting(reader, bad):
    """标签不接受宽松前缀、其他版本、空值或非字符串。"""
    tag, path, document, read, _ = reader
    document["schema"] = bad.format(tag=tag) if isinstance(bad, str) else bad
    original = write_document(path, document)
    with pytest.raises(RuntimeError if tag == "test-run-index" else CatalogError):
        read()
    assert path.read_bytes() == original


def test_framework_keeps_non_schema_validation(reader):
    """规范标签不放宽运行列表、Live 前提或实现身份验证。"""
    tag, path, document, read, _ = reader
    document["schema"] = f"tfi.{tag}.v1"
    if tag == "test-run-index":
        document["runs"] = {}
    elif tag == "test-case-ids":
        document["ids"] = 42
    elif tag == "portal-design-live-cases":
        document["cases"][0].pop("live")
    else:
        document["cases"][0]["implementations"][0]["id"] = "different-implementation"
    original = write_document(path, document)
    error = {"test-run-index": RuntimeError, "test-case-ids": TypeError}.get(tag, CatalogError)
    with pytest.raises(error):
        read()
    assert path.read_bytes() == original


def test_run_index_writer_still_emits_legacy_format(tmp_path):
    """4B 新写入使用 tfi；沿用测试入口以保留稳定 ID，读取不改写文件。"""
    path = tmp_path / "index.json"
    records = RunRecords(path)
    records._write_index([deepcopy(RUN)])
    original = path.read_bytes()
    assert json.loads(original) == {"schema": "tfi.test-run-index.v1", "runs": [RUN]}
    assert records._read_index() == [RUN]
    assert path.read_bytes() == original


def test_tfi_framework_javascript_deployment_reader():
    """Dev 目标解析用真实 Node 读取临时注册文件，不启动浏览器。"""
    source = Path(__file__).with_name("javascript") / "test_tfi_framework_format_compatibility.cjs"
    result = subprocess.run(["node", "--test", str(source)], capture_output=True, text=True, timeout=30)
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture(autouse=True)
def retired_reader_settings_do_not_enable_aliases(tmp_path, monkeypatch):
    site = tmp_path / "site.json"
    site.write_text(json.dumps({"deployment": {"compatibility": {
        "format_namespaces": ["archive."],
        "domain_values": {"test_framework.schema_aliases": {
            f"tfi.{tag}.v1": [f"archive.{tag}.v1"] for tag in READERS}},
    }}}))
    monkeypatch.setenv("PORTAL_SITE_CONFIG", str(site))
    before = site.read_bytes()
    yield
    assert site.read_bytes() == before
