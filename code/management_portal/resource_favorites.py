"""Personal resource bookmarks backed by the presentation store, never grants."""
from management_common.resource_identity import get_resource_type_registry
from management_portal import presentation_store
from management_portal.resources.address import AddressParser

SHARED_SURFACES = ('paper', 'resident', 'workbench')


def canonical_address(address):
    return AddressParser(get_resource_type_registry()).parse(address).value


def storage_key(address):
    root, separator, _ = address.partition('/')
    if not separator:
        for surface in SHARED_SURFACES:
            if root.startswith(surface + '_'):
                return surface, root
        if root.startswith('lineage_'):
            return 'lineage', 'lineage:' + root
    return 'resource', address


def addresses(actor):
    if not actor:
        raise PermissionError('需要登录后操作收藏')
    result = presentation_store.favorite_ids(actor, 'resource')
    for surface in (*SHARED_SURFACES, 'lineage'):
        for value in presentation_store.favorite_ids(actor, surface):
            if surface == 'lineage':
                if not value.startswith('lineage:'):
                    continue
                value = value.split(':', 1)[1]
            result.append(value)
    return list(dict.fromkeys(result))


def set_favorite(actor, address, favorite, reference):
    if not actor:
        raise PermissionError('需要登录后操作收藏')
    if type(favorite) is not bool:
        raise ValueError('favorite must be a boolean')
    address = canonical_address(address)
    if favorite:
        reference(address, actor)
    surface, target = storage_key(address)
    presentation_store.set_favorite(actor, surface, target, favorite)
    return {'address': address, 'favorite': favorite}


def list_favorites(actor, reference):
    result = []
    for address in addresses(actor):
        try:
            item = reference(address, actor)
            result.append({**item, 'address': address, 'status': 'available'})
        except PermissionError:
            result.append({'address': address, 'status': 'denied'})
        except (KeyError, FileNotFoundError, ValueError):
            result.append({'address': address, 'status': 'missing'})
    return {'items': result}
