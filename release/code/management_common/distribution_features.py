"""Capabilities included in this installed engineering package (all in Dev)."""
import json
from pathlib import Path


def included_features():
    path = Path(__file__).resolve().parents[1] / "config/distribution.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["features"]


def included(feature):
    features = included_features()
    return features is None or feature in features


def initial_admission_paused(application):
    """Only a new local application row uses package defaults; existing rows win."""
    if application != 'tau':
        return False
    path = Path(__file__).resolve().parents[1] / 'config/distribution.json'
    if not path.exists():
        return False
    state = json.loads(path.read_text(encoding='utf-8')).get('feature_resolution', {}).get('states', {}).get('tau')
    return not state['resolved_default_enabled'] if state and state.get('included') else False
