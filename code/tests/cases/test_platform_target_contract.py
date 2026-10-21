"""Platform selection is an install boundary, not native validation evidence."""
import pytest
from management_common.distribution_targets import resolve_targets, preflight_manifest


@pytest.mark.parametrize('platform', ['linux', 'windows', 'macos'])
@pytest.mark.parametrize('architecture', ['x86_64', 'aarch64'])
def test_native_adapter_selection_preserves_verification_and_rejects_wrong_host(platform, architecture):
    manifest = resolve_targets({'target_platforms':[platform]})
    host = {'platform':platform, 'architecture':architecture, 'os_version':'fixture'}
    assert preflight_manifest(manifest, host=host)['ok']
    assert all(row['verification'] == 'not_run' and not row['evidence'] for row in manifest['support_matrix'])
    assert not preflight_manifest(manifest, host={**host, 'platform':'other'})['ok']
    assert not preflight_manifest(manifest, host={**host, 'architecture':'mips'})['ok']
    incomplete = {**manifest, 'support_matrix':[{**manifest['support_matrix'][0], 'implementation':'incomplete'}]}
    assert not preflight_manifest(incomplete, host=host)['ok']
    unknown = resolve_targets({'target_platforms':['other']})
    unknown.update(installable=True, support_matrix=[])
    assert not preflight_manifest(unknown, host={**host, 'platform':'other'})['ok']
