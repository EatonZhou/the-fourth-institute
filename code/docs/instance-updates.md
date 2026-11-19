# Instance code updates

Run the existing entry point with the instance selected explicitly:

```sh
PORTAL_INSTANCE_ROOT=/path/to/instance python scripts/tfi_release_install.py update-code --plan
PORTAL_INSTANCE_ROOT=/path/to/instance python scripts/tfi_release_install.py update-code
```

Use the Python environment belonging to the new code. The code checkout must
remain stable throughout the operation. `--offline` keeps the existing offline
dependency rules; it does not silently fetch missing packages. The update
command owns the instance control lock. AMA should invoke this command and
report its receipt, rather than assemble prepare/stop/start commands itself.

When AMA runs inside the instance being updated, submit an independent native
control job so stopping its Provider cannot kill the update:

```sh
python scripts/tfi_instance_manager.py update-code --name <registered-instance-name>
```

This returns a queued operation, not an update completion. Follow
`runtime/portal-control.json` and `runtime/update-code.json` to their terminal
status; do not re-submit while the job is running. `--offline` is supported.
The native job calls the same updater under the instance control lock. The
HTTP administration API continues to accept only start/stop.

`runtime/update-code.json` records the code identity, original running services,
feature plans, phase, results and failures. Completed attempts are retained in
`runtime/updates/`. Repeating the same command resumes an interrupted update
using its original service intent and plan. An interruption after shutdown
requires the same code to resume. A failed preflight can be retried with corrected
code. A source change during preparation prevents shutdown.

Planning needs PostgreSQL. If the instance was stopped, planning temporarily
starts only its database and stops it afterwards. Domain stores may perform
their ordinary additive schema preparation when inspected. Planning does not
stop application services or publish product files. It is not a database-free
or transactionally read-only operation.

## Ownership and coverage

| Domain | Update behavior | Boundary |
| --- | --- | --- |
| PostgreSQL | Preserve manifest-selected cluster/programs, await readiness | Major version/cluster relocation uses its explicit migration, never automatic replacement |
| NATS, Resource, Execution, Society, Worker | Refresh registered definitions, verify launchers, restore running intent, check Web/Worker health | Instance identity, database and credentials retained |
| Release Web, Dev Web, gateway | Refresh registered definitions through native platform adapter | No inference that a second deployment should be created |
| Codex account servers, Kimi account servers, Kimi/Claude, relay | Registered components participate in the same stop/definition/start/state checks | Account activation and registration remain with the account owner; no fabricated account or credential |
| Custom services | Validate declared program path, render owned definition and check resulting state | Unknown launchers fail before stopping; updater cannot invent a program |
| Telepus | Prepare owner schema; inspect local bindings; register missing bridge requirements | Existing stopped bridges are retained; a conflicting partially stopped bridge blocks preflight; no source contacts copied |
| Installed Watchboards | Validate declarations and runtime file dependencies; reconcile versioned product templates | User-modified files conflict; unknown missing panels require original source, never guessed defaults |
| Published Workbenches | Prepare owner tables; check published build receipt, entry bytes and indexed asset presence/size | Missing immutable builds block update; no silent rebuild from a different Git revision |
| Paper, Article, Repo, Projects, WALL, Test, notices, chatrooms, reports, presentation | Code/dependencies and hosting services update together; existing domain-owned additive preparation remains authoritative | User resources, publications, histories and drafts are not replaced with examples |
| Permission schemes | Existing permission owner reads code-provided schemes and retains instance scheme edits | Updater does not activate a different scheme or overwrite custom permissions |
| Python/Node/native code dependencies | Prepare via existing code environment and native dependency owners | Optional Article/Test/Tau/provider/embedding tools retain the documented user-managed, check-on-use policy; receipt exposes this boundary |
| OS integration | Linux user systemd, macOS launchd, Windows user/SCM adapters | Native platform acceptance remains separate from mocked adapter tests |

All registered components are inventoried, including components not named in
the table. Registration is the authority for custom and account services, not
a filename scan. A stopped component stays stopped; an explicitly disabled
feature is not enabled merely because its module ships in the code.

## Feature contributions

An installed feature with persistent update requirements contributes
`instance.update` through `portal_features`. Its handler receives
`(instance, phase, plan)` and returns public JSON data with `services` and
`blockers`, and `start_services` (the subset of newly registered services to
start now). A stopped instance can receive missing service definitions without
starting those services. Phases are `plan`, `apply`, `verify`. Each owner must:

- derive requirements from authoritative installed state;
- make apply idempotent, including retry after partial writes;
- own additive schema evolution and reject unsupported transitions;
- verify the same paths/contracts used by its runtime;
- keep secrets and complete database rows out of plans and receipts.

The orchestrator never runs arbitrary migration SQL, imports seed SQL, resets
feature data, or assumes a code-only rollback can reverse a data migration.
An incompatible/destructive migration needs its owner's explicit backup and
recovery procedure before it can join this contract.

The legacy Telepus product-template repair is declared by the Watchboard owner
for its stable shipped resource identity. The generic updater has no Resident
IDs. Only an actually installed panel is eligible. Product asset hashes are
tracked per instance; existing unknown/user-modified bytes are preserved and
reported as conflicts. Empty contact lists and reconfiguration of local bindings
are distinct from missing panel files.

## Supported local service configuration

Use `configure-services --settings <json-file>` before updating a locally
adapted deployment. For example:

```json
{
  "launch_agents_directory": "/Users/huanyu/Library/TFI/dependencies/launch-agents",
  "path_prefixes": {"web": ["/Library/TeX/texbin"]}
}
```

These settings are persisted through the manifest owner with a configuration
version check. The launch-agent directory must be canonical and absolute;
PATH prefixes must be absolute and name registered components. Configuration
does not restart services. Updates render and verify the same configured native
location; foreign or redirected definitions are rejected. Configure the actual
existing location, rather than moving a loaded job by editing this setting.

## Completion and testing

The desktop startup path checks the update receipt as well as service
definitions, so a code change with identical dependencies still triggers an
update. A failed feature check produces a failed command and receipt.
The desktop's explicit open request starts a stopped instance after a successful
update; the update command alone preserves its stopped state. Services
left stopped by a failure can be restored by retrying the same update; the
updater does not pretend an interrupted update succeeded.

Automated receipts distinguish service/file checks from external round trips.
In particular, successful WeChat binding, a live bridge process, and an actual
WeChat → AMA → WeChat reply are three different observations. The update does
not send a message or claim a real round trip took place.
