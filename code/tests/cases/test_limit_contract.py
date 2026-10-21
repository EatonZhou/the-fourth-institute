from __future__ import annotations

from management_common.limit_contract import (
    UNSET,
    limit_reached,
    limit_remaining,
    normalize_optional_limit,
)
def test_nullable_limit_keeps_zero_distinct_from_unbounded_contracts(subtests, isolated_portal_case):
    """可空上限中 0 表示零容量，None 或 unlimited 表示不限，超出最大值时截断"""
    with subtests.test(contract='test_nullable_limit_keeps_zero_distinct_from_unbounded'), isolated_portal_case() as case_path:
        assert normalize_optional_limit(None, default=3) is None
        assert normalize_optional_limit("unlimited", default=3) is None
        assert normalize_optional_limit(0, default=3) == 0
        assert normalize_optional_limit("0", default=3) == 0
        assert normalize_optional_limit(UNSET, default=3) == 3
    with subtests.test(contract='test_zero_limit_has_zero_capacity'), isolated_portal_case() as case_path:
        assert limit_reached(0, 0) is True
        assert limit_remaining(0, 0) == 0
        assert limit_reached(None, 1_000_000) is False
        assert limit_remaining(None, 1_000_000) is None
    with subtests.test(contract='test_limit_normalization_clamps_maximum_and_rejects_negative_values'), isolated_portal_case() as case_path:
        assert normalize_optional_limit(200, maximum=100) == 100
        assert normalize_optional_limit(-1, default=4) == 4
        assert normalize_optional_limit(True, default=4) == 4


def test_paper_refresh_count_limits_do_not_treat_zero_as_all(
    tmp_path,
    monkeypatch,
) -> None:
    """CCF 论文缓存刷新的 max_venues 或 latest_years 为 0 时一项也不选"""
    from management_portal import paper_backend

    monkeypatch.setattr(
        paper_backend,
        "CCF_REFRESH_REPORT_PATH",
        tmp_path / "refresh-report.json",
    )
    monkeypatch.setattr(
        paper_backend,
        "_ccf_catalog",
        lambda **_kwargs: [
            {
                "dblp_conf_key": "conf/example",
                "dblp_section": "conf",
                "rank": "A",
                "abbr": "EX",
            }
        ],
    )
    monkeypatch.setattr(
        paper_backend,
        "_dblp_entries_for_venue",
        lambda *_args, **_kwargs: [{"entry_id": "2026", "year": 2026}],
    )
    monkeypatch.setattr(
        paper_backend,
        "ccf_papers",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("zero latest_years must select zero entries")
        ),
    )

    no_venues = paper_backend.refresh_ccf_cache(
        max_venues=0,
        refresh_catalog=False,
    )
    assert no_venues["venue_count"] == 0

    no_years = paper_backend.refresh_ccf_cache(
        latest_years=0,
        min_paper_year=None,
        max_venues=None,
        refresh_catalog=False,
    )
    assert no_years["venue_count"] == 1
    assert no_years["year_index_count"] == 1
    assert no_years["paper_year_count"] == 0
    assert no_years["all_entries"] is False
