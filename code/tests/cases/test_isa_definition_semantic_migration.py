"""society: current definitions migrate contracts, while execution evidence stays fixed."""

from copy import deepcopy
from hashlib import sha256
import json

import pytest

from management_portal.society_service import instruction_name_migration as migration
from management_portal.society_service import lineage_effect_script as script
from tests.cases.test_lineage_generic_primitives import ACTOR, WORK_A, _worker, primitive_store  # noqa: F401


def calls(source, inputs=None):
    observed = []

    def execute(request):
        observed.append((request.instruction, request.arguments))
        return script.CallResult(True, {"post": {"id": "post_kept"}})

    outcome = script.Interpreter(script.parse(source), {"arguments": inputs or {}}, execute).run()
    assert outcome.status == "succeeded"
    return observed, outcome.outputs


def test_semantic_rewrite_preserves_boolean_arguments_call_order_and_result_access():
    source = '''# SetPostPinned is historical commentary
first=$(PinPost post_id="post_kept" pinned:=false)
second=$(SetPostFeatured post_id="post_kept" featured="${arguments.mark}")
output post="${first.post}" literal="SetPostPinned"'''
    updated = migration.normalize_script(source)
    assert calls(updated, {"mark": True}) == ([
        ("UpdatePostMarks", {"post_id": "post_kept", "pinned": False}),
        ("UpdatePostMarks", {"post_id": "post_kept", "featured": True}),
    ], {"post": {"id": "post_kept"}, "literal": "SetPostPinned"})
    assert "# SetPostPinned is historical commentary" in updated
    assert migration.normalize_script(updated) == updated


@pytest.mark.parametrize("value,expected", [
    ('"openai"', "openai"),
    ('"${arguments.provider}"', {"native": [1, False]}),
    ('\'{"native":${arguments.provider}}\'', {"native": {"native": [1, False]}}),
])
def test_provider_fields_pack_into_objects_preserving_the_original_value(value, expected):
    operator = ":=" if value.startswith("'") else "="
    source = (
        f'UpdateProviderAccountConfiguration account_id="account" model_provider{operator}{value}\n'
        'login=$(OpenProviderAccountLogin provider="kimi" region="international")'
    )
    normalized = migration.normalize_script(source)
    observed, _ = calls(normalized, {"provider": {"native": [1, False]}})
    assert observed == [
        ("UpdateProviderAccountConfiguration", {"account_id": "account", "changes": {"model_provider": expected}}),
        ("CreateProviderAccountLogin", {"provider": "kimi", "options": {"region": "international"}}),
    ]
    assert migration.normalize_script(normalized) == normalized


@pytest.mark.parametrize("source", [
    'UpdateProviderAccountConfiguration model_provider="old" changes:={"model_provider":"new"}',
    'CreateProviderAccountLogin region="cn" options:={"region":"international"}',
    'UpdateProviderAccountConfiguration model_provider="prefix-${arguments.provider}"',
    'CreateProviderAccountLogin region="${arguments.region:1:2}"',
])
def test_ambiguous_parameter_rewrite_requires_owner_resolution(source):
    with pytest.raises(ValueError, match="definition migration"):
        migration.normalize_script(source)


def test_single_mark_migration_does_not_authorize_previously_invalid_dual_mark_calls():
    with pytest.raises(ValueError, match="old_single_mark_arguments_conflict"):
        migration.normalize_script('SetPostPinned post_id="post_kept" pinned:=true featured:=true')
    with pytest.raises(ValueError, match="old_single_mark_arguments_conflict"):
        migration.normalize_component_config({"instruction": "FeaturePost", "arguments": {"pinned": False}})
    current = 'UpdatePostMarks post_id="post_kept" pinned:=true featured:=true'
    assert migration.normalize_script(current) == current


@pytest.mark.parametrize("instruction", [
    "PublishRelease", "InstallProviderRuntime", "PrepareProviderRuntime", "SetColabSessionRunning",
    "CheckArticleRestore", "PreviewArticleRestore", "RestoreArticle", "GetResearchMapDigest",
    "GetKnowledgeTags", "UpdateKnowledgeTags",
])
def test_removed_calls_are_diagnosed_instead_of_replacing_results_with_async_handles(instruction):
    with pytest.raises(ValueError, match="definition migration") as failure:
        migration.normalize_script(f'answer=$({instruction})\noutput value="${{answer.value}}"')
    assert instruction in str(failure.value)
    assert "RunScript" not in str(failure.value) or "owner" in str(failure.value)


