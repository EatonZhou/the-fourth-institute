"""JSON columns while they move from JSON text to JSONB.

A JSONB column is read as the decoded value; the same column stored as JSON
text is read as a string.  Until every live column is converted, readers pass
the stored value through ``json_value`` and get the decoded value either way.
No column stores a JSON string, so a Python string is always JSON text.

Writers pass JSON text from ``json_text``, which PostgreSQL stores in either
column type.  PostgreSQL text cannot hold U+0000 and JSONB rejects its escape,
so the character is stored as U+FFFD.
"""
import json


def json_value(value):
    return json.loads(value) if isinstance(value, str) else value


def json_text(value, **options):
    text = json.dumps(value, **options)
    if "\\u0000" not in text:
        return text
    return json.dumps(_without_nul(value), **options)


def _without_nul(value):
    if isinstance(value, str):
        return value.replace("\x00", "�")
    if isinstance(value, dict):
        return {_without_nul(key): _without_nul(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_without_nul(item) for item in value]
    return value
