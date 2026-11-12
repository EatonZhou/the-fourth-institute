# Reader translation HTTP contract

This module owns the Google/DeepSeek Reader feature and the explicit Moonshot Model API option. It does not enable the
earlier Resident/Kimi translation experiments or change any default model route.

POST `/paper/api/reader/translation` accepts exactly:

```json
{"operation_id":"client-generated-stable-id","text":"The experiment uses three samples.","source_language":"en","target_language":"zh-CN","mode":"auto","document":{"paper_id":"...","attachment_id":"...","attachment_version":"..."},"refresh":false}
```

`mode` is `auto`, `google`, `deepseek` or `kimi`; default UI choice is `auto`. Text is
1–16000 UTF8 bytes; total JSON body at most 32768 bytes. `refresh` is optional.
Document fields are selectors only: the authoritative Reader owner resolves them
and checks current Paper access. Browser actor, provider/model, paths, arbitrary
prompts and claimed shared scopes are rejected. Terminology currently uses an
explicit empty `none-v1` recipe; new terms/settings require a new key.

GET the same path with `?task_id=...` observes an existing task/Execution and may
publish its completed result. It does not start a Google request or submit an
Execution. POST with the original operation/body can recover a prepared binding;
only its original saved Execution ID and exact request can be resubmitted through
ExecutionService's durable idempotence. An interrupted Google request is *not*
inferred to be a timeout; it fails and requires an explicit new operation.

POST `/paper/api/reader/translation/cancel` accepts only `{"task_id":"..."}`.
Cancelling a merged task affects all waiters of that same private task. Already
completed results stay completed. While Google is active, cancel acknowledgement
may still show `running`; its observer drops late output and settles `cancelled`.
Bound DeepSeek cancellation goes through ExecutionService. Stopping observation
does not prove a provider has not billed an already accepted request.

Every route requires a valid Portal cookie or bearer auth and matching frozen
`X-Reader-Principal`. Mutations require same-origin JSON/normal Portal CSRF
constraints. Authorization is repeated on every lookup, cache hit, observation,
before model admission and result publication. A disabled account cannot read or
publish through its stale context. No alternate privileged actor is used.

Successful HTTP responses have task_id, status, text, error, cache, provider,
model, google_request_ms and total_ms. Status is queued/running/succeeded/failed/
cancelled. Error is null or `{code,message}`; failed results are never cached.
`cache` is miss/inflight/hit/operation. API errors use `{error:{code,message}}`:
400 invalid_request, 401 authentication_required, 403 access_denied,
404 not_found, 409 principal_changed/operation_conflict, 413 request_too_large,
503 service_unavailable. No upstream body or credentials are returned.

`google_request_ms` measures the original real Google transport interval. The
absolute 3-second budget starts immediately before HTTP open, after preflight,
auth, cache and concurrency admission. A watchdog bounds DNS/connect/read waits;
trickling reads do not reset the budget. Timed-out I/O can finish in a bounded
daemon slot, but never writes the cache. Auto fallback occurs only on the
transport's GoogleTimeout, exactly once to DeepSeek. HTTP failures, malformed
responses, capacity and authorization errors do not trigger fallback. Manual
modes only call their selected source. `PORTAL_PAPER_GOOGLE_TIMEOUT_SECONDS`
configures the server budget (default 3, allowed .01–30); changing it changes keys.
DeepSeek gets its own independent 60-second provider request timeout, zero retries.

`total_ms` is the current HTTP-handler interval including auth/cache/concurrent
processing but excluding pre-handler Web queue/network time. `task_total_ms` is
the original task's persisted completion interval; a cache hit does not repeat
that work. Browser click-to-result time is separately measured by the UI.

Google provider is `google`, model `translate_a/single:gtx` with service-algorithm
provenance (not a wire LLM model claim). DeepSeek provider is `deepseek`; `model`
comes only from response.model and is null when absent; requested_model remains
`deepseek-v4-flash`. model_source explicitly distinguishes the actual wire field.
Execution IDs are retained for audit. No configured value fills a missing wire
model field. Auto cache entries retain the actual selected provider.

## Ownership and deployment

TranslationCache reuses its existing operation/key/task transactions and lease
fencing. Optional `outcome` JSONB is atomically committed with success/failure.
The new delivery table retains chosen phase, exact canonical ExecutionRequest,
submission receipt state, cancellation and Google timing. All tables are lazy
DomainDatabase `paper` tables; route assembly performs no DDL/model call.
Operations/receipts and successful results currently have no automatic pruning.
Private scope is derived from formal authentication. Shared cache is not exposed.

The cache descriptor contains exact text/languages, document version, scope,
mode, Google/DeepSeek budgets, API registry version, actual model and parameters,
materialized system parts, business recipe and terminology. Reusing an operation
after its material configuration changes returns conflict, never a new charge.
Concurrent identical keys merge. Refresh failure preserves the previous success.
Requests without confirmed completion retain their binding, not a new Execution.

The verified subscription experiment is permanently cancelled. Its public legacy
entrypoints and `request_verified` retain explicit rejection; existing binding
inspection remains read-only. The cache no longer creates or queries the dedicated
verified-results table, accepts `_verified`, or takes a reuse-proof callback.
Existing tables are not dropped. `publication_mode` remains so a late success for
a historical verified task is rejected; an authorized, lease-fenced failure can
still be recorded.

