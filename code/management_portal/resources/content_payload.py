from __future__ import annotations

import base64
import mimetypes
from collections.abc import Mapping
from typing import Any


def resolved_media_type(
    *metadata_sources: Mapping[str, Any] | None,
    fallback: str = "",
) -> str:
    for metadata in metadata_sources:
        if not isinstance(metadata, Mapping):
            continue
        for key in ("media_type", "mime_type", "content_type"):
            media_type = str(metadata.get(key) or "").partition(";")[0].strip().lower()
            if "/" in media_type and "\r" not in media_type and "\n" not in media_type:
                return media_type
    for metadata in metadata_sources:
        if not isinstance(metadata, Mapping):
            continue
        filename = str(metadata.get("filename") or metadata.get("resolved_path") or "").strip()
        if filename:
            guessed = mimetypes.guess_type(filename)[0]
            if guessed:
                return guessed.lower()
    return str(fallback or "").strip().lower()


def content_payload(address: str, content: bytes, content_type: str, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    media_type = content_type.partition(";")[0].strip()
    text_content = media_type.startswith("text/") or media_type in {
        "application/json", "application/toml", "application/xml", "application/x-yaml",
    }
    return {
        "address": address,
        "content_type": content_type,
        "encoding": "utf-8" if text_content else "base64",
        "content": content.decode("utf-8", errors="replace") if text_content else base64.b64encode(content).decode("ascii"),
        "metadata": dict(metadata or {}),
    }
