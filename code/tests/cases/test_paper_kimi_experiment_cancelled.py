"""Old subscription entrypoints stay closed without touching live translation."""
import pytest


def test_old_send_is_permanently_cancelled():
    from management_portal.paper_web_isa import translate_paper_text
    with pytest.raises(ValueError, match='permanently cancelled'):
        translate_paper_text(None)


def test_old_binding_creation_is_permanently_cancelled():
    from management_portal.paper_translation_binding import ensure_binding
    from management_portal.workbench_projections.store import ProjectionError
    with pytest.raises(ProjectionError, match='permanently cancelled'):
        ensure_binding('synthetic-no-resource')


def test_verified_experiment_cannot_be_reopened():
    from management_portal.paper_translation_cache import TranslationCache
    with pytest.raises(ValueError, match='permanently cancelled'):
        TranslationCache.request_verified(None, None, operation_id='cancelled', request={})
