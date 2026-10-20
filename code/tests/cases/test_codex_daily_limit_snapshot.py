"""
:module: spec.design-governance
:design: decision_8b510cbd9c58
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts import codex_daily_limit_snapshot as snapshot


class TestCodexDailyLimitSnapshot(unittest.TestCase):
    def test_build_snapshot_keeps_only_public_limit_fields(self) -> None:
        """codex 每日额度快照只保留公开的额度字段，日期按本地时区计算

        :design-point:"""
        payload = snapshot.build_snapshot(
            "one",
            {
                "zero": {
                    "status": "ok",
                    "rate_limits": [{
                            "remaining_percent": 99,
                            "window_duration_mins": 10080,
                            "observed_at": "2026-07-28T05:00:01Z",
                            "stale": False,
                    }],
                    "usage": {"summary": {"lifetime_tokens": 123}},
                    "secret": "must-not-appear",
                },
                "one": {
                    "status": "ok",
                    "rate_limits": [{
                            "remaining_percent": 98,
                            "window_duration_mins": 10080,
                            "observed_at": "2026-07-28T05:00:02Z",
                            "stale": False,
                    }],
                },
            },
            observed_at=datetime(
                2026,
                7,
                28,
                21,
                0,
                tzinfo=timezone.utc,
            ),
            timezone_name="Asia/Hong_Kong",
        )

        self.assertEqual(payload["snapshot_date"], "2026-07-29")
        self.assertEqual(
            payload["schema"],
            "tfi.codex-daily-limit-snapshot.v2",
        )
        self.assertEqual(payload["default_account_id"], "one")
        self.assertEqual(
            [item["account_id"] for item in payload["accounts"]],
            ["one", "zero"],
        )
        serialized = json.dumps(payload)
        self.assertNotIn("lifetime_tokens", serialized)
        self.assertNotIn("must-not-appear", serialized)
        self.assertTrue(snapshot.snapshot_is_healthy(payload))

    def test_collect_accounts_uses_isa_public_contract(self) -> None:
        """收集 codex 账户时用 ListProviderAccounts 刷新读取，只保留 codex 账户

        :design-point:"""
        def isa(name, **arguments):
            self.assertEqual(name, "ListProviderAccounts")
            self.assertTrue(arguments["refresh"])
            return {"default_routes": {"codex": "one"}, "accounts": [
                {"provider": "codex", "backend_route_id": "one", "status": "ok", "rate_limits": []},
                {"provider": "claude", "backend_route_id": "claude_one"},
            ]}
        default_account_id, accounts = snapshot.collect_codex_accounts(isa_call=isa)
        self.assertEqual(default_account_id, "one")
        self.assertEqual(list(accounts), ["one"])

    def test_write_snapshot_updates_dated_and_latest_files(self) -> None:
        """写入额度快照时同时更新当日文件和 latest.json

        :design-point:"""
        payload = {
            "snapshot_date": "2026-07-29",
            "accounts": [{"account_id": "one", "status": "ok"}],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dated_path = snapshot.write_snapshot(payload, root)

            self.assertEqual(dated_path, root / "2026-07-29.json")
            self.assertEqual(
                json.loads(dated_path.read_text(encoding="utf-8")),
                payload,
            )
            self.assertEqual(
                json.loads(
                    (root / "latest.json").read_text(encoding="utf-8")
                ),
                payload,
            )

    def test_unhealthy_when_an_account_is_stale_or_failed(self) -> None:
        """没有账户或有账户读数过期时，额度快照视为不健康

        :design-point:"""
        self.assertFalse(snapshot.snapshot_is_healthy({"accounts": []}))
        self.assertFalse(
            snapshot.snapshot_is_healthy(
                {
                    "accounts": [
                        {
                            "account_id": "one",
                            "status": "ok",
                            "stale": True,
                        }
                    ]
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
