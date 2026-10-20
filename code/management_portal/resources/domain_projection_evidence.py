"""Versioned evidence issued by the authenticated internal projection boundary."""
import hashlib
import json
from collections.abc import Mapping


def projection_fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


EVIDENCE_COLLECTION = "domain_tombstone_evidence"
EVIDENCE_SCHEMA = "tfi.domain-tombstone-evidence.v2"

# Replay identity is recorded for every accepted deletion. It is not an
# authenticated receipt and cannot establish authorization or provenance.
SOURCE_COLLECTION = "domain_tombstone_sources"
SOURCE_SCHEMA = "tfi.domain-tombstone-source.v2"


def tombstone_source(descriptor):
    return {"schema": SOURCE_SCHEMA, "resource_id": descriptor["resource_id"],
            "deleted_at": descriptor["deleted_at"],
            "source_fingerprint": projection_fingerprint(descriptor)}


def same_descriptor(left, right):
    """Compare canonical v2 descriptors exactly; payloads stay intact."""
    schema = "tfi.collaboration-descriptor.v2"
    return (isinstance(left, Mapping) and isinstance(right, Mapping)
            and left.get("schema") == schema
            and right.get("schema") == schema
            and left == right)


def matches_tombstone_source(source, descriptor):
    """Match only the canonical v2 proof of this exact descriptor."""
    if (not isinstance(source, Mapping)
            or source.get("schema") != SOURCE_SCHEMA
            or not isinstance(descriptor, Mapping)
            or descriptor.get("schema") != "tfi.collaboration-descriptor.v2"):
        return False
    return source == tombstone_source(descriptor)


def historical_tombstone_retry(source, receipt, descriptor):
    """Existing callers must supply a matching canonical v2 proof and receipt."""
    return (matches_tombstone_source(source, descriptor) and isinstance(receipt, Mapping)
            and receipt.get("schema") == EVIDENCE_SCHEMA
            and receipt.get("domain_identity") == "society-service"
            and same_descriptor(receipt.get("incoming_descriptor"), descriptor))
