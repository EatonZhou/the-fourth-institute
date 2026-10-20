"""Deployment-owned display labels; never used for identity or authorization."""
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _labels():
    path = Path(__file__).resolve().parents[1] / 'config/principal-labels.json'
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding='utf-8'))


def principal_label(principal):
    """A label is not an account lookup and cannot change a Principal."""
    label = _labels().get(principal['kind'], {}).get(principal['id'])
    return label if isinstance(label, str) else ''


def recipient_labels(recipients):
    """Expose only labels for validated recipients, preserving kind boundaries."""
    result = {}
    for principal in recipients:
        kind, identity = principal['kind'], principal['id']
        label = principal_label(principal)
        if label:
            result.setdefault(kind, {})[identity] = label
    return result
