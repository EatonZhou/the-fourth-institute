"""Deliver an already addressed facade input through ordinary relay admission.

No model or program chooses a target here. The accepted routing snapshot and
current membership determine it; the relay owner still decides acceptance.
"""
from __future__ import annotations

import logging

LOGGER = logging.getLogger(__name__)


def deliver(record, message_id, actor):
    from . import family_facade, family_quota, resident_conversation_stream

    rid = record['resident_id']
    repository = resident_conversation_stream.get_repository()
    if repository.input_state(rid, message_id).get('state') != 'queued':
        return
    if message_id in family_facade.relay_links(record):
        return
    message = repository.message(rid, message_id)
    if not message:
        return
    from .lineage_message_events import relay_depth
    if relay_depth(rid, message_id, repository.message_business_ref) >= family_facade.MAX_RELAY_HOPS:
        raise ValueError('经过的门面数超过上限')
    target = family_facade.direct_member(record, message)
    if not target:
        return
    # Use the actual accepting caller, never a fabricated User/Leader. The
    # owner rechecks source authority, cancellation, membership, quota and
    # deterministic relay identity, including nested facade deliveries.
    return family_quota.relay_message(rid, message_id, target, actor)


def resume(record, message_id, actor):
    """A committed input survives a failed attempt; its arrival remains retryable."""
    try:
        return deliver(record, message_id, actor)
    except Exception:
        LOGGER.exception('Direct facade delivery remains pending: %s/%s', record['resident_id'], message_id)
        return None
