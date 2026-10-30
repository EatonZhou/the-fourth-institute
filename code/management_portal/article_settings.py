"""An article's build settings: compiler, primary file, compile mode and error handling."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from management_common.clock import utc_now_iso
from management_common.text_values import stripped_text
from management_portal import (
    article_build_store,
    article_discovery,
    article_registry,
    article_tex,
    article_toolchain,
)


ARTICLE_COMPILE_MODES = {"normal", "draft"}
ARTICLE_ERROR_HANDLING = {"continue", "stop"}
ARTICLE_BUILD_PROFILE_SCHEMA = article_build_store.SETTINGS_SCHEMA


def load_article_settings(
    article_id: str,
    *,
    build_root: str | Path | None = None,
) -> dict[str, Any]:
    record, root_item, article_root = article_registry._article_source_context(
        article_id
    )
    default_primary_file = stripped_text(
        root_item.get("primary_file") or record.get("primary_file")
    )
    try:
        default_primary_path = article_root / article_tex._safe_relative_path(default_primary_file)
    except ValueError:
        default_primary_path = article_root
    defaults = {
        "compiler": article_discovery._tex_program_hint(default_primary_path) or "pdflatex",
        "primary_file": default_primary_file,
        "use_project_latexmkrc": False,
        "shell_escape": False,
        "auto_compile": False,
        "compile_mode": "normal",
        "syntax_check": True,
        "error_handling": "continue",
    }
    stored = article_build_store.settings(article_id) or {}
    compiler = stripped_text(stored.get("compiler") or defaults["compiler"]).casefold()
    if compiler not in article_toolchain.SUPPORTED_LATEX_COMPILERS:
        compiler = defaults["compiler"]
    try:
        primary_file = article_tex._safe_relative_path(
            stripped_text(stored.get("primary_file") or defaults["primary_file"])
        )
    except ValueError:
        primary_file = article_tex._safe_relative_path(defaults["primary_file"])
    compile_mode = stripped_text(
        stored.get("compile_mode") or defaults["compile_mode"]
    ).casefold()
    if compile_mode not in ARTICLE_COMPILE_MODES:
        compile_mode = defaults["compile_mode"]
    error_handling = stripped_text(
        stored.get("error_handling") or defaults["error_handling"]
    ).casefold()
    if error_handling not in ARTICLE_ERROR_HANDLING:
        error_handling = defaults["error_handling"]
    environment = article_toolchain.latex_environment()
    primary_candidates = [
        str(item["path"])
        for item in article_discovery._article_tex_candidates(article_root)
        if item.get("standalone")
    ]
    return {
        "schema": ARTICLE_BUILD_PROFILE_SCHEMA,
        "id": article_id,
        "compiler": compiler,
        "compiler_label": article_toolchain.SUPPORTED_LATEX_COMPILERS[compiler]["label"],
        "primary_file": primary_file,
        "primary_candidates": primary_candidates,
        "use_project_latexmkrc": bool(
            stored.get("use_project_latexmkrc", defaults["use_project_latexmkrc"])
        ),
        "shell_escape": bool(stored.get("shell_escape", defaults["shell_escape"])),
        "auto_compile": bool(stored.get("auto_compile", defaults["auto_compile"])),
        "compile_mode": compile_mode,
        "syntax_check": bool(stored.get("syntax_check", defaults["syntax_check"])),
        "error_handling": error_handling,
        "tex_live_version": environment["version"],
        "latexmk": environment["latexmk"],
        "synctex_available": bool(article_toolchain._tex_which("synctex")),
        "environment_profile": environment["profile_id"],
        "environment_policy": environment["policy"],
        "available_compilers": environment["available_compilers"],
        "updated_at": stripped_text(stored.get("updated_at")),
    }


def update_article_settings(
    article_id: str,
    settings: dict[str, Any],
    *,
    build_root: str | Path | None = None,
) -> dict[str, Any]:
    if not isinstance(settings, dict):
        raise ValueError("article settings must be an object")
    current = load_article_settings(
        article_id, build_root=build_root
    )
    compiler = stripped_text(settings.get("compiler", current["compiler"])).casefold()
    if compiler not in article_toolchain.SUPPORTED_LATEX_COMPILERS:
        raise ValueError(f"unsupported LaTeX compiler: {compiler or '(empty)'}")
    primary_file = article_tex._safe_relative_path(
        stripped_text(settings.get("primary_file", current["primary_file"]))
    )
    compile_mode = stripped_text(
        settings.get("compile_mode", current["compile_mode"])
    ).casefold()
    if compile_mode not in ARTICLE_COMPILE_MODES:
        raise ValueError(f"unsupported compile mode: {compile_mode or '(empty)'}")
    error_handling = stripped_text(
        settings.get("error_handling", current["error_handling"])
    ).casefold()
    if error_handling not in ARTICLE_ERROR_HANDLING:
        raise ValueError(
            f"unsupported compile error handling: {error_handling or '(empty)'}"
        )
    auto_compile = settings.get("auto_compile", current["auto_compile"])
    syntax_check = settings.get("syntax_check", current["syntax_check"])
    use_project_latexmkrc = settings.get(
        "use_project_latexmkrc", current["use_project_latexmkrc"]
    )
    shell_escape = settings.get("shell_escape", current["shell_escape"])
    if not isinstance(auto_compile, bool):
        raise ValueError("auto_compile must be a boolean")
    if not isinstance(syntax_check, bool):
        raise ValueError("syntax_check must be a boolean")
    if not isinstance(use_project_latexmkrc, bool):
        raise ValueError("use_project_latexmkrc must be a boolean")
    if not isinstance(shell_escape, bool):
        raise ValueError("shell_escape must be a boolean")
    article_root, source = article_registry._article_target_path(
        article_id, primary_file
    )
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.suffix.casefold() != ".tex":
        raise ValueError("the main document must be a .tex file")
    if not article_discovery._is_standalone_tex_document(source):
        raise ValueError(
            f"the main document must contain \\documentclass: {source.relative_to(article_root).as_posix()}"
        )
    article_build_store.save_settings(article_id, {
            "schema": ARTICLE_BUILD_PROFILE_SCHEMA,
            "id": article_id,
            "compiler": compiler,
            "primary_file": source.relative_to(article_root).as_posix(),
            "use_project_latexmkrc": use_project_latexmkrc,
            "shell_escape": shell_escape,
            "auto_compile": auto_compile,
            "compile_mode": compile_mode,
            "syntax_check": syntax_check,
            "error_handling": error_handling,
            "updated_at": utc_now_iso(),
    })
    return load_article_settings(
        article_id, build_root=build_root
    )
