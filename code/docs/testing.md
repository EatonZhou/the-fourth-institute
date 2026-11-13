# Testing a shared-code installation

Tests belong to the shared source tree. User records, test accounts, browser
sessions, test reports and TAU Resident assignments belong to the selected
instance. A release containing the Test capability includes `tests/cases`, its
fixtures and explicit TAU case definitions. Browser acceptance uses TAU.

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
On Linux, isolated Python and browser subprocesses use the sandbox's private
`/tmp` through `TMPDIR`, `TMP`, and `TEMP`; host runtime paths are not inherited.

Local test processes share a default budget of 32 across executors; an explicit
`PORTAL_TEST_MAX_CONCURRENCY` may lower it. TAU does not consume that budget.
TAU modules use the requested Campaign parallelism and ordinary Provider account
capacity; the same module remains exclusive across Campaigns to protect fixtures
and already accepted inputs.

## TAU

Follow [the browser setup and target contract](../management_portal/test_framework/tau_runtime/README.md).
Select the instance explicitly, provision that instance's configured test user,
and verify the running instance, expected build, authenticated user and Test
component before starting a campaign. Neither a Git branch named `dev` nor a
obsolete deployment registry selects the target automatically.

TAU runs through the configured application Lineage and module Resident slots.
Cases may mutate only their declared test fixtures and must clean up objects they
create. Each case delivers a usage report: the process and where it stopped,
issues classified as product, case, execution or tool issues with blocking,
impact or minor severity, and performance observations for key steps. Missing
screenshots, responses or timing are marked “not recorded”; they do not cause a
case to fail. Correct mistaken case instructions and repair broken helpers.
After a deployment, restart or brief disconnect, wait or refresh and continue;
only recovery failures that affect use are product issues. Use the actual feature set of the package: an optional feature the package
leaves out, such as the Projects page or Resident semantic search, is not an
acceptance requirement of that package. Project resources and their ISA
operations are supported in every package and keep their tests.

## Publication evidence

Record the tested source commit, any candidate patch, selected cases, runner
configuration and raw results. An unavailable runner, a skipped case or an
unresolved design reference is a coverage limitation, not a pass. Update obsolete
requirements using current product behavior and retain regression checks for
supported functionality. Publish only the verified changes; instance data and
credentials never enter Git. Code publication and instance activation are
separate steps.
