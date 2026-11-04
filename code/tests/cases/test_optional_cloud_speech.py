"""Optional speech must not be a Portal capability or startup prerequisite.

These tests use no cloud, speech packages, credentials or synthesized audio.
"""
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / 'optional_tools/cloud_speech/edge_speech.py'


def test_uninstalled_tool_is_unavailable_without_breaking_discovery(tmp_path):
    # -S also proves --check works with only the Python standard library.
    check = subprocess.run([sys.executable, '-S', str(TOOL), '--check'],
                           capture_output=True, text=True, check=True, timeout=10)
    result = json.loads(check.stdout)
    assert result == {'available': False, 'missing_packages': ['edge_tts', 'mutagen'], 'cloud_probed': False}
    invocation = subprocess.run([sys.executable, '-S', str(TOOL),
                                 '--text-file', str(tmp_path/'unused'), '--output-dir', str(tmp_path/'audio')],
                                capture_output=True, text=True, timeout=10)
    assert invocation.returncode == 2
    assert json.loads(invocation.stdout)['status'] == 'unavailable'
    assert not (tmp_path/'audio').exists()


def test_core_catalog_loads_without_speech_packages():
    program = '''
import importlib.abc, sys
class AbsentSpeech(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'edge_tts', 'mutagen'}:
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, AbsentSpeech())
from management_portal.hatg.catalog import instruction_catalog
from management_portal.execution import backend_services, isa
from management_portal.execution.contracts import EXECUTION_SERVICE_TYPES
from management_portal import home_explanation_audio
assert EXECUTION_SERVICE_TYPES == {'agent_session', 'chat_completion'}
assert not {'GenerateSpeech', 'GetSpeechGeneration', 'ListSpeechProviders'} & instruction_catalog().keys()
assert not any(k.split('.')[0] in {'edge_tts', 'mutagen'} for k in sys.modules)
'''
    result = subprocess.run([sys.executable, '-c', program], cwd=ROOT,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


def test_cloud_failure_stays_in_tool_and_does_not_retry(tmp_path, monkeypatch, capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location('optional_edge_speech', TOOL)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    text = tmp_path/'input.txt'
    text.write_text('Fixture only', encoding='utf-8')
    output = tmp_path/'output'
    monkeypatch.setattr(sys, 'argv', [str(TOOL), '--text-file', str(text), '--output-dir', str(output)])
    monkeypatch.setattr(importlib.util, 'find_spec', lambda name: object())
    calls = []
    def offline(*args, **kwargs):
        calls.append(args)
        raise ConnectionError('fixture cloud unreachable')
    monkeypatch.setattr(tool, 'generate', offline)
    assert tool.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert len(calls) == 1 and result['automatic_retry'] is False
    assert result['error_type'] == 'ConnectionError'
    assert not (output/'receipt.json').exists() and not (output/'speech.mp3').exists()
    with pytest.raises(FileExistsError):
        tool.main()
    assert len(calls) == 1
