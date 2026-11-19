"""The dependency doctor must enforce the requirements used by installation."""
import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("version, expected", [("1.4.3", 0), ("2.0.0", 1), (None, 1)])
def test_test_profile_doctor_enforces_declared_bibtex_upper_bound(tmp_path, monkeypatch, capsys, version, expected):
    script = runpy.run_path(str(ROOT / "scripts/bootstrap_portal"))
    python = tmp_path / "bin/python"
    python.parent.mkdir()
    python.touch()
    requirements = script["declared_requirements"](ROOT / "requirements-test.txt")
    requirements.update(script["declared_requirements"](ROOT / "requirements.txt"))
    installed = {name: "999.0" for name in requirements}
    if version is None:
        installed.pop("bibtexparser")
    else:
        installed["bibtexparser"] = version
    namespace = script["doctor"].__globals__
    monkeypatch.setitem(namespace, "installed_versions", lambda _: installed)
    monkeypatch.setitem(namespace, "interpreter_version", lambda _: (3, 12, 9))
    monkeypatch.setitem(namespace, "node_major", lambda: 22)
    args = SimpleNamespace(profiles=["test"], venv=tmp_path, json=True)
    assert script["doctor"](args) == expected
    checks = json.loads(capsys.readouterr().out)
    if isinstance(checks, dict):
        checks = checks["checks"]
    bibtex = next(item for item in checks if item["name"] == "test: bibtexparser")
    assert bibtex["ok"] is (expected == 0)
    assert "<2" in bibtex["detail"]
    assert any(item["name"] == "JavaScript test tooling: @babel/parser" for item in checks)


def test_nested_requirement_ranges_keep_upper_and_exclusion_constraints(tmp_path):
    script = runpy.run_path(str(ROOT / "scripts/bootstrap_portal"))
    (tmp_path / "nested.txt").write_text("Example_Package>=1,<2,!=1.5\n")
    requirement = tmp_path / "requirements.txt"
    requirement.write_text("-r nested.txt\nplain-package\n")
    declared = script["declared_requirements"](requirement)
    from packaging.specifiers import SpecifierSet
    limits = SpecifierSet(declared["example-package"])
    assert "1.4" in limits
    assert "1.5" not in limits
    assert "2.0" not in limits
    assert not declared["plain-package"]
