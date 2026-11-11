"""Content-free input selection diagnostics shared by admission and dispatch."""
import json


def trace(stage, *, business_ref='', role='', prompt='', spec=None, decision=None):
    decision = decision or {}
    materials = (spec or {}).get('materials', [])
    paths = [m.get('path') for m in materials if isinstance(m, dict) and m.get('path')]
    row = {'event': 'input_variant_trace', 'stage': stage,
           'business_ref': str(business_ref), 'role': str(role),
           'selected': decision.get('selected', 'pending' if spec else 'full'),
           'reason': (decision.get('policy') or {}).get('reason') or decision.get('reason') or ('await_final_policy' if spec else 'input_variants_missing'),
           'prompt_chars': len(prompt), 'contains_snapshot_path': any(p in prompt for p in paths)}
    try:
        print(json.dumps(row, ensure_ascii=False, separators=(',', ':')), flush=True)
    except OSError:
        pass  # Diagnostic delivery must never fail a paid task.
    return row


def trace_invocation(stage, invocation, context):
    request = invocation.request
    return trace(stage, business_ref=request.metadata.get('business_ref', ''),
                 role=request.agent_id, prompt=invocation.user_prompt,
                 spec=request.metadata.get('input_variants'), decision=getattr(context, 'current_input_variant', lambda: None)())
