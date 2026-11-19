"""The retained cache guard keeps the subscription experiment closed.

Removed ISA entrypoints are covered by test_hatg_instruction_discovery.py::
test_retired_translation_has_no_declaration_handler_or_callable_alias.
"""
import pytest


def test_verified_experiment_cannot_be_reopened():
    from management_portal.paper_translation_cache import TranslationCache
    with pytest.raises(ValueError, match='permanently cancelled'):
        TranslationCache.request_verified(None, None, operation_id='cancelled', request={})
