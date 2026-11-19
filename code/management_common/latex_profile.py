"""TeX build requirements checked against the current host toolchain."""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any
from management_common.text_values import stripped_text
from management_common import latex_source, latex_toolchain

def _font_name_matches(requested: str, resolved: str) -> bool:
    normalize = lambda value: re.sub(r"[^a-z0-9]+", "", value.casefold())
    target = normalize(requested)
    return bool(target) and any(
        normalize(candidate) == target for candidate in resolved.split(",")
    )

def _latex_dependency_files(root: Path, source: Path) -> list[Path]:
    pending = [source]
    visited: set[Path] = set()
    while pending and len(visited) < 512:
        current = pending.pop()
        if current in visited or not current.is_file():
            continue
        visited.add(current)
        try:
            text = current.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in re.finditer(
            r"\\(?:input|include)\s*\{\s*([^{}]+?)\s*\}", text
        ):
            raw = match.group(1).strip()
            if not raw or "\\" in raw or "#" in raw:
                continue
            candidate = (current.parent / raw).with_suffix(Path(raw).suffix or ".tex")
            candidate = candidate.resolve(strict=False)
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if candidate.is_file() and candidate not in visited:
                pending.append(candidate)
    return sorted(visited, key=lambda item: str(item).casefold())

def inspect_build_profile(
    path: str | Path,
    *,
    primary_file: str = "",
    compiler: str = "",
    require_toolchain: bool = True,
    toolchain=latex_toolchain,
) -> dict[str, Any]:
    root = Path(path).expanduser().resolve(strict=False)
    if not root.is_dir():
        raise FileNotFoundError(f"article directory not found: {root}")
    candidates = latex_source.tex_candidates(root)
    selected = stripped_text(primary_file)
    if not selected and candidates:
        selected = str(candidates[0]["path"])
    selected_candidate = next(
        (item for item in candidates if item["path"] == selected), None
    )
    standalone = [item for item in candidates if item["standalone"]]
    issues: list[dict[str, Any]] = []

    def issue(
        severity: str,
        category: str,
        message: str,
        *,
        dependency: str = "",
        file_name: str = "",
    ) -> None:
        if not require_toolchain and category in {"environment", "dependency", "font"}:
            severity = "warning"
        issues.append(
            {
                "severity": severity,
                "category": category,
                "message": message,
                "dependency": dependency,
                "file": file_name,
            }
        )

    if not candidates:
        issue("error", "configuration", "目录中没有可编译的 .tex 文档")
    elif not selected_candidate:
        issue("error", "configuration", f"主文档不存在或不在候选列表中：{selected}")
    elif not selected_candidate["standalone"]:
        issue("error", "configuration", f"主文档缺少 \\documentclass：{selected}", file_name=selected)
    if not primary_file and len(standalone) > 1:
        issue(
            "warning",
            "configuration",
            f"检测到 {len(standalone)} 个可独立编译的主文档，请确认主文档",
        )

    selected_compiler = stripped_text(compiler).casefold()
    if not selected_compiler:
        selected_compiler = (
            str(selected_candidate.get("compiler_hint") or "")
            if selected_candidate
            else ""
        ) or "pdflatex"
    if selected_compiler not in toolchain.SUPPORTED_LATEX_COMPILERS:
        issue("error", "configuration", f"不支持的 LaTeX 编译器：{selected_compiler}")
    elif not toolchain._tex_which(selected_compiler):
        issue("error", "environment", f"缺少 TeX 编译器 {selected_compiler}；请自行安装并加入 Portal 进程的 PATH 后再编译")
    if not toolchain._tex_which("latexmk"):
        issue("error", "environment", "缺少 latexmk；请自行安装并加入 Portal 进程的 PATH 后再编译")

    dependencies: list[dict[str, str]] = []
    if selected_candidate and selected_candidate["standalone"]:
        source = root / selected
        seen_dependencies: set[tuple[str, str]] = set()
        for dependency_file in _latex_dependency_files(root, source):
            try:
                text = dependency_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            relative = dependency_file.relative_to(root).as_posix()
            declarations = [
                ("document-class", match.group(1), ".cls")
                for match in re.finditer(
                    r"\\documentclass(?:\s*\[[^\]]*\])?\s*\{\s*([^{}]+?)\s*\}",
                    text,
                )
            ]
            declarations.extend(
                ("bibliography-style", match.group(1).strip(), ".bst")
                for match in re.finditer(r"\\bibliographystyle\s*\{\s*([^{}]+?)\s*\}", text)
            )
            declarations.extend(
                ("latex-package", name.strip(), ".sty")
                for match in re.finditer(
                    r"\\(?:usepackage|RequirePackage)(?:\s*\[[^\]]*\])?\s*\{\s*([^{}]+?)\s*\}",
                    text,
                )
                for name in match.group(1).split(",")
                if name.strip()
            )
            for kind, name, suffix in declarations:
                key = (kind, name)
                if key in seen_dependencies or not re.fullmatch(r"[A-Za-z0-9_.+@/-]+", name):
                    continue
                seen_dependencies.add(key)
                target_name = name if name.casefold().endswith(suffix) else f"{name}{suffix}"
                local_matches = [root / target_name, dependency_file.parent / target_name]
                resolved = next((str(item) for item in local_matches if item.is_file()), "")
                resolved = resolved or toolchain._kpsewhich_file(target_name)
                dependencies.append(
                    {"kind": kind, "name": name, "file": target_name, "resolved": resolved}
                )
                if not resolved:
                    issue(
                        "error",
                        "dependency",
                        f"缺少 {target_name}（由 {relative} 引用）",
                        dependency=name,
                        file_name=relative,
                    )
            font_matches = [
                match.group(1).strip()
                for match in re.finditer(
                    r"\\(?:set(?:main|sans|mono)font|setCJK(?:main|sans|mono)font)"
                    r"(?:\s*\[[^\]]*\])?\s*\{\s*([^{}]+?)\s*\}",
                    text,
                )
            ]
            font_matches.extend(
                match.group(1).strip()
                for match in re.finditer(
                    r"\\newfontfamily\s*\\[A-Za-z@]+(?:\s*\[[^\]]*\])?"
                    r"\s*\{\s*([^{}]+?)\s*\}",
                    text,
                )
            )
            for font_name in font_matches:
                key = ("font", font_name)
                if key in seen_dependencies:
                    continue
                seen_dependencies.add(key)
                resolved_family = toolchain._fontconfig_family(font_name)
                resolved = resolved_family if _font_name_matches(font_name, resolved_family) else ""
                dependencies.append(
                    {"kind": "font", "name": font_name, "file": "", "resolved": resolved}
                )
                if not resolved:
                    issue(
                        "error",
                        "font",
                        f"缺少字体 {font_name}（由 {relative} 引用）",
                        dependency=font_name,
                        file_name=relative,
                    )

    environment = toolchain.latex_environment()
    blockers = sum(1 for item in issues if item["severity"] == "error")
    return {
        "schema": "tfi.article-build-preflight.v1",
        "path": str(root),
        "status": "blocked" if blockers else ("warning" if issues else "ready"),
        "requires_selection": not bool(primary_file) and len(standalone) > 1,
        "primary_file": selected,
        "compiler": selected_compiler,
        "candidates": candidates,
        "dependencies": dependencies,
        "issues": issues,
        "counts": {
            "error": blockers,
            "warning": sum(1 for item in issues if item["severity"] == "warning"),
        },
        "environment": environment,
    }

