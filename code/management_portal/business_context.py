"""Bounded business navigation metadata. Labels never grant resource access."""
from __future__ import annotations
from collections.abc import Mapping
import re

FIELDS = frozenset({'application', 'subject_ref', 'group_ref', 'attempt_ref'})


def message_metadata(value):
    if value is None:
        return {}
    if not isinstance(value, Mapping) or set(value) != {'business_context'}:
        raise ValueError('message metadata supports only business_context')
    context = value['business_context']
    if not isinstance(context, Mapping) or set(context) != FIELDS:
        raise ValueError('business_context requires application/subject_ref/group_ref/attempt_ref')
    for key, item in context.items():
        if not isinstance(item, str) or not item or len(item) > 512 or any(ord(c) < 32 for c in item):
            raise ValueError('invalid business_context '+key)
    if not re.fullmatch(r'[a-z][a-z0-9._-]{0,95}', context['application']):
        raise ValueError('invalid business application')
    return {'business_context': dict(context)}
