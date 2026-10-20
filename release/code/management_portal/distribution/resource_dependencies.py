"""Release type/page dependencies from native provider and application owners."""
from copy import deepcopy
import importlib

from .sanitize import RESOURCE_ID
from .store import digest

SCHEMA = 'tfi.release-resource-dependencies.v4'


def input_digest(snapshot, *, version=3):
    from .features import normalize_snapshot
    return digest({**{key: snapshot.get(key) for key in ('resource_types', 'document_indexes', 'lineages', 'projections')},
                   **({key: snapshot.get(key) for key in ('projects', 'manual_projects', 'resource_lineage_options')} if version >= 3 else {}),
                   **({'projects_selected': normalize_snapshot(snapshot)['projects_page']} if version >= 3 else {}),
                   'presentation_selection': presentation_selection(snapshot)})


def projection_owners():
    """Use native builtin identity; names, labels and old URLs are not IDs."""
    from management_portal.portal_features import FEATURES
    result = {}
    for feature in FEATURES:
        reference = feature.contributions.get('workbench.builtin')
        if reference:
            module, attribute = reference.split(':', 1)
            builtin = getattr(importlib.import_module(module), attribute)['name']
            result[builtin] = {'feature': feature.key, 'selection_feature': feature.presentation_feature}
    return result


def presentation_selection(snapshot):
    from .features import normalize_snapshot
    states = normalize_snapshot(snapshot)['feature_states']
    return sorted(owner['selection_feature'] for owner in projection_owners().values()
                  if owner['selection_feature'] and states.get(owner['selection_feature'], {}).get('included') is True)


def selected_types(snapshot, additional_ids=()):
    return set(type_reasons(snapshot, additional_ids))


def type_reasons(snapshot, additional_ids=()):
    reasons = {}
    for item in snapshot.get('resource_types', []):
        reasons.setdefault(item['type'], set()).add('resource_types.' + item['type'])
    from .features import normalize_snapshot
    if normalize_snapshot(snapshot).get('projects_page'):
        reasons.setdefault('project', set()).add('feature_states.projects')
    for identity in snapshot.get('manual_projects', []):
        reasons.setdefault('project', set()).add('manual_projects:' + identity)
    for item in snapshot.get('projects', []):
        reasons.setdefault('project', set()).add('projects:' + item['id'])
    addresses = [(address, 'resident_resolution.additional_ids') for address in additional_ids]
    for name, selection in snapshot.get('document_indexes', {}).items():
        if selection.get('enabled'):
            addresses.extend((address, 'document_indexes.' + name) for address in selection.get('entries', []))
    for address, origin in addresses:
        if isinstance(address, str) and RESOURCE_ID.fullmatch(address):
            reasons.setdefault(address.rsplit('_', 1)[0], set()).add(origin + ':' + address)
    return reasons


def feature_requirements(types):
    from management_portal.portal_features import FEATURES
    required = {}
    for kind in types:
        owners = {feature.key for feature in FEATURES if kind in feature.related_types}
        # Existing release code capabilities for resource owners without a
        # Workbench contribution still retain their explicit type contract.
        if kind in {'repo', 'article'}:
            owners.add(kind)
        for owner in owners:
            required.setdefault(owner, set()).add('resource_types.' + kind)
    return required


