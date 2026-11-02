"""Encode authorized, staged images as ACP prompt content, without fetching URLs."""
import base64
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from management_portal.execution.contracts import MAX_INPUT_MEDIA_ITEMS

# Portal transport budgets, not claims about Google's model context limits.
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_TURN_IMAGE_BYTES = 40 * 1024 * 1024
IMAGE_MIMES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}


def prompt_content(text, media):
    blocks = [{"type": "text", "text": text}]
    if len(media) > MAX_INPUT_MEDIA_ITEMS:
        raise ValueError("Antigravity accepts at most 20 images per input")
    total = 0
    for item in media:
        item.validate()
        if item.source_type != "local_path":
            raise ValueError("Antigravity images must be uploaded or staged locally before execution")
        path = Path(item.source)
        if not path.is_file():
            raise ValueError("Antigravity image requires a readable file")
        with path.open("rb") as reader:
            data = reader.read(min(MAX_IMAGE_BYTES, MAX_TURN_IMAGE_BYTES - total) + 1)
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ValueError("Antigravity image is empty or exceeds the 20 MiB transport limit")
        total += len(data)
        if total > MAX_TURN_IMAGE_BYTES:
            raise ValueError("Antigravity images exceed the 40 MiB per-turn transport limit")
        try:
            with Image.open(BytesIO(data)) as image:
                mime = IMAGE_MIMES.get(image.format)
                animated = getattr(image, "n_frames", 1) > 1
                image.verify()
        except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
            raise ValueError("Antigravity attachment is not a valid image") from exc
        if not mime or (item.mime_type and item.mime_type != mime):
            raise ValueError("Antigravity image must be PNG, JPEG, WebP or GIF with a matching MIME type")
        if mime == "image/gif":
            # ACP 1.4.5 silently skips GIF: SDK Image only accepts PNG/JPEG/WebP/BMP.
            if animated:
                raise ValueError("Antigravity does not support animated GIF; upload the required frames as PNG images")
            with Image.open(BytesIO(data)) as image:
                converted = BytesIO()
                image.convert("RGBA").save(converted, format="PNG")
            png = converted.getvalue()
            total += len(png) - len(data)
            if len(png) > MAX_IMAGE_BYTES or total > MAX_TURN_IMAGE_BYTES:
                raise ValueError("Converted GIF exceeds the Antigravity image transport limit")
            data, mime = png, "image/png"
        blocks.append({"type": "image", "mimeType": mime, "data": base64.b64encode(data).decode("ascii")})
    return blocks
