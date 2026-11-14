"""HATG toolbox ownership, pickup, editing and preserved drag paths.

:module: lineage.hatg
:importance: P1
:design: decision_13725a7900f6
"""
from pathlib import Path
import json
import os
import subprocess

import pytest


def test_hatg_toolbox():
    """工具箱按创建者分组展示，拿起、取消、编辑和原语拖入入口保持一致。"""
    result = subprocess.run(
        ['node', '--test', str(Path(__file__).with_name('javascript') / 'hatg_toolbox.cjs')],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_template_registry_and_created_template_visibility(tmp_path):
    """真实收藏解析不接受模板；工具箱用实际新建模板元数据展示本人内容，不访问收藏。"""
    from management_portal import resource_favorites
    from management_portal.society_service import templates

    before = templates.list_templates()
    prototype = next(item for item in before if item['kind'] == 'structure')
    content = {key: prototype[key] for key in (
        'kind', 'category', 'description', 'targets', 'parameters', 'body',
    )}
    created = templates.create({**content, 'title': 'My duty'}, 'alice')
    other = templates.create({**content, 'title': 'Other duty'}, 'bob')
    assert created['created_by'] == 'alice'
    with pytest.raises(ValueError, match='unregistered resource type: template'):
        resource_favorites.canonical_address(created['template_id'])
    payload_file = tmp_path / 'templates.json'
    payload_file.write_text(json.dumps({
        'before': before, 'created': created, 'other': other,
        'after': templates.list_templates(),
    }), encoding='utf-8')
    result = subprocess.run(
        ['node', '--test', '--test-name-pattern=real registry payload',
         str(Path(__file__).with_name('javascript') / 'hatg_toolbox.cjs')],
        env={**os.environ, 'TFI_TOOLBOX_REAL_CASE': '1',
             'TFI_TOOLBOX_REAL_DATA_PATH': str(payload_file)},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
