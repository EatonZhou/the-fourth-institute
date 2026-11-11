# Native local-file reading contract

This is an Execution capability, not a Paper, subscription-account, or UI
allowlist. `AgentProvider.local_file_read` defaults to false. Codex, Kimi Code
and Claude Code declare nominal support; Antigravity remains undeclared.
The declaration is not authorization, process availability, or proof of reading.

## Executable assessment

```python
from management_portal.execution.local_file_read import assess_local_file_read

# prepared is the same BackendInvocation about to reach the actual adapter.
policy = assess_local_file_read(prepared).to_payload()
file_state = assess_local_file_read(
    prepared, path="/authorized/materialized/paper.txt",
).to_payload()
```

Both results contain only `status`, `reason` and `scope`.

| scope | available | blocked | unknown |
| --- | --- | --- | --- |
| policy | Final request options permit native reading | This invocation cannot offer native reading | Effective policy cannot be established from these options |
| file | Reserved for verified native per-file evidence; never emitted by this implementation | A policy or input constraint prevents this use | Actual native file access remains unverified |

No original prompt, path, exception text, credential or file content is included
in the result. Native tool-event observability, account labels, model names,
caller JSON receipts and Web/Worker filesystem checks cannot establish access.
In particular `policy/available` does not mean that the runtime is installed,
that the requested tools settled successfully, or that the model read the file.

Invoke after account/route selection, `native_options.prepare_invocation`,
Harness preparation and any invocation transformations, on the invocation that
will actually run. The existing final dispatch point is inside
`AgentSessionService.execute.execute_with_context_seed`, immediately before
`provider.execute(prepared, backend_context)`. A Web admission decision on raw
settings is provisional: `runtime_settings` still present returns
`runtime_options_not_prepared`. Do not prepare a second invocation with
different settings solely for a positive capability result.

This patch exposes the callable assessment only. It does not insert an event
into every execution, mutate requests, change prompts, choose a business
fallback, or retry a model call. The consumer must wire its admission/dispatch
policy to this interface and re-evaluate after route/session/options changes.
Existing Worker handover and recovery must keep the original execution.

## Adapter policies

* **Codex:** the existing named-profile-over-sandbox precedence is reused.
  Explicit read-only, workspace-write and full-access sandboxes permit reading
  in their native scope. A named custom profile, structured sandbox policy,
  unrecognized native config or inherited/unresolved policy remains unknown.
  Normal `CodexOptions.prepare` identity injection and Harness compaction
  settings must not alone turn an otherwise available policy into unknown.
  The adapter recognizes individual typed read-neutral leaves: reasoning
  display/summary, positive context/compaction limits (`scope=total`),
  update-plan enablement, agent enablement/interrupt notices, goal enablement,
  its own fixed compaction prompt and disabled project-document loading.
  In `shell_environment_policy.set`, only the two canonical Resident/Lineage
  identity variables pass, using the identity owner's existing validation.
  This is not a blanket exemption for environment, `tools` or `features`.
  Dotted and nested forms are supported; duplicate leaves, invalid values,
  unknown keys, read-tool/permission changes and custom process environment
  (including PATH/HOME/loader overrides) remain unknown. A full-access
  profile does not override these unknown settings. Assessment is passive,
  does not mutate native configuration, and does not read a target file.
* **Kimi:** final tools must include Read or ReadMediaFile. No read tool blocks
  the capability; wildcards are not guessed. Text-only constraints block it.
  Manual/unknown permission modes require native observation; yolo permits the
  policy. The native server must still settle the session profile/tool catalog.
* **Claude:** the daemon currently includes Read for both owner and reader.
  Its bypassPermissions mode permits the policy; dontAsk requires native
  path/permission evidence. Unsupported modes are blocked as the daemon rejects
  them. A cwd or add_dirs entry is not proof of readable content.
* **Antigravity:** the same native-tools gate used by execution is used here.
  Portal-only mode blocks native reading. Even when native tools are allowed,
  the real installed runtime's file capability is unverified, so the result
  stays unknown. A fake ACP view_file event cannot change this.

## Per-file verification and safe consumption

None of these adapters currently offers a common native per-path probe. Passing
an absolute path returns `unknown/native_file_probe_required` when policy
permits reading. No model is invoked to obtain a preflight result. Even an
existing Web-readable path or a successful Worker open cannot promote it.

A real probe must be owned by the selected adapter, after its native session,
effective tools and policy are settled: after Codex thread startup/native policy
resolution, Kimi profile/tool settlement, Claude SDK handle initialization, or
Antigravity ACP session/mode negotiation. It must use the native tool execution
boundary with the same process identity, mounts, cwd, read roots, approvals and
symlink resolution as the actual turn. Do not introduce a separate privileged
shell or run an extra model turn. Where no such provider RPC exists, retain
unknown and obtain evidence only from the actual task's native read operation.

Future successful evidence needs execution, account route, native session and
process generation identity, effective policy/tool identity, resolved target
and immutable artifact/content version, plus actual read outcome. It cannot be
accepted from a business-provided dict, reused after restart/policy change, or
inferred solely from a tool-start event. Reading bytes is separate from complete
PDF extraction or correct model comprehension. A preflight cannot eliminate
later file changes; the business owner keeps authorized material immutable and
available for the whole execution.

Paper may use policy availability to identify a candidate path mode. If it
requires proven readability before dispatch, current file=unknown must retain
the full-text mode (or wait for an adapter-owned native probe). A post-dispatch
read failure is not authorization to replay business work. API chat completion
has no native local-file tools and keeps the existing full-text path. Neither
this contract nor this patch changes per-call history, session bindings,
authorization, or application storage lifecycle.

## Validation

`tests/cases/test_local_file_read.py` provides direct unittest coverage without
models, services or a database. In an isolated checkout, unset the inherited
live `PORTAL_INSTANCE_ROOT` first, then select a disposable fixture manifest
pointing at this checkout via `tests.cases.instance_test_support.write_instance`.
Assert the imported provider_catalog file is from this checkout: production
instance manifests otherwise intentionally redirect shared package imports.
The accompanying delivery includes a repeatable isolated runner.