def test_structured_calls_prompts_and_templates_share_migration_but_review_stays_fixed():
    draft = {"script": "RestoreArticle", "hash": "original-approval-hash"}
    original = {
        "instruction": "SetPostPinned", "arguments": {"post_id": "post_kept", "pinned": False},
        "script_draft": draft,
        "prompt": "SetPostFeatured 与 PublishRelease、GetKnowledgeTags。",
        "nested": {"instruction": "CreateProviderAccountLogin", "arguments": {"provider": "kimi", "region": "cn"}},
    }
    before = deepcopy(original)
    actual = migration.normalize_component_config(original)
    assert original == before
    assert actual["instruction"] == "UpdatePostMarks"
    assert actual["arguments"] == before["arguments"]
    assert actual["nested"]["arguments"] == {"provider": "kimi", "options": {"region": "cn"}}
    assert actual["script_draft"] == draft
    assert "SetPostFeatured" not in actual["prompt"]
    assert "PublishRelease" not in actual["prompt"]
    assert "GetKnowledgeTags" not in actual["prompt"]
    assert migration.normalize_component_config(actual) == actual
    assert migration.normalize_template({"body": original})["body"] == actual
    with pytest.raises(ValueError, match="definition migration"):
        migration.normalize_wire_config({"instruction": "GetKnowledgeTags", "arguments": {}})


def make_lineage(source):
    from management_portal.society_service import lineage_lifecycle

    return lineage_lifecycle.create_lineage({
        "title": "Semantic cutover", "nodes": [_worker(WORK_A)], "edges": [],
        "program": {"components": [{
            "id": "effect_a11a11a11a11", "kind": "effect",
            "config": {"script": source, "endpoints": []},
        }], "wires": []},
    }, ACTOR)["lineage_id"]


def test_builtin_digest_migrates_as_a_whole_and_custom_variants_require_resolution():
    from management_portal import research_map_automation as digest

    updated = migration.normalize_script(digest.LEGACY_SCRIPT)
    assert updated == digest.digest_script()
    assert migration.normalize_template({"body": {"script": digest.LEGACY_SCRIPT}})["body"]["script"] == updated
    assert migration.normalize_script(updated) == updated
    with pytest.raises(ValueError, match="operation_moved_out_of_isa"):
        migration.normalize_script(digest.LEGACY_SCRIPT + '\noutput custom="retained"')


@pytest.fixture
def migration_features(monkeypatch):
    from management_portal import portal_features

    clear = portal_features.contributions.cache_clear
    clear()
    yield portal_features
    clear()


@pytest.mark.parametrize("absent", ["omitted", "not_installed"])
def test_migration_without_optional_feature_does_not_load_it_or_accept_its_retired_calls(
    migration_features, monkeypatch, absent,
):
    features = migration_features
    module = "management_portal.research_map_automation"
    imported = []
    original_import = features.importlib.import_module

    def load(name, *args, **kwargs):
        if name == module:
            imported.append(name)
            assert absent == "not_installed", "omitted feature must not be loaded"
            raise ModuleNotFoundError(name=module)
        return original_import(name, *args, **kwargs)

    if absent == "omitted":
        monkeypatch.setattr(features, "FEATURES", tuple(f for f in features.FEATURES if f.key != "projects"))
    monkeypatch.setattr(features.importlib, "import_module", load)
    assert features.contributions("workflow.script_migration") == ()
    assert imported == ([] if absent == "omitted" else [module])
    assert migration.normalize_script('SetPostPinned pinned:=false') == 'UpdatePostMarks pinned:=false'
    with pytest.raises(migration.DefinitionMigrationRequired, match="GetResearchMapDigest"):
        migration.normalize_script('digest=$(GetResearchMapDigest)\noutput value="${digest.value}"')


@pytest.mark.parametrize("problem", ["multiple_owners", "invalid_result", "broken_owner"])
def test_feature_script_migration_contract_fails_closed(migration_features, monkeypatch, problem):
    source = 'output status="original"'

    def owner(original):
        assert original == source
        if problem == "broken_owner":
            raise RuntimeError("feature migration failed")
        return None if problem == "invalid_result" else 'output status="migrated"'

    def contributions(hook):
        assert hook == "workflow.script_migration"
        return (("first", owner), ("second", owner)) if problem == "multiple_owners" else (("first", owner),)

    monkeypatch.setattr(migration_features, "contributions", contributions)
    with pytest.raises({"multiple_owners": ValueError, "invalid_result": TypeError,
                        "broken_owner": RuntimeError}[problem]):
        migration.normalize_script(source)


def test_society_script_migration_has_no_feature_import_edge():
    from fnmatch import fnmatchcase
    from management_portal.portal_features import FEATURES
    from tests.cases.test_core_layers import _imports

    targets = _imports()["management_portal.society_service.instruction_name_migration"]
    patterns = [pattern for feature in FEATURES for pattern in feature.modules]
    assert not {target for target in targets if any(fnmatchcase(target, pattern) for pattern in patterns)}
    assert "management_portal.portal_features" in targets