Execution belongs to ReaderTranslationService, which uses the cache's
request/get/descriptor/claim/finish protocol. The former internal `run` wrapper and
`execute` constructor callback are removed; there is no second execution path in
the cache. Production DeepSeek uses ExecutionService's existing chat_completion
service and registry/credential owner for deepseek_primary. The ordinary Kimi API
option below is separate from the cancelled subscription experiment.

Google endpoint and response-segment semantics follow Translate for Zotero
google.ts at commit 4e9ca763b9d8f07604aceb7671b5e0c29c3b5a3a (AGPL-3.0).
No TypeScript source, token algorithm or Zotero runtime code is copied; the Python
network/timeout implementation is new. Its fixed endpoint is translate.google.com,
`translate_a/single`, `client=gtx`, not a billing API or client-supplied URL.

Rollback: revert the explicit UI/backend publication paths; retain PG rows and
receipts. Do not drop tables, resubmit failed tasks, migrate PDFs or activate Kimi.


## Explicit Moonshot Model API option (Dev implementation)

`mode: "kimi"` selects the existing `kimi_primary` registration. It is a manual
paid-API selection. New auto tasks use Google → DeepSeek → Kimi as specified below. No subscription CLI,
Resident role, native runtime, tools or history is used. Qwen-MT is excluded
because this Portal has no corresponding Model API registration.

The server validates an enabled Moonshot OpenAI-chat registration at the official
HTTPS `/v1` origin and its enabled default model (`kimi-k2.6` or `kimi-k2.5`).
Registration revision, model, origin, defaults and a separate recipe revision are
private cache material. Unsupported/missing registration returns HTTP503
`service_unavailable`; payload provider/model/endpoint overrides remain rejected.
The existing Model API owner reads credentials only when executing; the Paper
service never reads secret files. Credential availability/account billing access
is not established by metadata validation or these isolated tests.

Execution remains stateless `chat_completion`, operation `generate`, exact durable
Execution ID; timeout60s and zero retries. The final body contains one concise
system instruction, the user translation material, `thinking:{type:"disabled"}`,
`max_tokens:4096`, `stream:false`. Only wire `response.model` is reported; missing
model stays null. Failed/truncated responses do not become successful cache hits.
GET observes only, cancellation uses the existing execution owner, a new manual
operation is required to retry a failed task; no cross-provider retry occurs.

Official model instructions: https://huggingface.co/moonshotai/Kimi-K2.6
This is unbilled Dev validation, not proof of successful live API translation.


## Auto chain v4

New auto admissions freeze both DeepSeek and Kimi registration/recipe material
in the private cache key. Google starts DeepSeek only on a proven real network
timeout, as before. DeepSeek starts Kimi only after a terminal `failed` Execution
with recorded `tfi.provider-transport.v1` evidence: timeout/read_timeout/connection_error
with no HTTP response, or HTTP500/502/503/504. A running/queued task is not a
proven timeout. Cancellation, resource/auth permission errors, HTTP401/402/403/429,
quota/credentials, unknown error codes, TLS/JSON errors, empty/truncated/refused
content never admit another paid source. Manual modes stay single-source.
Old admitted v2 tasks keep their original two-source policy; they are never
retroactively charged for Kimi. Each model has its own60s request budget and zero
model retries; the Kimi fallback receives a different, durable immutable Execution
ID. The DeepSeek request/ID and fallback reason remain in delivery storage; final
provider/model and fallback provenance are cached with the actual result.

POST admits one bounded local continuation per task/owner (maximum16 concurrent,
180s observation window). Existing database leases and Execution idempotence
remain authoritative across observers; models are never raced. This continuation
only observes and advances a terminal predecessor, retaining original context and
rechecking current principal/document authorization at each step. It holds no DB
transaction during RPC/network waits. GET does not prepare or submit a new stage.
Cancellation is rechecked before each stage and submission. A process restart,
authorization loss or an exhausted observation window cannot invent a failure or
advance an unproven predecessor; a new explicit POST can resume the same task and
saved Execution IDs. No task is automatically reposted by the browser. Kimi is
attempted at most once and has no further fallback. Missing frozen Kimi configuration
ends the failed chain explicitly without choosing an alternative API.


## Explicit translation direction (new admissions)

The owner validates language-code syntax before constructing a short directional
system instruction, e.g. `Translate the JSON field "text" from English (en) into
Simplified Chinese (zh-CN).` The reverse request explicitly names English (en)
as the target. The full instruction treats text/terminology as data and preserves
paragraphs, terminology, numbers, mathematical symbols and formulas. The user
message remains JSON with source_language, target_language, text and terminology;
there is no Agent identity, conversation history or tool declaration. Both stages
of auto receive the same frozen directional instruction.

Manual DeepSeek uses reader-translation-flash-direction-v3, Kimi uses
reader-translation-moonshot-direction-v2, and auto uses
reader-translation-google-deepseek-kimi-v4. These new versions and the actual
system instructions participate in private cache material; old admitted tasks
keep their frozen instructions and policy. Manual Google keeps its original
recipe and request behavior. Prompt changes have only isolated fake-transport
verification, with no additional paid API probes.
