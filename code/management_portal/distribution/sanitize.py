"""Transform only the export copy; never mutate a live domain object."""
from __future__ import annotations

import re
import json
from pathlib import PurePosixPath

RESOURCE_ID = re.compile(r"\b[a-z][a-z0-9_]*_[0-9a-f]{12}\b")
SECRET_FIELDS = re.compile(r"(^|_)(password|passwd|secret|credential|private_key|api_key|authorization|cookie|auth_json)(_|$)|^(?:access|refresh|bearer|auth|login|session)_token$", re.I)
ACCOUNT_FIELDS = {"provider_account_id", "account_id", "account_dir", "execution_account", "backend_account_id", "backend_route_id", "auth_file", "secret_file", "credential_path"}
HUMAN_FIELDS = {"created_by", "updated_by", "deleted_by", "actor", "actor_ref", "created_by_actor_ref", "updated_by_actor_ref", "performed_by_actor_ref", "username", "user_id", "user", "owner", "owner_id", "personal_owner_id", "sender_id", "sender", "sender_name", "author", "author_id", "display_name", "human_name", "issued_by", "selected_by"}
ANONYMOUS_USER_ID = 'tfi-exported-user'
ANONYMOUS_LABEL = 'TFI portal'
HUMAN_LABEL_FIELDS = {'sender_name', 'display_name', 'human_name'}
ANONYMIZATION = {'schema': 'tfi.release-anonymization.v1', 'human_principal': {'kind': 'user', 'id': ANONYMOUS_USER_ID},
                 'rule_version': 2, 'recipient_collision_rule': 'one anonymous User per recipient set; other principals preserved',
                 'human_display_name': ANONYMOUS_LABEL, 'authentication_exported': False,
                 'resident_service_mailbox_identities': 'preserved'}
