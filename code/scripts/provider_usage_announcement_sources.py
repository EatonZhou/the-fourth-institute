"""The reviewed source list is the authority for third-party evidence admission."""
from functools import lru_cache
import json
from pathlib import Path


@lru_cache(maxsize=1)
def source_list():
    return json.loads(Path(__file__).with_suffix('.json').read_text())['sources']


def trusted_source(url, provider):
    return next((s for s in source_list() if s['events_url'] == url
                 and provider in s['providers'] and s['status'] == 'trusted_for_inference'), None)


DISCOVERY_SOURCE = next(s['events_url'] for s in source_list() if s['id'] == 'divin')
