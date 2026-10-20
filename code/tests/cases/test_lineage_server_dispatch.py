"""SocietyService reaches its domain modules by name; every name it reads must exist.

``server._domain_module("resident")`` returns a module at run time, so a name
the server reads from it is invisible to a search for callers: removing it
breaks the service only when that path runs.

:importance: P1
"""
from __future__ import annotations

import ast
import inspect

from management_portal.society_service import server


def _domain_reads() -> set[tuple[str, str]]:
    tree = ast.parse(inspect.getsource(server))
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and _domain_call(node.value)):
            bound[node.targets[0].id] = _domain_call(node.value)
    reads = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        domain = _domain_call(node.value) or (bound.get(node.value.id) if isinstance(node.value, ast.Name) else "")
        if domain:
            reads.add((domain, node.attr))
    return reads


def _domain_call(node: ast.AST) -> str:
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_domain_module"
            and len(node.args) == 1 and isinstance(node.args[0], ast.Constant)):
        return str(node.args[0].value)
    return ""


def test_every_name_the_server_reads_from_a_domain_module_exists() -> None:
    """SocietyService 按名称读取的领域模块成员都存在

    server.py 通过 _domain_module("resident") 等在运行时取得领域模块，再读取其中的函数；这些读取在搜索调用者时看不到。测试解析 server.py，检查每个被读取的名称都存在，防止清理时删掉仍在线上使用的再导出。
    """
    reads = _domain_reads()
    assert {domain for domain, _ in reads} >= {"lineage", "resident"}
    missing = sorted(f"{domain}.{name}" for domain, name in reads if not hasattr(server._domain_module(domain), name))
    assert not missing, missing
