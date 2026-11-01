"""Repo/Article ReadResource describes the same file whose bytes it returns."""
from types import SimpleNamespace

import pytest

from management_portal.resources import indexed_content, isa
from management_portal.resources.address import AddressParser
from management_common.resource_identity import get_resource_type_registry


@pytest.mark.parametrize('name,kind', [('README.md', 'markdown'), ('main.tex', 'text')])
def test_regular_file_read_reports_name_kind_and_real_modification_time(tmp_path, monkeypatch, name, kind):
    path = tmp_path / name
    path.write_text('current content', encoding='utf-8')
    resolved = {'target_kind': 'file', 'resolved_path': str(path), 'resource_type': 'repo', 'record': {}}
    def read(address, **kwargs):
        return indexed_content.read_resolved_resource_content(resolved, address=address, **kwargs)
    monkeypatch.setattr(isa, '_service', lambda: SimpleNamespace(
        read_content=read, parse_address=AddressParser(get_resource_type_registry()).parse))
    args = {'address': 'repo_111111111111/file/' + name}
    result = isa.read_resource(SimpleNamespace(caller='sample_user', argument=args.get))
    assert result['text'] == 'current content'
    assert result['name'] == name
    assert result['kind'] == kind
    assert result['mtime_ns'] == str(path.stat().st_mtime_ns)
    assert result['size'] == path.stat().st_size
    assert result['modified_at'] != '1970-01-01T00:00:00Z'
    assert 'filename' not in resolved
    with pytest.raises(ValueError, match='content limit'):
        read(args['address'], max_bytes=1)
