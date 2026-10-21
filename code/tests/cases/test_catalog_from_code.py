"""The Test Catalog is built from the test sources (management_portal/test_framework/catalog_source.py).

:module: operations.test-framework
:design: decision_8273271b67b1
"""
import json
from pathlib import Path

import pytest

from management_portal.test_framework import catalog
from management_portal.test_framework.catalog import CatalogRepository
from management_portal.test_framework.catalog_source import derived_case_id, implementation_id, parse_source
from management_portal.test_framework.models import CatalogError
from management_portal.test_framework.tools import sync_catalog


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def pin(root: Path, ids: dict[str, str]) -> None:
    write(root, "tests/cases/case_ids.json", json.dumps({"schema": "tfi.test-case-ids.v1", "ids": ids}))


def catalog_of(root: Path):
    cases, errors = CatalogRepository(root=root).projection()
    return {case.key: case for case in cases}, errors


def test_docstrings_give_the_title_description_and_classification(tmp_path):
    """函数 docstring 给出标题、说明和分类，模块 docstring 给出默认模块和设计引用"""
    write(tmp_path, "tests/cases/test_sample.py", '''"""Sample tests.

:module: workbench.chat
:design: decision_aaaaaaaaaaaa
"""


def test_plain_name():
    pass


def test_documented():
    """输入历史只取自己发的消息

    共享会话里按上箭头
    只取回自己的输入。

    :importance: P1
    :design: decision_bbbbbbbbbbbb
    :design-point: decision_bbbbbbbbbbbb#point
    :risk: no-external-side-effects
    """


class TestGroup:
    def test_method(self):
        """:module: lineage.hatg"""
''')
    cases, errors = catalog_of(tmp_path)
    assert errors == []
    plain = cases["tests/cases/test_sample.py::test_plain_name"]
    assert (plain.title, plain.description, plain.module, plain.design_refs) == (
        "Plain name", "", "workbench.chat", ("decision_aaaaaaaaaaaa",))
    assert plain.id == derived_case_id(plain.key)
    assert plain.implementations[0].id == implementation_id(plain.key)
    documented = cases["tests/cases/test_sample.py::test_documented"]
    assert (documented.title, documented.description) == ("输入历史只取自己发的消息", "共享会话里按上箭头只取回自己的输入。")
    assert documented.importance == "P1"
    assert documented.design_refs == ("decision_aaaaaaaaaaaa", "decision_bbbbbbbbbbbb")
    assert documented.is_design_point and documented.design_point_ref == "decision_bbbbbbbbbbbb#point"
    assert documented.implementations[0].risk == "no-external-side-effects"
    method = cases["tests/cases/test_sample.py::TestGroup::test_method"]
    assert (method.title, method.module) == ("Method", "lineage.hatg")


def test_a_case_belongs_to_the_features_it_imports_or_names(tmp_path):
    """用例的功能取自测试文件导入的功能模块，加上 feature 字段点名的功能；都没有的是核心用例，可按功能筛选"""
    write(tmp_path, "tests/cases/test_features.py", '''"""Feature tests."""
from management_portal import paper_backend
from management_portal.http_wall import routes


def test_imports():
    import management_portal.test_framework.catalog


def test_named():
    """:feature: notice_board"""


def test_unknown():
    """:feature: nowhere"""
''')
    write(tmp_path, "tests/cases/test_core.py", '"""Core tests."""\n\n\ndef test_core():\n    pass\n')
    cases, errors = catalog_of(tmp_path)
    assert [item["error"] for item in errors] == ["tests/cases/test_features.py::test_unknown：未知功能：'nowhere'"]
    assert cases["tests/cases/test_features.py::test_imports"].features == ("paper", "wall", "test_framework")
    assert cases["tests/cases/test_features.py::test_named"].features == ("paper", "wall", "test_framework", "notice_board")
    assert cases["tests/cases/test_core.py::test_core"].features == ()
    repository = CatalogRepository(root=tmp_path)
    assert [case.key for case in repository.query({"features": ["core"]})] == ["tests/cases/test_core.py::test_core"]
    assert {case.key for case in repository.query({"features": ["notice_board"]})} == {"tests/cases/test_features.py::test_named"}
    with pytest.raises(CatalogError):
        repository.query({"features": ["nowhere"]})


def test_browser_specs_are_not_discovered(tmp_path):
    """浏览器测试目录不再进入测试目录。"""
    write(tmp_path, "tests/cases/ui/sample.spec.js", 'test("browser", async () => {});')
    cases, errors = catalog_of(tmp_path)
    assert errors == []
    assert cases == {}


