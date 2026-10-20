"""Every name a Portal function reads resolves to something its module defines.

A function that calls a name its module never imports or defines fails only
when that line runs, with ``NameError``.  Tests that replace the function, or
never reach the branch, pass anyway: ``execution_client.reset_provider_context``
called ``get_agent_data_store`` without importing it, and clearing a
Resident's context on Session restart failed while the restart tests, which
stub that function, passed.

The check reads each source file's symbol table.  A name a function reads as
a global must be assigned or imported at module level, or be a builtin.
Annotations are not checked: with ``from __future__ import annotations`` they
are never evaluated.  Modules with ``import *`` are skipped, since their
globals cannot be known without importing.

:module: spec.design-governance
:importance: P1
"""
from __future__ import annotations

import builtins
import symtable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ("management_portal", "management_common", "knowledge_board_lan", "skill_board_lan", "scripts", "tests")
MODULE_NAMES = {"__file__", "__name__", "__doc__", "__spec__", "__loader__", "__package__", "__builtins__", "__path__", "__annotations__"}
KNOWN = set(dir(builtins)) | MODULE_NAMES


def _python_files() -> list[Path]:
    return sorted(
        path for source in SOURCES for path in (ROOT / source).rglob("*.py")
        if not {"node_modules", "__pycache__", "vendor"} & set(path.parts)
    )


def _undefined_names(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    if "import *" in source:
        return []
    module = symtable.symtable(source, str(path), "exec")
    defined = {symbol.get_name() for symbol in module.get_symbols()
               if symbol.is_assigned() or symbol.is_imported() or symbol.is_namespace()} | KNOWN
    found = [f"<module> reads {symbol.get_name()}" for symbol in module.get_symbols()
             if symbol.is_referenced() and symbol.get_name() not in defined]

    def visit(table: symtable.SymbolTable) -> None:
        for symbol in table.get_symbols():
            if (symbol.is_global() and not symbol.is_declared_global() and symbol.is_referenced()
                    and symbol.get_name() not in defined):
                found.append(f"{table.get_name()} reads {symbol.get_name()}")
        for child in table.get_children():
            visit(child)

    for child in module.get_children():
        visit(child)
    return found


def test_every_global_a_function_reads_is_defined_in_its_module() -> None:
    """Portal 函数读取的全局名称都在所在模块中定义

    读取 Portal、共享包、脚本和测试的每个 Python 文件的符号表，检查函数读取的每个全局名称都在模块顶层赋值、导入或是内建名称。漏掉的 import 只在那一行运行时才报 NameError，替换了该函数或没走到该分支的测试照样通过；例如 Session 重启清空上下文曾因 execution_client 没有导入 get_agent_data_store 而失败。
    """
    undefined = [
        f"{path.relative_to(ROOT)}: {problem}"
        for path in _python_files() for problem in _undefined_names(path)
    ]
    assert undefined == []
