"""Freeze only native binding inputs; the native owner validates semantics."""
from copy import deepcopy

NAMES = ('pulse', 'architecture_audit')
FIELD = 'native_switch_site_config'
SYSTEM = 'lineage_ed619225d417'


def minimal_site_config(site):
    from management_portal.portal_feature_switches import mapping_contract
    if not isinstance(site, dict):
        raise ValueError('Native switch site configuration must be an object')
    deployment = site.get('deployment', {})
    if not isinstance(deployment, dict):
        return {'deployment': deepcopy(deployment)}
    result = {}
    for name in NAMES:
        contract = mapping_contract(name)
        section = contract['site_section'][1]
        if section not in deployment:
            continue
        value = deployment[section]
        result[section] = ({key: deepcopy(value[key]) for key in
                           (contract['lineage_field'], contract['components_field']) if key in value}
                          if isinstance(value, dict) else deepcopy(value))
    return {'deployment': result}


def snapshot_site(snapshot, source=None):
    if FIELD in snapshot:
        return minimal_site_config(snapshot[FIELD])
    reader = getattr(source, 'installation_site_config', None)
    return minimal_site_config(reader() if reader is not None else {})


def mappings(snapshot, source, dependencies):
    from management_portal.portal_feature_switches import installation_mapping
    site = snapshot_site(snapshot, source)
    selected = {item['id'] for item in (dependencies or {}).get('lineages', [])} - {SYSTEM}
    rows = source.definition_rows(selected) if source is not None and selected else {}
    return {name: installation_mapping(name, site_config=site, selected_lineage_ids=selected,
                                      definition_rows=rows) for name in NAMES}
