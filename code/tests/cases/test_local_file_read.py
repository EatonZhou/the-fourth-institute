"""Run directly: python -m unittest tests.cases.test_local_file_read -v.

No database, account, native service, or model call is needed.
"""
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import patch

from management_portal.execution.backend_contracts import BackendInvocation
from management_portal.execution.contracts import ExecutionRequest
from management_portal.execution.local_file_read import assess_local_file_read
from management_portal.execution.provider_catalog import AgentProvider, AGENT_PROVIDERS
from management_portal.execution.session_manager import RuntimeSession
from management_portal.execution.codex_backend import CodexBackend
from management_portal.execution.kimi_backend import KimiBackend
from management_portal.execution.claude_backend import ClaudeBackend
from management_portal.execution.antigravity_backend import AntigravityBackend
from management_portal.execution.native_options import KimiOptions
from management_portal.execution.harness_capabilities import NATIVE_TOOL_EVENTS


def invocation(provider="codex", options=None, **request_fields):
    request = ExecutionRequest(
        execution_id="execution-file-capability-test", actor="test-only",
        prompt="test", service_type="agent_session", provider=provider,
        operation="turn", runtime_options=options or {},
    )
    request = replace(request, **request_fields)
    session = RuntimeSession(
        mode="stateless", binding_id="", backend_session_id="",
        is_new_backend_session=True,
    )
    return BackendInvocation(request, session, "test", "", False, True)


