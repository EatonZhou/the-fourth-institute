"""The template domain keeps previews and independent store mutations separate."""

import pytest

from management_portal.society_service import lineage_lifecycle, project_folders, templates
from management_portal.society_service.templates import marketplace, store
from tests.cases.isa_test_support import ACTOR, ok, run
from tests.cases.test_resident_folder_creation_contract import (
    future_creation_store as future_creation_store,  # noqa: PLC0414 - pytest fixture
)


@pytest.fixture
def lineage(future_creation_store, monkeypatch):
    monkeypatch.setattr(project_folders, "project_root_for_id", lambda _: future_creation_store)
    return lineage_lifecycle.create_lineage({"title": "Templates", "project_id": "project_test"}, ACTOR)["lineage_id"]


def test_template_previews_do_not_refresh_or_write(lineage, monkeypatch):
    raw = {"kind": "structure", "title": "Preview recipe", "body": {}}
    saved = templates.create(raw, ACTOR)
    identity = saved["template_id"]

    def fail(*args, **kwargs):
        pytest.fail("preview attempted to write template or Lineage state")

    monkeypatch.setattr(store, "ensure_builtins", fail)
    monkeypatch.setattr(store.database(), "transaction", fail)
    from management_portal.society_service import lineage_graph_edits

    monkeypatch.setattr(lineage_graph_edits, "commit_lineage_edit", fail)
    assert templates.create_preview(raw, ACTOR)["title"] == "Preview recipe"
    assert templates.update_preview(identity, {"title": "Edited"}, ACTOR)["title"] == "Edited"
    result = templates.apply_preview(lineage, identity, {"kind": "canvas"}, ACTOR)
    assert result["preview"][0]["created"] == {"roles": 0, "components": 0, "wires": 0}
    assert store.get_persisted(identity)["title"] == "Preview recipe"


def test_template_store_install_update_and_detach_are_distinct(lineage, monkeypatch):
    monkeypatch.setattr(marketplace, "is_admin", lambda actor: False)
    source = templates.create(
        {"kind": "prompt", "title": "Store recipe", "body": {"message": "First"}}, ACTOR
    )
    item = marketplace.publish(source["template_id"], ACTOR)["item"]
    identity = item["store_id"]
    installer = "store-reader"
    installed = marketplace.install(identity, installer)["template"]
    target = installed["template_id"]
    templates.update(source["template_id"], {"title": "New version"}, ACTOR)
    marketplace.publish(source["template_id"], ACTOR)
    assert marketplace.get_item(identity, installer)["has_update"]
    for modified in (False, True):
        if modified:
            templates.update(target, {"title": "Local edit"}, installer)
        repeated = marketplace.install(identity, installer)["template"]
        assert repeated["template_id"] == target
        assert repeated["installed_store_version"] == 1
        assert repeated["store_status"]["locally_modified"] is modified

    with pytest.raises(templates.TemplateError, match="只能更新自己"):
        marketplace.update_installed(identity, ACTOR, target_template_id=target)
    with pytest.raises(templates.TemplateError, match="只能更新自己"):
        marketplace.install_as_new(identity, ACTOR, target_template_id=target)
    updated = marketplace.update_installed(identity, installer, target_template_id=target)["template"]
    assert updated["template_id"] == target and updated["installed_store_version"] == 2
    templates.update(target, {"title": "Keep this edit"}, installer)
    detached = marketplace.install_as_new(identity, installer, target_template_id=target)
    new_id = detached["template"]["template_id"]
    assert detached["detached_template_id"] == target and new_id != target
    assert marketplace.get_item(identity, installer)["installed_template_id"] == new_id
    assert templates.get(target)["title"] == "Keep this edit"
    assert templates.get(target)["store_status"] is None
    marketplace.unpublish(identity, ACTOR)
    with pytest.raises(KeyError):
        marketplace.get_item(identity, installer)


def test_template_instruction_split_is_bound(lineage, monkeypatch):
    monkeypatch.setattr(marketplace, "is_admin", lambda actor: False)
    raw = {"kind": "prompt", "title": "ISA recipe", "body": {"message": "Check this"}}
    assert ok("GetTemplateCreatePreview", {"template": raw})["template"]["title"] == raw["title"]
    assert run("CreateTemplate", {"template": raw, "dry_run": True}).status == "failed"
    template_id = ok("CreateTemplate", {"template": raw})["template"]["template_id"]
    identity = ok("PublishTemplate", {"template_id": template_id})["item"]["store_id"]
    args = {"store_id": identity}
    assert ok("GetTemplateStoreItem", args)["item"]["store_id"] == identity
    assert run("ListTemplateStore", args).status == "failed"
    assert run("InstallStoreTemplate", {**args, "mode": "install"}).status == "failed"
    installed = ok("InstallStoreTemplate", args, actor="template-reader")["template"]
    update = {**args, "target_template_id": installed["template_id"]}
    assert ok("UpdateInstalledTemplate", update, actor="template-reader")["template"]["template_id"] == installed["template_id"]
    new = ok("InstallStoreTemplateAsNew", update, actor="template-reader")
    assert new["detached_template_id"] == installed["template_id"]
    assert new["template"]["template_id"] != installed["template_id"]


