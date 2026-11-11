"""Direct, isolated protocol tests. No Provider, model, service or database."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from management_portal.execution.backend_contracts import BackendContext, BackendInvocation, BackendResult
from management_portal.execution.backend_services import AgentSessionService
from management_portal.execution.chat_completion_service import ChatCompletionService
from management_portal.execution.contracts import ExecutionRequest, ExecutionRecoveryDeferred, SessionOptions
from management_portal.execution.input_variants import select_input_variant, validate_input_variants
from management_portal.execution.runtime import ExecutionRuntime, RuntimeContext
from management_portal.execution.session_manager import RuntimeSession


def request(provider="kimi", **kwargs):
    values = dict(actor="sample_admin", prompt="FULL CONTENT", service_type="agent_session",
                  provider=provider, operation="turn", cwd="/task",
                  runtime_options={"tools": ["Read"], "permission_mode": "yolo"},
                  metadata={"input_variants": {
                      "version": 1, "path_prompt": "Read /authorized/paper.txt",
                      "materials": [{"identity": "workspace_test/file/paper.txt",
                                     "sha256": "a" * 64, "path": "/authorized/paper.txt"}],
                  }})
    values.update(kwargs)
    return ExecutionRequest.new(**values)


def session():
    return RuntimeSession("stateless", "", "", True)


def invocation(req=None, recovery=None):
    req = req or request()
    return BackendInvocation(req, session(), "PREFIX\n" + req.prompt + "\nSUFFIX",
                             "SYSTEM", True, True, recovery_checkpoint=recovery or {})


def context(save=None):
    return BackendContext(emit_event=Mock(), cancellation_requested=lambda: False,
                          set_interrupt_target=Mock(), input_variant_observer=save)


class NoHarness:
    def prepare_invocation(self, call, provider):
        return call

    def should_run(self, call):
        return False


class InputVariantTests(unittest.TestCase):
    def setUp(self):
        self.prepare = patch("management_portal.execution.resident_capability_assembly.prepare",
                             side_effect=lambda call, ctx: call).start()
        patch("management_portal.execution.resident_capability_assembly.observe_delivery").start()
        self.addCleanup(patch.stopall)

    def test_request_roundtrip_and_validation(self):
        req = request()
        self.assertEqual(ExecutionRequest.from_payload(req.to_payload()), req)
        for replacement in (
            {"version": 2, "path_prompt": "a", "materials": []},
            {"version": True, "path_prompt": "a", "materials": []},
            {"version": 1, "path_prompt": "", "materials": []},
        ):
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                request(metadata={"input_variants": replacement})

    def test_material_integrity_and_identity_are_required(self):
        for field, value in (("sha256", "not-sha"), ("identity", ""), ("path", "relative")):
            spec = deepcopy(request().metadata["input_variants"])
            spec["materials"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                request(metadata={"input_variants": spec})
        spec = deepcopy(request().metadata["input_variants"])
        spec["materials"] *= 2
        with self.assertRaises(ValueError):
            request(metadata={"input_variants": spec})

    def test_scope_is_explicit_stateless_text_only(self):
        with self.assertRaisesRegex(ValueError, "stateless"):
            request(session=SessionOptions(mode="persistent", binding_id="other"))

    def test_path_choice_preserves_injections_and_freezes_before_return(self):
        decisions = []
        call = invocation()
        ctx = context(decisions.append)
        selected = select_input_variant(call, ctx)
        self.assertEqual(selected.user_prompt, "PREFIX\nRead /authorized/paper.txt\nSUFFIX")
        self.assertEqual(selected.system_context, "SYSTEM")
        self.assertIs(selected.request, call.request)
        self.assertEqual(decisions[0]["selected"], "path")
        self.assertEqual(decisions[0]["user_prompt"], selected.user_prompt)

    def test_blocked_and_unknown_choose_full(self):
        for options in ({"tools": []}, {"tools": ["Read"], "permission_mode": "manual"}):
            with self.subTest(options=options):
                call = invocation(request(runtime_options=options))
                saved = []
                self.assertEqual(select_input_variant(call, context(saved.append)).user_prompt, call.user_prompt)
                self.assertEqual(saved[0]["selected"], "full")

    def test_chat_completion_always_selects_full(self):
        call = invocation(request("test-api", service_type="chat_completion", operation="generate"))
        saved = []
        client = ChatCompletionService(provider_registry=Mock())
        client._execute = Mock(return_value=BackendResult({"message": "done"}))
        result = client.execute(call, context(saved.append))
        self.assertEqual(client._execute.call_args.args[0].user_prompt, call.user_prompt)
        self.assertEqual(result.output["input_variant"]["selected"], "full")
        self.assertNotIn("user_prompt", result.output["input_variant"])

    def test_no_variants_leave_existing_calls_untouched(self):
        call = invocation(request(metadata={}))
        ctx = context()
        self.assertIs(select_input_variant(call, ctx), call)
        self.assertIsNone(ctx.current_input_variant())

    def test_missing_or_failed_persistence_never_dispatches(self):
        provider = Mock()
        service = AgentSessionService({"kimi": provider}, harness=NoHarness())
        for ctx in (context(), context(Mock(side_effect=OSError("fixture persistence failure")))):
            with self.subTest(ctx=ctx), self.assertRaises((RuntimeError, OSError)):
                service.execute(invocation(), ctx)
        provider.execute.assert_not_called()

    def test_selection_uses_final_harness_options(self):
        class Restrict(NoHarness):
            def prepare_invocation(self, call, provider):
                options = {**call.request.runtime_options, "tools": []}
                return replace(call, request=replace(call.request, runtime_options=options))
        provider = Mock()
        provider.execute.return_value = BackendResult({"message": "done"})
        service = AgentSessionService({"kimi": provider}, harness=Restrict())
        saved = []
        service.execute(invocation(), context(saved.append))
        self.assertEqual(saved[0]["selected"], "full")
        self.assertIn("FULL CONTENT", provider.execute.call_args.args[0].user_prompt)

    def test_path_after_final_native_options_are_prepared(self):
        provider = Mock()
        provider.execute.return_value = BackendResult({"message": "done"})
        service = AgentSessionService({"kimi": provider}, harness=NoHarness())
        call = invocation(request(runtime_options={"runtime_settings": {}, "is_owner": False}))
        saved = []
        service.execute(call, context(saved.append))
        self.assertEqual(saved[0]["selected"], "path")
        self.assertNotIn("FULL CONTENT", provider.execute.call_args.args[0].user_prompt)

    def test_active_harness_final_invocation_and_effective_input_are_used(self):
        class RestrictAtDispatch(NoHarness):
            def should_run(self, call):
                return True

            def execute(self, *, provider, invocation, context, execute):
                req = invocation.request
                final = replace(invocation, request=replace(
                    req, runtime_options={**req.runtime_options, "tools": []}))
                return execute(final, context)

        provider = Mock()
        provider.execute.return_value = BackendResult({"message": "done"})
        service = AgentSessionService({"kimi": provider}, harness=RestrictAtDispatch())
        saved, observed = [], []
        ctx = BackendContext(emit_event=Mock(), cancellation_requested=lambda: False,
                             set_interrupt_target=Mock(), input_variant_observer=saved.append,
                             invocation_observer=lambda call, reports: observed.append(call))
        result = service.execute(invocation(), ctx)
        self.assertEqual(saved[0]["selected"], "full")
        self.assertEqual(result.output["input_variant"]["selected"], "full")
        self.assertEqual(observed[-1].user_prompt, provider.execute.call_args.args[0].user_prompt)

    def test_changed_or_duplicated_body_fails_without_guessing(self):
        for prompt in ("transformed body", "FULL CONTENT plus FULL CONTENT"):
            with self.subTest(prompt=prompt), self.assertRaisesRegex(ValueError, "canonical"):
                select_input_variant(replace(invocation(), user_prompt=prompt), context(Mock()))

    def test_recovery_reuses_exact_prompt_without_reassessment(self):
        saved = []
        first = select_input_variant(invocation(), context(saved.append))
        call = invocation(replace(first.request, runtime_options={"tools": []}),
                          {"input_variant": saved[0], "resumable": True, "turn_id": "same-turn"})
        call = replace(call, user_prompt="new preparation does not overwrite frozen bytes")
        with patch("management_portal.execution.input_variants.assess_local_file_read",
                   side_effect=AssertionError("must not reassess")):
            self.assertEqual(select_input_variant(call, context(Mock())).user_prompt, first.user_prompt)

    def test_material_or_prompt_changed_on_recovery_is_rejected(self):
        saved = []
        call = invocation()
        select_input_variant(call, context(saved.append))
        for changed in (
            replace(call.request, prompt="different full text"),
            replace(call.request, metadata={"input_variants": {
                **call.request.metadata["input_variants"],
                "materials": [{"identity": "new", "sha256": "b" * 64, "path": "/different"}]}}),
        ):
            with self.subTest(changed=changed.prompt), self.assertRaisesRegex(ValueError, "checkpoint"):
                select_input_variant(invocation(changed, {"input_variant": saved[0]}), context(Mock()))

    def test_frozen_decision_cannot_be_modified_or_forged_via_metadata(self):
        ctx = context(Mock())
        call = invocation()
        select_input_variant(call, ctx)
        changed = ctx.current_input_variant()
        changed["selected"] = "full"
        with self.assertRaises(ValueError):
            ctx.record_input_variant(changed)
        self.assertEqual(ctx.current_input_variant()["selected"], "path")

    def test_corrupt_recovery_decision_fails_before_provider_dispatch(self):
        call = invocation()
        saved = []
        select_input_variant(call, context(saved.append))
        provider = Mock()
        service = AgentSessionService({"kimi": provider}, harness=NoHarness())
        for changes in ({"version": True}, {"policy": None}, {"policy": {}},
                        {"selected": []}, {"user_prompt": "changed"},
                        {"policy": {"scope": "policy", "status": "blocked", "reason": "disabled"}}):
            recovery = {"input_variant": {**saved[0], **changes}}
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "checkpoint"):
                service.execute(replace(call, recovery_checkpoint=recovery), context(Mock()))
        provider.execute.assert_not_called()

    def test_running_input_without_decision_is_not_reselected(self):
        with self.assertRaisesRegex(ValueError, "no frozen"):
            select_input_variant(invocation(recovery={"turn_id": "already-running"}), context(Mock()))

    def test_real_read_failure_never_retries_with_full_body(self):
        provider = Mock()
        provider.execute.side_effect = FileNotFoundError("fixture native Read failure")
        saved = []
        with self.assertRaises(FileNotFoundError):
            AgentSessionService({"kimi": provider}, harness=NoHarness()).execute(invocation(), context(saved.append))
        provider.execute.assert_called_once()
        self.assertEqual(saved[0]["selected"], "path")
        self.assertNotIn("FULL CONTENT", provider.execute.call_args.args[0].user_prompt)

    def test_runtime_checkpoints_preserve_selection_through_recovery_and_completion(self):
        checkpoints, seen = [], []
        req = request()

        class Native:
            provider_id = "kimi"
            def execute(self, call, ctx):
                self.assert_frozen = deepcopy(checkpoints[-1]["input_variant"])
                seen.append(call.user_prompt)
                ctx.set_interrupt_target("session-test", "turn-test")
                ctx.set_provider_recovery_state({"native_phase": "running"})
                ctx.observe_context_usage({"input_tokens": 1}, 100)
                if len(seen) == 1:
                    raise ExecutionRecoveryDeferred("fixture handover")
                return BackendResult({"message": "done"}, "session-test")
            def interrupt(self, **kwargs):
                raise AssertionError("no interrupt needed")

        native = Native()
        manager, projector = Mock(), Mock()
        manager.prepare.return_value = session()
        with tempfile.TemporaryDirectory() as temp:
            runtime = ExecutionRuntime(
                services={"agent_session": AgentSessionService({"kimi": native}, harness=NoHarness())},
                agent_harness=NoHarness(), session_manager=manager,
                agent_data_projector=projector, state_root=Path(temp),
            )
            save = lambda record: checkpoints.append(deepcopy(record))
            with self.assertRaises(ExecutionRecoveryDeferred):
                runtime.execute(req, RuntimeContext(checkpoint_provider=save))
            recovered = deepcopy(checkpoints[-1])
            self.assertTrue(recovered["resumable"])
            self.assertTrue(all(record["input_variant"]["selected"] == "path" for record in checkpoints))
            with patch("management_portal.execution.input_variants.assess_local_file_read",
                       side_effect=AssertionError("must not reassess during recovery")):
                result = runtime.execute(req, RuntimeContext(checkpoint_provider=save, recovery_checkpoint=recovered))
            self.assertEqual(seen[0], seen[1])
            self.assertEqual(result.output["input_variant"]["selected"], "path")
            self.assertTrue(checkpoints[-1]["provider_completed"])
            self.assertEqual(checkpoints[-1]["input_variant"], recovered["input_variant"])
            completed = deepcopy(checkpoints[-1])
            runtime.execute(req, RuntimeContext(checkpoint_provider=save, recovery_checkpoint=completed))
            self.assertEqual(len(seen), 2)  # Completed-result recovery never calls the model.


if __name__ == "__main__":
    unittest.main()
