"""Persistent instance text and Python CLI pipes do not inherit the ANSI locale."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys

import pytest
from scripts import instance_services
from tests.cases.instance_test_support import write_instance


@pytest.mark.parametrize('record', ['area-relocation.json','instance-relocation.json'])
def test_relocation_records_read_utf8_in_legacy_locale(tmp_path, monkeypatch, record):
    root=tmp_path/'实例 🧪';(root/'runtime').mkdir(parents=True)
    (root/'runtime'/record).write_text(json.dumps({'status':'moving','target':'中文 🧪'},ensure_ascii=False),encoding='utf-8')
    read=Path.read_text
    monkeypatch.setattr(Path,'read_text',lambda self,encoding=None,errors=None:read(self,encoding=encoding or 'cp1252',errors=errors))
    assert 'relocation' in instance_services.start_blocker(SimpleNamespace(root=root)).lower()


def test_service_export_writes_utf8_definitions_and_reads_unicode_proposal(tmp_path):
    root=write_instance(tmp_path/'实例 🧪')
    proposal=tmp_path/'proposal.json'
    proposal.write_text(json.dumps(json.loads((root/'instance.json').read_text(encoding='utf-8')),ensure_ascii=False),encoding='utf-8')
    output=tmp_path/'服务定义 🧪'
    script=Path(__file__).resolve().parents[2]/'scripts/instance_service_definitions.py'
    result=subprocess.run([sys.executable,'-X','utf8=0',str(script),'--root',str(root),'--proposal',str(proposal),'--platform','windows','--output',str(output)],
        env={**os.environ,'PYTHONUTF8':'0','PYTHONIOENCODING':'cp1252'},capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['activated'] is False
    contents=[p.read_text(encoding='utf-8') for p in output.iterdir()]
    assert contents and all('实例 🧪' in content for content in contents)


@pytest.mark.parametrize('owner', ['area-controller','node-resolver'])
def test_utf8_child_json_is_not_decoded_with_parent_ansi_locale(tmp_path,monkeypatch,owner):
    from management_portal import http_admin,dsh_client
    expected={'status':'completed','module':'中文 clients 🧪'}
    original=subprocess.run
    def child(command,**kwargs):
        kwargs['encoding']=kwargs.get('encoding') or 'cp1252'
        kwargs.pop('input',None)
        code='import sys;sys.stdout.buffer.write('+repr(json.dumps(expected,ensure_ascii=False).encode('utf-8'))+')'
        return original([sys.executable,'-c',code],**kwargs)
    monkeypatch.setattr(subprocess,'run',child)
    if owner=='area-controller':
        assert http_admin._move_area_command('test','features','中文 target 🧪')==expected
    else:
        monkeypatch.setattr(dsh_client,'RUNTIME',tmp_path)
        assert dsh_client._runtime_modules(['module'])==expected


def test_native_catalog_utf8_metadata_survives_legacy_locale(tmp_path,monkeypatch):
    from management_common.code_environment import native_directory
    code=tmp_path/'code';(code/'config').mkdir(parents=True)
    entry={'version':'1','sha256':'a'*64,'description':'安装工具 🧪'}
    (code/'config/release-native-dependencies.json').write_text(json.dumps({'packages':{'windows/x86_64':{'git':entry}}},ensure_ascii=False),encoding='utf-8')
    read=Path.read_text
    monkeypatch.setattr(Path,'read_text',lambda self,encoding=None,errors=None:read(self,encoding=encoding or 'cp1252',errors=errors))
    assert native_directory(code,'git',{'platform':'windows','architecture':'x86_64'}).name=='git-1-aaaaaaaaaaaa'