def test_read_only_preflight_collects_unknown_current_calls_and_templates_without_partial_writes(
    primitive_store, monkeypatch,
):
    from management_portal.society_service import lineage_aggregate
    from management_portal.society_service.templates import store

    lineage_id = make_lineage('result=$(RestoreArticle article_id="sensitive")\nGetKnowledgeTags')
    catalog = lineage_aggregate.lineage_catalog_store()
    before = catalog.get(lineage_id)
    template = store.save({
        "kind": "script", "category": "apply", "title": "Legacy export",
        "description": "", "targets": ["canvas"], "parameters": [],
        "body": {"script": 'output status="ok"', "endpoints": []},
    }, ACTOR)
    # Seed a genuinely old current template in this isolated database only.
    body = {"script": 'UpdateKnowledgeTags tags:=["private-payload"]', "endpoints": []}
    with store.database().transaction("templates") as connection:
        connection.execute("UPDATE templates SET body=%s::jsonb WHERE template_id=%s",
                           (json.dumps(body), template["template_id"]))

    from management_portal.society_service import lineage_metadata_storage
    original_read = lineage_metadata_storage.read_many

    def read_only(connection, *args):
        assert connection.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] == "on"
        return original_read(connection, *args)

    with monkeypatch.context() as patch:
        patch.setattr(lineage_metadata_storage, "read_many", read_only)
        patch.setattr(lineage_aggregate, "lineage_catalog_store", lambda: pytest.fail("owner store constructed"))
        patch.setattr(store, "database", lambda: pytest.fail("template schema initialized"))
        report = migration.check()
    assert report["blocked_count"] == 3
    assert {item["instruction"] for item in report["blockers"]} == {
        "RestoreArticle", "GetKnowledgeTags", "UpdateKnowledgeTags",
    }
    effect = [item for item in report["blockers"] if item["kind"] == "definition_contract"]
    assert [item["line"] for item in effect] == [1, 2]
    assert all(item["lineage_id"] == lineage_id and item["path"] == ["script"] for item in effect)
    assert "sensitive" not in json.dumps(report) and "private-payload" not in json.dumps(report)
    with pytest.raises(RuntimeError, match="unresolved contracts"):
        migration.migrate()
    assert catalog.get(lineage_id) == before
    with store.database().read() as connection:
        saved = next(row for row in store.read_rows(connection, "templates") if row["template_id"] == template["template_id"])
    assert saved["body"] == body and saved["version"] == template["version"]


def test_semantic_cutover_waits_for_journal_then_preserves_historical_calls_and_versions(primitive_store):
    from management_portal.society_service import lineage_aggregate
    from management_portal.society_service import lineage_effect_script_journal as journal

    source = 'UpdateProviderAccountConfiguration account_id="account" model_provider="openai"'
    lineage_id = make_lineage(source)
    catalog = lineage_aggregate.lineage_catalog_store()
    before = catalog.get(lineage_id)
    journal.begin(lineage_id, "semantic-running", effect_id="effect_a11a11a11a11", trigger_path_id="",
                  script_sha=sha256(source.encode()).hexdigest(), source={})
    journal.record_call_started(lineage_id, "semantic-running", "c1", {
        "instruction": "UpdateProviderAccountConfiguration", "caller": ACTOR,
        "arguments": {"model_provider": "openai"}, "instruction_id": "inst_lin_11111111111111111111",
    })
    active = journal.load(lineage_id, "semantic-running")
    assert migration.check()["blockers"][0]["activation_key"] == "semantic-running"
    with pytest.raises(RuntimeError, match="semantic-running"):
        migration.migrate()
    assert catalog.get(lineage_id) == before
    assert journal.load(lineage_id, "semantic-running") == active
    journal.record_call(lineage_id, "semantic-running", "c1", {
        "instruction": "UpdateProviderAccountConfiguration", "caller": ACTOR,
        "status": "committed", "ok": True, "result": {}, "arguments": {"model_provider": "openai"},
    })
    journal.finish(lineage_id, "semantic-running", status="succeeded", outputs={})
    history = journal.load(lineage_id, "semantic-running")
    assert migration.check()["blocked_count"] == 0
    migration.migrate()
    after = catalog.get(lineage_id)
    assert after["revision"] == before["revision"] + 1
    assert after["layout_revision"] == before["layout_revision"]
    assert calls(after["program"]["components"][0]["config"]["script"])[0] == [
        ("UpdateProviderAccountConfiguration", {"account_id": "account", "changes": {"model_provider": "openai"}}),
    ]
    assert journal.load(lineage_id, "semantic-running") == history
    migration.migrate()
    assert catalog.get(lineage_id) == after
