"""Article identity, settings, cache/report ownership and cancellation for TeX builds."""
from __future__ import annotations
import subprocess
from pathlib import Path
from typing import Any
from management_common import latex_build
from management_portal import article_build_profile, article_build_results, article_registry, article_settings, article_toolchain
from management_portal.resources import get_resource_service

_RUNTIME = latex_build.BuildRuntime()
_ACTIVE_ARTICLE_BUILDS_LOCK = _RUNTIME.lock
_ACTIVE_ARTICLE_BUILDS = _RUNTIME.active
_RUNNING_ARTICLE_BUILDS = _RUNTIME.running
_STOP_REQUESTED_ARTICLE_BUILDS = _RUNTIME.stop_requested
ArticleCompilationStopped = latex_build.CompilationStopped
_begin_article_build = _RUNTIME.begin
_finish_article_build = _RUNTIME.finish
_run_article_build_command = _RUNTIME.run
_terminate_article_process = latex_build.terminate_process

def stop_article_compile(article_id: str) -> dict[str, Any]:
    if not get_resource_service().is_valid_id(article_id, "article"):
        raise ValueError(f"invalid article id: {article_id}")
    with _ACTIVE_ARTICLE_BUILDS_LOCK:
        running = article_id in _RUNNING_ARTICLE_BUILDS
        if running:
            _STOP_REQUESTED_ARTICLE_BUILDS.add(article_id)
    # The build thread observes the flag and is the sole owner of tree cleanup;
    # stop and timeout must not concurrently close the same Windows Job handle.
    return {
        "id": article_id,
        "running": running,
        "stop_requested": running,
    }

def compile_article_pdf(
    article_id: str,
    *,
    build_root: str | Path | None = None,
    timeout_seconds: int = 360,
    from_scratch: bool = False,
) -> dict[str, Any]:
    """Compile the registered canonical TeX manuscript into a cached PDF."""
    build = _ArticleBuild(article_id, build_root=build_root, from_scratch=from_scratch)
    build.check_preflight()
    return build.run(timeout_seconds)

class _ArticleBuild(latex_build.LatexBuild):
    """Resolve Article identity/settings and retain the Article report contract."""

    toolchain = article_toolchain

    def __init__(self, article_id: str, *, build_root: str | Path | None, from_scratch: bool) -> None:
        self.article_id = article_id
        _record, _root_item, source_root = article_registry._article_source_context(article_id)
        super().__init__(
            runtime=_RUNTIME, build_id=article_id, source_root=source_root,
            settings=article_settings.load_article_settings(article_id, build_root=build_root),
            output=article_build_results.article_build_pdf_path(article_id, build_root=build_root),
            cache_root=article_build_results.article_build_cache_path(article_id, build_root=build_root),
            from_scratch=from_scratch,
            load_report=lambda: article_build_results.load_article_build_report(article_id, build_root=build_root),
            save_report=lambda report: article_build_results._save_article_build_report(article_id, report, build_root=build_root),
        )

    def inspect_preflight(self) -> dict[str, Any]:
        return {
            **article_build_profile.inspect_article_build_profile(
                self.source_root, primary_file=self.primary_file, compiler=self.compiler,
            ),
            "id": self.article_id,
        }

    def _compile(self, timeout_seconds):
        from management_common.file_retention import cache_file
        with cache_file(self.cache_root, group='article', owner_id=self.build_id), cache_file(self.output, group='article', owner_id=self.build_id):
            return self._compile_owned_cache(timeout_seconds)

    def run_command(self, build_id, command, *, cwd, timeout_seconds, env):
        return _run_article_build_command(build_id, command, cwd=cwd, timeout_seconds=timeout_seconds, env=env)