def resolve(snapshot, source, *, additional_ids=()):
    from management_common.resource_identity import get_resource_type_registry
    from management_portal.resources.providers.ownership import KNOWLEDGE_TYPES
    from management_portal.application_targets import CHAT_TYPES
    from management_portal.portal_features import FEATURES
    declarations = get_resource_type_registry().declarations
    origins = type_reasons(snapshot, additional_ids)
    types = set(origins)
    pages, gaps, edges = [], [], []
    lineages, projects, projections = {}, {}, {}
    for item in snapshot.get('projects', []):
        projects.setdefault(item['id'], set()).add('manual.projects')
    for identity in snapshot.get('manual_projects', []):
        projects.setdefault(identity, set()).add('manual.projects')
    required_features, applications = set(feature_requirements(types)), set()
    owners = projection_owners()
    selected_presentations = set(presentation_selection(snapshot))
    apps = source.rows('workbenches', 'apps') if types or snapshot.get('projections') or selected_presentations else []
    bindings = source.rows('workbenches', 'bindings') if apps else []
    projection_projects = source.rows('workbenches', 'projects') if apps else []
    targets = {row['application']: row for row in source.rows('lineage', 'application_targets')} if types else {}

    def include(collection, identity, reason):
        collection.setdefault(identity, set()).add(reason)

    def projection(app, reason):
        owner = owners.get(app.get('builtin'))
        if owner:
            required_features.add(owner['feature'])
            if owner['selection_feature']:
                required_features.add(owner['selection_feature'])
        revision = app.get('published')
        if revision is None:
            gaps.append({'id': app['id'], 'reason': 'Required projection has no published revision'})
            return
        selected = [b for b in bindings if b['app_id'] == app['id'] and b['revision'] == revision]
        if not selected:
            gaps.append({'id': app['id'], 'reason': 'Required projection has no published Lineage binding'})
        projections.setdefault(app['id'], {'id': app['id'], 'revision': revision, 'required_by': [],
            'bindings': sorted([{'alias': b['alias'], 'lineage_id': b['lineage_id']} for b in selected], key=lambda b: b['alias'])})['required_by'].append(reason)
        if owner and owner['selection_feature']:
            projections[app['id']]['selection_feature'] = owner['selection_feature']
            projections[app['id']]['builtin'] = app['builtin']
        for binding in selected:
            include(lineages, binding['lineage_id'], app['id'])
            edges.append({'from': app['id'], 'to': binding['lineage_id'], 'relation': 'published_binding', 'revision': revision})
        for row in projection_projects:
            if row['app_id'] == app['id'] and row['revision'] == revision:
                include(projects, row['project_id'], app['id'])
                edges.append({'from': app['id'], 'to': row['project_id'], 'relation': 'projection_project', 'revision': revision})

    for kind in sorted(types):
        declaration = declarations.get(kind)
        if declaration is None:
            gaps.append({'id': kind, 'reason': 'Resource type is not registered'})
            continue
        page = 'resource:' + kind
        pages.append({'id': page, 'resource_type': kind, 'provider': declaration['provider']})
        edges.extend({'from': origin, 'to': page, 'relation': 'selected_resource_type'} for origin in sorted(origins[kind]))
        chats = ({'knowledge'} if kind in KNOWLEDGE_TYPES else set()) | ({kind} if kind in CHAT_TYPES else set())
        for chat in sorted(chats):
            application = 'resource_chat:' + chat
            target = targets.get(application, {})
            lineage = target.get('lineage_id')
            if not lineage:
                gaps.append({'id': kind, 'application': application, 'reason': 'Required resource page application has no Lineage target'})
                continue
            applications.add(application)
            include(lineages, lineage, page)
            edges.append({'from': page, 'to': lineage, 'relation': 'application_target', 'application': application})
        for feature in FEATURES:
            if kind not in feature.related_types:
                continue
            required_features.add(feature.key)
            hook = feature.contributions.get('workbench.builtin')
            if not hook:
                continue
            module, attribute = hook.split(':', 1)
            builtin = getattr(importlib.import_module(module), attribute)['name']
            matches = [app for app in apps if app['builtin'] == builtin]
            if len(matches) != 1:
                gaps.append({'id': kind, 'builtin': builtin, 'reason': 'Required page must have one unambiguous installed projection'})
            else:
                projection(matches[0], page)
                edges.append({'from': page, 'to': matches[0]['id'], 'relation': 'feature_workbench', 'feature': feature.key})
    for builtin, owner in owners.items():
        if owner['selection_feature'] not in selected_presentations:
            continue
        matches = [app for app in apps if app.get('builtin') == builtin]
        if len(matches) != 1:
            gaps.append({'id': owner['selection_feature'], 'builtin': builtin, 'reason': 'Selected view must have one unambiguous installed projection'})
        else:
            projection(matches[0], 'feature_states.' + owner['selection_feature'])
    for item in snapshot.get('projections', []):
        match = next((app for app in apps if app['id'] == item['id']), None)
        if match:
            projection(match, 'manual.projections')
        else:
            gaps.append({'id': item['id'], 'reason': 'Selected projection is missing'})
    for item in snapshot.get('lineages', []):
        include(lineages, item['id'], 'manual.lineages')
    from .projection_dependencies import declarations as projection_declarations
    try:
        projection_resources = projection_declarations(source, projections.values())
    except (ValueError, OSError) as error:
        projection_resources = []
        gaps.append({'reason': str(error), 'path': 'projection_resources'})
    if projection_resources:
        required_features.add('repo')
        dependency_ids = {item['code_repository'] for item in projection_resources}
        dependency_ids.update(file['address'].split('/file/', 1)[0]
                              for item in projection_resources for file in item['files'])
        for identity, record in source.read_index(resource_ids=dependency_ids).items():
            for project in record.get('attributes', {}).get('project_ids', []):
                include(projects, project, identity)
    definitions = source.rows('lineage', 'lineages', predicates={'lineage_id': set(lineages)}) if lineages else []
    found = {row['lineage_id'] for row in definitions if not row.get('deleted_at')}
    for missing in sorted(set(lineages) - found):
        gaps.append({'id': missing, 'reason': 'Required Lineage definition is missing'})
    for row in definitions:
        if row.get('project_id'):
            include(projects, row['project_id'], row['lineage_id'])
            edges.append({'from': row['lineage_id'], 'to': row['project_id'], 'relation': 'lineage_project'})
    # Project entry ownership is a native persisted relation, not a title or
    # the ordinary resource-chat Project Lineage. It selects only that graph;
    # its references must never pull unselected sibling Lineages into export.
    entries = source.rows('lineage', 'project_lineage_entries', predicates={'project_id': set(projects)}) if projects else []
    entry_definitions = {row['lineage_id']: row for row in source.rows(
        'lineage', 'lineages', predicates={'lineage_id': {row['lineage_id'] for row in entries}})} if entries else {}
    project_principals = []
    for entry in entries:
        project, identity = entry['project_id'], entry['lineage_id']
        graph = entry_definitions.get(identity)
        if not graph or graph.get('deleted_at') or graph.get('project_id') != project or graph.get('lineage_membership') == 'personal':
            gaps.append({'id': identity, 'project_id': project, 'reason': 'Project principal entry has no matching active Project graph'})
            continue
        include(lineages, identity, 'project_principal:' + project)
        project_principals.append({'project_id': project, 'lineage_id': identity})
        edges.append({'from': project, 'to': identity, 'relation': 'project_principal'})
    for item in projections.values():
        item['required_by'] = sorted(set(item['required_by']))
    return {'schema': SCHEMA, 'input_sha256': input_digest(snapshot), 'types': sorted(types), 'pages': pages,
            'type_reasons': {key: sorted(value) for key, value in sorted(origins.items())},
            'projections': sorted(projections.values(), key=lambda p: p['id']),
            'lineages': [{'id': key, 'required_by': sorted(value)} for key, value in sorted(lineages.items())],
            'projects': [{'id': key, 'required_by': sorted(value)} for key, value in sorted(projects.items())],
            'project_principals': sorted(project_principals, key=lambda item: item['project_id']),
            'projection_resources': projection_resources,
            'features': sorted(required_features), 'application_targets': sorted(applications),
            'application_bindings': [{'application': name, 'lineage_id': targets[name]['lineage_id'],
                                      'revision': targets[name].get('revision')} for name in sorted(applications)],
            'edges': edges, 'gaps': gaps, 'valid': not gaps}


