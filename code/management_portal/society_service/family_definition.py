"""Family event sources and scripts; task guides live in family_tasks."""
from __future__ import annotations

FAMILY_KEY = "family"


# One Event Source per kind of member fact watches every member; the
# "事件主体" line of the Event names the member it is about.  A member's
# context filling up needs nobody: its Provider compacts it.
FAMILY_MEMBER_SIGNALS = (
    ("member_queue", "成员队列积压",
     # Two waiting requests already call for a look; the source fires above the threshold.
     {"event_type": "resident.queue_pressure_exceeded", "pressure_metric": "queue_length",
      "queue_length_threshold": 1}),
)


# The Family Router (decision_44cf587c1721) is a Resident with two Effects
# around it: the Router Effect gathers what the choice needs and asks the
# Router Resident; the Resident answers with a Packet; the relay Effect
# hands the facade message to the member it names.  The model work is the
# Router Resident's own turn, so its Provider, account and model are its own
# settings: switching it to Codex or another model is a
# RestartSession or settings change on that Resident, not a script edit.
# The Router keeps its session and reads current candidate facts as needed.
# Two kinds of request need no choice and skip the Router Resident: a quote
# of a member's message goes back to that member, and a Resident's next
# request goes to the member that took its latest one (family_facade).

FAMILY_ROUTER_SCRIPT = r'''facade="${event.payload.resident_id}"
mid="${event.payload.message_id}"
if [ "${event.payload.relay_depth}" -ge 4 ]; then
  fail "经过的门面数超过上限"
fi
if [ -n "${event.payload.direct_member_id}" ]; then
  relayed=$(RelayFamilyMessage resident_id="$facade" message_id="$mid" member_id="${event.payload.direct_member_id}")
  output target="${event.payload.direct_member_id}" state="${relayed.status}" confidence:=100 reason="按发送者的直连目标转交"
  exit
fi
if [ -n "${event.payload.quoted_member_id}" ]; then
  relayed=$(RelayFamilyMessage resident_id="$facade" message_id="$mid" member_id="${event.payload.quoted_member_id}")
  output target="${event.payload.quoted_member_id}" state="${relayed.status}" confidence:=100 reason="引用了这个成员的消息，直接交回"
  exit
fi
if [ -n "${event.payload.continued_member_id}" ]; then
  relayed=$(RelayFamilyMessage resident_id="$facade" message_id="$mid" member_id="${event.payload.continued_member_id}")
  output target="${event.payload.continued_member_id}" state="${relayed.status}" confidence:=100 reason="同一 Resident 接着发来的请求，交给承接它上一条请求的成员"
  exit
fi
# Even an empty or single-member roster can receive organization work for its steward.
asked=$(DispatchFamilyTask resident_id="$facade" trigger="route" source_message_id="$mid")
output router="${arguments.router}" request_message_id="${asked.message_id}"'''

# A Router completion starts its own path. Resolve the original request from
# the immutable business reference on the Router's accepted input message.
FAMILY_ROUTE_RELAY_SCRIPT = r'''ref="${event.payload.input_business_ref}"
if [ "${ref:0:13}" != "family-route/" ]; then
  fail "Router 输入缺少 Family 请求引用"
fi
if [ "${ref:34:9}" != "/message/" ]; then
  fail "Router 输入的 Family 请求引用无效"
fi
facade="${ref:13:21}"
mid="${ref:43}"
target="${packet.message.data.target}"
if [ -z "$target" ]; then
  DispatchFamilyTask resident_id="$facade" trigger="route_review" source_message_id="$mid" facts:={"reason":"Router 缺少 target"}
  fail "Family Router 输出格式或字段无效：缺少 message.data.target"
fi
chosen=""
if [ "$target" = "${arguments.steward}" ]; then
  chosen="$target"
fi
for member in ${arguments.members}; do
  if [ "$member" = "$target" ]; then
    chosen="$member"
  fi
done
if [ -z "$chosen" ]; then
  DispatchFamilyTask resident_id="$facade" trigger="route_review" source_message_id="$mid" facts:={"reason":"成员已不在名册"}
  fail "Family Router 选择的成员不在当前名册中"
fi
if [ "${packet.message.data.scope}" = "out" ]; then
  # No member's duties cover it: the steward decides whether a new member takes it.
  gap="${packet.message.data.gap}"
  if [ -z "$gap" ]; then
    gap="Router 未写明"
  fi
  held=$(RelayFamilyMessage resident_id="$facade" message_id="$mid" member_id="$chosen" scope_gap="$gap" reason="${packet.message.text}")
  output target="$chosen" state="${held.status}" scope="out" gap="$gap" reason="${packet.message.text}"
  exit
fi
relayed=$(RelayFamilyMessage resident_id="$facade" message_id="$mid" member_id="$chosen")
if [ "${relayed.status}" = "withdrawn" ]; then
  # Stopped while the Router was choosing: nobody gets it, and there is nothing to review.
  output target="$chosen" state="withdrawn"
  exit
fi
confidence="${packet.message.data.confidence}"
if [ -z "$confidence" ]; then
  confidence="0"
fi
if [ "$confidence" -lt "${arguments.threshold}" ]; then
  DispatchFamilyTask resident_id="$facade" trigger="route_review" source_message_id="$mid" facts:=${packet.message.data}
fi
output target="$chosen" confidence="$confidence" reason="${packet.message.text}"'''


FAMILY_SUMMARY_ROLLUP_SCRIPT = r'''state=$(ListResidentStates resident_ids:=[${arguments.steward}] include_viewer_state:=false)
if [ "${state.states.0.state.work_state}" != "idle" ]; then
  output status="busy"
  exit
fi
if [ "${state.states.0.state.queued_count}" -gt 0 ]; then
  output status="busy"
  exit
fi
inputs=$(GetFamilySummaryInputs resident_id="${arguments.facade}" overview_only:=true)
if [ "${inputs.needs_update}" != "true" ]; then
  output status="unchanged"
  exit
fi
sent=$(DispatchFamilyTask resident_id="${arguments.facade}" trigger="summary" message_id="${inputs.notification.message_id}")
output status="requested" message_id="${sent.message_id}"'''

FAMILY_SUMMARY_CHECK_SCRIPT = r'''checked=$(CheckResidentWorkSummary resident_id="${event.subject_resident_id}" message_id="${event.payload.message_id}")
if [ "${checked.consolidation_due}" != "true" ]; then
  output status="${checked.status}"
  exit
fi
''' + FAMILY_SUMMARY_ROLLUP_SCRIPT
