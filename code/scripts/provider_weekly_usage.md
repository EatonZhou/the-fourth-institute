# Existing-Record Provider Usage Report

`provider_weekly_usage.py` is an offline report, not a replacement ledger or a
background service. It never sends a model prompt, refreshes remote usage, changes
an account or writes usage back to Portal. Run it with the installed Portal Python
environment. Schedule it externally once daily if desired; no schedule is installed.

## Parallel Incremental Parsing

The CLI defaults to `--workers 8`; use `--workers 12` on an idle host, or
`--workers 1` for the original serial path. Values are limited to 1-16 and
available CPUs. Only new/changed native sources enter the parsing pool; an
unchanged run does not start workers. The entitlement-evidence readers change the
parser fingerprint: rebuild only the explicitly selected private index when
upgrading. Never rebuild the shared default cache for this work.

Workers use spawn isolation, read source files and return normalized observations,
not message bodies. Only the parent writes SQLite, using one batch transaction per
source. No write lock is held while parsing. Pending source jobs are bounded to
twice the worker count; results are consumed in source order. Memory still scales
with the normalized observations in those pending files, not just process count.
File/WAL signatures and the old checkpoint are rechecked before committing. A
failed parse or changed source retains the previous checkpoint and is reported
as a source failure; it is not silently served as fresh data. Scheduling failures
are fatal rather than silently switching to fewer workers. This change does not
parallelize final report aggregation, metadata discovery or quota-history reads.

## Offline Inputs

### Entitlement attribution export

The weekly report includes evidence-status counts. For a per-request ledger run:

```bash
python scripts/provider_usage_attribution_report.py --manifest /absolute/manifest.json \
  --since 2026-11-10T00:00:00Z --until 2026-11-13T00:00:00Z \
  --output-root /absolute/private-output/new-run
```

Use a new or empty output directory per run. All three artifacts (requests.jsonl,
summary.json, summary.md) are staged privately and exposed together only after
success. Existing results are never overwritten. Duplicate account entries are
rejected before output creation. A source failure or an account with no requests
is explicitly `partial_or_empty`; the CLI retains its evidence and exits 2.
Exit 0 means the supplied files were read without that gap, not that the account's
whole usage is covered or the funding source is proven. Exceptions do not expose
a partial run directory.

Latest stale observations block older successful quota state; fresh native
evidence can supersede them. State is at most 300 seconds old, never from the
future, and invalid after its reset. `confirmed:credits_involved` proves credit
involvement only; request tokens are not split by funding source. Existing price
fits are not automatically certified or filtered by this classification.

Antigravity's existing ACP responses now populate catalog.json.subscription with
allowlisted tier name/id, observation time and source; missing metadata is unknown,
not Free. This is a latest snapshot, not the daily subscription history collector.
No status read initiates authentication; no Provider calls were added. Daily
metadata scheduling remains a separate, not-yet-enabled item in PLAN.

Pass explicit, authorized exports. The script does not discover credential homes
or assume permission to other Residents' records. File inputs accept instance
area references; canonical host paths also pass through the unified deployment
resolver. No machine-specific account/code paths are compiled into the script.

```json
{
  "accounts": [{
    "provider": "claude",
    "account_id": "claude_one",
    "sources": [{
      "format": "claude",
      "path": "features:usage_import/session.jsonl",
      "session_id": "native-session-id"
    }],
    "resident_records": [{
      "execution_id": "execution-id",
      "session_id": "native-session-id",
      "started_at": "2026-11-02T00:00:00Z",
      "finished_at": "2026-11-02T01:00:00Z",
      "usage": {"accounted": {"total_tokens": 100}}
    }]
  }]
}
```

```bash
python scripts/provider_weekly_usage.py --manifest features:usage_import/manifest.json \
  --since 2026-11-01T00:00:00Z --until 2026-11-08T00:00:00Z
```

Formats: `codex` rollout JSONL; `claude` transcript JSONL; `kimi` wire JSONL;
`dsh` native event JSONL; `opencode` existing requests export
`{"requests":[{"id":"r","at_ms":1793577600000,"model":"m","usage":{...}}]}`;
`opencode_sqlite` native message/part/session SQLite; `antigravity` conversation
SQLite. Explicit directories are expanded. Claude subagent transcripts, Kimi
sibling agent wires and DSH catalog-referenced children are discovered recursively;
OpenCode follows native session parent links. Symlink sources are excluded.

