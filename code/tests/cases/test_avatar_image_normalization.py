"""头像按比例适配显示尺寸，用户无需手工裁改源图宽高。

:design: decision_5bc3fdfc7eec
:importance: P1
"""
import base64
import io

import pytest
from PIL import Image

from management_portal.avatar_store import normalized_image


def encoded_image(size):
    stream = io.BytesIO()
    Image.new('RGBA', size, (20, 60, 100, 128)).save(stream, 'PNG')
    return base64.b64encode(stream.getvalue()).decode()


@pytest.mark.parametrize(('size', 'expected'), [
    ((1, 1), (1, 1)),
    ((15, 16), (15, 16)),
    ((2049, 16), (256, 2)),
    ((3024, 4032), (192, 256)),
])
def test_image_dimensions_are_adapted_without_manual_resize(size, expected):
    with Image.open(io.BytesIO(normalized_image(encoded_image(size)))) as image:
        assert image.format == 'PNG'
        assert image.size == expected
        assert image.mode == 'RGBA'
        assert image.getpixel((0, 0))[3] == 128
        assert not image.info


def test_decoder_resource_protection_remains_active(monkeypatch):
    encoded = encoded_image((64, 64))
    monkeypatch.setattr(Image, 'MAX_IMAGE_PIXELS', 3000)
    with pytest.raises(ValueError, match='Invalid avatar image'):
        normalized_image(encoded)
