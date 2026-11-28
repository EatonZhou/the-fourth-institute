"""One-time, additive information-forwarding duty; never reinstall withdrawn work."""
from copy import deepcopy
import hashlib
import json

from . import family_role_composition, family_roles, family_tasks


def upgrade(body):
    from .templates import role_recipes
    source_body = role_recipes.definition('steward')
    item = next(d for d in source_body['duties'] if d['key'] == 'info_forward')
    identity = role_recipes.references()['steward']
    for parent in list(body.get('function_blocks', [])):
        if not parent.get('family'):
            continue
        roles = family_roles.role_blocks(body, parent)
        steward = next((r for r in roles if r['role']['key'] == 'steward'), None)
        if steward:
            for record in family_roles.duties(body, steward):
                parameters=record['duty']['parameters']
                if parameters.get('family_task')=='organization':
                    old_title='Family 初始化、名册或背景变化时，核对职责分工并完成必要的组织调整'
                    if record['title']==old_title:
                        record['title']=role_recipes.DUTY_TITLES['organization']
                        if record['duty'].get('description')==old_title:record['duty']['description']=record['title']
                    memory=record['duty'].get('memory', {})
                    saved={'guide':memory.get('prompt',''),'prompt':parameters['prompt']}
                    preserved=family_tasks.saved_fields('organization',saved)
                    defaults=family_tasks.defaults('recheck_recovery')['organization']
                    if 'guide' not in preserved:memory['prompt']=defaults['guide']
                    if 'prompt' not in preserved:parameters['prompt']=defaults['prompt']
            metadata = steward['role']
            contracts = metadata.setdefault('contracts', [])
            if 'info_forward' in contracts:
                continue
            contracts.append('info_forward')
            if 'info_forward' in metadata['duties']:
                continue
            # A new declaration has its own content-addressed origin. Do not
            # relabel the frozen source version of the other installed duties.
            source = {'template_id': identity, 'version': 1,
                      'body': {'memories': [], 'duties': [deepcopy(item)]}}
            family_role_composition.install(body, parent, 'steward', source, 'portal')
            duty = next(d for d in family_roles.duties(body, steward)
                        if d['duty']['parameters'].get('family_task') == 'info_forward')
            duty['duty']['parameters']['source'] = {
                'template_id': identity, 'builtin_key': 'family_role_steward',
                'declaration_hash': hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest(),
            }
        elif not parent['family'].get('role_bindings'):
            # Legacy message-only declarations retain their existing wire owner
            # and authored text; an absent wire is never created.
            owned = {e['id'] for e in parent['elements']}
            for wire in body['program']['wires']:
                if wire['id'] not in owned:
                    continue
                for profile in wire.get('config', {}).get('invoke_profiles', {}).values():
                    entries = profile.get('arguments', {}).get('family_tasks', {}).get('value', {})
                    if 'organization' in entries:
                        entries.setdefault('info_forward', family_tasks.defaults('recheck_recovery')['info_forward'])
