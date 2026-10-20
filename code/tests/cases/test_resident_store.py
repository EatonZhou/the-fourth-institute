from __future__ import annotations

import json

import pytest

from management_portal.society_service import resident_store


def test_missing_optional_resident_files_use_the_declared_empty_value_contracts(tmp_path, subtests, isolated_portal_case):
    """缺失的 Resident 可选文件读取为声明的空值，损坏的 JSON 照常报错"""
    with subtests.test(contract='test_missing_optional_resident_files_use_the_declared_empty_value'), isolated_portal_case() as case_path:
        tmp_path = case_path
        missing = tmp_path / 'missing.json'
        assert resident_store.read_json(missing, {'state': 'empty'}) == {'state': 'empty'}
        assert resident_store.read_text(missing) == ''
    with subtests.test(contract='test_invalid_resident_json_is_not_hidden'), isolated_portal_case() as case_path:
        tmp_path = case_path
        path = tmp_path / 'document.json'
        path.write_text('{broken', encoding='utf-8')
        with pytest.raises(json.JSONDecodeError):
            resident_store.read_json(path, {})
