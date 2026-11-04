"""Shared media runtime remains usable when optional reports are excluded."""
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_portal import media_tools


def test_explanation_imports_without_optional_report_modules():
    result = subprocess.run([sys.executable, '-c', '''
import importlib.abc, sys
class ExcludeReports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('management_portal.home_report'):
            raise ImportError('optional reports excluded: ' + fullname)
sys.meta_path.insert(0, ExcludeReports())
from management_portal import home_explanation_render, media_tools
assert home_explanation_render.capabilities is media_tools.capabilities
assert 'font' in media_tools.capabilities()
'''], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_managed_tool_environment_and_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(media_tools, 'directory', lambda: tmp_path)
    calls = []
    def invoke(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=3, stderr=b'encoder failed')
    monkeypatch.setattr(media_tools.subprocess, 'run', invoke)
    with pytest.raises(RuntimeError, match='encoder failed'):
        media_tools.run([str(tmp_path/'bin/ffmpeg'), '-version'])
    assert calls[0][1]['env']['LD_LIBRARY_PATH'] == str(tmp_path/'lib')
    assert calls[0][1]['timeout'] == 180
    with pytest.raises(RuntimeError):
        media_tools.run(['/usr/bin/ffmpeg'])
    assert 'env' not in calls[1][1]


def test_existing_tool_overrides_and_caption_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(media_tools, 'directory', lambda: tmp_path/'bundle')
    for setting in ('FFMPEG', 'FONT'):
        path = tmp_path/setting
        path.touch()
        monkeypatch.setenv('TFI_HOME_REPORT_'+setting, str(path))
    tools = media_tools.capabilities()
    assert tools['available'] and tools['missing'] == []
    assert tools['ffmpeg'] == os.environ['TFI_HOME_REPORT_FFMPEG']
    assert media_tools.stamp(3661.234) == '01:01:01.234'
    assert media_tools.directory() == tmp_path/'bundle'


def test_documented_bundle_installer_entry_point():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, str(root/'management_portal/home_report_dependencies.py'), '--help'],
                            cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert '--bundle' in result.stdout


def test_report_renderer_creates_captioned_video(tmp_path, monkeypatch):
    from PIL import ImageFont
    from management_portal import home_report_media as media, home_report_visual
    monkeypatch.setattr(media, 'capabilities', lambda: {'available': True, 'ffmpeg': '/fake/ffmpeg', 'font': 'fixture'})
    monkeypatch.setattr(ImageFont, 'truetype', lambda *args: object())
    monkeypatch.setattr(media, 'chapters', lambda report: [
        {'title': '固定事实', 'speech': '先查看来源。', 'beats': [{'speech': '先查看来源。'}]}])
    class Frame:
        def save(self, path): path.write_bytes(b'frame')
        def tobytes(self): return b'frame'
    monkeypatch.setattr(home_report_visual, 'frame', lambda *args: Frame())
    calls = []
    class Encoder:
        def __init__(self, args, **kwargs):
            calls.append(args)
            assert args[0] == '/fake/ffmpeg' and '-an' in args
            assert args.count('-i') == 1 and args[args.index('-i')+1] == 'pipe:0'
            self.stdin = SimpleNamespace(write=lambda data: None, close=lambda: None)
            Path(args[-1]).write_bytes(b'encoded-video')
        def wait(self, **kwargs): return 0
        def kill(self): pytest.fail('encoder should finish normally')
    monkeypatch.setattr(media.subprocess, 'Popen', Encoder)
    result = media.render({'version': 'fixed', 'reasoning': {'assumption': '固定', 'limit': '范围'}}, tmp_path)
    assert len(calls) == 1
    assert '无配音' in result['voice_note']
    assert '先查看来源。' in (tmp_path/'captions.vtt').read_text()
    assert (tmp_path/'video.mp4').read_bytes() == b'encoded-video'