def test_template_review_actions_keep_distinct_authority(lineage):
    confirmer = "script-confirmer"
    script = 'output result="ready"'
    raw = {"kind": "structure", "title": "Review candidate", "body": {"components": [
        {"key": "work", "kind": "effect", "config": {"script_draft": {
            "description": "Prepare the output", "script": script, "confirm_by": confirmer,
        }}},
    ]}}

    def draft():
        recipe = ok("CreateTemplate", {"template": raw})["template"]
        config = recipe["body"]["components"][0]["config"]
        return recipe["version"], {
            "template_id": recipe["template_id"], "effect_id": "work",
            "draft_hash": config["script_draft"]["hash"],
        }

    version, args = draft()
    assert run("ReviewTemplateScript", {**args, "decision": "confirm"}, version=version).error["code"] == "instruction.unknown"
    assert run("ConfirmTemplateScript", args, version=version).error["code"] == "instruction.forbidden"
    assert run("CancelTemplateScriptDraft", args, version=version, actor="other-user").error["code"] == "instruction.forbidden"
    cancelled = ok("CancelTemplateScriptDraft", args, version=version)["template"]
    config = cancelled["body"]["components"][0]["config"]
    assert config["script"] == "" and "script_draft" not in config

    version, args = draft()
    assert run("ConfirmTemplateScript", args, version=version + 1, actor=confirmer).error["code"] == "instruction.conflict"
    approved = ok("ConfirmTemplateScript", args, version=version, actor=confirmer)["template"]
    config = approved["body"]["components"][0]["config"]
    assert config["script"] == script and "script_draft" not in config


@pytest.mark.parametrize("rounds", [None, 0, -1, True, 2.5, "5"])
def test_structure_recent_fork_requires_a_positive_integer(rounds):
    role = {"key": "child", "from": "fork", "fork_of": "source", "fork_mode": "recent"}
    if rounds is not None:
        role["recent_rounds"] = rounds
    with pytest.raises(templates.TemplateError, match="正整数 recent_rounds"):
        templates.create_preview(
            {"kind": "structure", "title": "Recent", "body": {
                "roles": [{"key": "source", "from": "target"}, role],
            }},
            ACTOR,
        )


def test_template_fork_migration_preserves_store_versions_and_installation(lineage, monkeypatch):
    monkeypatch.setattr(marketplace, "is_admin", lambda actor: False)
    source = templates.create(
        {"kind": "structure", "title": "Fork recipe", "body": {"roles": [
            {"key": "source", "from": "target"},
            {"key": "child", "from": "fork", "fork_of": "source",
             "fork_mode": "recent", "recent_rounds": 10},
        ]}},
        ACTOR,
    )
    item = marketplace.publish(source["template_id"], ACTOR)["item"]
    store_id = item["store_id"]
    installer = "store-reader"
    installed = marketplace.install(store_id, installer)["template"]
    installed_id = installed["template_id"]

    with store.database().transaction("templates") as connection:
        rows = store.read_rows(connection, "templates")
        snapshots = marketplace.items(connection)
        for row in [*rows, *snapshots]:
            if row.get("template_id") not in {source["template_id"], installed_id} and row.get("store_id") != store_id:
                continue
            role = row["body"]["roles"][1]
            role["fork_mode"] = "recent-10"
            del role["recent_rounds"]
            if row.get("store_id") == store_id:
                row["content_hash"] = store.content_hash(row)
                row["versions"][0]["content_hash"] = row["content_hash"]
            elif row["template_id"] == installed_id:
                row["installed_store_hash"] = store.content_hash(row)
        history = snapshots[0]["versions"]
        store._write(connection, rows)
        marketplace._write(connection, snapshots)

    store.migrate_instruction_names()
    saved = store.get_persisted(source["template_id"])
    copy = templates.get(installed_id)
    active = marketplace.get_item(store_id, installer)
    assert active["versions"] == history and active["version"] == item["version"]
    assert active["body"] == saved["body"] == copy["body"]
    assert active["body"]["roles"][1]["fork_mode"] == "recent"
    assert active["body"]["roles"][1]["recent_rounds"] == 10
    assert active["content_hash"] == copy["installed_store_hash"]
    assert not active["locally_modified"]
    store.migrate_instruction_names()
    assert store.get_persisted(source["template_id"])["version"] == saved["version"]
