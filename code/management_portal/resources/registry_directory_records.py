"""Directory facts extracted by the resource registry owner.

Registry documents remain authoritative. These descriptors enlist only their
common derived relation rows in the authoritative writer's transaction.
"""

COLLECTIONS = ('repo', 'article', 'paper', 'knowledge', 'report')


def locations(collection, document):
    if collection == 'repo':
        entries = document.get('checkouts') or []
        canonical = document.get('canonical_checkout')
    elif collection == 'article':
        entries = document.get('roots') or []
        canonical = document.get('canonical_root')
    elif collection in {'paper', 'knowledge', 'report'}:
        return document.get('directories') or []
    else:
        raise ValueError('Registry collection has not declared its directory locations')
    result = [entry for entry in entries if entry.get('path')]
    if canonical and canonical not in {entry['path'] for entry in result}:
        result.append({'path': canonical, 'owner_id': document['id']})
    return result



def refresh_directory_dependency(connection, collection, resource_id, document):
    if collection not in COLLECTIONS:
        return
    from management_common import directory_dependencies as deps
    from management_common.directory_paths import cleanup_path
    entries = [] if document is None else [
        (str(index), resource_id, cleanup_path(entry['path']), 'protected')
        for index, entry in enumerate(locations(collection, document))]
    deps.replace(connection, 'resource', collection + ':' + resource_id, entries)

