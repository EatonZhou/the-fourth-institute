# Reader translation HTTP contract

This module owns the Google/DeepSeek Reader feature. It does not enable the
earlier Resident/Kimi translation experiments or change any default model route.

POST `/paper/api/reader/translation` accepts exactly:

```json
{"operation_id":"client-generated-stable-id","text":"The experiment uses three samples.","source_language":"en","target_language":"zh-CN","mode":"auto","document":{"paper_id":"...","attachment_id":"...","attachment_version":"..."},"refresh":false}
```

`mode` is `auto`, `google` or `deepseek`; default UI choice is `auto`. Text is
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

The cache file also retains opt-in verified-Resident APIs from earlier isolated
experiments. Production uses only request/get/claim/finish and never imports or
calls their workflow evidence dependency. Do not include paper_translation_execution,
paper_translation_workflow, Kimi runtime patches or experimental provider controls
in this feature's publication. Production DeepSeek uses ExecutionService's existing
chat_completion service and registry/credential owner for deepseek_primary, never
direct provider HTTP or a Resident business-role substitute.

Google endpoint and response-segment semantics follow Translate for Zotero
google.ts at commit 4e9ca763b9d8f07604aceb7671b5e0c29c3b5a3a (AGPL-3.0).
No TypeScript source, token algorithm or Zotero runtime code is copied; the Python
network/timeout implementation is new. Its fixed endpoint is translate.google.com,
`translate_a/single`, `client=gtx`, not a billing API or client-supplied URL.

Rollback: revert the explicit UI/backend publication paths; retain PG rows and
receipts. Do not drop tables, resubmit failed tasks, migrate PDFs or activate Kimi.
