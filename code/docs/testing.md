# Testing a shared-code installation

Tests belong to the shared source tree. User records, test accounts, browser
sessions, test reports and TAU Resident assignments belong to the selected
instance. A release containing the Test capability includes `tests/cases`, its
fixtures and explicit TAU case definitions. The retired `tests/ui` Playwright
suite is not a runner; browser acceptance uses TAU.

## Local regression

Run from `code/` with the installation's Python environment and test dependencies:

```sh
python -m pytest tests/cases
```

The session fixture starts its own temporary PostgreSQL cluster with a private
socket and schema namespace. It must not connect to the live Portal database.
For a command-line run, point `PORTAL_TEST_POSTGRES_BIN` and
`PORTAL_TEST_POSTGRES_LIB` at the installed PostgreSQL executables and libraries.
The Test workbench supplies those executable paths automatically. It does not
pass the production database connection to local tests. The fixture stops and
removes its temporary cluster at session end.

The formal runner uses Linux namespaces to isolate local regression from live
network services and credentials. Missing isolation dependencies must produce
an unavailable result, never fallback execution against the host instance.

## TAU

Follow [the browser setup and target contract](../management_portal/test_framework/tau_runtime/README.md).
Select the instance explicitly, provision that instance's configured test user,
and verify the running instance, expected build, authenticated user and Test
component before starting a campaign. Neither a Git branch named `dev` nor a
retired deployment registry selects the target automatically.

TAU runs through the configured application Lineage and module Resident slots.
Cases may mutate only their declared test fixtures; they must retain cleanup
and evidence of any incomplete work. Use the actual feature set of the package.
The retired Project page, RoboLab integration and semantic-vector search are
not acceptance requirements of this distribution. Project resources and their
ISA operations remain supported and keep their tests.

## Publication evidence

Record the tested source commit, any candidate patch, selected cases, runner
configuration and raw results. An unavailable runner, a skipped case or an
unresolved design reference is a coverage limitation, not a pass. Update obsolete
requirements using current product behavior and retain regression checks for
supported functionality. Publish only the verified changes; instance data and
credentials never enter Git. Code publication and instance activation are
separate steps.
