"""Installed Resident panels own their portable files, never their runtime data."""
import json
from pathlib import Path

from management_common.configuration_files import atomic_json
from management_common.instance_assets import reconcile
from . import resident_catalog, watchboard_lineage
from . import resident_watchboard_domain as panels


def update(instance, phase, plan):
    result = {'panels': [], 'blockers': []}
    templates = json.loads((Path(__file__).parents[1] / 'watchboards' / 'update_templates.json').read_text())
    ledger_path = instance.directory('runtime') / 'managed-watchboard-assets.json'
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
    for resident in resident_catalog.get_catalog().all():
        if not watchboard_lineage.installed(resident):
            continue
        identity = resident['resident_id']
        root = watchboard_lineage.root(resident)
        entry = {'resident_id': identity, 'root': str(root)}
        # These are versioned product templates with stable resource identities,
        # not matches on a user's title or directory name. Unknown panels are
        # validated, never replaced with the generic default or seed data.
        template = templates.get(identity)
        if template:
            contents = {name: json.dumps(value, ensure_ascii=False, indent=2) + '\n'
                        for name, value in template.items()}
            changes = reconcile(root, contents, ledger.setdefault(identity, {}), apply=phase == 'apply')
            entry.update(changes)
            result['blockers'].extend(changes['blockers'])
        missing = [name for name in ('agent.yaml', 'watchboard.yaml') if not (root / name).is_file()]
        if missing and (not template or phase == 'verify'):
            result['blockers'].append({'resident_id': identity, 'error': 'missing_panel_assets', 'files': missing})
        if not missing:
            try:
                agent, error = panels.read_watchboard_agent_manifest(resident)
                view, view_error = panels._read_watchboard_view_manifest(resident)
                if error or view_error:
                    raise ValueError('Invalid panel declaration')
                required = []
                if agent.get('commands'):
                    required.append(agent['entrypoint'])
                    required.extend(panels._watchboard_config_paths(resident, agent))
                for name in required:
                    target = root / name
                    if not target.resolve().is_relative_to(root.resolve()) or not target.is_file():
                        raise ValueError('Panel dependency is missing or outside its root')
                entry['validated'] = ['agent.yaml', 'watchboard.yaml', *required]
            except (ValueError, OSError) as exc:
                result['blockers'].append({'resident_id': identity, 'error': type(exc).__name__})
        result['panels'].append(entry)
    if phase == 'apply':
        atomic_json(ledger_path, ledger)
    return result
