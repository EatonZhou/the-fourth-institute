import hashlib
from pathlib import Path
import pytest

from scripts.build_explanation_package import build


def test_pins_actual_bytes_and_omits_unselected_modules(tmp_path):
    (tmp_path / 'interaction.html').write_text('<button>比较</button>', encoding='utf-8')
    result = build(tmp_path, 'workspace_0123456789ab/file/lesson', title='解释', interaction='interaction.html')
    assert result['interaction']['sha256'] == hashlib.sha256((tmp_path / 'interaction.html').read_bytes()).hexdigest()
    assert result['interaction']['address'] == 'workspace_0123456789ab/file/lesson/interaction.html'
    assert 'video' not in result and 'video_plan' not in result


def test_cannot_pin_outside_package(tmp_path):
    package = tmp_path / 'package'
    package.mkdir()
    (tmp_path / 'outside.html').write_text('private')
    (package / 'link.html').symlink_to(tmp_path / 'outside.html')
    for filename in ('../outside.html', 'link.html', str(tmp_path / 'outside.html')):
        with pytest.raises(ValueError):
            build(package, 'workspace_0123456789ab/file/lesson', title='解释', interaction=filename)
