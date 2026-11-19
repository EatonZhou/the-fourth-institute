"""Knowledge type groups derived from the shared resource declarations.

Declaration readers and runtime providers use the same groups without
loading one another's implementation.
"""
from .resource_identity import get_resource_type_registry


KNOWLEDGE_TYPES = tuple(
    kind for kind, declaration in get_resource_type_registry().declarations.items()
    if declaration['provider'].partition(':')[0] in {
        'management_portal.resources.providers.knowledge',
        'management_portal.resources.providers.experiment',
    }
)
MUTABLE_RESOURCE_TYPES = tuple(kind for kind in KNOWLEDGE_TYPES if kind != "experiment")
MARKDOWN_REGISTERABLE_RESOURCE_TYPES = tuple(
    kind for kind in MUTABLE_RESOURCE_TYPES if kind != "skill"
)
