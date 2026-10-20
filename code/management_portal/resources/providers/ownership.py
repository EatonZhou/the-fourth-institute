"""Static provider ownership, available before domain modules are imported."""

from management_common.resource_identity import get_resource_type_registry

KNOWLEDGE_TYPES = tuple(kind for kind, declaration in get_resource_type_registry().declarations.items()
                        if declaration['provider'].partition(':')[0] in {
                            'management_portal.resources.providers.knowledge',
                            'management_portal.resources.providers.experiment',
                        })
