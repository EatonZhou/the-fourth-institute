"""Publication unit boundaries, without live services.

:module: spec.design-governance
:importance: P2
"""
from scripts import portal_release_units as release_units


def test_removed_implicit_release_member_does_not_block_existing_paths(tmp_path):
    """发布单元中已删除的隐含成员不阻止发布仍存在的路径"""
    units = (('page.js', 'removed.js'),)
    (tmp_path / 'page.js').write_text('current')
    assert release_units.expand_release_unit_paths(('page.js',), tmp_path, units=units) == ('page.js',)
    assert release_units.expand_release_unit_paths(('removed.js',), tmp_path, units=units) == ('page.js', 'removed.js')
