"""Built-in Family review Effects and their shared-state bindings."""

RECORD_ACTIVITY = r'''member="${event.subject_resident_id}"
mid="${event.payload.message_id}"
if [ -z "$member" ] || [ -z "$mid" ]; then
  output status="skipped"
  exit
fi
input=$(GetInputState resident_id="$member" message_id="$mid" include_input_facts:=true)
cursor="${input.input_state.input_facts.completed_cursor}"
if [ "${input.input_state.state}" != "completed" ] || [ "${input.input_state.input_facts.role}" != "user" ] || [ "${input.input_state.input_facts.is_info}" = "true" ] || [ "${input.input_state.input_facts.business_ref:0:5}" = "duty/" ] || [ -z "$cursor" ]; then
  output status="skipped"
  exit
fi
for attempt in 1 2 3 4; do
  ledger=$(GetSharedState lineage_id="${arguments.lineage_id}" key="${arguments.review_key}")
  previous=$(json_get value:=${ledger.value} path:=["activity",${member}])
  if [ -n "${previous.value.0}" ] && [ "${previous.value.0}" -ge "$cursor" ]; then
    output status="recorded"
    exit
  fi
  next=$(json_set value:=${ledger.value} path:=["activity",${member}] item:=[${cursor},${mid}])
  committed=$(UpdateSharedState lineage_id="${arguments.lineage_id}" key="${arguments.review_key}" value:=${next.value} expected_value:=${ledger.value})
  if [ "${committed.matched}" = "true" ]; then
    output status="recorded"
    exit
  fi
done
fail "并发更新 Family 回顾活动，请重查原完成输入"'''


NOTICE = r'''lineage="${arguments.lineage_id}"
key="${arguments.review_key}"
steward="${arguments.steward}"
ledger=$(GetSharedState lineage_id="$lineage" key="$key")
pending:=${ledger.value.pending}
if [ -z "${pending.message_id}" ]; then
  if [ -z "${ledger.value.activity}" ] || [ "${ledger.value.activity}" = "${ledger.value.reviewed}" ]; then
    output status="unchanged"
    exit
  fi
  facade_state=$(ListResidentStates resident_ids:=[${arguments.facade}] include_viewer_state:=false)
  if [ "${facade_state.states.0.state.intake_state}" != "open" ]; then
    output status="paused"
    exit
  fi
  steward_state=$(ListResidentStates resident_ids:=[$steward] include_viewer_state:=false)
  if [ "${steward_state.states.0.state.work_state}" != "idle" ] || [ "${steward_state.states.0.state.queued_count}" -gt 0 ]; then
    output status="busy"
    exit
  fi
  mid="msg_d066000000000007${event.id:6}"
  brief="Family 门面 ${arguments.facade} 的周期回顾。活动位置：${ledger.value.activity}。${arguments.family_tasks.review.prompt}\n${arguments.family_tasks.review.guide}"
  pending:={"message_id":${mid},"steward":${steward},"activity":${ledger.value.activity},"envelope":{"message_id":${mid},"content_parts":[{"type":"text","text":${brief}}]}}
  next=$(json_set value:=${ledger.value} path:=["pending"] item:=${pending})
  committed=$(UpdateSharedState lineage_id="$lineage" key="$key" value:=${next.value} expected_value:=${ledger.value})
  if [ "${committed.matched}" != "true" ]; then
    output status="busy"
    exit
  fi
fi
mid="${pending.message_id}"
if [ "${pending.steward}" != "$steward" ]; then
  output status="target_changed"
  exit
fi
if [ -n "${pending.envelope.message_id}" ]; then
  DeliverMessage resident_id="$steward" business_ref="duty/family-review" message:=${pending.envelope}
else
  GetInputState resident_id="$steward" message_id="$mid"
fi
for attempt in 1 2 3 4; do
  ledger=$(GetSharedState lineage_id="$lineage" key="$key")
  if [ "${ledger.value.pending.message_id}" != "$mid" ]; then
    output status="requested" message_id="$mid"
    exit
  fi
  consumed=$(json_set value:=${ledger.value} path:=["reviewed"] item:=${pending.activity})
  next=$(json_set value:=${consumed.value} path:=["pending"] item:=null)
  committed=$(UpdateSharedState lineage_id="$lineage" key="$key" value:=${next.value} expected_value:=${ledger.value})
  if [ "${committed.matched}" = "true" ]; then
    output status="requested" message_id="$mid"
    exit
  fi
done
output status="accepted_pending_checkpoint" message_id="$mid"'''


def bindings(lineage_id, family_id, family):
    return {key: {"value": value} for key, value in {
        "lineage_id": lineage_id, "review_key": f"family.{family_id}.review",
        "facade": family["facade"], "steward": family["steward"],
    }.items()}
