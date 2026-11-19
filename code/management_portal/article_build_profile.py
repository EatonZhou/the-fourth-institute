"""What building an article needs: its engine, packages, fonts and files, checked against
this host's toolchain before a build.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from management_common.latex_profile import inspect_build_profile
from management_portal import (
    article_registry,
    article_settings,
    article_toolchain,
    resource_identity_backend as repo_backend,
)


def inspect_article_build_profile(path: str | Path, *, primary_file: str = "",
                                  compiler: str = "", require_toolchain: bool = True) -> dict[str, Any]:
    result = inspect_build_profile(path, primary_file=primary_file, compiler=compiler,
                                   require_toolchain=require_toolchain, toolchain=article_toolchain)
    result["project_ids"] = repo_backend.project_ids_for_path(Path(path).expanduser().resolve(strict=False))
    return result


def article_build_preflight(
    article_id: str,
    *,
    build_root: str | Path | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _record, _root_item, article_root = article_registry._article_source_context(
        article_id
    )
    profile = settings or article_settings.load_article_settings(
        article_id, build_root=build_root
    )
    result = inspect_article_build_profile(
        article_root,
        primary_file=str(profile.get("primary_file") or ""),
        compiler=str(profile.get("compiler") or ""),
    )
    result["id"] = article_id
    return result
