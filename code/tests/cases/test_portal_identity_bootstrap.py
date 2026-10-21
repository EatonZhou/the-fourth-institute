from pathlib import Path

from management_portal.serve_management_portal import _inject_portal_identity_bootstrap


def test_portal_identity_bootstrap_precedes_shared_identity_module_contracts(subtests, isolated_portal_case):
    """身份引导脚本先于共享身份模块注入并转义脚本分隔符，bfcache 恢复后重新校验身份并重连事件"""
    with subtests.test(contract='test_portal_identity_bootstrap_precedes_shared_identity_module'), isolated_portal_case() as case_path:
        marker = b'<script src="/static/portal_identity.js"></script>'
        html = b'<html><head>' + marker + b'</head></html>'
        rendered = _inject_portal_identity_bootstrap(html, 'sample_admin')
        assert rendered.count(marker) == 1
        assert rendered.index(b'__PORTAL_IDENTITY_BOOTSTRAP__') < rendered.index(marker)
        assert b'Object.freeze({"username":"sample_admin"})' in rendered
    with subtests.test(contract='test_portal_identity_bootstrap_escapes_script_delimiters'), isolated_portal_case() as case_path:
        marker = b'<script src="/static/portal_identity.js"></script>'
        rendered = _inject_portal_identity_bootstrap(marker, 'user</script>&')
        assert b'</script>&' not in rendered.split(marker, 1)[0]
        assert b'\\u003c/script\\u003e\\u0026' in rendered


    with subtests.test(contract='test_bfcache_restore_revalidates_identity_and_reconnects_events'), isolated_portal_case() as case_path:
        static_root = Path(__file__).resolve().parents[2] / "management_portal" / "static"
        identity = (static_root / "portal_identity.js").read_text(encoding="utf-8")
        events = (static_root / "portal_events.js").read_text(encoding="utf-8")
        refresh = (static_root / "portal_refresh_coordinator.js").read_text(encoding="utf-8")

        assert 'window.addEventListener("pageshow", revalidateHistoryRestore)' in identity
        assert 'load({ force: true })' in identity
        assert 'portal:history-restored' in identity
        assert 'window.addEventListener("pageshow", event =>' in events
        assert 'if (event.persisted) connect();' in events
        assert events.count('if (event.persisted) connect();') == 1
        assert 'window.addEventListener("pagehide", close);' in events
        assert 'window.addEventListener("pagehide", close, { once: true });' not in events
        assert 'channel?.close();' in events
        assert 'openChannel();' in events
        assert 'window.addEventListener("pagehide", closeChannel);' in refresh
        assert 'if (event.persisted) openChannel();' in refresh

        for filename in (
            "workbench_studio.js",
            "lineage_workbench_watchboard.js",
            "resource_backup.js",
        ):
            source = (static_root / filename).read_text(encoding="utf-8")
            assert "event.persisted" in source


def test_browser_password_policy_accepts_short_passwords_and_preserves_other_rules():
    import json
    import shutil
    import subprocess
    import pytest
    from management_portal.portal_access_control import PortalCredentialStore

    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed to execute the browser password validator')
    module = Path(__file__).resolve().parents[2] / 'management_portal/static/portal_identity.js'
    values = ['Ab1!', 'Äb1', '', 'lowercase', 'Ab1!' * 65]
    script = '''const fs = require('fs'); const vm = require('vm');
const window = {PortalCompatibility: {identifier: x => x},
  __PORTAL_IDENTITY_BOOTSTRAP__: {}, addEventListener() {}};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {window});
console.log(JSON.stringify(JSON.parse(process.argv[2]).map(
  value => !window.PortalIdentity.passwordStrengthError(value))));'''
    result = subprocess.run([node, '-e', script, str(module), json.dumps(values)],
                            check=True, text=True, capture_output=True)
    accepted = json.loads(result.stdout)
    assert accepted == [True, True, False, False, False]
    for password, allowed in zip(values, accepted):
        if allowed:
            PortalCredentialStore.validate_password(password)
        else:
            with pytest.raises(ValueError):
                PortalCredentialStore.validate_password(password)
