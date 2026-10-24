"""One release feature identity, separate code inclusion and startup intent.

This module reads definitions, never source runtime switches or credentials.
Its initialization actions are consumed only by the independent installer.
"""
from copy import deepcopy
import json
import re

# Releases carry the Portal System Lineage under this ID; installers bind
# deployment.system_workflow to it. Distribution modules share this one value.
SYSTEM = 'lineage_ed619225d417'
SCHEMA = 'tfi.release-feature-states.v1'
SWITCH_KEYS = {'system:request-timing:enabled': 'model_request_timing',
               'system:harness-recording:enabled': 'harness_recording'}


def _spec(identity, title, default, owner, *, code=(), runtime=(), requires=(), **extra):
    applicable = extra.get('adapter') != 'availability'
    return {'id': identity, 'title': title, 'initial_default': default if applicable else None,
            'default_applicable': applicable,
            'kind': ('flow' if extra.get('adapter') in {'lineage_events', 'native_component_switch'} else 'runtime_feature') if applicable else 'code_support',
            'default_source': owner, 'code_requires': list(code), 'runtime_requires': list(runtime),
            'missing_requirements': list(requires), 'system_flow_ids': [], **extra}


def catalog(source=None):
    values = [
        _spec('permission_system', '权限系统', True, 'ExecutionSettings.permission_system_enabled initial TRUE',
              adapter='permission_setting', mandatory=True),
        _spec('resident_semantic_search', 'Resident 向量／语义搜索', False, 'management_common.embedding_feature.enabled',
              requires=('local_embedding_model', 'embedding_dependencies'), adapter='embedding_setting'),
        _spec('model_request_timing', '模型请求时间记录', True, 'ExecutionSettings.service_settings initial TRUE', adapter='execution_setting', field='model_request_timing_enabled'),
        _spec('harness_recording', 'Harness 记录', True, 'ExecutionSettings.service_settings initial TRUE', adapter='execution_setting', field='harness_recording_enabled'),
        _spec('wall_scheduling', 'WALL 定时任务', True, 'WallState.read missing scheduled_jobs=True', code=('wall',), runtime=('wall',), adapter='wall_setting'),
        _spec('telepus', 'Telepus', False, 'independent installation has no Telepus service units', requires=('local_telepus_units', 'local_telepus_configuration'), adapter='telepus_services'),
        _spec('tau', 'TAU', True, 'application_target_configuration: new admission_paused=False', code=('test_framework',), runtime=('test_framework',), requires=('local_tau_target', 'local_backend_account'), adapter='application_admission', application='tau'),
        _spec('pulse', 'Pulse', None, 'explicit intent; native component mapping is not an installation default', adapter='native_component_switch'),
        _spec('architecture_audit', '架构审计', None, 'explicit intent; native component mapping is not an installation default', adapter='native_component_switch'),
    ]
    from management_portal.portal_features import FEATURES
    present = {v['id'] for v in values}
    for feature in FEATURES:
        if feature.key not in present:
            values.append(_spec(feature.key, feature.title, True, 'portal_features: installed contribution available', adapter='availability'))
    values.extend([_spec('portal_core', 'Portal 基础支持', True, 'mandatory core', adapter='availability', mandatory=True),
                   _spec('test_view', 'Test 界面', True, 'installed view available', code=('test_framework',), runtime=('test_framework',), adapter='availability'),
                   _spec('society_templates', 'Society 模板支持', True, 'installed templates available', adapter='availability')])
    by_id = {v['id']: v for v in values}
    from .resource_dependencies import projection_owners
    for builtin, owner in projection_owners().items():
        if owner['selection_feature'] in by_id:
            by_id[owner['selection_feature']]['projection_builtin'] = builtin
    if source is not None:
        rows = source.definition_rows([SYSTEM])
        nodes = {r['node_id']: r for r in rows.get(('lineage','lineage_nodes'), []) if not r.get('deleted_at')}
        components = {r['node_id']: r for r in rows.get(('lineage','lineage_components'), []) if r['node_id'] in nodes}
        membership = rows.get(('lineage','function_block_elements'), [])
        groups = [(r['function_block_id'], r['title'], {e['element_id'] for e in membership if e['function_block_id'] == r['function_block_id']})
                  for r in rows.get(('lineage','function_blocks'), [])]
        covered = set().union(*(ids for _, _, ids in groups)) if groups else set()
        groups += [(i, r.get('title') or i, {i}) for i,r in components.items() if i not in covered and nodes[i]['node_type'] == 'event_source']
        for identity, title, members in groups:
            keys = {components[i].get('client_key') for i in members if i in components}
            aliases = {SWITCH_KEYS[k] for k in keys if k in SWITCH_KEYS}
            events = sorted(i for i in members if i in nodes and nodes[i]['node_type'] == 'event_source')
            if len(aliases) == 1:
                item = by_id[next(iter(aliases))]
                option = 'request_timing_lineage_id' if item['id'] == 'model_request_timing' else 'harness_recording_lineage_id'
                item['workflow_binding'] = {option: SYSTEM}
            else:
                feature_id = 'flow:' + identity
                item = _spec(feature_id, title, None, 'unset: source progress is frozen, not an installation default', adapter='lineage_events', requires=('local_workflow_configuration',))
                from .application_flows import installation_binding
                request = installation_binding(source, rows, members, events)
                if request:
                    item['application_binding'] = request
                    item['missing_requirements'] = []
                by_id[feature_id] = item
            item['system_flow_ids'].append(identity)
            item.setdefault('event_sources', []).extend({'lineage_id': SYSTEM, 'source_id': event} for event in events)
            if not events or len(aliases) > 1:
                item['mapping_error'] = 'Flow has no unique supported event-source enable mapping'
            # These semantic keys are owned by the existing System definitions.
            for prefix, dependency in [('system:backup:', 'backup'), ('system:notice_classifier:', 'notice_board')]:
                if any(k and k.startswith(prefix) for k in keys):
                    item['code_requires'] = sorted(set(item['code_requires']) | {dependency})
    return {'schema': SCHEMA, 'features': list(by_id.values())}


