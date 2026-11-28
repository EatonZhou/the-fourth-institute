"""The generated Family recovery Effect and its Shared State bindings."""
from __future__ import annotations


RECOVERY = r'''lineage="${arguments.lineage_id}"
key="${arguments.recovery_key}"
facade="${arguments.facade}"
steward="${arguments.steward}"
front=$(ListResidentStates resident_ids:=[$facade] include_viewer_state:=false)
if [ "${front.states.0.state.intake_state}" != "open" ]; then
  output status="paused"
  exit
fi
ledger=$(GetSharedState lineage_id="$lineage" key="$key")
source:=${event}
if [ -z "${source.payload.message_id}" ] && [ -n "${event.payload.trigger_path_id}" ] && [ "${event.type}" != "time.reached" ]; then
  path=$(GetTriggerPath lineage_id="$lineage" trigger_path_id="${event.payload.trigger_path_id}" limit:=1)
  source:=${path.path.input}
fi
rid="${source.payload.resident_id}"
if [ -z "$rid" ]; then
  rid="${source.subject_resident_id}"
fi
mid="${source.payload.message_id}"
ref="${source.payload.input_business_ref}"
if [ -z "$ref" ]; then
  ref="${source.payload.business_ref}"
fi
if [ "${ref:0:13}" = "family-route/" ] && [ "${ref:13:21}" = "$facade" ] && [ "${ref:34:9}" = "/message/" ]; then
  rid="$facade"
  mid="${ref:43}"
fi
member="false"
for target in ${arguments.members}; do
  if [ "$rid" = "$target" ]; then
    member="true"
  fi
done
if [ "$member" = "true" ] && [ -n "$mid" ] && [ "$mid" != "${ledger.value.notice.message_id}" ]; then
  incoming="$rid:$mid"
  if [ "$rid" = "$facade" ]; then
    incoming="request:$mid"
  fi
  if [ "${event.type}" = "resident.message_received" ]; then
    intake=$(ListResidentStates resident_ids:=[$rid] include_viewer_state:=false)
    if [ "${intake.states.0.state.intake_state}" = "open" ]; then
      incoming=""
    fi
  fi
  if [ -n "$incoming" ]; then
    registered="false"
    for retry in 1 2 3; do
      if [ "$registered" != "true" ]; then
        known=$(json_get value:=${ledger.value} path:=["incidents",$incoming])
        if [ -n "${known.value.message_id}" ]; then
          registered="true"
        else
          item:={"resident_id":$rid,"message_id":$mid,"status":"open","attempts":0,"previous":${ledger.value.head}}
          next=$(json_set value:=${ledger.value} path:=["incidents",$incoming] item:=${item})
          next=$(json_set value:=${next.value} path:=["head"] item:=$incoming)
          committed=$(UpdateSharedState lineage_id="$lineage" key="$key" value:=${next.value} expected_value:=${ledger.value})
          if [ "${committed.matched}" = "true" ]; then
            registered="true"
            ledger:={"value":${committed.value}}
          elif [ "$retry" != "3" ]; then
            ledger=$(GetSharedState lineage_id="$lineage" key="$key")
          fi
        fi
      fi
    done
    if [ "$registered" != "true" ]; then
      fail "并发记录 Family 待恢复输入，请按原事件重查"
    fi
  fi
fi
notice:=${ledger.value.notice}
if [ -n "${notice.message_id}" ]; then
  if receipt=$(GetInputState resident_id="$steward" message_id="${notice.message_id}"); then
    if [ "${receipt.input_state.state}" = "queued" ] || [ "${receipt.input_state.state}" = "running" ]; then
      output status="waiting" message_id="${notice.message_id}"
      exit
    fi
  else
    if [ -n "${notice.envelope.message_id}" ] && [ "${notice.steward}" = "$steward" ]; then
      DeliverMessage resident_id="$steward" business_ref="duty/family-recovery" message:=${notice.envelope}
      output status="requested" message_id="${notice.message_id}"
      exit
    fi
    output status="legacy_receipt_pending" message_id="${notice.message_id}"
    exit
  fi
fi
head="${ledger.value.head}"
if [ -n "${notice.incident_keys.0}" ]; then
  head="${notice.incident_keys.0}"
fi
if [ -z "$head" ]; then
  output status="unchanged"
  exit
fi
item=$(json_get value:=${ledger.value} path:=["incidents",$head])
rid="${item.value.resident_id}"
mid="${item.value.message_id}"
if [ "${item.value.request_message_id}" != "" ]; then
  rid="$facade"
  mid="${item.value.request_message_id}"
fi
if state=$(GetInputState resident_id="$rid" message_id="$mid"); then
  status="${state.input_state.state}"
else
  status="missing"
fi
resolved="false"
if [ "${item.value.status}" = "escalated" ]; then
  status="escalated"
  resolved="true"
elif [ "$status" = "completed" ] || [ "$status" = "cancelled" ]; then
  resolved="true"
fi
if [ "$resolved" != "true" ] && [ "$status" = "running" ]; then
  output status="waiting" message_id="$mid"
  exit
fi
if [ "$resolved" != "true" ] && [ "$status" = "queued" ]; then
  intake=$(ListResidentStates resident_ids:=[$rid] include_viewer_state:=false)
  if [ "${intake.states.0.state.intake_state}" = "open" ]; then
    output status="waiting" message_id="$mid"
    exit
  fi
fi
if [ "$resolved" = "true" ] || [ "${item.value.attempts}" -ge 3 ] || [ "$rid" = "$steward" ] || [ "${receipt.input_state.state}" = "failed" ] || [ "${receipt.input_state.state}" = "cancelled" ]; then
  if [ "$resolved" != "true" ]; then
    if [ -z "${arguments.human_familizer_id}" ]; then
      output status="missing_creator" message_id="$mid"
      exit
    fi
    CreateNotification lineage_id="$lineage" recipients:=[${arguments.human_familizer_id}] severity="error" notification_id="family-recovery:$rid:$mid" title="Family 需要恢复协助" message="原请求 $mid；承接者 $rid；状态 $status。原任务与故障记录已保留。"
    status="escalated"
  fi
  updated=$(json_set value:=${item.value} path:=["status"] item:=$status)
  for retry in 1 2; do
    next=$(json_set value:=${ledger.value} path:=["incidents",$head] item:=${updated.value})
    if [ "$head" = "${ledger.value.head}" ]; then
      next=$(json_set value:=${next.value} path:=["head"] item:=${item.value.previous})
    fi
    next=$(json_set value:=${next.value} path:=["notice"] item:=null)
    committed=$(UpdateSharedState lineage_id="$lineage" key="$key" value:=${next.value} expected_value:=${ledger.value})
    if [ "${committed.matched}" = "true" ]; then
      output status="$status" message_id="$mid"
      exit
    fi
    if [ "$retry" = "2" ]; then
      output status="busy"
      exit
    fi
    ledger=$(GetSharedState lineage_id="$lineage" key="$key")
    current=$(json_get value:=${ledger.value} path:=["incidents",$head])
    if [ "${current.value}" != "${item.value}" ] || [ "${ledger.value.notice}" != "$notice" ]; then
      output status="busy"
      exit
    fi
  done
  output status="busy"
  exit
fi
steward_state=$(ListResidentStates resident_ids:=[$steward] include_viewer_state:=false)
if [ "${steward_state.states.0.state.intake_state}" != "open" ]; then
  output status="steward_unavailable"
  exit
fi
attempt="1"
if [ "${item.value.attempts}" = "1" ]; then
  attempt="2"
elif [ "${item.value.attempts}" = "2" ]; then
  attempt="3"
fi
notice_id="msg_d066000000000008${event.id:6}"
brief="请核对 Family 原请求 $mid 的状态，当前承接者 $rid。保留原请求和审计记录。第 $attempt 轮恢复。\n${arguments.family_tasks.recovery.prompt}\n${arguments.family_tasks.recovery.guide}"
previous_notice:=${notice}
notice:={"message_id":$notice_id,"steward":$steward,"incident_keys":[$head],"envelope":{"message_id":$notice_id,"content_parts":[{"type":"text","text":$brief}]}}
updated=$(json_set value:=${item.value} path:=["attempts"] item:=${attempt})
for retry in 1 2; do
  next=$(json_set value:=${ledger.value} path:=["incidents",$head] item:=${updated.value})
  next=$(json_set value:=${next.value} path:=["notice"] item:=${notice})
  reserved=$(UpdateSharedState lineage_id="$lineage" key="$key" value:=${next.value} expected_value:=${ledger.value})
  if [ "${reserved.matched}" = "true" ]; then
    DeliverMessage resident_id="$steward" business_ref="duty/family-recovery" message:=${notice.envelope}
    output status="requested" message_id="$notice_id"
    exit
  fi
  if [ "$retry" = "2" ]; then
    output status="busy"
    exit
  fi
  ledger=$(GetSharedState lineage_id="$lineage" key="$key")
  current=$(json_get value:=${ledger.value} path:=["incidents",$head])
  if [ "${current.value}" != "${item.value}" ] || [ "${ledger.value.notice}" != "$previous_notice" ]; then
    output status="busy"
    exit
  fi
done
output status="busy"'''


def bindings(lineage_id, family_id, family):
    return {key: {"value": value} for key, value in {
        "lineage_id": lineage_id, "recovery_key": f"family.{family_id}.recovery",
        "facade": family["facade"], "steward": family["steward"],
        "members": list(dict.fromkeys([family["facade"], family["steward"], family["router"], *family["members"]])),
        "human_familizer_id": family.get("human_familizer_id") or "",
    }.items()}