`requests` accepts content-free JSONL exports of existing API/Provider request
records: `request_id`, timezone-aware `occurred_at`, `model`, `usage` and
`session_id` (or the source's session_id). Do not invent request IDs or repeat
session totals as requests. Unknown direct-API schemas need an explicit export
adapter; their raw responses are not automatically recognized.

## Cycles and Coverage

`weekly_cycles` measures capacity from whole weekly windows only. Each distinct
`resets_at` of a quota group is one window. It starts full (100% remaining) at
`resets_at` minus seven days; its consumption is 100 minus the lowest remaining
percentage observed in it, so a sample that lags its usage cannot shrink the
decline. Tokens are summed from the window start to the time of that lowest
sample, per model and per type; tokens after it are reported separately as
`tokens_after_final_observation`. Partial segments between two samples are not
used. Only `status: measured` windows carry `tokens_at_100_percent_same_mix`;
the others say why: `ongoing`, `start_before_query`, `plan_changed`,
`below_min_decline` (under 5 points) or `no_tokens_recorded`. An early reset
gives the next window a new endpoint, which also finishes the previous one.
When an account has several quota groups, a group without a model list covers
the remaining models only if every other group names its models (Codex daily
snapshots name the Spark limit by `limit_name`); otherwise it is skipped.
`tokens_at_100_percent_same_mix` is conditional on the observed model/token mix,
not an independent capacity for each model or token type. Cache reads are inside
input; reasoning is inside output. Do not add these overlapping fields together.
`requests_reporting_field` counts field presence in normalized records, not
original upstream reporting coverage. Normalization can omit explicit zeros,
so these counts cannot distinguish missing upstream fields from omitted zeros;
absence never proves zero. Current normalized
indexes do not retain cache writes separately. Missing weekly quota evidence
produces `insufficient_weekly_quota_evidence`, not a fabricated capacity or a fit
of provider billing weights. Account-wide errors and quota timing/rounding errors
remain unbounded. This aggregation change does not invalidate native indexes.

Optional account-level `quota_observations` contain `at`, `resets_at` (ISO or epoch
seconds), `window_duration_mins: 10080`, `quota_group`, and optionally `models`.
Only observations at or before the request, with a future reset, are applicable.
Current snapshots cannot retroactively classify old requests. Stale observations
are ignored. Multiple matching quota groups without a model mapping are ambiguous.

Retained Home history is normalized by `provider_usage_refresh.py`. Explicit
`seven_day*`, `limit7d` and `/Weekly Limit Remaining` window identifiers are
recognized when duration is absent; an existing duration is never overridden.
Five-hour windows are excluded. Each window's source `observed_at` is retained:
repeated cache reads are not new quota observations. `resets_at` is a scheduled
endpoint, not proof that a reset occurred. A rise to 100% plus first later native
usage is supporting boundary evidence, not an exact reset timestamp. Conflicting
equal-priority observations remain unusable; source ranking is ordinal, not a
probability. Historical Home coverage starts when logging was enabled.
Claude transcript `quotaLimits` and `apiErrorParams.rate_limit_info` also retain
`rateLimitType=seven_day*` and `resetsAt`. These supply native scheduled endpoints;
explicit numeric `utilization` in [0,1] additionally supplies remaining percent.
SDK `rate_limit_event.rate_limit_info` and `unifiedWindows` are recognized too;
absent, boolean, nonfinite or out-of-range utilization remains unknown. Quoted
conversation text is never interpreted as provider quota metadata.
`rejected` is not converted into an invented 100% consumption sample. Repeated
copies in one record are deduplicated. Moving reset predictions alone no longer
qualify as cycle boundaries: unused rolling windows can move forward without
an actual reset. A quota recovery or explicit reset needs separate corroboration.

For a read-only historical inventory, run `python -m scripts.audit_claude_quota_raw
--manifest <configured-reference> --output features:provider_usage/audit/raw.json
--workers 8`. It scans every discovered transcript and child log plus retained
Claude telemetry, state, cache, command history and session metadata. Additional
runtime directories are discovered through deployment configuration; their
account identity remains unattributed until proven. Only field counts and quota
metadata are exported. Missing/unavailable journals, erased logs and other hosts
remain outside the inventory; a zero match is not proof that an event never
occurred. The SDK account snapshot is in memory, and the CLI `/usage` probe uses
`--no-session-persistence`; neither guarantees a native historical quota log.

## Automated Auxiliary Evidence

Local CLI runs now enrich registered subscriptions with an independent
`features:provider_usage/refresh-cache/refresh.sqlite` journal. Native token
indexes remain compatible. Claude quota metadata is scanned once, then resumed
by file identity, byte offset and prefix anchors. Partial trailing lines wait for
completion; rewrites replace that file's evidence atomically. Unchanged files
are not reread. Only normalized quota metadata is retained, never conversation
bodies. File mutation or invalid matching JSON keeps the previous checkpoint
and marks that source failed for this run; stale results are not served as fresh.

Home rows are read from the existing PostgreSQL history in 1000-row pages using
an account-scoped persistent ID high-water mark. Each page's observations and
cursor commit together. A 24-hour `finished_at` replay window catches ordinary
out-of-order commits; this is bounded replay, NOT a guarantee for transactions
committing more than a day late. Such cases, database restores and parser-version
changes require explicit auxiliary backfill using a new `--refresh-cache-root`.
Copies with the same source observation time and values are idempotent. Repeated
Home page/cache reads do not create new provider observations. Home windows
without source `observed_at` are excluded from timing evidence, rather than
using collection completion as a substitute. Failed auxiliary
sources are reported in `refresh_evidence_ingestion`; token reporting can continue
with partial evidence clearly flagged. Existing execution-provenance reads no
longer rescan Home history when the journal owns ingestion.

`--native-only` skips Portal execution/session provenance but still consumes
retained Home evidence. `--skip-refresh-evidence` disables the new auxiliary
ingestion. `--subscription-only` limits both account discovery and the generated
manifest to registered subscriptions. A normal repeatable daily invocation is:

```bash
python scripts/provider_weekly_usage.py --resident-id YOUR_RESIDENT_ID \
  --native-only --subscription-only --workers 8 \
  --since START_ISO --until END_ISO --write-report
```

No scheduler, service restart, provider process or remote usage refresh is
installed or triggered. An external scheduler must supply the intended interval.
Snapshots retain source timestamps; quota history is filtered to that interval.

Portal (`--resident-id`) report runs also check the already-fetched account
snapshot for available reset cards expiring after now and within seven days.
One line per card lists its account, title and Hong Kong expiry time. Retained
failed readings are marked. Missing expiry dates and spent/expired cards are
excluded; no matching cards means no user lookup and no message.
Active users with the authoritative `category=portal_admin` receive Mailbox
`CreateNotification` notices. Stable per-user/day/card-set IDs prevent duplicate
delivery on retries; administrator membership is re-evaluated each run.
`--skip-reset-reminders` keeps a Portal report read-only. Offline manifests never
send notices. Notification failures are reported separately without losing the
usage report. The independent equivalent (no usage parsing or native queries) is:

```bash
python scripts/provider_usage_reset_reminders.py --resident-id YOUR_RESIDENT_ID
```

## Reset Review Rules

Evidence reports `timing_confidence`, `reset_cause`, `actual_reset_at` and an
explicitly unknown timing error bound. Inferred recoveries are observation
candidates, not exact resets; cached/late reports can move the true event outside
the observation interval. Source priority is not a calibrated probability.
An explicit account reset requires `provider_reset_event`,
`reset_event_verified=true`, a stable `event_id` and an `account_id`. The verified
flag is a trusted importer's attestation, not receipt/signature validation
performed by this offline script; public/global
announcements alone cannot qualify. Manual collection is not card redemption.
No inferred recovery is automatically labeled a card use, global reset, scheduled
reset or plan upgrade. Conflicts block inferred cycle boundaries. Missing fields
are not contradictions; compatible lower-ranked observations may fill an unknown
percentage with its source retained. A known plan change splits capacity segments;
an unrecorded plan change cannot be detected. Cycle token bounds remain conditional
on interpreting candidates as resets and do not bound reporting lag or external use.

Codex quota observations are read directly from its token events. Tokens are
grouped by observed reset endpoint, NOT asserted to be a complete refresh cycle:
the true cycle start and out-of-scope usage remain unknown. Requests lacking an
unambiguous weekly observation use local ISO calendar weeks, clearly labeled.
The query interval is half-open. The timezone defaults to Asia/Hong_Kong.

Resident totals are compared only for complete execution intervals in the query;
they are never added or distributed across guessed models. Missing records are
not zero usage. Every result lists coverage gaps. Antigravity model and time
decoding follows the installed 1.4.5 protobuf descriptors: response_model_full /
response_model, chat_start_metadata.created_at and ModelUsageStats. Display names
are not substituted for a served model. OpenCode counts step-finish parts once,
using message totals only when those parts are absent.

`cycle_bounds` brackets inferred reset changes between adjacent observations and
reports lower/upper token sums per model. `within_10_percent` refers ONLY to token
uncertainty caused by inferred boundary timing within supplied records. It does
not certify reset causality or completeness across machines/accounts. The
whole-account error bound remains unknown. A partial query cannot receive that
10% assessment. Remaining-quota jumps are evidence, not a card-redemption record.
Old dictionary-shaped snapshots, newer list snapshots and existing refresh
history are accepted; stale failures are excluded. Newer samples supersede older
reset endpoints.

Deduplication is scoped to provider/account/session/request identity. Repeated
Claude response blocks use the maximum count per field. Codex sanitized exports
without cumulative counters (including older sanitized exports) use timestamp + usage as an explicitly flagged
fallback. Current Codex ISA exports retain cumulative counters, context window and
available ordinals. The report reuses Codex's native usage fingerprint and excludes
compaction context-size estimates. Kimi uses agent-scoped canonical-record hashes
plus occurrence numbers when upstream IDs are absent, preserving two identical
local records. These are not guaranteed compatible with the live ledger's
raw-line hashes. Never write these IDs back to the live ledger.
Overlapping source formats for the same session are excluded with a warning;
this report does not claim universal cross-source identity reconciliation.

## Existing Portal Interfaces

```bash
python scripts/provider_weekly_usage.py --resident-id YOUR_RESIDENT_ID \
  --isa-export --since 2026-11-10T00:00:00Z --until 2026-11-11T00:00:00Z --max-pages 10
```

Uses that Resident's authenticated ISA only: `ListProviderAccounts(refresh=false)`
and paginated `GetProviderUsageEvents`. Today only Codex subscription accounts
have that native-history export; others remain listed with an explicit gap.
No private directory fallback or identity substitution is attempted. Page budgets
are per account; truncated results are flagged, never called complete. Existing
source services may scan local files to serve pages; large backfills should run
outside interactive workloads. JSON stdout contains usage metadata, not messages,
prompts, local source paths or credentials.

## Local Operator Mode

### Native History Is Primary

For every supported native runtime (Codex, Claude Code, Kimi, Antigravity, and the
existing DSH/OpenCode readers), native session history supplies token totals,
models and time buckets. Discovery includes all retained sessions under configured
account roots, not just sessions linked to Portal executions. This includes local
non-Portal sessions and child-agent history. Counting never requires a matching
Resident, Portal execution, input-text match, or logical session ID.

Portal execution usage is reconciliation evidence only. It cannot replace a
missing native history or be added to native totals. If native and normalized
exports coexist, native history takes priority independent of source order;
reader failures remain failures rather than silently promoting an export.
Reports label `usage_source`, and call failed joins "Unlinked Portal executions",
not missing native usage. Direct API terminal-result handling is unchanged and
remains separately identified, not relabelled native history.

`--native-only` skips Portal execution/session/observation provenance queries.
Account registration still comes from `ListProviderAccounts(refresh=false)` and
all roots use the deployment resolver. By default Portal enrichment is attempted;
database/I/O failures are reported through `portal_provenance.status=unavailable`
without suppressing native totals. Other programming/validation errors still fail.
This aggregation-policy change does not alter the native readers or require a
rebuild of a valid parser index. Existing incremental defaults remain in force.

### Historical Native Identity

Subscription runtime now writes immutable `subscription_native_link` operations
(`tfi.subscription-native-link.v1`) to AgentData, separately from logical sessions
and runtime requests/checkpoints. Eligibility is the account registration's
`subscription_login` kind, not the provider's name; API profiles/services are not
included. Records retain execution/provider/account/route/native session/turn,
requested model (not a claim about the served model), and an account-relative raw
locator. No secrets, message text, or deployment host paths are copied.

The first native identity and each distinct turn identity are persisted; repeated
stream observations reuse an in-memory receipt. Replay after recovery is also
idempotent in durable storage. Persistence errors propagate rather than silently
marking an identity saved. If completion has no observed native identity, an
explicit `identity_status=unavailable` fact is written instead of treating the
logical execution ID as a native session. Existing stateless isolation is intact.

The audit prefers these explicit facts over legacy artifact/hash reconstruction;
multiple native sessions remain an explicit conflict instead of being flattened.
No usage endpoint is called. This contract does not archive native file contents
or recreate missing historical raw. Running workers need to load the updated
code before they produce these new facts.

Stateless logical sessions stay isolated. Reporting never overwrites them with
native IDs. Linkage recovery uses durable native artifacts, exact Kimi prompt IDs,
Claude deterministic prompt UUIDs (only with a retained request proving its
session key), and retained native tool/message IDs. Claude runtime discovery
includes the configured standalone `lineages` area as well as current/legacy
Project layouts. No deployment host location is hardcoded.

For Codex, a final-answer SHA-256 may additionally join a native assistant answer
within the recorded execution interval and matching account. Boundary whitespace
may be stripped on both sides; no other text normalization is used. Short replies
are accepted only with the same unique account/time-scoped source requirement.
Matching native IDs refine the candidate intersection. Conflicting or nonunique
matches remain unresolved; equality of token counts alone is never a join key.
Only hashes and linkage metadata are cached, not message text. Unchanged raw files
reuse metadata checkpoints; changed files refresh their linkage metadata.

The explicit coverage audit is separate from a full token-history rebuild:

```bash
python -m scripts.provider_usage_coverage --resident-id YOUR_RESIDENT_ID \
  --since 2026-01-01T00:00:00Z --until 2026-11-11T00:00:00Z \
  --baseline-report features:provider_usage/PRIOR_REPORT.json
```

It has its own private `features:provider_usage/provenance-cache`; initialize it
once explicitly with `--rebuild`. Thereafter omit `--rebuild`. It checks historical
source presence, keeps direct API terminal results separate, and reparses tokens
only for the prior report's previously unlocated sessions. Presence is not a
complete token reconciliation, and an unlocated source is not proof of deletion
or extra uncounted consumption. Results include provider/month coverage and
individual unresolved reasons in `features:provider_usage/latest-coverage.json`.
The audit never refreshes Provider usage, runs a model, or publishes Knowledge.

```bash
python scripts/provider_weekly_usage.py --resident-id YOUR_RESIDENT_ID --local-records \
  --since 2026-11-03T16:00:00Z --until 2026-11-10T16:00:00Z \
  --write-report --output-root features:provider_usage
```

This mode requires the operator's existing filesystem/database access. It is not
a public endpoint or a way around ISA authorization. The only ISA request is
`ListProviderAccounts(refresh=false)`. Registered subscription homes, Codex/Kimi
API-profile homes, provider-state histories and retained Claude runtime directories
are read locally. PostgreSQL queries use read-only transactions and select
usage/provenance metadata and, for unlinked Codex executions, final-answer bytes
to compute hashes. No prompts or answer text are exported. Default operation uses the incremental
index below, not an implicit full historical scan.

Historical AgentData routes take precedence over a physical directory's account
when unambiguous. Missing/ambiguous routes remain unresolved, not guessed from a
Resident's current settings. Direct API results with recorded api_id/model/usage
are execution-level evidence; unreported retries remain unknown. Harness totals
are comparison evidence only. `unplaced_execution_tokens` reports positive
comparison residuals, not an extra amount to add to native totals: timing,
identity and counting semantics can also cause residuals. `evidence_source_present`
describes whether that Portal execution can be linked to a supplied source; a
false value does not prove native usage is missing from the account totals. Source
failures are counted without exception text or paths; empty Antigravity databases
are identified separately.

The local database must already exist. No quota-history initialization, service
restart, schedule installation or Provider refresh is performed. Large full scans
belong in offline/daily operation, not the chat response path.

`--instance NAME` selects a host-registered instance; otherwise the existing
`PORTAL_INSTANCE_ROOT` selection is used. Instance locations are projected from
`instance.json` through `portal_paths`; Provider homes come from account registration.
Native path references, manifest files, snapshots and output roots use that same
resolver. Temporary ISA exports live under the selected instance runtime/tmp.

`--write-report` saves versioned JSON/Markdown and latest files under the configured
output area. `latest-manifest.json` records artifact references and SHA-256 hashes.
The JSON includes per-model, calendar/reset-period totals, comparison residuals,
inferred boundary bounds and quota projections. No current API prices are fetched
or extrapolated from obsolete pricing constants.

Codex physical session identity comes only from the first session_meta; replayed
parent metadata cannot replace it or clear fork-import exclusion. Quota-only
token_count records contribute observations, not consumed tokens. Reset endpoint
jitter is clustered within 60 seconds without transitive chaining; that tolerance
is not proof of a redemption or a new cycle. Cycle sums and execution comparisons
use indexed time ranges rather than repeatedly scanning every request.

Weekly 100% projections require at least five observed percentage points of quota
consumption within one monotonic same-reset segment. The estimate is recorded
tokens / observed percentage change * 100, preserving the observed model mix.
It is not an independent capacity for each model or a complete account estimate;
unseen consumption, quota weights, rounding and timestamp alignment remain unknown.
API accounts without actual weekly quota evidence remain calendar-week totals,
not fabricated subscription capacities.

With `--local-records --knowledge-resource RESOURCE_ID`, the script explicitly
publishes to an existing Knowledge item through the same Resident's ISA. It reads
the current version, preserves project/front-matter metadata, replaces stale source
links with code-relative and deployment-area references, and uses expected_version.
The caller must already hold the update permission; the script does not impersonate
a user or acquire permissions. Publishing is opt-in and never installs a timer.

## Incremental Default

Local records are now the default with --resident-id; --local-records remains an
explicit equivalent. --isa-export is a separate, opt-in bounded export, not the
default incremental entry. Manifest inputs also use the incremental index.

The index lives at `features:provider_usage/cache`; --cache-root uses the same
deployment resolver. Initialize once with the usual command plus `--rebuild`.
Without an index, or when the parser fingerprint changes, the default command
stops and requests --rebuild. It never silently scans all historical raw files.

Unchanged sources reuse normalized usage metadata. JSONL resumes at the last
complete line's byte offset, carrying model/session/fork state and Kimi occurrence
identities forward. Incomplete trailing lines wait for their newline. Replacement,
truncation, same-size edits and changed prefix anchors invalidate only that file.
SQLite/WAL and non-append JSON snapshots are cached per file and re-read only when
their signatures change, because rows can be updated/deleted. Header, child-catalog
and quota-snapshot discovery metadata is also cached.

Signatures include device/inode, size, mtime and ctime. Append validation checks
bounded prefix and previous-tail hashes and assumes append-only history. Arbitrary
middle edits combined with growth cannot be detected from these anchors alone;
use --rebuild after such edits. Refreshes are transactional; failed/concurrently
changing sources cannot commit partial checkpoints. Cached records store usage
and parser state, not prompts, responses or credentials.

Reports still cover the full requested interval using cached plus new records,
not only the delta. JSON exposes cache hits, raw files read, appended files and
JSONL bytes read. No Provider refresh, model call or schedule is added.

## Reset Evidence Ranking

`reset_evidence` records the boundary interval, first recorded usage time, source
priority, conflicts and limitations. Boundary priorities (highest first) are:

1. An explicit Provider reset timestamp, only when an existing record supplies it.
2. Reported zero usage (remaining=100) followed by a recorded token-consuming request.
3. Reported zero without a matching first-use record.
4. A substantial remaining-quota jump.
5. A changed scheduled reset endpoint alone.

Source precedence is Provider reset event > native quota > monitor quota > daily
snapshot > execution usage > unspecified. These are ordinal policy ranks, NOT
probabilities or empirically calibrated weights. Identical observations with the
same group/model scope and observation timestamp count once, even if copied to
several stores. Different stores do not provide independent votes for the same
fact. Higher-priority conflicting sources are selected with an explicit conflict
flag; equal-priority contradictions are not used as boundaries. Stale and invalid
observations are excluded. reset_occurred_at requires evidence_source set to
provider_reset_event and is never synthesized from the scheduled endpoint.

Zero is a reported percentage and may be rounded. With previous positive usage,
the inferred reset lies between that observation and the zero observation. The
first recorded use after zero is a useful activity anchor/upper bound, not proof
that reset occurred at that instant or that no other device used the account.
Without preceding evidence the reset lower bound remains unknown. Explicit model
scopes and the next scheduled endpoint bound the first-use search. Cycle token
bounds consume these ranked intervals and retain their evidence, but never claim
an account-wide 10% error guarantee.

Raw-parser fingerprints now isolate reader definitions from report aggregation;
future evidence-ranking changes need not reparse raw. Upgrading from the former
fingerprint format still requires explicit --rebuild; no automatic rebuild runs.
