"""Admission for new reporting work, after the caller's source authorization.

Society owns the effective policy. Callers supply only the authenticated actor
and, when present, a responsible Resident verified by the artifact owner.
"""
import re

RESIDENT = re.compile(r'resident_[0-9a-f]{12}')


def require_creation(actor, *, responsible=''):
    if not actor or (responsible and not RESIDENT.fullmatch(responsible)):
        raise PermissionError('缺少经核验的汇报创作身份')
    targets = {responsible} if responsible else set()
    if actor.startswith('resident_'):
        targets.add(actor)
    # A human's own operation is not attributed to an arbitrary Resident.
    if not targets:
        return
    from management_portal.society_service import get_society_service
    try:
        service = get_society_service().resident
        allowed = all(service.reporting_creation_allowed(target, actor) is True
                      for target in sorted(targets))
    except Exception as exc:
        raise PermissionError('无法确认汇报创作能力，未开始新创作') from exc
    if not allowed:
        raise PermissionError('汇报能力未开启或配置未确认，不能开始新创作')


def require_report_creation(store, snapshot, actor):
    from management_portal.home_report_review import responsible
    require_creation(actor, responsible=responsible(store, snapshot, actor))


def explanation_responsible(ref, actor):
    """Use the authenticated creator of a currently readable fixed Report."""
    from management_portal.home_report_explanations import ExplanationReports
    from management_portal.home_report_store import ReportStore
    store = ReportStore()
    try:
        saved = ExplanationReports(store).lookup(ref, actor)
    except FileNotFoundError:
        return ''
    record, _, snapshot = store.get(saved['report_id'], actor, saved['revision'])
    if snapshot.get('content_kind') != 'explanation' or snapshot.get('explanation_target') != ref:
        raise ValueError('汇报登记与解释创作的固定来源不一致')
    creator = record.get('created_by', '')
    return creator if RESIDENT.fullmatch(creator) else ''
