"""Effect definitions own their human failure notification recipients."""
from management_portal.portal_access_control import get_portal_access_store

from . import lineage_aggregate, resident_work_origin


def initialize_recipients(program: dict, previous: dict, actor: str) -> None:
    existing = {item["id"]: item for item in previous.get("components") or []}
    defaults = None
    for component in program.get("components") or []:
        if component["kind"] != "effect":
            continue
        config = component["config"]
        old = existing.get(component["id"])
        if "failure_recipients" not in config:
            if old:
                old_config = old.get("config") or {}
                # An older Effect without a list stays unconfigured (not notified) until a
                # person sets one; other saves must not turn it into an explicit closed list.
                if "failure_recipients" in old_config:
                    config["failure_recipients"] = list(old_config["failure_recipients"] or [])
            else:
                if defaults is None:
                    defaults = resident_work_origin.notification_recipients(actor)
                config["failure_recipients"] = list(defaults)
            continue  # The server derived these from authenticated work provenance.
        # Invalid identities must not be silently saved as an apparently valid list.
        old_recipients = (old or {}).get("config", {}).get("failure_recipients", [])
        for username in config["failure_recipients"]:
            if username in old_recipients:
                continue
            user = get_portal_access_store().user(username)
            if not user.get("configured") or user.get("status") != "active":
                raise ValueError(f"Effect 失败通知收件人不是有效 User：{username}")


def recipients(lineage_id: str, effect_id: str) -> list[str]:
    try:
        lineage = lineage_aggregate.read_lineage_state(lineage_id)
    except (KeyError, FileNotFoundError):
        return []
    component = next((item for item in (lineage.get("program") or {}).get("components") or []
                      if item.get("id") == effect_id and item.get("kind") == "effect"), None)
    if component is None:
        return []
    selected = (component.get("config") or {}).get("failure_recipients") or []
    return [username for username in selected
            if (user := get_portal_access_store().user(username)).get("configured")
            and user.get("status") == "active"]
