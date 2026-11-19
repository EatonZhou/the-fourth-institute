"""A code update must not report missing published application assets as healthy."""
import json

from .store import Store
from .application_build import read_artifact, _artifact_root


def update(instance, phase, plan):
    store = Store()  # Workbench owns its additive table preparation.
    with store.database.read() as db:
        rows = db.execute('SELECT a.id,p.build FROM apps a JOIN application_packages p '
                          'ON p.app_id=a.id AND p.revision=a.published').fetchall()
    result = {'applications': [], 'blockers': []}
    for row in rows:
        try:
            build = json.loads(row['build'])
            if not isinstance(build, dict) or not isinstance(build.get('entries'), dict) or not build['entries']:
                raise ValueError('Published application has no entrypoints')
            # Verify entrypoint bytes with the normal serving contract, and
            # presence/size of the remaining indexed assets (not a full rehash).
            for entry in build.get('entries', {}).values():
                read_artifact(build, entry)
            root = _artifact_root() / build['artifact_hash'] / 'files'
            for path, metadata in build['files'].items():
                target = root / path
                if not target.resolve().is_relative_to(root.resolve()) or target.is_symlink() or target.stat().st_size != metadata['size']:
                    raise ValueError('Published asset is missing or invalid')
            result['applications'].append({'id': row['id'], 'artifact_hash': build['artifact_hash'], 'status': 'ready'})
        except (ValueError, OSError, KeyError) as exc:
            result['blockers'].append({'workbench_id': row['id'], 'error': 'published_assets_unavailable',
                                       'recovery': 'restore_original_artifact_or_publish_through_workbench_owner'})
    return result