def normalize_snapshot(snapshot):
    """Save a single map for new configs/runs; never mutate historical records."""
    value = deepcopy(snapshot)
    if 'resource_lineage_options' in value:
        from .lineage_options import normalize_preferences
        value['resource_lineage_options'] = normalize_preferences(value['resource_lineage_options'])
    from .native_switches import FIELD, minimal_site_config
    if FIELD in value:
        value[FIELD] = minimal_site_config(value[FIELD])
    if 'feature_states' not in value:
        old = value.get('optional_features') or {}
        states = {k: {'included': v, 'default_enabled': None} for k,v in old.items()}
        for field, identity in [('projects_page', 'projects'), ('test_view', 'test_view')]:
            if field in value:
                states[identity] = {'included': value[field], 'default_enabled': None}
        for group, label, identity in [('tests','TAU 功能','tau'), ('tests','测试框架','test_framework'), ('tools','Telepus','telepus')]:
            states[identity] = {'included': label in (value.get('categories') or {}).get(group, []), 'default_enabled': None}
        for flow in (value.get('system_lineage') or {}).get('selected') or []:
            identity = flow['id'] if isinstance(flow, dict) else flow
            states['flow:' + identity] = {'included': True, 'default_enabled': None}
        value['feature_states'] = states
        value['feature_states_origin'] = 'legacy_selection_fields'
    states = value['feature_states']
    if not isinstance(states, dict):
        raise ValueError('feature_states must be an object')
    for identity, state in states.items():
        if not isinstance(identity, str) or not re.fullmatch(r'[a-z][a-z0-9_:.-]{0,127}', identity) or not isinstance(state, dict) or set(state) - {'included','default_enabled'}:
            raise ValueError('Invalid feature state')
        if any(state.get(key) is not None and type(state[key]) is not bool for key in ('included','default_enabled')):
            raise ValueError('Feature included/default_enabled must be boolean or null')
        states[identity] = {'included': state.get('included'), 'default_enabled': state.get('default_enabled')}
    # Compatibility controls are projections, never a second source of truth.
    value['projects_page'] = states.get('projects', {}).get('included') is True
    value['test_view'] = states.get('test_view', {}).get('included') is True
    return value


