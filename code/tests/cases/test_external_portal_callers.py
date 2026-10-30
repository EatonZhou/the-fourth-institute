"""Bundled Skill scripts call Portal functions directly; those functions must exist.

``core_skills/tfi-unified-id/scripts/resource_id.py`` imports Portal
modules and calls scan and registration functions outside the server packages.
This test reads every bundled Skill script and checks that each Portal
name it imports, each attribute it reads from a Portal module, and each
method it calls on a Portal class or on an instance of one, exists.

:module: resource.identity
:importance: P1
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parents[2] / "core_skills"
PORTAL_PACKAGES = ("management_portal", "management_common", "knowledge_board_lan", "skill_board_lan")
# resource_id.py binds the modules _portal_backends() and _device_backend() return to these names.
RETURNED_MODULES = {
    "repo_backend": "management_portal.resource_identity_backend",
    "article_backend": "management_portal.article_registry",
    "paper_resources": "management_portal.paper_resources",
    "search_backend": "management_portal.search_identity_backend",
    "_device_backend": "management_portal.device_identity_backend",
}


def _portal_module(name: str) -> bool:
    return name.split(".")[0] in PORTAL_PACKAGES


def _uses(script: Path) -> set[tuple[str, str]]:
    """(module, attribute) pairs the script needs from Portal."""

    tree = ast.parse(script.read_text(encoding="utf-8"))
    modules = dict(RETURNED_MODULES)
    names: dict[str, tuple[str, str]] = {}  # a name imported from Portal -> (module, dotted path)
    needed: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and _portal_module(node.module):
            for alias in node.names:
                submodule = f"{node.module}.{alias.name}"
                if importlib.util.find_spec(submodule.split(".")[0]) and _is_module(submodule):
                    modules[alias.asname or alias.name] = submodule
                else:
                    needed.add((node.module, alias.name))
                    names[alias.asname or alias.name] = (node.module, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _portal_module(alias.name) and alias.asname:
                    modules[alias.asname] = alias.name
    def reference(node: ast.AST) -> tuple[str, str] | None:
        """The Portal (module, dotted path) an expression names, if any."""
        if isinstance(node, ast.Name):
            return names.get(node.id)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            module = modules.get(node.value.id) or modules.get(node.value.id.lstrip("_"))
            return (module, node.attr) if module else None
        return None

    # An instance of a Portal class: ``store = IndexStore(...)``.
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Call) and reference(node.value.func)):
            names[node.targets[0].id] = reference(node.value.func)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        owner = node.value
        if isinstance(owner, ast.Call):
            owner = owner.func
        found = reference(owner) if not isinstance(owner, ast.Name) or owner.id in names else None
        if found:
            needed.add((found[0], f"{found[1]}.{node.attr}"))
        elif isinstance(owner, ast.Name):
            name = owner.id if owner.id in modules else owner.id.lstrip("_")
            if name in modules:
                needed.add((modules[name], node.attr))
    return needed


def _exists(module: str, path: str) -> bool:
    target = importlib.import_module(module)
    for part in path.split("."):
        if not isinstance(target, type) and inspect.isroutine(target):
            return True  # an attribute of what a function returns: its type is unknown here
        if not hasattr(target, part):
            return False
        target = getattr(target, part)
    return True


def _is_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


SCRIPTS = sorted(SKILLS.glob("*/scripts/*.py"))
assert SCRIPTS, "Bundled Skill scripts must be present in the source checkout"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: f"{path.parent.parent.name}/{path.name}")
def test_every_portal_function_a_shared_skill_calls_exists(script: Path) -> None:
    """仓库内 Skill 脚本调用的 Portal 函数都存在

    读取 core_skills 下各脚本，检查它们导入的 Portal 名称、以及从 Portal 模块读取的属性（例如资源 ID 扫描与登记函数）在当前代码中都存在；保护服务端包之外的脚本调用，不依赖本站安装的 Skill。
    """
    missing = [f"{module}.{path}" for module, path in sorted(_uses(script)) if not _exists(module, path)]
    assert not missing, f"{script} calls Portal names that no longer exist: {missing}"
