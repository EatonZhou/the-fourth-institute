#!/usr/bin/env python3
"""Run the fixed, isolated TAU prompt/helper examples; never start a Campaign."""
from pathlib import Path
import hashlib
import os
import subprocess
import sys
import tempfile
import time
from xml.etree import ElementTree


SAMPLES = (
    "test_tau_observer_request_contract.py::test_native_resource_instruction_failures_retain_safe_target_scope",
    "test_tau_chat_ready_evidence.py",
    "test_tau_readiness_samples.py",
    "test_tau_home_chat_navigation.py",
    "test_tau_session_dom_evidence.py::test_session_prerequisites_read_native_identity_search_and_node_captures",
    "test_tau_hatg_fork_evidence.py",
    "test_tau_browser_bootstrap.py::test_template_account_preflight_checks_steward_before_apply",
    "test_tau_browser_bootstrap.py::test_region_wait_follows_replaced_dom_and_requires_settled_attribute",
    "test_tau_browser_bootstrap.py::test_trigger_path_wait_observes_original_path_after_input_completion",
    "test_tau_browser_bootstrap.py::test_terminal_wait_retains_transient_reads_and_original_deadline",
    "test_tau_browser_bootstrap.py::test_prompt_first_refresh_waits_for_the_original_view_to_remount",
    "test_family_lifecycle_elements.py::test_tau_fixture_isolation_preserves_existing_family_policy_and_members",
    "test_family_lifecycle_elements.py::test_tau_family_template_disables_migration_before_first_delivery",
    "test_hatg_arrange_viewport.py::test_hatg_effect_diagnostics_and_script_remain_readable",
    "test_tau_browser_bootstrap.py::test_the_bootstrap_waits_for_a_running_tool_in_the_live_chat_card[summary]",
    "test_tau_browser_bootstrap.py::test_the_bootstrap_waits_for_a_running_tool_in_the_live_chat_card[reloaded_detail]",
    "test_tau_browser_bootstrap.py::test_the_bootstrap_waits_for_a_running_tool_in_the_live_chat_card[completed_only]",
)


def main():
    code = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env.pop("PORTAL_DEPLOYMENT", None)
    env["PYTHONPATH"] = str(code)
    env["TFI_TEST_PYTHON"] = sys.executable
    # Repository test fixtures select their disposable database and runtime.
    framework = code / "management_portal/test_framework"
    def sources():
        paths = [framework / "config/tau_runner_prompt.md", *sorted(framework.glob("tau*.js"))]
        return {str(p.relative_to(code)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    before = sources()
    print("TAU fixed samples: native UI + isolated ISA; no model or production mutation", flush=True)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="tau-fixed-samples-") as temporary:
        report = Path(temporary) / "results.xml"
        command = [sys.executable, "-m", "pytest", "-q", "-ra", "--junitxml=" + str(report),
                   *("tests/cases/" + node for node in SAMPLES)]
        try:
            result = subprocess.run(command, cwd=code, env=env, check=False, timeout=300)
        except subprocess.TimeoutExpired:
            print("FAIL: fixed samples exceeded five minutes", flush=True)
            return 1
        if result.returncode:
            return result.returncode
        if not report.is_file() or list(ElementTree.parse(report).iter("skipped")):
            print("FAIL: every fixed sample must run; missing results or skips are not a pass", flush=True)
            return 1
    if sources() != before:
        print("FAIL: prompt/helpers changed during the sample run", flush=True)
        return 1
    print(f"PASS: {len(SAMPLES)} fixed selections, {time.monotonic() - started:.2f}s; "
          f"prompt sha256={before['management_portal/test_framework/config/tau_runner_prompt.md']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
