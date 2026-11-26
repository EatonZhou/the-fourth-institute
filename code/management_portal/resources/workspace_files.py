"""Lineage Workspace file policy.

This module owns the hidden-path rule, file classification and the copy
limits of Handoff preparation. Graph and Program state are intentionally
absent.
"""
from __future__ import annotations

from pathlib import Path


MAX_WORKSPACE_COPY_FILES = 60_000

MAX_WORKSPACE_COPY_BYTES = 2 * 1024 * 1024 * 1024

TEXT_FILE_SUFFIXES = {
    ".c", ".cc", ".conf", ".cpp", ".css", ".csv", ".env.example", ".h", ".hpp",
    ".html", ".ini", ".java", ".js", ".jsx", ".log", ".md", ".markdown", ".mdx",
    ".py", ".rs", ".sh", ".sql", ".tex", ".toml", ".ts", ".tsx", ".txt",
    ".xml", ".yaml", ".yml",
}

IMAGE_SUFFIXES = {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}


def _hidden_workspace_path(rel_path: str) -> bool:
    parts = [part.casefold() for part in Path(rel_path).parts]
    if any(part.startswith(".") for part in parts):
        return True
    sensitive = {"secrets", "credentials", "auth", "private_keys", "node_modules", "__pycache__"}
    return any(part in sensitive for part in parts)


def workspace_file_kind(name: str) -> str:
    """Preview kind of a regular file, from its name."""
    suffix = Path(name).suffix.casefold()
    if suffix in {".md", ".markdown", ".mdx"}:
        return "markdown"
    if suffix == ".json":
        return "json"
    if suffix == ".jsonl":
        return "jsonl"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix == ".pdf":
        return "pdf"
    if suffix in TEXT_FILE_SUFFIXES:
        return "text"
    return "binary"
