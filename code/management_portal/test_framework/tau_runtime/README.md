# Optional TAU browser runtime

TAU uses a Resident to verify an explicitly selected local Portal instance. Its shared browser helper needs
the pinned Playwright package in this directory and the matching Chromium binary.
The removed Playwright test suite is not needed. Core and ordinary `test` installs
do not install this runtime.

Install it in the Portal instance that serves the Test Workbench:

```sh
python3.12 scripts/bootstrap_portal install tau
python3.12 scripts/bootstrap_portal doctor tau
```

The instance’s configured test account (`test_framework.actor`) must be active. Provision its private browser storage
state with `python3.12 -m management_portal.test_framework.tools.provision_portal_test_profile`.
TAU also needs a reachable test target, a configured TAU Resident and
provider account, and available account quota. Restart the Portal service after
installing the browser runtime so the Test Workbench refreshes runner availability.

Before provisioning, put `target.json` in the instance's test framework state directory,
or set `PORTAL_TEST_TARGET_FILE` to its absolute path. Keep it outside Git:

```json
{
  "schema": "tfi.portal-test-target.v1",
  "deployment": {
    "id": "release",
    "label": "Local validation instance",
    "kind": "release",
    "origin": "http://127.0.0.1:28399",
    "build_id": "the-expected-active-build"
  }
}
```

Use the instance identity and build ID of the installation being tested; the
example port is not a default. TAU checks both against health and the authenticated
session, and checks the Test component fingerprint before dispatch. A missing
or mismatched target is an unavailable runner, never a passed test. Worktree
names do not select a running instance. To test a Dev instance, install it with
its own database, data and consumers, then select its actual identity and port.
The same shared code and optional browser runtime can serve several instances;
each instance keeps its own test identity, target and evidence.

Linux starts Campaign executors through `systemd-run`. Windows starts a native
Python executor in a separate Windows Job, with a lifetime lock per run. It
survives Web restart; recovery reclaims the original durable run and input IDs.
The managed TAU browser uses its own Job and lease for verified cleanup. An
unmanaged Windows helper without a Job receipt still blocks cleanup instead of
guessing ownership from a PID. macOS Campaign launch remains unavailable.

On Windows, use the installed Portal Python in the commands above (`python`,
not an unrelated Python installation). Register an authorized Codex account
through that Windows instance's own Portal, configure the TAU application
Lineage/module Resident, and select core TAU cases in that Portal's Test
Workbench. The account may be the same account used on another host. Prefer a
separate login; an explicitly authorized login-cache transfer must use a private
channel and serialize refresh ownership, without exposing credentials in source,
logs or artifacts. The TAU executor, browser and model client must all run on
Windows; another host's TAU must not drive this acceptance run.
`scripts/windows/tau_preflight.py --output <file>`
records prerequisite failures and the selected core case manifest; it does not
start a Campaign. Native CI validates process and browser ownership with
disposable fixtures, without a real account or model, so its success is not a
formal TAU result.
