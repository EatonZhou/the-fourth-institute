from __future__ import annotations
from management_common.portal_paths import path as portal_path

import os
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError


STAGING_ROOT = portal_path("staging_root")
CHAT_IMAGE_CACHE_ROOT = (
    STAGING_ROOT / "shared" / "portal_uploads" / "resident_chat_images"
)
CHAT_ATTACHMENT_CACHE_ROOT = (
    STAGING_ROOT / "shared" / "portal_uploads" / "resident_chat_attachments"
)
CHAT_IMAGE_MAX_BYTES = int(
    os.getenv("RESIDENT_CHAT_IMAGE_MAX_BYTES", "209715200")
)
CHAT_IMAGE_MAX_PIXELS = int(
    os.getenv("RESIDENT_CHAT_IMAGE_MAX_PIXELS", "40000000")
)
CHAT_ATTACHMENT_MAX_BYTES = int(
    os.getenv("RESIDENT_CHAT_ATTACHMENT_MAX_BYTES", "209715200")
)
CHAT_IMAGE_ALLOWED_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
CHAT_IMAGE_FORMAT_TYPES = {
    "PNG": ("image/png", ".png"),
    "JPEG": ("image/jpeg", ".jpg"),
    "WEBP": ("image/webp", ".webp"),
    "GIF": ("image/gif", ".gif"),
}


def image_type(filename: str, content_type: str) -> tuple[str, str]:
    normalized = str(content_type or "").split(";", 1)[0].strip().lower()
    if normalized in CHAT_IMAGE_ALLOWED_TYPES:
        return normalized, CHAT_IMAGE_ALLOWED_TYPES[normalized]
    suffix = Path(str(filename or "")).suffix.lower()
    for allowed_type, allowed_suffix in CHAT_IMAGE_ALLOWED_TYPES.items():
        if suffix == allowed_suffix or (
            suffix == ".jpeg" and allowed_suffix == ".jpg"
        ):
            return allowed_type, allowed_suffix
    raise ValueError("只支持 PNG、JPEG、WebP 或 GIF 图片")


def validate_image_upload(data: bytes) -> tuple[str, str, int, int]:
    """Decode uploaded bytes and return their canonical image type."""
    try:
        with Image.open(BytesIO(data)) as image:
            detected = CHAT_IMAGE_FORMAT_TYPES.get(str(image.format or "").upper())
            width, height = image.size
            if not detected:
                raise ValueError("只支持 PNG、JPEG、WebP 或 GIF 图片")
            if width <= 0 or height <= 0:
                raise ValueError("图片尺寸无效")
            if width * height > CHAT_IMAGE_MAX_PIXELS:
                raise ValueError(
                    f"图片像素数不能超过 {CHAT_IMAGE_MAX_PIXELS}"
                )
            image.verify()
        with Image.open(BytesIO(data)) as image:
            image.load()
    except (
        Image.DecompressionBombError,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
    ) as exc:
        raise ValueError("上传内容不是有效图片") from exc
    return detected[0], detected[1], width, height
