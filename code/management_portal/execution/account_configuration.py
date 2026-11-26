"""Provider-neutral native account configuration instructions."""

from .provider_account_operations import account_operation, unsupported, validate_fields


def get_configuration(inv):
    row, operation = account_operation(inv.argument("account_id"), "configuration")
    include_references = bool(inv.argument("include_provider_references"))
    if include_references and not operation.references:
        raise unsupported("configuration_references")
    return operation(row, include_references=include_references)


def update_configuration(inv):
    account_id = inv.argument("account_id")
    row, operation = account_operation(account_id, "configuration")
    changes = validate_fields(inv.argument("changes"), operation.schema, argument="changes")
    return operation(row, changes=changes, expected_version=inv.expected_version_for(account_id))


HANDLERS = {
    "GetProviderAccountConfiguration": get_configuration,
    "UpdateProviderAccountConfiguration": update_configuration,
}
