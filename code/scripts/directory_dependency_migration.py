"""Installation composition for directory owners; no independent transaction."""


def migrate_all_dependencies(connection, *, check_budget=lambda: None):
    """Certify all installed owners before publishing directory readiness.

    Both callbacks receive the caller's connection, including its migration
    budget adapter. Each owner alone interprets its authoritative records.
    """
    from management_portal.resources.directory_identity_lifecycle import migrate_dependencies
    from management_portal.society_service.directory_owner import migrate_identity_directories

    return migrate_dependencies(connection, identity_source=migrate_identity_directories,
                                check_budget=check_budget)
