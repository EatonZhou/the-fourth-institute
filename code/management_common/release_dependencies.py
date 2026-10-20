"""Runtime dependency contract shared by engineering and installation."""
SCHEMA = 'tfi.release-runtime-dependencies.v1'
FEATURE_PROFILES = {'article': 'article', 'test_framework': 'test', 'tau': 'tau',
                    'resident_semantic_search': 'embeddings'}


def declaration(features, resource_types=()):
    reasons = {'core': ['portal_core']}
    for feature in sorted(set(features)):
        profile = FEATURE_PROFILES.get(feature)
        if profile:
            reasons.setdefault(profile, []).append('feature:' + feature)
    if 'article' in resource_types:
        reasons.setdefault('article', []).append('resource_type:article')
    return {'schema': SCHEMA, 'profiles': sorted(reasons), 'reasons': reasons,
            'host_prerequisites': ['Python 3.12 with venv'],
            'native_preparation': 'Omitted PostgreSQL/NATS paths are installed privately; Node.js with npm is prepared for test/tau/provider profiles',
            'preparation': 'reuse verified explicit tools or prepare pinned instance-owned native tools and a Python environment'}


def profiles(manifest):
    value = manifest.get('runtime_dependencies')
    if value is None:
        return []  # Historical bundles retain their original preflight contract.
    selected = value.get('profiles')
    allowed = {'core', 'article', 'test', 'tau', 'provider', 'embeddings'}
    if (value.get('schema') != SCHEMA or not isinstance(selected, list)
            or any(not isinstance(p, str) or p not in allowed for p in selected)
            or 'core' not in selected or len(selected) != len(set(selected))):
        raise ValueError('Invalid runtime dependency declaration')
    required = declaration(manifest.get('features', []),
                           manifest.get('resource_dependencies', {}).get('types', []))['profiles']
    if not set(required) <= set(selected):
        raise ValueError('Runtime dependency declaration omits a selected capability')
    return selected