PRIVATE_PARTS = {".git", ".env", ".ssh", ".aws", ".codex", ".claude", ".tfi", ".codex-accounts", ".claude-accounts", ".kimi-accounts", ".antigravity-accounts", "secrets", "node_modules", ".venv", ".nodeenv", "__pycache__", ".pytest_cache"}
PRIVATE_FILES = {"auth.json", "credentials.json", "id_rsa", "id_ed25519", ".pgpass", ".netrc"}
SECRET_VALUE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\b(?:sk|ghp|github_pat)-[A-Za-z0-9_-]{20,}|\bghp_[A-Za-z0-9]{20,}|(?i:bearer)\s+[A-Za-z0-9._-]{24,}|(?i:postgres(?:ql)?://)[^\s:/]+:[^\s@]+@")


def private_path(path):
    parts = PurePosixPath(str(path)).parts
    # Workspace automation can create new numbered runtime profiles after a
    # scope audit. Their authentication/cache directories are never outputs.
    if any(parts[i:i + 2] == ("browser-runtime", "profile") or
           (parts[i] == "impress-runtime" and i + 2 < len(parts) and parts[i + 2] == "lo-profile")
           for i in range(len(parts))):
        return True
    if any(part.startswith(".") and part not in {".", ".github", ".wall", ".well-known", ".gitignore", ".gitattributes", ".editorconfig", ".tfi-workspace", ".tfi-project"} for part in parts):
        return True
    return any(part in PRIVATE_PARTS or part in PRIVATE_FILES or part.startswith(".env.") or part.endswith((".key", ".pem", ".p12")) for part in parts)


class _PathMappings(tuple):
    """Sort once per structured value instead of once for every JSON leaf."""
    def __new__(cls, mappings):
        return super().__new__(cls, sorted(mappings, key=lambda pair: len(pair[0]), reverse=True))


def relocate_path_key(key, mappings):
    """Relocate absolute file-path keys, such as research checksum indexes."""
    if key.startswith('/'):
        for original, replacement in mappings:
            if key == original or key.startswith(original.rstrip('/') + '/'):
                return replacement + key[len(original):]
    return key


def scrub(value, *, mappings=(), field="", anonymize=True, source_usernames=None):
    """Keep role names/Resident identity; remove source authentication values."""
    if source_usernames is not None:
        from .privacy import redact
        # Exact selected-resource relocation must precede generic environment
        # examples; otherwise canonical runtime roots lose their binding.
        value = redact(value, source_usernames, field=field, environment=False)
        clean = scrub(value, mappings=mappings, field=field, anonymize=anonymize)
        return redact(clean, (), field=field)
    if mappings and not isinstance(mappings, _PathMappings):
        mappings = _PathMappings(mappings)
    if SECRET_FIELDS.search(field) or field in ACCOUNT_FIELDS:
        return None
    if isinstance(value, dict):
        human = anonymize and value.get("kind", value.get("type")) in ("human", "user")
        result = {}
        for key, item in value.items():
            if SECRET_FIELDS.search(key) or key in ACCOUNT_FIELDS:
                continue
            mapped_key = relocate_path_key(key, mappings)
            if mapped_key in result:
                raise ValueError('Relocated JSON path keys collide')
            result[mapped_key] = (ANONYMOUS_USER_ID if human and key == 'id' else ANONYMOUS_LABEL if human and key in {'name', 'title'} else scrub(item, mappings=mappings, field=key, anonymize=anonymize))
        return result
    if isinstance(value, (tuple, list)):
        result = [scrub(item, mappings=mappings, field=field, anonymize=anonymize) for item in value]
        if anonymize and field in {'recipients', 'recipient_users', 'recipient_user_ids'}:
            # Recipient lists are sets of identities, unlike messages or text.
            # Only the human identity collapsed by this transform is merged.
            seen_anonymous = False
            kept = []
            for item in result:
                anonymous = (isinstance(item, dict) and item.get('kind') == 'user' and item.get('id') == ANONYMOUS_USER_ID) if field == 'recipients' else item == ANONYMOUS_USER_ID
                if anonymous and seen_anonymous:
                    continue
                seen_anonymous = seen_anonymous or anonymous
                kept.append(item)
            return kept
        return result
    if isinstance(value, str):
        # Frozen control evidence can contain typed JSON encoded in TEXT.
        # Preserve that storage shape while treating its fields semantically.
        if field.endswith('_json'):
            try:
                embedded = json.loads(value)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(embedded, (dict, list)):
                    return json.dumps(scrub(embedded, mappings=mappings, anonymize=anonymize), ensure_ascii=False, separators=(',', ':'))
        if anonymize and (field in HUMAN_FIELDS or field in {'recipient_users', 'recipient_user_ids', 'human_familizer_id', 'writer_id', 'initiated_by'}) and value and not value.startswith(("resident_", "family_", "resident:", "agent:resident_", "@", "${")):
            from management_common.principal import SERVICE_IDS, NON_PRINCIPAL_SENDER_IDS
            if value in SERVICE_IDS or value in NON_PRINCIPAL_SENDER_IDS:
                return value
            return ANONYMOUS_LABEL if field in HUMAN_LABEL_FIELDS else ANONYMOUS_USER_ID
        for original, replacement in mappings:
            value = value.replace(original, replacement)
        return SECRET_VALUE.sub("[removed source credential]", value)
    return value


def knowledge_links(text, selected):
    """Prune only references; plain prose and IDs are not selection expansion."""
    import yaml
    body = text
    prefix = ""
    if text.startswith("---\n") and "\n---\n" in text[4:]:
        header, body = text[4:].split("\n---\n", 1)
        metadata = yaml.safe_load(header)
        if isinstance(metadata, dict):
            links = metadata.get("links")
            if isinstance(links, dict):
                metadata["links"] = {key: [v for v in values if not RESOURCE_ID.search(str(v)) or all(i in selected for i in RESOURCE_ID.findall(str(v)))]
                                     for key, values in links.items() if isinstance(values, list)}
            metadata = scrub(metadata)
            prefix = "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + "---\n"
    def link(match):
        label, target = match.groups()
        ids = RESOURCE_ID.findall(target)
        if ids and any(identity not in selected for identity in ids):
            return label
        if ids:
            return f"[{label}](/r/{ids[0]})"
        return match.group(0)
    return prefix + re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, body)


def prune_permissions(tables, selected, *, rule_nodes=()):
    """Drop dangling subjects/resources and descendants without broadening grants."""
    nodes = tables.get("scheme_nodes", [])
    removed = set()
    for node in nodes:
        identity = str(node.get("id", ""))
        references = RESOURCE_ID.findall(identity)
        # Human principals are never exported as login-capable subjects.
        human = node.get("tree") in {"P", "p"} and node.get("kind") in {"user", "human"}
        omitted_object = node.get('kind') not in (None, '', 'class') and identity not in selected
        if human or omitted_object or any(ref not in selected for ref in references):
            removed.add((node["scheme_id"], node["tree"], identity))
    changed = True
    while changed:
        changed = False
        for node in nodes:
            key = (node["scheme_id"], node["tree"], node["id"])
            if key not in removed and (node["scheme_id"], node["tree"], node["parent"]) in removed:
                removed.add(key)
                changed = True
    kept = [node for node in nodes if (node["scheme_id"], node["tree"], node["id"]) not in removed]
    valid = {(node["scheme_id"], str(node["tree"]).lower(), node["id"]) for node in kept}
    # A classes/elements and P/R classes belong to compiled rules, not the
    # instance's object placement table. Do not discard their valid entries.
    valid.update(rule_nodes)
    tables["scheme_nodes"] = kept
    tables["scheme_entries"] = [entry for entry in tables.get("scheme_entries", [])
        if not entry.get("expires_at") and all((entry["scheme_id"], tree, entry.get(tree + "_node")) in valid for tree in ("p", "a", "r"))]
    tables["scheme_objects"] = [row for row in tables.get("scheme_objects", []) if row.get("object_id") in selected]
    return tables
