"""The AgentData form of a delivered input that carries images.

AgentData keeps such an input as one user message of text and image parts,
frozen when it is delivered: later Forks read only these PostgreSQL bytes,
and each Provider translates the message into its own history.
"""
from __future__ import annotations

import base64
import mimetypes
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def input_message(prompt: str, media: Sequence[Any], *, detail: str = "") -> dict[str, Any]:
    """Freeze the input at delivery time; later Forks read only these PostgreSQL bytes."""
    parts: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    for item in media:
        if item.media_type != "image":
            raise ValueError("unsupported historical input media")
        if item.source_type == "local_path":
            mime = item.mime_type or mimetypes.guess_type(item.source)[0]
            if not mime or not mime.startswith("image/"):
                raise ValueError("historical image requires an image MIME type")
            encoded = base64.b64encode(Path(item.source).read_bytes()).decode("ascii")
            url = f"data:{mime};base64,{encoded}"
        elif item.source_type == "url":
            url = item.source
        else:
            raise ValueError("unsupported historical image source")
        part = {"type": "input_image", "image_url": url}
        if detail:
            part["detail"] = detail
        parts.append(part)
    return {"type": "message", "role": "user", "content": parts}
