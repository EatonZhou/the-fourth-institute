#!/usr/bin/env python3
"""Opt-in live DSH acceptance. Uses a new native session and bounded API calls."""
import argparse
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from management_common.product_environment import mirror_product_env
from management_portal.dsh_client import DshClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--resident-id", required=True)
    parser.add_argument("--lineage-id", required=True)
    args = parser.parse_args()
    marker = "DSH_" + uuid.uuid4().hex
    client = DshClient()
    calls, observed = [], []
    resident_environment = {}
    mirror_product_env(resident_environment, "PORTAL_RESIDENT_ID", args.resident_id)
    mirror_product_env(resident_environment, "PORTAL_LINEAGE_ID", args.lineage_id)
    common = dict(session_key=marker, cwd=args.cwd, provider_profile="deepseek", model="deepseek-v4-flash",
                  effort="low", is_owner=True, permission_profile="owner", resident_environment=resident_environment)
    tool = {"name": "portal_probe", "description": "Return the acceptance nonce. Call exactly once.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}
    def callback(call):
        calls.append(call)
        return {"content": [{"type": "text", "text": marker}], "isError": False}
    params = dict(common, session_id="", turn_id=marker + "-1", tools=[tool],
                  system_prompt_append="Follow the request. Literal {{portal_sample}} must remain unchanged.",
                  prompt="Call portal_probe once and remember its exact nonce. Reply with that nonce only.")
    first = client.run_turn(**params, tool_callback=callback, event_callback=observed.append)
    try:
        assert marker in first.response and len(calls) == 1, (first.response, len(calls))
        replay = client.run_turn(**params, tool_callback=callback)
        assert replay.response == first.response and len(calls) == 1
        client.close_session(marker, first.session_id)
        cold_replay = client.run_turn(**{**params, "session_id": first.session_id, "system_prompt_append": ""}, tool_callback=callback)
        assert cold_replay.response == first.response and len(calls) == 1
        second = client.run_turn(**common, session_id=first.session_id, turn_id=marker + "-2", tools=[],
                                 system_prompt_append="", prompt="Return the exact nonce obtained from portal_probe in our previous turn. Do not call any tools.")
        assert marker in second.response, second.response
        third = client.run_turn(**common, session_id=first.session_id, turn_id=marker + "-3", tools=[],
                                system_prompt_append="", prompt='Use Bash to execute: test -z "$DEEPSEEK_API_KEY" && printf "KEY_ABSENT\\n"; printf "%s\\n" "$PORTAL_RESIDENT_ID" "$PORTAL_LINEAGE_ID". Report the output exactly.',
                                event_callback=observed.append)
        assert "KEY_ABSENT" in third.response and args.resident_id in third.response and args.lineage_id in third.response, third.response
        stopped = []
        def interrupt_on_tool(event):
            observed.append(event)
            if event.get("method") == "item/started" and not stopped:
                stopped.append(client.interrupt(marker, first.session_id, turn_id=marker + "-4"))
        try:
            client.run_turn(**common, session_id=first.session_id, turn_id=marker + "-4", tools=[], system_prompt_append="",
                            prompt='Use Bash to run exactly: sleep 30; echo FINISHED. Wait for its output.', event_callback=interrupt_on_tool)
            raise AssertionError("Cancelled operation returned success")
        except RuntimeError as error:
            assert "aborted" in str(error), str(error)
        assert stopped == [{"interrupted": True}], stopped
        continued = client.run_turn(**common, session_id=first.session_id, turn_id=marker + "-5", tools=[],
                                   system_prompt_append="", prompt="Reply exactly DSH_CONTINUED.")
        assert "DSH_CONTINUED" in continued.response, continued.response
        assert client.interrupt(marker, first.session_id, turn_id=marker + "-4") == {"interrupted": False}
        historical_events = []
        historical = client.run_turn(**{**params, "session_id": first.session_id, "system_prompt_append": ""},
                                     tool_callback=callback, event_callback=historical_events.append)
        assert historical.response == first.response and len(calls) == 1
        assert "DSH_CONTINUED" not in json.dumps(historical_events)

        print(json.dumps({"session": first.session_id, "checks": ["live_model", "worker_tool_callback", "idempotent_replay", "cold_resume", "cold_idempotent_replay", "historical_replay_isolated", "native_shell", "credential_scrubbing", "resident_identity", "cancel", "continue_after_cancel", "stale_cancel_fenced"], "usage_first_turn": first.usage}, ensure_ascii=False))
    finally:
        client.close_session(marker, first.session_id)


if __name__ == "__main__":
    main()
