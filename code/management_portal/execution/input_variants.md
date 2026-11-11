# Authorized input variants (v1)

This is an opt-in Execution input contract. A material-owning service supplies
two authorized representations of the same task. Execution chooses one after
the adapter and Harness have prepared their final policy. No business or
Provider allowlist belongs in the caller.

## Request

Use the existing `ExecutionRequest.prompt` for the full-text representation:

```python
prompt = full_prompt
metadata = {
    "input_variants": {
        "version": 1,
        "path_prompt": "Read /authorized/task/material.txt and perform the task ...",
        "materials": [{
            "identity": "workspace_example/file/material.txt",
            "sha256": "<64 lowercase hexadecimal characters>",
            "path": "/authorized/task/material.txt",
        }],
    },
}
```

The caller owns material authorization, stable identity and contents, digest
verification, and lifetime through execution/recovery. Metadata does not grant
filesystem access. The caller supplies both complete task representations;
Execution does not generate or fetch either one. Both may contain the same
instructions, while only `prompt` contains the full material body.

Version 1 supports stateless text `agent_session/turn` and
`chat_completion/generate` requests. It rejects persistent/fork operations,
input media, malformed/duplicate materials, non-absolute paths and invalid
digests. Existing calls without `input_variants` are unchanged. This uses
existing metadata/checkpoint storage; no new HATG instruction, table or
per-call history schema is introduced.

## Decision and dispatch

For Agent sessions, `AgentSessionService.execute_with_context_seed` selects
after native options, Harness, Resident capabilities and context-seed preparation:

| Final assessment | Selected body |
| --- | --- |
| Agent session, `scope=policy`, `status=available` | `path_prompt` |
| Agent session, blocked or unknown | full `prompt` |
| Chat completion API | full `prompt` |

The assessment uses the generic `assess_local_file_read` contract. It does not
probe a path, use Web-process `os.access`, launch a model, or claim an actual
file was read. `available` means the final native policy permits the nominal
capability. Actual task-native Read evidence is required to establish access.
An unavailable or unreadable file can still make the chosen path task fail.

Injection and Harness prefix/suffix text is retained. The prepared user text
must contain exactly one occurrence of the original full `prompt`; otherwise
Execution rejects the request before dispatch, rather than guessing the
boundary of a transformed input. System context is unchanged.

The selected invocation is passed to the existing effective-input observer,
so the existing record reflects the actual selected body. This does not
introduce a new history interface or change history persistence rules.

## Durability and failures

Before Provider dispatch, the runtime synchronously stores `input_variant` in
the Execution checkpoint. This is separate from adapter-owned
`provider_recovery_state`, which adapters can replace. The record binds the
execution ID, full input/spec fingerprint, selection, policy assessment,
exact prepared user text, and its SHA256. Fingerprints hash UTF-8 bytes;
the input/spec fingerprint uses sorted-key compact JSON with Unicode retained.
The checkpoint text uses existing Execution storage/access boundaries and is
not included in the result receipt.

The same Execution restores the stored text byte-for-byte without reassessing
and changing representation. Input changes or corrupt/missing decisions for
an already running turn fail before dispatch. Checkpoint write failures also
prevent dispatch. Subsequent native-state/usage checkpoints and the completed
checkpoint retain the decision. Selection alone does not make a native turn
resumable; existing execution recovery rules still determine that.

There is no path-to-full retry after a failed Read, and no extra preflight or
fallback model call. Native execution errors follow the existing error path.
A caller must not automatically create another Execution with full text to
hide such a failure.

Successful output includes `output.input_variant`, with `version`,
`execution_id`, `input_fingerprint`, `selected`, `policy` and
`user_prompt_sha256`. It is a selection receipt, not native Read evidence and
not proof that the model consumed the material or fulfilled the task.

## Verification and integration

`tests/cases/test_input_variants.py` uses isolated protocol fixtures with the
real service/runtime selection and checkpoint code, without a real model or
database. It covers policy preparation, API fallback, immutable recovery,
checkpoint failure, invalid recovery, preserved wrappers and failure without
representation replay.

This slice depends on the separate local-file policy capability change.
Callers need only send the two representations and authorized material
identities; they must not predict `native_options`, inspect account names or
maintain their own Provider support table. This extension is isolated pending
review and is not available in Release until separately integrated/published.
