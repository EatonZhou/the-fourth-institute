"""Distribution scan detects tracked regressions without hiding old writers."""
import json
from pathlib import Path
import re
import subprocess

import pytest
import yaml

from scripts import check_distribution_naming as check


def policy(**overrides):
    value = check.load_policy(check.ROOT)
    value["rules"]["legacy-name"] = r"(?i)retired[-_]?product|formerproduct"
    value.update(exceptions=[], deferred=[], **overrides)
    return value


def tracked(tmp_path, files):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for name, text in {check.POLICY: yaml.safe_dump(policy()), **files}.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    (tmp_path/"scope.json").write_text(json.dumps({"forbidden_brands":["retired-product","RetiredProduct","RETIRED_PRODUCT","FormerProduct"]}))


def test_cli_tracks_names_paths_environment_and_network_without_untracked_secrets(tmp_path, capsys):
    tracked(tmp_path, {"old-retired-product.py": 'name="FormerProduct"\nenv="RETIRED_PRODUCT_RESIDENT_ID"\nroot="/share/Projects"\nhost="10.77.0.1"\n'})
    (tmp_path / "secret.txt").write_text('RetiredProduct secret is untracked')
    assert check.main(["--identity-scope", str(tmp_path/"scope.json"), "--root", str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert 'old-retired-product.py:0:' in output
    assert 'legacy-name' in output and 'laboratory-path' in output and 'private-address' in output
    assert 'secret' not in output


def test_exact_compatibility_line_does_not_hide_old_writer(tmp_path):
    source = 'accepted = {"tfi.input.v1", "retired-product.input.v1"}'
    writer = 'write("retired-product.input.v1")'
    (tmp_path / "reader.py").write_text(source + '\n' + writer)
    p = policy()
    p['exceptions'] = [{"kind":"compat_read", "path":"reader.py", "rule":"legacy-name", "pattern":'^'+re.escape(source)+'$', "reason":"Retain old stored inputs during migration."}]
    findings, _, deferred = check.scan(tmp_path, ["reader.py"], p)
    assert [(f.line, f.rule) for f in findings] == [(2, "legacy-name")]
    assert not deferred


def test_vendor_exception_is_bounded_and_symlinks_are_not_followed(tmp_path):
    vendor = 'management_portal/static/vendor/d3'
    for path in [vendor + '/lib.js', vendor + '-app.js']:
        p = tmp_path / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('RetiredProduct')
    outside = tmp_path / 'untracked-private.txt'
    outside.write_text('RetiredProduct content must not be read')
    (tmp_path / 'link').symlink_to(outside.name)
    p = policy()
    p['exceptions'] = [{"kind":"vendor", "path":vendor, "reason":"Upstream library"}]
    findings, _, _ = check.scan(tmp_path, [vendor + '/lib.js', vendor + '-app.js', 'link'], p)
    assert [f.path for f in findings] == [vendor + '-app.js']


@pytest.mark.parametrize('entry', [
    {"kind":"compat_read", "path":"src/*", "rule":"legacy-name", "pattern":"^x$", "reason":"r"},
    {"kind":"vendor", "path":"management_portal", "reason":"r"},
    {"kind":"compat_read", "path":"a.py", "rule":"legacy-name", "pattern":"^x$", "reason":""},
    {"kind":"legacy_fixture", "path":"tests", "rule":"legacy-name", "pattern":"^.+$", "reason":"r"},
])
def test_invalid_broad_allowlist_fails_closed(tmp_path, entry):
    p = tmp_path / check.POLICY
    p.parent.mkdir()
    (tmp_path / 'tests').mkdir()
    rules = policy()
    rules['exceptions'] = [entry]
    p.write_text(yaml.safe_dump(rules))
    with pytest.raises(ValueError):
        check.load_policy(tmp_path)


def test_clean_output_and_portable_network_constants(tmp_path, capsys):
    tracked(tmp_path, {"clean.py": 'name="The Fourth Institute"\nnetworks=["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.1"]\n'})
    assert check.main(["--identity-scope", str(tmp_path/"scope.json"), "--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out == ''
    rules = policy()['rules']
    assert list(check.matches('10.77.0.0/16', rules))
    assert not list(check.matches('version 999.0.0.1', rules))


def test_deleted_tracked_file_is_not_a_false_io_failure(tmp_path, capsys):
    tracked(tmp_path, {"old.py":"RetiredProduct"})
    (tmp_path / 'old.py').unlink()
    assert check.main(["--identity-scope",str(tmp_path/"scope.json"),"--root",str(tmp_path)]) == 0
    assert capsys.readouterr().out == ''


def test_deferred_is_visible_but_only_authorized_token_is_exempt(tmp_path, capsys):
    source = 'After=retired-product-postgresql.service retired-product-other.service'
    entry = {'kind':'deferred', 'path':'app.service', 'rule':'legacy-name', 'pattern':'^'+re.escape(source)+'$', 'match_pattern':r'retired-product-postgresql\.service', 'reason':'Wait for database window.', 'basis':'the operator deferred database rename.'}
    p = policy()
    p['deferred'] = [entry]
    tracked(tmp_path, {'app.service':source, check.POLICY:yaml.safe_dump(p)})
    assert check.main(['--identity-scope',str(tmp_path/'scope.json'),'--root',str(tmp_path),'--json']) == 1
    report = json.loads(capsys.readouterr().out)
    assert len(report['findings']) == 1 and len(report['deferred']) == 1
    source = 'After=retired-product-postgresql.service'
    (tmp_path/'app.service').write_text(source)
    entry['pattern'] = '^'+re.escape(source)+'$'
    (tmp_path/check.POLICY).write_text(yaml.safe_dump(p))
    assert check.main(['--identity-scope',str(tmp_path/'scope.json'),'--root',str(tmp_path)]) == 0
    captured = capsys.readouterr()
    assert captured.out == '' and 'deferred: app.service' in captured.err
    p['deferred'] = []
    (tmp_path/check.POLICY).write_text(yaml.safe_dump(p))
    assert check.main(['--identity-scope',str(tmp_path/'scope.json'),'--root',str(tmp_path)]) == 1


def test_compressed_fixture_is_scanned_and_scope_is_required(tmp_path, capsys):
    import gzip
    tracked(tmp_path, {})
    target=tmp_path/'capture.json.gz';target.write_bytes(gzip.compress(b'{"author":"example-author"}'))
    p=check.identity_policy(policy(), {'source_usernames':['example-author']})
    findings,_,_=check.scan(tmp_path,['capture.json.gz'],p)
    assert any(f.rule=='source-user' for f in findings)
    with pytest.raises(SystemExit): check.main(['--root',str(tmp_path)])
