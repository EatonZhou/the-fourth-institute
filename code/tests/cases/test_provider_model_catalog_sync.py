"""Daily catalog receipts are observed, not inferred from refresh acceptance.

:module: spec.design-governance
:importance: P1
"""
from copy import deepcopy
import json
import pytest

from scripts.provider_model_catalog_sync import sync_catalogs


def account(aid="ultra", *, at=0, fresh=True, models=None, enabled=True):
    return {"account_id": aid, "provider": "antigravity", "enabled": enabled,
            "selection_catalog": {"checked_at": at, "fresh": fresh,
                                  "models": models if models is not None else ["carbon"],
                                  "default_model": "carbon"},
            "registration": {"private": "secret-not-for-logs"}}


def fixture(snapshots):
    snapshots = iter(deepcopy(snapshots))
    calls, tick = [], [1000]
    def call(name, **kwargs):
        calls.append((name, kwargs))
        if name == "ListProviders":
            return {"providers": [{"provider": "antigravity", "capabilities": {"settings": ["model"]}}]}
        return {"accounts": next(snapshots)}
    def run(timeout=4):
        return sync_catalogs(call, timeout=timeout, clock=lambda: tick[0],
                             monotonic=lambda: tick[0], sleep=lambda n: tick.__setitem__(0, tick[0] + n))
    return run, calls


def test_waits_for_catalog_and_records_changes_without_model_or_extra_refresh():
    old = account()
    run, calls = fixture([[old], [old], [account(at=1002, models=["neon"])]] )
    result = run()
    assert result["status"] == "ok"
    row = result["accounts"][0]
    assert row["status"] == "refreshed"
    assert row["added"] == ["neon"] and row["removed"] == ["carbon"]
    assert "secret" not in json.dumps(result)
    assert [args["refresh"] for name, args in calls if name == "ListProviderAccounts"] == [False, True, False]


def test_recent_monitor_observation_is_explicitly_reused_and_disabled_skipped():
    rows = [account(at=950), account("disabled", enabled=False)]
    run, calls = fixture([rows, rows])
    result = run()
    assert result["status"] == "ok"
    assert len(result["accounts"]) == 1
    assert result["accounts"][0]["status"] == "recent_observation"


def test_partial_failure_retains_success_and_does_not_claim_timeout_as_synced():
    old = [account(), account("failed"), account("silent")]
    after = [account(at=1001), account("failed", at=1001, fresh=False)]
    run, _ = fixture([old, after, after, after])
    result = run()
    assert result["status"] == "partial"
    rows = {a["account_id"]: a for a in result["accounts"]}
    assert rows["ultra"]["status"] == "refreshed"
    assert rows["failed"]["error_code"] == "catalog_unavailable"
    assert rows["silent"]["error_code"] == "catalog_observation_timeout"


def test_old_failure_does_not_preempt_new_observation():
    old = account(at=950, fresh=False)
    run, _ = fixture([[old], [old], [account(at=1002)]])
    assert run()["accounts"][0]["status"] == "refreshed"


def test_authorization_failure_is_visible_and_sanitized():
    def refused(*args, **kwargs):
        raise PermissionError("private credential")
    result = sync_catalogs(refused)
    assert result["status"] == "failed" and result["error_code"] == "PermissionError"
    assert "private" not in json.dumps(result)


def test_no_enabled_catalog_accounts_does_not_request_refresh():
    run, calls = fixture([[account(enabled=False)]])
    assert run()["status"] == "skipped"
    assert not any(args.get("refresh") for _, args in calls)


@pytest.mark.parametrize("catalog_status,expected", [("ok", 0), ("partial", 1), ("failed", 1)])
def test_daily_script_always_records_catalogs_even_when_no_runtime_update(monkeypatch, tmp_path, capsys, catalog_status, expected):
    from scripts import update_provider_runtimes as script
    monkeypatch.setattr(script, "run_update_pass", lambda: {"checked": {"antigravity": {}}, "errors": {}})
    monkeypatch.setattr(script, "sync_model_catalogs", lambda: {"status": catalog_status, "accounts": []})
    monkeypatch.setattr(script, "instance_runtime_root", lambda: tmp_path)
    assert script.main(["--lineage-update"]) == expected
    assert json.loads(capsys.readouterr().out)["model_catalogs"]["status"] == catalog_status
    assert json.loads((tmp_path / "provider-update-result.json").read_text())["model_catalogs"]["status"] == catalog_status


def test_catalog_only_probe_does_not_update_runtimes(monkeypatch, capsys):
    from scripts import update_provider_runtimes as script
    monkeypatch.setattr(script, "run_update_pass", lambda: pytest.fail("no runtime update"))
    monkeypatch.setattr(script, "sync_model_catalogs", lambda: {"status": "ok"})
    assert script.main(["--sync-model-catalogs"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
