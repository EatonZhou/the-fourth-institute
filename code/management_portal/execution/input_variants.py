"""Choose one authorized text representation once, at final dispatch.

The business owns material authorization/lifetime. Execution owns policy,
selection and recovery; it never reads files or retries with another input.
"""
from dataclasses import replace
import hashlib
import json
from pathlib import PurePosixPath
import re

from .local_file_read import assess_local_file_read


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_input_variants(request):
    spec = request.metadata.get("input_variants")
    if spec is None:
        return None
    if (not isinstance(spec, dict) or set(spec) != {"version", "path_prompt", "materials"}
            or type(spec["version"]) is not int or spec["version"] != 1):
        raise ValueError("Unsupported input_variants contract")
    if (request.session.mode != "stateless" or request.input_media
            or (request.service_type, request.operation) not in {
                ("agent_session", "turn"), ("chat_completion", "generate")}):
        raise ValueError("input_variants requires a stateless text execution")
    if not isinstance(spec["path_prompt"], str) or not spec["path_prompt"].strip():
        raise ValueError("input_variants path_prompt must be nonempty text")
    if not isinstance(request.prompt, str) or not request.prompt.strip():
        raise ValueError("input_variants full prompt must be nonempty text")
    materials = spec["materials"]
    if not isinstance(materials, list) or not materials:
        raise ValueError("input_variants requires identified materials")
    identities, paths = set(), set()
    for material in materials:
        if not isinstance(material, dict) or set(material) != {"identity", "sha256", "path"}:
            raise ValueError("Invalid input_variants material")
        identity, sha, path = (material[key] for key in ("identity", "sha256", "path"))
        if not isinstance(identity, str) or not identity.strip() or "\x00" in identity:
            raise ValueError("Invalid input_variants material identity")
        if not isinstance(sha, str) or not re.fullmatch("[0-9a-f]{64}", sha):
            raise ValueError("Invalid input_variants material digest")
        if not isinstance(path, str) or "\x00" in path or not PurePosixPath(path).is_absolute():
            raise ValueError("input_variants requires absolute material paths")
        if identity in identities or path in paths:
            raise ValueError("Duplicate input_variants material")
        identities.add(identity)
        paths.add(path)
    return spec


def _fingerprint(request, spec):
    return _sha(json.dumps({"full_prompt": request.prompt, "alternatives": spec},
                           ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def select_input_variant(invocation, context):
    spec = validate_input_variants(invocation.request)
    if spec is None:
        from .input_variant_trace import trace_invocation
        trace_invocation('decision', invocation, context)
        return invocation
    request = invocation.request
    fingerprint = _fingerprint(request, spec)
    frozen = context.current_input_variant()
    if frozen is None:
        frozen = invocation.recovery_checkpoint.get("input_variant")
    if frozen is not None:
        if (not isinstance(frozen, dict) or type(frozen.get("version")) is not int
                or frozen["version"] != 1
                or frozen.get("execution_id") != request.execution_id
                or frozen.get("input_fingerprint") != fingerprint
                or frozen.get("selected") not in ("path", "full")
                or not isinstance(frozen.get("user_prompt"), str)
                or frozen.get("user_prompt_sha256") != _sha(frozen["user_prompt"])):
            raise ValueError("Input variant checkpoint does not match this execution")
        policy = frozen.get("policy")
        if (not isinstance(policy, dict) or policy.get("scope") != "policy"
                or policy.get("status") not in ("available", "blocked", "unknown")
                or not isinstance(policy.get("reason"), str) or not policy["reason"]
                or (frozen["selected"] == "path" and (
                    request.service_type != "agent_session" or policy["status"] != "available"))):
            raise ValueError("Invalid input variant checkpoint policy")
        # Do not reassess and switch representations after an input was chosen.
        context.record_input_variant(frozen)
        selected_call = replace(invocation, user_prompt=frozen["user_prompt"])
        from .input_variant_trace import trace_invocation
        trace_invocation('decision', selected_call, context)
        return selected_call
    if invocation.recovery_checkpoint.get("turn_id") or invocation.recovery_checkpoint.get("resumable"):
        raise ValueError("Running execution has no frozen input variant")
    policy = assess_local_file_read(invocation).to_payload()
    selected = ("path" if request.service_type == "agent_session"
                and policy["status"] == "available" and policy["scope"] == "policy" else "full")
    # Preserve injections and Harness/context wrappers. Never guess a lossy
    # transformation's boundary or silently discard authorization context.
    if invocation.user_prompt.count(request.prompt) != 1:
        raise ValueError("Input variant lost its canonical prompt boundary")
    prompt = (invocation.user_prompt.replace(request.prompt, spec["path_prompt"], 1)
              if selected == "path" else invocation.user_prompt)
    decision = {
        "version": 1, "execution_id": request.execution_id,
        "input_fingerprint": fingerprint, "selected": selected, "policy": policy,
        "user_prompt": prompt, "user_prompt_sha256": _sha(prompt),
    }
    # Synchronous durable checkpoint MUST finish before a model can receive it.
    context.record_input_variant(decision)
    selected_call = replace(invocation, user_prompt=prompt)
    from .input_variant_trace import trace_invocation
    trace_invocation('decision', selected_call, context)
    return selected_call


def input_variant_result(invocation, context, result):
    if invocation.request.metadata.get("input_variants") is None:
        return replace(result, output={**result.output, 'input_variant': {
            'selected': 'full', 'reason': 'input_variants_missing',
            'prompt_chars': len(invocation.user_prompt), 'contains_snapshot_path': False}})
    decision = context.current_input_variant()
    summary = {key: decision[key] for key in (
        "version", "execution_id", "input_fingerprint", "selected", "policy", "user_prompt_sha256",
    )}
    from .input_variant_trace import trace_invocation
    row = trace_invocation('result', invocation, context)
    summary.update({key: row[key] for key in ('business_ref', 'role', 'reason', 'prompt_chars', 'contains_snapshot_path')})
    # A selection receipt is not a native Read receipt.
    return replace(result, output={**result.output, "input_variant": summary})
