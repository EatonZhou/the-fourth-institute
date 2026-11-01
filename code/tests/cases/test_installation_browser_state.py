"""Stable instance identity is projected, never build identity or secrets."""
import shutil

from management_common import site_config
from management_portal.page_bootstrap import inject_site_name
from tests.cases.instance_test_support import select_instance, write_instance


def test_browser_namespace_tracks_instance_not_build_or_display_name(tmp_path, monkeypatch):
    root = select_instance(monkeypatch, tmp_path / 'one', site={
        'site_name': 'Institute', 'deployment': {'secret': 'private'}}).root
    first = site_config.browser_storage_namespace()
    write_instance(root, site={'site_name': 'Renamed', 'deployment': {'secret': 'private'}},
                   code=tmp_path / 'other-code' / 'code')
    assert site_config.browser_storage_namespace() == first
    html = inject_site_name(b'<html><head><title data-portal-site-title="[]">TFI</title></head></html>')
    assert first.encode() in html
    assert b'private' not in html and b'other-code' not in html
    select_instance(monkeypatch, tmp_path / 'two', site={'site_name': 'Institute'})
    assert site_config.browser_storage_namespace() != first


def test_browser_namespace_survives_moving_the_instance(tmp_path, monkeypatch):
    root = select_instance(monkeypatch, tmp_path / 'one').root
    namespace = site_config.browser_storage_namespace()
    moved = tmp_path / 'moved'
    shutil.copytree(root, moved)
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', str(moved))
    assert site_config.browser_storage_namespace() == namespace
