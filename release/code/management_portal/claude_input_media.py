"""Convert authorized, staged Portal images to Claude user content blocks."""
import base64
import mimetypes
from pathlib import Path
from urllib.parse import urlsplit


IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
MAX_IMAGE_ENCODED_BYTES = 10 * 1024 * 1024
MAX_REQUEST_ENCODED_BYTES = 28 * 1024 * 1024


def content_blocks(prompt: str, attachments: object) -> list[dict]:
    if not isinstance(attachments, (list, tuple)) or len(attachments) > 20:
        raise ValueError("Claude input_media must be an array of at most 20 images")
    blocks = []
    encoded_size = 0
    for item in attachments:
        if not isinstance(item, dict) or item.get("media_type") != "image":
            raise ValueError("Claude input_media supports images only")
        source = str(item.get("source") or "")
        if item.get("source_type") == "local_path":
            path = Path(source)
            if not path.is_absolute() or not path.is_file():
                raise ValueError("Claude image requires a readable absolute file path")
            mime = str(item.get("mime_type") or mimetypes.guess_type(path.name)[0] or "").lower()
            if mime not in IMAGE_TYPES:
                raise ValueError("Claude images must be PNG, JPEG, WebP or GIF")
            with path.open("rb") as reader:
                data = reader.read(MAX_IMAGE_ENCODED_BYTES * 3 // 4 + 1)
            if not data or 4 * ((len(data) + 2) // 3) > MAX_IMAGE_ENCODED_BYTES:
                raise ValueError("Claude image exceeds the 10 MiB encoded limit or is empty")
            encoded = base64.b64encode(data).decode("ascii")
            encoded_size += len(encoded)
            if encoded_size > MAX_REQUEST_ENCODED_BYTES:
                raise ValueError("Claude image attachments exceed the per-turn size limit")
            image_source = {"type": "base64", "media_type": mime, "data": encoded}
        elif item.get("source_type") == "url":
            parsed = urlsplit(source)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
                raise ValueError("Claude image URL must use HTTP(S) without credentials")
            # Pass through to the provider; never fetch remote URLs in Portal.
            image_source = {"type": "url", "url": source}
        else:
            raise ValueError("Unsupported Claude image source type")
        blocks.append({"type": "image", "source": image_source})
    if prompt:
        blocks.append({"type": "text", "text": prompt})
    return blocks
