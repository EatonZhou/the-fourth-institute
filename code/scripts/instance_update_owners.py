"""Run update contributions inside the selected instance's code environment."""
from pathlib import Path
import json
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def execute(phase, plans):
    from management_portal.portal_features import contributions
    from management_common.instance_manifest import load
    from management_portal.society_service.instance_update import update as society_update
    instance = load()
    owners = [('society', society_update), *contributions('instance.update')]
    result = {'owners': {}, 'services': [], 'start_services': [], 'blockers': []}
    from management_common.release_dependencies import FEATURE_PROFILES
    result['dependency_policy'] = {
        'required': ['core'], 'optional_feature_profiles': FEATURE_PROFILES,
        'optional_tools': 'user_managed_checked_on_use',
        'postgres_major': 'explicit_cluster_migration_only',
    }
    from management_common.service_components import MODULES
    from scripts.instance_service_definitions import components
    import importlib.util
    result['service_inventory'] = []
    for component in components(instance):
        module = MODULES.get(component)
        row = {'component': component, 'launcher': module or 'native_or_declared_program'}
        if module and importlib.util.find_spec(module) is None:
            result['blockers'].append({'component': component, 'error': 'missing_service_module'})
        program = instance.service_program(component)
        if program and not instance.resolve(program['entrypoint']).is_file():
            result['blockers'].append({'component': component, 'error': 'missing_service_entrypoint'})
        result['service_inventory'].append(row)
    for name, update in owners:
        try:
            value = update(instance, phase, plans.get('owners', {}).get(name, {}))
        except Exception as exc:
            frames = traceback.extract_tb(exc.__traceback__)
            value = {'blockers': [{'owner': name, 'error': type(exc).__name__,
                                   'location': f'{Path(frames[-1].filename).name}:{frames[-1].lineno}' if frames else '',
                                   'recovery': 'resolve_owner_error_and_retry_same_update'}]}
        result['owners'][name] = value
        result['services'].extend(value.get('services', []))
        if set(value.get('start_services', [])) - set(value.get('services', [])):
            raise ValueError('Feature startup intent must refer to its newly declared services')
        result['start_services'].extend(value.get('start_services', []))
        result['blockers'].extend(value.get('blockers', []))
    result['services'] = sorted(set(result['services']))
    result['start_services'] = sorted(set(result['start_services']))
    return result


if __name__ == '__main__':
    request = json.loads(Path(sys.argv[1]).read_text())
    if request['phase'] not in {'plan', 'apply', 'verify'}:
        raise ValueError('Unknown update phase')
    print(json.dumps(execute(request['phase'], request['plan'])))
