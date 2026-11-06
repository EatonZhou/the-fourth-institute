"""Society owns reporting intent; Execution receives only the materialized prompt."""
from __future__ import annotations

import json
from pathlib import Path

from . import resident_record_domain as records

from .resident_settings_contract import REPORTING_DEFAULT as DEFAULT, normalize_reporting as normalize

AMA_DEFAULT = {"initiative": "medium", "quality": "high"}


def configured(record: dict) -> dict[str, str]:
    value = record.get("reporting")
    if value is not None and value != {}:
        return normalize(value)
    return dict(AMA_DEFAULT if _personal_ama_entry(record) else DEFAULT)


def _personal_ama_entry(record: dict) -> bool:
    """Use canonical personal ownership and the actual entry relation, never titles."""
    owner = str(record.get("personal_owner_id") or "")
    lineage_id = str(record.get("lineage_id") or "")
    resident_id = str(record.get("resident_id") or "")
    if not owner or not lineage_id or record.get("deleted_at"):
        return False
    from .state_store import get_lineage_data_store
    homes = get_lineage_data_store().lineage_access_records([lineage_id])
    home = next((row for row in homes if row["lineage_id"] == lineage_id), {})
    if (home.get("lineage_membership") != "personal"
            or str(home.get("created_by") or "").casefold() != owner.casefold()
            or home.get("leader_resident_id") != resident_id
            or resident_id not in home.get("lineage_node_ids", [])):
        return False
    family = record.get("family") or {}
    if not family:
        return True  # The user's registered AMA before personal Familize.
    if family.get("role") != "facade" or not family.get("personal_ama_id"):
        return False
    ama = records.find_resident(family["personal_ama_id"])
    return bool(ama and not ama.get("deleted_at") and ama.get("lineage_id") == lineage_id
                and str(ama.get("personal_owner_id") or "").casefold() == owner.casefold()
                and (ama.get("family") or {}).get("family_id") == family.get("family_id")
                and (ama.get("family") or {}).get("role") == "member")


def effective(resident_id: str, message_id: str = "") -> dict:
    from . import family_facade
    source = resident_id
    if message_id:
        chain = family_facade.relay_sources_of(resident_id, message_id)
        if chain:
            source = chain[-1][0]
    record = records.find_resident(source)
    if not record:
        return {"state": "unknown", "source_resident_id": source}
    return {**configured(record), "state": "known", "source_resident_id": source,
            "source": "family_facade" if family_facade.is_facade(record) else "resident",
            "configured": bool(record.get("reporting")),
            "application": "next_input"}


TEMPLATE_ROOT = Path(__file__).with_name("prompt_templates") / "reporting"
INITIATIVE_TEMPLATES = {name: f"initiative_{name}.md" for name in ("off", "low", "medium", "high")}
QUALITY_TEMPLATES = {name: f"quality_{name}.md" for name in ("low", "medium", "high")}


def template_paths(policy: dict) -> tuple[Path, ...]:
    """Select only shipped templates, never a path supplied by message text."""
    scope = (TEMPLATE_ROOT / "scope.md",)
    if policy.get("state") != "known":
        return (*scope, TEMPLATE_ROOT / "unknown.md")
    normalize({key: policy[key] for key in DEFAULT})
    paths = (*scope, TEMPLATE_ROOT / INITIATIVE_TEMPLATES[policy["initiative"]])
    if policy["initiative"] == "off":
        return paths
    return (*paths, TEMPLATE_ROOT / QUALITY_TEMPLATES[policy["quality"]], TEMPLATE_ROOT / "enabled.md")


def prompt(record: dict, job: dict) -> str:
    from string import Template
    policy = effective(record["resident_id"], str(job.get("user_message_id") or ""))
    code_root = Path(__file__).resolve().parents[2]
    body = "\n\n".join(Template(path.read_text(encoding="utf-8")).substitute(code_root=str(code_root)).strip()
                         for path in template_paths(policy))
    return "<reporting-policy>\n" + json.dumps(policy, ensure_ascii=False) + "\n" + body + "\n</reporting-policy>"


