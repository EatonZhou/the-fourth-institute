"""Passive Codex policy regression; no account, native process, model or DB."""
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

from management_portal.execution.backend_contracts import BackendContext
from management_portal.execution.codex_backend import CodexBackend
from management_portal.execution.harness import ContextWindowObservation
from management_portal.execution.input_variants import select_input_variant
from management_portal.execution.local_file_read import assess_local_file_read
from management_portal.execution.native_options import CodexOptions
from tests.cases.test_local_file_read import invocation


IDENTITY = {"PORTAL_RESIDENT_ID": "resident_012345abcdef",
            "PORTAL_LINEAGE_ID": "lineage_012345abcdef"}


def configured(config, **options):
    return invocation(options={"sandbox": "danger-full-access", "config_overrides": config, **options})


def prepared_call():
    call = CodexOptions().prepare(invocation(options={
        "runtime_settings": {"sandbox": "danger-full-access"},
        "resident_environment": dict(IDENTITY),
    }))
    # Run the real compaction transformation; only its model-window observation
    # is a fixture. Do not construct an account runtime or a native client.
    backend = CodexBackend.__new__(CodexBackend)
    backend.observe_context_window = Mock(return_value=ContextWindowObservation(400000))
    return backend.configure_auto_compact_threshold(invocation=call, threshold_percent=80).invocation


def selected(call):
    spec = {"version": 1, "path_prompt": "Read /authorized/snapshot.txt",
            "materials": [{"identity": "workspace_fixture/file/snapshot.txt",
                           "path": "/authorized/snapshot.txt", "sha256": "a" * 64}]}
    call = replace(call, request=replace(call.request, metadata={"input_variants": spec}))
    saved = []
    context = BackendContext(emit_event=Mock(), cancellation_requested=lambda: False,
                             set_interrupt_target=Mock(), input_variant_observer=saved.append)
    result = select_input_variant(call, context)
    return result, saved[0]


class CodexLocalFileConfigTests(unittest.TestCase):
    def test_real_native_prepare_and_compactor_choose_path(self):
        call = prepared_call()
        self.assertEqual(call.request.runtime_options["config_overrides"]["shell_environment_policy.set"], IDENTITY)
        self.assertEqual(assess_local_file_read(call).status, "available")
        result, decision = selected(call)
        self.assertEqual(decision["selected"], "path")
        self.assertEqual(result.user_prompt, "Read /authorized/snapshot.txt")

    def test_adapter_generated_thread_config_is_read_neutral(self):
        call = prepared_call()
        options = {**call.request.runtime_options, "goal": {}}
        config = CodexBackend._thread_config(options)
        self.assertEqual(assess_local_file_read(configured(config)).status, "available")

    def test_dotted_and_nested_standard_config_forms(self):
        for config in (
            {"tools.update_plan.enabled": True, "agents.enabled": False, "features.goals": True},
            {"tools.update_plan": {"enabled": False}, "agents": {"enabled": True}},
            {"tools": {"update_plan": {"enabled": True}}, "features": {"goals": False}},
            {"shell_environment_policy": {"set": IDENTITY}},
        ):
            with self.subTest(config=config):
                self.assertEqual(assess_local_file_read(configured(config)).status, "available")

    def test_standard_presentation_and_context_fields(self):
        config = {"show_raw_agent_reasoning": False, "model_reasoning_summary": "auto",
                  "model_context_window": 400000, "model_auto_compact_token_limit": 320000,
                  "model_auto_compact_token_limit_scope": "total", "project_doc_max_bytes": 0}
        self.assertEqual(assess_local_file_read(configured(config)).status, "available")

    def test_unknown_read_tools_permissions_and_features_remain_unknown(self):
        for extra in (
            {"features.shell_tool": False}, {"features": {"shell_tool": True}},
            {"tools": {"Read": {"enabled": False}}}, {"permissions": {"filesystem": "none"}},
            {"sandbox_mode": "danger-full-access"}, {"approval_policy": "never"},
            {"features": {"goals": True, "future_tool": False}},
            {"agents": {"config_file": "/other/config.toml"}}, {"unknown": {}},
        ):
            with self.subTest(extra=extra):
                config = {"model_auto_compact_token_limit": 10, **extra}
                status = assess_local_file_read(configured(config))
                self.assertEqual((status.status, status.reason), ("unknown", "native_config_requires_observation"))

    def test_custom_process_environment_never_gets_blanket_allow(self):
        for name in ("PATH", "HOME", "CODEX_HOME", "LD_PRELOAD", "BASH_ENV", "PORTAL_NEW_FIELD"):
            with self.subTest(name=name):
                call = configured({"shell_environment_policy.set": {**IDENTITY, name: "/custom"}})
                self.assertEqual(assess_local_file_read(call).status, "unknown")
        for config in ({"shell_environment_policy": {"inherit": "all"}},
                       {"shell_environment_policy.set.PORTAL_RESIDENT_ID": []}):
            self.assertEqual(assess_local_file_read(configured(config)).status, "unknown")

    def test_invalid_standard_values_and_shapes_remain_unknown(self):
        for config in ([], "", False, {"show_raw_agent_reasoning": "true"},
                       {"model_auto_compact_token_limit": True}, {"model_context_window": -1},
                       {"model_auto_compact_token_limit_scope": "invented"},
                       {"tools": []}, {"agents.enabled": "false"},
                       {"model_reasoning_summary": {}}, {"compact_prompt": "custom prompt"}):
            with self.subTest(config=config):
                self.assertEqual(assess_local_file_read(configured(config)).status, "unknown")

    def test_duplicate_dotted_and_nested_leaf_is_not_guessed(self):
        for config in (
            {"tools.update_plan.enabled": True, "tools": {"update_plan": {"enabled": False}}},
            {"agents.enabled": True, "agents": {"enabled": True}},
        ):
            self.assertEqual(assess_local_file_read(configured(config)).status, "unknown")

    def test_neutral_config_does_not_override_other_policy(self):
        config = {"model_auto_compact_token_limit": 10}
        for options in ({"permissions": "custom"},
                        {"sandbox": "", "sandbox_policy": {"type": "readOnly"}},
                        {"execution_constraints": {"version": 1, "text_only": True, "context": "fresh"}}):
            with self.subTest(options=options):
                self.assertEqual(assess_local_file_read(configured(config, **options)).status, "unknown")

    def test_specific_path_still_requires_native_read_evidence(self):
        result = assess_local_file_read(prepared_call(), path="/authorized/snapshot.txt")
        self.assertEqual((result.status, result.reason, result.scope),
                         ("unknown", "native_file_probe_required", "file"))

    def test_read_restriction_still_selects_full(self):
        call = configured({"features.shell_tool": False, "model_auto_compact_token_limit": 10})
        result, decision = selected(call)
        self.assertEqual(decision["selected"], "full")
        self.assertEqual(result.user_prompt, call.user_prompt)

    def test_assessment_is_passive_and_does_not_mutate_options(self):
        call = prepared_call()
        original = deepcopy(call.request.runtime_options)
        with patch("os.access", side_effect=AssertionError("no Web/Worker file probe")), \
             patch.object(CodexBackend, "__init__", side_effect=AssertionError("no account runtime")):
            self.assertEqual(assess_local_file_read(call).status, "available")
        self.assertEqual(call.request.runtime_options, original)


if __name__ == "__main__":
    unittest.main()
