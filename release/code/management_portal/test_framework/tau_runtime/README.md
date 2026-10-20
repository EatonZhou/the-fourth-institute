# Optional TAU browser runtime

TAU uses a Resident to verify the live DEV Portal. Its shared browser helper needs
the pinned Playwright package in this directory and the matching Chromium binary.
The removed Playwright test suite is not needed. Core and ordinary `test` installs
do not install this runtime.

Install it in the Portal instance that serves the Test Workbench:

```sh
python3.12 scripts/bootstrap_portal install tau
python3.12 scripts/bootstrap_portal doctor tau
```

The `test-actor` account must be active. Provision its private browser storage
state with `python3.12 -m management_portal.test_framework.tools.provision_portal_test_profile`.
TAU also needs a unique reachable DEV deployment, a configured TAU Resident and
provider account, and available account quota. Restart the Portal service after
installing the browser runtime so the Test Workbench refreshes runner availability.

For a Dev/Release pair with separate code directories, install this optional
runtime in each instance from which TAU Campaigns may be started. The Chromium
cache can be shared by the same operating-system user.

Native TAU Campaign execution currently requires Linux: the executor is started
through `systemd-run`, and cleanup of case-owned helper processes uses `/proc`.
Windows and macOS can install the optional browser package, but `doctor tau`
reports the executor as unavailable until those two integrations are ported.
