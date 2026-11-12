# Optional TAU browser runtime

TAU uses a Resident to verify an explicitly selected local Portal instance. Its shared browser helper needs
the pinned Playwright package in this directory and the matching Chromium binary.
The removed Playwright test suite is not needed. Core and ordinary `test` installs
do not install this runtime.

`package-lock.json` is published with the source. The installer uses `npm ci`
to reproduce its package versions. Browser installation sets
`PLAYWRIGHT_SKIP_BROWSER_GC=1` so it cannot remove browsers used by other
applications sharing the host cache. To refresh only the Node packages without
installing browsers, run `npm ci --ignore-scripts --no-audit --no-fund` in this
directory.

Install it in the Portal instance that serves the Test Workbench:

```sh
python3.12 scripts/bootstrap_portal install tau
python3.12 scripts/bootstrap_portal doctor tau
```

The instance's configured test account (`site.json`: `deployment.test_framework.actor`) must exist and be active. Provision its private browser storage
state with `python3.12 -m management_portal.test_framework.tools.provision_portal_test_profile`.
For a case requiring an ordinary user, configure a dedicated, authorized `member`
account and set `deployment.test_framework.actor_category` to `member`. Both the
provisioner and each TAU preflight check the current category; preflight reads it
from the target's authenticated `/api/me`. A missing or different category fails
before dispatch. Results record the observed category. Existing configurations
without `actor_category` retain their account-only contract and do not establish
an ordinary-user prerequisite. Supported explicit categories are `member`,
`intern`, and `portal_admin`; unknown or empty values are rejected.

Account creation uses the authorized `CreateUser` instruction, independently of
local browser profile provisioning. The provisioner neither creates accounts nor
changes permissions. Do not demote an existing administrator or use a private
user to satisfy a test. After all affected runs finish, an installation operator
may switch the configured actor and provision a new private (0600) profile.
Recheck the selected target and category before a targeted case. The browser
account does not grant permissions to the Resident executing ISA instructions.
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