class LocalFileReadTests(unittest.TestCase):
    def assert_result(self, call, status, reason, *, scope="policy"):
        self.assertEqual(call.to_payload(), dict(status=status, reason=reason, scope=scope))

    def test_nominal_support_is_explicit(self):
        for name in ("codex", "kimi", "claude"):
            with self.subTest(provider=name):
                self.assertTrue(AGENT_PROVIDERS[name].local_file_read)
        self.assertFalse(AGENT_PROVIDERS["antigravity"].local_file_read)
        self.assertFalse(AgentProvider("new", "New", frozenset(), ()).local_file_read)

    def test_passive_class_lookup_keeps_existing_backend_construction(self):
        definition = AGENT_PROVIDERS["codex"]
        with patch.object(CodexBackend, "__init__", return_value=None) as constructor:
            self.assertIs(definition.backend_class(), CodexBackend)
            constructor.assert_not_called()
            self.assertIsInstance(definition.build_backend(), CodexBackend)
            constructor.assert_called_once_with()

    def test_native_events_do_not_imply_reading(self):
        definition = AgentProvider("events-only", "Events", frozenset({"turn"}), (),
                                   harness=frozenset({NATIVE_TOOL_EVENTS}))
        with patch.dict(AGENT_PROVIDERS, {definition.provider: definition}):
            self.assert_result(assess_local_file_read(invocation("events-only")),
                               "blocked", "local_file_read_not_supported")

    def test_nominal_support_without_policy_is_unknown(self):
        definition = AgentProvider("new", "New", frozenset({"turn"}), (), local_file_read=True)
        with patch.dict(AGENT_PROVIDERS, {"new": definition}):
            self.assert_result(assess_local_file_read(invocation("new")),
                               "unknown", "adapter_policy_unavailable")

    def test_raw_settings_are_not_effective_policy(self):
        self.assert_result(assess_local_file_read(invocation(options={
            "runtime_settings": {"permissions": "full-access"},
            "permissions": ":danger-full-access",
        })), "unknown", "runtime_options_not_prepared")

    def test_route_mismatch_blocks(self):
        call = invocation("kimi", {"provider_profile": "route-b", "tools": ["Read"], "permission_mode": "yolo"})
        call = replace(call, session=replace(call.session, backend_route_id="route-a"))
        self.assert_result(assess_local_file_read(call), "blocked", "provider_route_mismatch")

    def test_plain_api_and_other_operations_do_not_get_native_access(self):
        for service in ("chat_completion", "process"):
            with self.subTest(service=service):
                self.assert_result(assess_local_file_read(invocation(service_type=service)),
                                   "blocked", "service_has_no_native_file_tools")
        self.assert_result(assess_local_file_read(invocation(operation="release")),
                           "blocked", "operation_is_not_turn")

    def test_unknown_provider_is_not_guessed_from_name(self):
        self.assert_result(assess_local_file_read(invocation("codex-subscription")),
                           "unknown", "provider_not_registered")

    def test_codex_read_only_does_allow_read_policy(self):
        for sandbox in ("read-only", "workspace-write", "danger-full-access"):
            with self.subTest(sandbox=sandbox):
                self.assert_result(assess_local_file_read(invocation(options={"sandbox": sandbox})),
                                   "available", "native_read_policy_allows")

    def test_codex_named_profile_has_actual_precedence(self):
        call = invocation(options={"permissions": "custom", "sandbox": "danger-full-access"})
        self.assert_result(assess_local_file_read(call),
                           "unknown", "native_permission_profile_requires_observation")
        call = invocation(options={"permissions": ":danger-full-access", "sandbox": "read-only"})
        self.assert_result(assess_local_file_read(call), "available", "native_read_policy_allows")

    def test_codex_unresolved_or_custom_policy_is_unknown(self):
        for options, reason in (
            ({}, "native_policy_requires_observation"),
            ({"sandbox_policy": {"type": "readOnly"}}, "native_sandbox_policy_requires_observation"),
            ({"sandbox": "danger-full-access", "config_overrides": {"features.shell_tool": False}},
             "native_config_requires_observation"),
        ):
            with self.subTest(options=options):
                self.assert_result(assess_local_file_read(invocation(options=options)), "unknown", reason)

    def test_codex_invalid_options_do_not_leak_errors(self):
        call = invocation(options={"sandbox": "read-only", "sandbox_policy": {"type": "readOnly"}})
        self.assert_result(assess_local_file_read(call), "unknown", "invalid_native_options")

    def test_kimi_final_tools_control_policy_not_owner_label(self):
        call = invocation("kimi", {"is_owner": True, "tools": [], "permission_mode": "yolo"})
        self.assert_result(assess_local_file_read(call), "blocked", "native_read_tool_not_enabled")
        call = invocation("kimi", {"is_owner": False, "tools": ["Read"], "permission_mode": "yolo"})
        self.assert_result(assess_local_file_read(call), "available", "native_read_policy_allows")

    def test_kimi_manual_policy_needs_native_permission(self):
        call = invocation("kimi", {"tools": ["Read"], "permission_mode": "manual"})
        self.assert_result(assess_local_file_read(call), "unknown", "native_permission_requires_observation")

    def test_kimi_missing_malformed_and_wildcard_tools_not_assumed(self):
        for tools, status, reason in (
            (None, "blocked", "native_read_tool_not_enabled"),
            ("Read", "unknown", "native_tool_policy_unverified"),
            (["*"], "unknown", "native_tool_policy_unverified"),
        ):
            with self.subTest(tools=tools):
                self.assert_result(assess_local_file_read(invocation("kimi", {
                    "tools": tools, "permission_mode": "yolo"})), status, reason)

    def test_kimi_text_only_remains_blocked_after_real_option_preparation(self):
        call = invocation("kimi", {
            "tools": ["Read"], "permission_mode": "yolo",
            "execution_constraints": {"version": 1, "text_only": True, "context": "fresh"},
        })
        prepared = KimiOptions().prepare(call)
        self.assertEqual(prepared.request.runtime_options["tools"], [])
        self.assert_result(assess_local_file_read(prepared), "blocked", "text_only_disables_native_tools")

    def test_unrecognized_constraints_never_enable_reading(self):
        for provider, options in (
            ("codex", {"sandbox": "danger-full-access"}),
            ("claude", {"permission_mode": "bypassPermissions"}),
            ("kimi", {"tools": ["Read"], "permission_mode": "yolo"}),
        ):
            with self.subTest(provider=provider):
                call = invocation(provider, {**options, "execution_constraints": {"invented": True}})
                self.assertEqual(assess_local_file_read(call).status, "unknown")

    def test_claude_final_permission_mode_applies_to_owner_and_reader(self):
        for owner in (True, False):
            with self.subTest(owner=owner):
                call = invocation("claude", {"is_owner": owner, "permission_mode": "bypassPermissions"})
                self.assert_result(assess_local_file_read(call), "available", "native_read_policy_allows")
                call = invocation("claude", {"is_owner": owner})
                self.assert_result(assess_local_file_read(call), "unknown", "native_permission_requires_observation")

    def test_claude_rejects_unsupported_permission_mode(self):
        call = invocation("claude", {"permission_mode": "yolo"})
        self.assert_result(assess_local_file_read(call), "blocked", "unsupported_native_permission_mode")

    def test_antigravity_does_not_claim_real_capability_from_acp_support(self):
        call = invocation("antigravity", permission_profile="owner")
        self.assert_result(assess_local_file_read(call), "unknown", "native_file_capability_unverified")
        for call in (invocation("antigravity"),
                     invocation("antigravity", {"native_tools_enabled": False}, permission_profile="owner")):
            with self.subTest(call=call.request.permission_profile):
                self.assert_result(assess_local_file_read(call), "blocked", "native_tools_disabled")
                self.assertFalse(AntigravityBackend.native_tools_allowed(call.request))

    def test_specific_file_requires_native_evidence_even_when_policy_allows(self):
        for provider, options in (
            ("codex", {"sandbox": "danger-full-access"}),
            ("kimi", {"tools": ["Read"], "permission_mode": "yolo"}),
            ("claude", {"permission_mode": "bypassPermissions"}),
        ):
            with self.subTest(provider=provider):
                self.assert_result(assess_local_file_read(invocation(provider, options),
                                   path="/server/authorized-paper.txt"),
                                   "unknown", "native_file_probe_required", scope="file")

    def test_invalid_paths_do_not_trigger_resolution(self):
        for path in ("relative.txt", "~/paper.txt", "/server/a\x00b", 42, 0, None):
            with self.subTest(path=path):
                self.assert_result(assess_local_file_read(invocation(), path=path),
                                   "blocked", "absolute_path_required", scope="file")

    def test_blocked_policy_cannot_be_promoted_by_a_path(self):
        self.assert_result(assess_local_file_read(invocation("kimi"), path="/readable/file"),
                           "blocked", "native_read_tool_not_enabled", scope="file")

    def test_assessment_neither_probes_web_filesystem_nor_constructs_clients(self):
        # All modules are already imported. Native policy inspection cannot
        # create a session, perform I/O, or use Web's identity to open a file.
        with patch("os.access", side_effect=AssertionError("Web access is not proof")), \
             patch("builtins.open", side_effect=AssertionError("no file reads")), \
             patch("pathlib.Path.resolve", side_effect=AssertionError("wrong namespace")), \
             patch.object(CodexBackend, "__init__", side_effect=AssertionError("no client")), \
             patch.object(KimiBackend, "__init__", side_effect=AssertionError("no client")), \
             patch.object(ClaudeBackend, "__init__", side_effect=AssertionError("no client")), \
             patch.object(AntigravityBackend, "__init__", side_effect=AssertionError("no client")):
            for provider in ("codex", "kimi", "claude", "antigravity"):
                with self.subTest(provider=provider):
                    result = assess_local_file_read(invocation(provider), path="/anything")
                    self.assertNotEqual(result.status, "available")

    def test_assessment_does_not_mutate_request_or_session(self):
        call = invocation("kimi", {"tools": ["Read"], "permission_mode": "yolo"})
        before = deepcopy(call)
        assess_local_file_read(call, path="/paper.txt")
        self.assertEqual(call, before)

    def test_caller_supplied_receipt_is_not_evidence(self):
        call = invocation("codex", {
            "sandbox": "danger-full-access",
            "local_file_read_receipt": {"path": "/paper.txt", "readable": True},
        })
        self.assert_result(assess_local_file_read(call, path="/paper.txt"),
                           "unknown", "native_file_probe_required", scope="file")


if __name__ == "__main__":
    unittest.main()
