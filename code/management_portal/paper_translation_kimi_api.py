"""Server-selected Moonshot Model API recipe. Never accesses provider secrets."""
from dataclasses import asdict
from urllib.parse import urlsplit
from .agent_prompt_context import material_task_instructions

RECIPE = 'reader-translation-moonshot-v1'
API_ID = 'kimi_primary'
MODELS = frozenset({'kimi-k2.6', 'kimi-k2.5'})


class TranslationConfigurationUnavailable(RuntimeError):
    pass


def configuration(registry, instruction):
    try:
        entry = registry.get(API_ID)
    except ValueError as exc:
        raise TranslationConfigurationUnavailable('Moonshot translation API is unavailable') from exc
    model = entry.get('default_model')
    enabled_models = {m.get('id') for m in entry.get('models', []) if m.get('enabled', True)}
    url = urlsplit(entry.get('base_url', ''))
    if (entry.get('enabled') is False or entry.get('provider') != 'moonshot'
            or entry.get('kind') != 'openai_compatible_chat'
            or model not in MODELS or model not in enabled_models
            or url.scheme != 'https' or url.hostname not in {'api.moonshot.cn', 'api.moonshot.ai'}
            or url.username or url.password or url.query or url.fragment or url.path.rstrip('/') != '/v1'):
        raise TranslationConfigurationUnavailable('Moonshot translation registration is incompatible')
    defaults = entry.get('default_parameters') or {}
    if set(defaults) - {'temperature', 'top_p', 'max_tokens', 'thinking', 'stream'}:
        raise TranslationConfigurationUnavailable('Moonshot translation defaults are incompatible')
    # Version, origin, model and defaults participate in the private cache key.
    return dict(api_id=API_ID, model=model, api_version=registry.configuration_revision(API_ID),
        base_url=entry['base_url'], default_parameters=defaults,
        parameters={'thinking': {'type': 'disabled'}, 'max_tokens': 4096, 'stream': False},
        system_instructions=asdict(material_task_instructions(instruction)))