def test_a_pinned_id_stays_with_its_test_and_follows_it_to_another_file(tmp_path):
    """固定的用例 ID 随测试移到其他文件，已删除测试的 ID 被清理"""
    write(tmp_path, "tests/cases/test_new_home.py", "def test_moved():\n    pass\n\n\ndef test_other():\n    pass\n")
    pin(tmp_path, {
        "tests/cases/test_old_home.py::test_moved": "test_aaaaaaaaaaaa",
        "tests/cases/test_new_home.py::test_other": "test_bbbbbbbbbbbb",
        "tests/cases/test_gone.py::test_deleted": "test_cccccccccccc",
    })
    cases, errors = catalog_of(tmp_path)
    assert errors == []
    assert cases["tests/cases/test_new_home.py::test_moved"].id == "test_aaaaaaaaaaaa"
    assert cases["tests/cases/test_new_home.py::test_other"].id == "test_bbbbbbbbbbbb"
    result = sync_catalog.check(tmp_path)
    assert result["moved_ids"] == {"tests/cases/test_new_home.py::test_moved": "test_aaaaaaaaaaaa"}
    assert result["orphan_ids"] == {"tests/cases/test_gone.py::test_deleted": "test_cccccccccccc"}
    sync_catalog.apply(result, tmp_path)
    assert json.loads((tmp_path / "tests/cases/case_ids.json").read_text())["ids"] == {
        "tests/cases/test_new_home.py::test_moved": "test_aaaaaaaaaaaa",
        "tests/cases/test_new_home.py::test_other": "test_bbbbbbbbbbbb",
    }


def test_entrypoints_pinned_to_one_id_are_one_case(tmp_path):
    """固定到同一 ID 的多个 entrypoint 合并为一个用例的多个实现"""
    write(tmp_path, "tests/cases/test_pair.py", 'def test_a():\n    """一对实现"""\n\n\ndef test_b():\n    pass\n')
    pin(tmp_path, {"tests/cases/test_pair.py::test_a": "test_dddddddddddd",
                   "tests/cases/test_pair.py::test_b": "test_dddddddddddd"})
    cases, errors = catalog_of(tmp_path)
    assert errors == []
    assert list(cases) == ["tests/cases/test_pair.py::test_a"]
    case = cases["tests/cases/test_pair.py::test_a"]
    assert case.title == "一对实现"
    assert [item.entrypoint for item in case.implementations] == [
        "tests/cases/test_pair.py::test_a", "tests/cases/test_pair.py::test_b"]


def test_a_rejected_field_is_reported_and_the_test_keeps_its_place(tmp_path):
    """docstring 字段不合法时报告错误，测试仍以默认模块和重要度留在目录中"""
    write(tmp_path, "tests/cases/test_odd.py", 'def test_odd():\n    """Odd\n\n    :module: no.such\n    :importance: P9\n    :colour: red\n    """\n')
    write(tmp_path, "tests/cases/test_broken.py", "def test_broken(:\n")
    cases, errors = catalog_of(tmp_path)
    odd = cases["tests/cases/test_odd.py::test_odd"]
    assert (odd.title, odd.module, odd.importance) == ("Odd", "platform.core", "P2")
    messages = " ".join(error["error"] for error in errors if error["resource_id"] == odd.id)
    assert "no.such" in messages and "P9" in messages and "colour" in messages
    assert any(error["resource_id"] == "tests/cases/test_broken.py" for error in errors)
    with pytest.raises(CatalogError):
        CatalogRepository(root=tmp_path).all()
    assert sync_catalog.check(tmp_path)["errors"]


def test_untitled_tests_and_stale_design_points_are_reported_without_failing(tmp_path, monkeypatch, capsys):
    """缺中文标题的测试和失效的设计点绑定只被报告，不使检查失败"""
    projects = tmp_path / "projects"
    write(projects, "Demo/knowledge_base/decision/20261018__decision__Demo__decision_aaaaaaaaaaaa.md",
          '## 设计点\n\n<!-- wall: {"key": "kept"} -->\n- **改写后仍保留 key 的点。** 正文。\n')
    write(tmp_path, "tests/cases/test_sample.py", '''def test_bare():
    pass


def test_kept():
    """绑定到仍然存在的点

    :design-point: decision_aaaaaaaaaaaa#kept
    """


def test_reworded():
    """绑定到已被改写的点

    :design-point: decision_aaaaaaaaaaaa#0123456789abcdef
    """


def test_missing():
    """绑定到找不到的文档

    :design-point: decision_bbbbbbbbbbbb#0123456789abcdef
    """


def test_unreadable():
    """绑定到存在但读不了的文档

    :design-point: decision_cccccccccccc#0123456789abcdef
    """
''')
    (projects / "Demo/knowledge_base/decision/20261018__decision__Odd__decision_cccccccccccc.md").mkdir()
    result = sync_catalog.check(tmp_path, projects)
    assert result["errors"] == []
    assert result["untitled"] == ["tests/cases/test_sample.py::test_bare"]
    assert list(result["stale_points"].values()) == ["decision_aaaaaaaaaaaa#0123456789abcdef"]
    assert sorted(result["unchecked_points"].values()) == [
        "decision_bbbbbbbbbbbb#0123456789abcdef", "decision_cccccccccccc#0123456789abcdef"]
    monkeypatch.setattr(sync_catalog, "check", lambda: result)
    assert sync_catalog.main([]) == 0
    output = capsys.readouterr().out
    assert "untitled tests/cases/test_sample.py::test_bare" in output
    assert "stale design point" in output and "decision_aaaaaaaaaaaa#0123456789abcdef" in output


def test_the_repository_catalog_has_no_errors_and_its_ids_resolve():
    """仓库测试目录没有错误，case_ids.json 中的 ID 都对应到现有用例"""
    result = sync_catalog.check()
    assert result["errors"] == []
    cases, _errors = CatalogRepository().projection()
    ids = {case.id for case in cases}
    assert set(result["case_ids"].values()) <= ids
