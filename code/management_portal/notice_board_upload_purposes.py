"""Notice Board's image-upload limits and accepted decoded media types."""

PURPOSES = {
    "post_image": {
        "max_bytes": 12 * 1024 * 1024,
        "image_formats": ("image/png", "image/jpeg", "image/gif", "image/webp"),
    },
}
