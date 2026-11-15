# Input execution diagnostics

`GetInputState(resident_id, message_id, include_execution_diagnostics=true)`
adds `input_state.execution_diagnostics`. The default read performs no extra
Execution RPC. Diagnostics is observational: it does not cancel, retry, recover,
refresh a Provider session, or change the input's queue position.

Society authorizes the real current caller using the existing `GetInputState`
permission and input/conversation read boundary. Persisted Family relay and
Mailbox group relations resolve the concrete input. An isolated input's caller
scope still applies. A deleted Resident's sender-fate receipt does not grant
runtime diagnostics. A retained input with its own execution is not rebound to
a later group.

The Execution owner port accepts only `actor`, `resident_id`, and `message_id`.
It resolves and authorizes the association again, then checks the admitted input
references and Resident identity. There is no public execution-ID selector and
no substitution of the original execution actor. Rejections remain rejections.

The versioned `tfi.execution-input-diagnostics.v1` result contains only explicit
lifecycle, worker lease, cancellation, attempt, and recovery-marker fields.
Request bodies, prompts, Provider output, credentials, and complete checkpoints
are excluded. Missing observations are `unknown`; an unassociated input or
unavailable Resident returns `available=false` with a reason. In particular,
unknown delivery or a stale Society `queued` projection is not proof that the
Provider never received the input. Runtime diagnostics is not evidence that
the user's business task is complete.

`submission` reports the Society owner's exact current Resident/input/execution
association and its submission, dispatch, completion and acknowledgement receipts.
It reads only whitelisted timestamps, enums and booleans in SQL, never the stored
request or queue bodies. A replaced or absent job returns `current_job_matches=false`;
missing receipts remain `unknown`. Neither result proves that a Provider prompt
was never sent. This section is attached only after the authorized Execution port
has resolved and checked the association.
The same exact-job view exposes registration readiness, intake/admission and
pending lifecycle-work booleans plus the supported activation-policy enum.
The pending step is limited to `session_restart`, `execution_ack`, `other` or `none`.
These are recovery gate observations, not permission to reopen intake or replay
an execution; lifecycle payloads remain private.

For the same current job, `reserve_admission` projects only the quota owner's
policy, migration status, pending/account-wait booleans, whether its source
matches the current account, and cached quota freshness/headroom. It neither
refreshes quota nor runs migration. Account-service failure keeps observations
`unknown`. Migration payloads, account identities and free-text reasons are not
returned. `continuation_running` observes only this Society process's runner;
false does not prove that another process has no continuation lease.