def structural_requirements(snapshot):
    """Explicit existing selection contracts shared by candidates and export."""
    from .resource_dependencies import feature_requirements, selected_types
    required = feature_requirements(selected_types(snapshot))
    if (snapshot.get('society_templates') or {}).get('selected'):
        required.setdefault('society_templates', set()).add('society_templates.selected')
    return required


def resolve_features(snapshot, *, source=None, required=(), dependencies=None):
    if dependencies is None and source is not None:
        from .resource_dependencies import resolve
        dependencies = resolve(snapshot, source)
    declarations = catalog(source)
    specs = {s['id']: s for s in declarations['features']}
    from .native_switches import mappings
    for name, mapping in mappings(snapshot, source, dependencies).items():
        specs[name].update(binding=mapping['binding'], site_config=mapping['site_config'],
                           mapping_readiness=mapping['readiness'], mapping_diagnostics=mapping['diagnostics'])
    normalized = normalize_snapshot(snapshot)
    requested = deepcopy(normalized['feature_states'])
    legacy = normalized.get('feature_states_origin') == 'legacy_selection_fields'
    aliases = {'flow:' + flow: s['id'] for s in specs.values() for flow in s['system_flow_ids']}
    conflicts = []
    for identity in list(requested):
        canonical = aliases.get(identity, identity)
        if canonical == identity:
            continue
        value = requested.pop(identity)
        old = requested.get(canonical)
        if old and old != value and not legacy:
            conflicts.append({'id': canonical, 'reason': 'Conflicting aliases for the same feature', 'alias': identity})
        elif old and legacy:
            value = {'included': old['included'] is True or value['included'] is True, 'default_enabled': old['default_enabled']}
        requested[canonical] = value
    code_reasons = structural_requirements(normalized)
    for projection in (dependencies or {}).get('projections', []):
        identity = projection.get('selection_feature')
        if identity:
            code_reasons.setdefault(identity, set()).add('projections.' + projection['id'])
    for identity in required:
        code_reasons.setdefault(identity, set()).add('explicit_resolution')
    included = {k for k,s in specs.items() if s.get('mandatory')} | set(code_reasons) | {k for k,v in requested.items() if v['included'] is True}
    for _ in range(len(specs) + 1):
        before = set(included)
        for identity in sorted(before):
            for dependency in specs.get(identity, {}).get('code_requires', []):
                included.add(dependency)
                code_reasons.setdefault(dependency, set()).add(identity)
        if included == before:
            break
    states = {}
    for identity in sorted(set(specs) | set(requested) | included):
        spec = specs.get(identity)
        request = requested.get(identity, {})
        selected = identity in included
        intent = request.get('default_enabled')
        applicable = spec.get('default_applicable', True) if spec else True
        native = spec is not None and spec['adapter'] == 'native_component_switch'
        default = (intent if intent is not None else spec.get('initial_default') if spec else None) if applicable else None
        if selected and not spec:
            conflicts.append({'id': identity, 'reason': 'Unknown feature or missing source flow'})
        if selected and spec and spec.get('mapping_error'):
            conflicts.append({'id': identity, 'reason': spec['mapping_error']})
        if selected and applicable and default is None and not native:
            conflicts.append({'id': identity, 'reason': 'Default enable state is unset and has no verified initialization rule'})
        missing = list(spec.get('missing_requirements', [])) if selected and default and spec else []
        if selected and native and intent is not None and spec['mapping_readiness'] != 'ready':
            missing.append('native_component_mapping')
            if spec['mapping_readiness'] == 'conflict':
                conflicts.append({'id': identity, 'reason': 'Native component binding conflicts with selected definitions',
                                  'diagnostics': deepcopy(spec['mapping_diagnostics'])})
        states[identity] = {'included': selected, 'requested_included': request.get('included'),
                            'default_applicable': applicable, 'kind': spec['kind'] if spec else 'unknown',
                            'default_enabled': intent, 'resolved_default_enabled': default if selected else None,
                            'default_source': (('explicit' if intent is not None else spec['default_source'] if spec else 'unknown') if applicable else 'not_applicable') if selected else 'not_included',
                            'effective_enabled': bool(default and not missing) if selected and applicable else None,
                            'readiness': 'needs_configuration' if missing else 'ready' if selected else 'not_included',
                            'missing_requirements': missing, 'code_required_by': sorted(code_reasons.get(identity, [])),
                            'runtime_required_by': [], 'code_requires': spec['code_requires'] if spec else [],
                            'runtime_requires': spec['runtime_requires'] if spec else []}
        if native:
            states[identity].update(mapping_readiness=spec['mapping_readiness'],
                                    mapping_diagnostics=deepcopy(spec['mapping_diagnostics']),
                                    initialization_status='pending_installation' if selected and intent is not None else 'not_requested')
            if selected and intent is None:
                states[identity].update(readiness='code_only', effective_enabled=None)
            elif selected and missing:
                states[identity]['effective_enabled'] = None
        states[identity]['code_required_by_details'] = [
            {'id': reason, 'title': specs[reason]['title'] if reason in specs else
             '已选资源类型：' + reason.removeprefix('resource_types.') if reason.startswith('resource_types.') else
             '所选 Society 模板' if reason == 'society_templates.selected' else '发行内容依赖',
             'source': reason}
            for reason in states[identity]['code_required_by']]
    for identity in sorted(included):
        state = states[identity]
        if state['resolved_default_enabled'] is not True:
            continue
        for dependency in state['runtime_requires']:
            target = states.get(dependency)
            if target is None or not target['included']:
                conflicts.append({'id': identity, 'dependency': dependency, 'reason': 'Runtime dependency is not included'})
            elif not target['default_applicable']:
                # Code availability is a prerequisite, not a runtime switch.
                target['runtime_required_by'].append(identity)
            elif requested.get(dependency, {}).get('default_enabled') is False:
                conflicts.append({'id': identity, 'dependency': dependency, 'reason': 'Runtime dependency explicitly disabled'})
            else:
                target['runtime_required_by'].append(identity)
                target['resolved_default_enabled'] = True
                target['effective_enabled'] = not target['missing_requirements']
                if target['default_enabled'] is None:
                    target['default_source'] = 'runtime_dependency'
    for conflict in conflicts:
        state = states.get(conflict['id'])
        if state:
            state['readiness'] = 'conflict'
            state['effective_enabled'] = False if state['default_applicable'] else None
    actions = []
    for identity in sorted(included & specs.keys()):
        spec, state = specs[identity], states[identity]
        if spec['adapter'] == 'native_component_switch' and (state['resolved_default_enabled'] is None or spec['mapping_readiness'] != 'ready'):
            continue
        if spec['adapter'] != 'availability':
            actions.append({'id': identity, 'adapter': spec['adapter'], 'desired_enabled': state['resolved_default_enabled'],
                            'effective_enabled': state['effective_enabled'], 'missing_requirements': state['missing_requirements'],
                            **{key: deepcopy(spec[key]) for key in ('field','application','event_sources','workflow_binding',
                                'binding','site_config','mapping_readiness','mapping_diagnostics','application_binding') if key in spec}})
    return {'schema': SCHEMA, 'selection_origin': normalized.get('feature_states_origin', 'explicit'),
            'states': states, 'included_features': sorted(included), 'conflicts': conflicts,
            'valid': not conflicts, 'initialization': {'schema': SCHEMA, 'actions': actions},
            'catalog': declarations}


def selection_snapshot(snapshot, resolution):
    """Project the sole feature map into the existing data-selection vocabulary."""
    value = deepcopy(snapshot)
    included = set(resolution['included_features'])
    value['projects_page'] = 'projects' in included
    value['test_view'] = 'test_view' in included
    value['optional_features'] = {key: key in included for key in (value.get('optional_features') or {})}
    value['optional_features'].update({key: key in included for key in ['resident_semantic_search','model_request_timing','harness_recording','wall_scheduling','telepus','tau','test_framework']})
    selected = []
    for spec in resolution['catalog']['features']:
        if spec['id'] in included:
            selected.extend(spec['system_flow_ids'])
    value['system_lineage'] = {**(value.get('system_lineage') or {}), 'id': SYSTEM, 'selected': sorted(set(selected))}
    return value