def freeze(snapshot):
    """Refresh derived data for a new config/run; never rewrite an old record."""
    from .database import read_snapshot
    value = deepcopy(snapshot)
    with read_snapshot() as source:
        value['resource_dependencies'] = resolve(value, source)
        from .native_switches import FIELD, snapshot_site
        value[FIELD] = snapshot_site(value, source)
    return value


def frozen_or_resolve(snapshot, source, *, additional_ids=()):
    frozen = snapshot.get('resource_dependencies')
    if frozen:
        legacy = frozen.get('schema') == 'tfi.release-resource-dependencies.v1'
        version = 2 if frozen.get('schema') == 'tfi.release-resource-dependencies.v2' else 3
        expected = digest({key: snapshot.get(key) for key in ('resource_types', 'document_indexes', 'lineages', 'projections')}) if legacy else input_digest(snapshot, version=version)
        if frozen.get('schema') not in {SCHEMA, 'tfi.release-resource-dependencies.v1', 'tfi.release-resource-dependencies.v2', 'tfi.release-resource-dependencies.v3'} or frozen.get('input_sha256') != expected:
            raise ValueError('Frozen resource dependency scope differs from selected inputs')
        if not additional_ids:
            return deepcopy(frozen)
    result = resolve(snapshot, source, additional_ids=additional_ids)
    if frozen:
        # A Resident can resolve additional exact resources, but cannot use
        # that extension to silently refresh already reviewed page bindings.
        for collection, identity, fields in (
            ('application_bindings', 'application', ('lineage_id', 'revision')),
            ('projections', 'id', ('revision', 'bindings')),
            ('project_principals', 'project_id', ('lineage_id',)),
        ):
            current = {item[identity]: item for item in result[collection]}
            for item in frozen.get(collection, []):
                actual = current.get(item[identity], {})
                if any(actual.get(field) != item.get(field) for field in fields):
                    result['gaps'].append({'id': item[identity], 'reason': 'Additional resource resolution encountered changed frozen page bindings'})
        result['valid'] = not result['gaps']
    return result
