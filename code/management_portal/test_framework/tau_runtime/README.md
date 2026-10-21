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

Native TAU Campaign execution currently requires Linux: the executor is started
through `systemd-run`, and cleanup of case-owned helper processes uses `/proc`.
Windows and macOS can install the optional browser package, but `doctor tau`
reports the executor as unavailable until those two integrations are ported.
