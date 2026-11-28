"""Family graph facts, event sources and scripts."""
from __future__ import annotations

import hashlib
import secrets
from . import family_recovery_effects, family_review_effects, family_roles
from .family_roles import REVIEW_ELEMENT_KEYS

FAMILY_KEY = "family"
REVIEW_DUTY = "review"
REVIEW_BLOCK_KEY = "duty:" + REVIEW_DUTY


def has_independent_review(family):
    return REVIEW_DUTY in family.get("independent_duties", [])


def find_review_duty(body, parent):
    duty_id = next((e["id"] for e in parent["elements"] if e.get("key") == REVIEW_BLOCK_KEY), None)
    return next((block for block in body["function_blocks"] if block["id"] == duty_id
                 and block.get("duty", {}).get("holder_id") == parent["family"]["steward"]), None)


def review_task(body, parent):
    """The task entry the independent review duty's notice wire carries, if one is installed."""
    block = find_review_duty(body, parent)
    if not block:
        return None
    wid = next((e['id'] for e in block['elements'] if e.get('key') == 'notify_periodic_review'), None)
    wire = next((w for w in body['program']['wires'] if w['id'] == wid), {})
    return wire.get('config', {}).get('invoke_profiles', {}).get('effect', {}).get('arguments', {}).get('family_tasks', {}).get('value', {}).get(REVIEW_DUTY)


def family_elements(body, parent):
    review = find_review_duty(body, parent)
    return [*parent.get("elements", []), *family_roles.elements(body, parent), *(review["elements"] if review else [])]


def bind_review_activity(body, parent):
    """Bind the review duty to the Family's shared completion event on installation."""
    block = find_review_duty(body, parent)
    if block is None:
        return
    source = next((e["id"] for e in parent["elements"] if e.get("key") == "member_done"), None)
    target = next((e["id"] for e in block["elements"] if e.get("key") == "review_activity"), None)
    if not source or not target:
        return
    wires = body["program"]["wires"]
    if any(w["source"] == source and w["target"] == target for w in wires):
        return
    # A vanished shared source also deletes its wire. Replace its stale membership.
    block["elements"] = [e for e in block["elements"] if e.get("key") != "record_review_activity"]
    wire_id = "wire_" + secrets.token_hex(6)
    arguments = family_review_effects.bindings(body["lineage_id"], parent["id"], parent["family"])
    wires.append({"id": wire_id, "kind": "route", "source": source, "target": target,
                  "config": {"invoke_profiles": {"effect": {"arguments": arguments}}}})
    block["elements"].append({"id": wire_id, "kind": "wire", "key": "record_review_activity"})


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
# The triggering Event fixes the identity of this routing attempt. The
# business reference continues to name the original request across attempts.
context=$(GetRelayContext resident_id="$facade" message_id="$mid" section="request")
if [ "${context.request.input_state.state}" = "cancelled" ]; then
  output state="withdrawn"
  exit
fi
if [ -z "${context.request.author_id}" ]; then
  fail "门面原请求缺少作者"
fi
# Addressed requests skip model selection, but use the same target policy
# as a Router choice. The relay owner retains the original request's author.
chosen="${event.payload.direct_member_id}"
reason="按发送者的直连目标转交"
if [ -z "$chosen" ]; then
  chosen="${event.payload.quoted_member_id}"
  reason="引用了这个成员的消息，直接交回"
fi
if [ -z "$chosen" ]; then
  chosen="${event.payload.continued_member_id}"
  reason="同一 Resident 接着发来的请求，交给承接它上一条请求的成员"
fi
if [ -n "$chosen" ]; then
  target_state=$(CheckRelayTarget resident_id="$facade" member_id="$chosen")
  review_ref="family-review/$facade/message/$mid"
  if [ "${target_state.status}" = "unavailable" ]; then
    recruit_id="msg_d066000000000003${event.id:6}"
    brief="为门面 $facade 原请求 $mid 安排承接者。所选成员 $chosen，转交理由：$reason。可用性缺口：${target_state.reason}"
    held=$(DeliverMessage resident_id="${arguments.steward}" business_ref="$review_ref" message:={"message_id":${recruit_id},"content_parts":[{"type":"text","text":${brief}}]})
    output target="$chosen" state="waiting_for_steward" confidence:=100 reason="$reason" gap="${target_state.reason}" steward_message_id="${held.message_id}"
    exit
  fi
  if [ "${target_state.status}" = "quota_low" ]; then
    quota_id="msg_d066000000000004${event.id:6}"
    brief="为门面 $facade 原请求 $mid 核对额度。所选成员 $chosen，转交理由：$reason。剩余百分比 ${target_state.remaining_percent}。"
    held=$(DeliverMessage resident_id="${arguments.steward}" business_ref="$review_ref" message:={"message_id":${quota_id},"content_parts":[{"type":"text","text":${brief}}]})
    output target="$chosen" state="waiting_for_quota_review" confidence:=100 reason="$reason" steward_message_id="${held.message_id}"
    exit
  fi
  if [ "${target_state.status}" != "ready" ] && [ "${target_state.status}" != "unknown" ]; then
    fail "成员状态不可转交"
  fi
  relayed=$(RelayMessage resident_id="$facade" message_id="$mid" member_id="$chosen")
  output target="$chosen" state="${relayed.status}" confidence:=100 reason="$reason"
  exit
fi
ref="family-route/$facade/message/$mid"
dispatch_id="msg_d066000000000001${event.id:6}"
facts:={"facade":${facade},"message_id":${mid},"sender":${context.request.author_id},"content":${context.request.content_parts},"quote":${context.request.quote}}
brief="根据原请求选择承接者。按需用 GetRelayContext 读取当前候选。\n${facts}"
asked=$(DeliverMessage resident_id="${arguments.router}" business_ref="$ref" message:={"message_id":${dispatch_id},"content_parts":[{"type":"text","text":${brief}}]})
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
review_ref="family-review/$facade/message/$mid"
review_id="msg_d066000000000002${event.id:6}"
recruit_id="msg_d066000000000003${event.id:6}"
quota_id="msg_d066000000000004${event.id:6}"
raw_facts="${packet.message.data}"
# Preserve the Packet value's JSON type: string "true" must never opt in.
continuation="${packet.message.data.continue_previous}"
continuation_type:=[${continuation}]
if [ "$continuation_type" != '[true]' ]; then
  if [ "$continuation_type" != '[false]' ]; then
    if [ "$continuation_type" != '[""]' ]; then
      brief="复核 Family 路由。门面 $facade 原请求 $mid。continue_previous 必须为布尔值。"
      DeliverMessage resident_id="${arguments.steward}" business_ref="$review_ref" message:={"message_id":${review_id},"content_parts":[{"type":"text","text":${brief}}]}
      fail "Family Router 输出格式或字段无效：continue_previous 必须为布尔值"
    fi
  fi
fi
if [ "${packet.message.data.disposition}" = "notice" ]; then
  if [ "$continuation_type" = '[true]' ]; then
    fail "notice 不能同时声明 continue_previous"
  fi
  # A result report or plain notice: the facade keeps it and no member works on it.
  kept=$(RelayMessage resident_id="$facade" message_id="$mid" notice:=true reason="${packet.message.text}")
  output state="${kept.status}" disposition="notice" reason="${packet.message.text}"
  exit
fi
target="${packet.message.data.target}"
if [ -z "$target" ]; then
  brief="复核 Family 路由。门面 $facade 原请求 $mid。Router 缺少 target。"
  DeliverMessage resident_id="${arguments.steward}" business_ref="$review_ref" message:={"message_id":${review_id},"content_parts":[{"type":"text","text":${brief}}]}
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
  brief="复核 Family 路由。门面 $facade 原请求 $mid。Router 所选成员已不在名册。"
  DeliverMessage resident_id="${arguments.steward}" business_ref="$review_ref" message:={"message_id":${review_id},"content_parts":[{"type":"text","text":${brief}}]}
  fail "Family Router 选择的成员不在当前名册中"
fi
target_state=$(CheckRelayTarget resident_id="$facade" member_id="$chosen")
if [ "${packet.message.data.scope}" = "out" ] || [ "${target_state.status}" = "unavailable" ]; then
  # No member's duties cover it: the steward decides whether a new member takes it.
  gap="${packet.message.data.gap}"
  if [ -z "$gap" ]; then
    gap="${target_state.reason}"
  fi
  if [ -z "$gap" ]; then
    gap="Router 未写明"
  fi
  brief="为门面 $facade 原请求 $mid 安排承接者。所选成员 $chosen，职责或可用性缺口：$gap。Router 输出：$raw_facts"
  held=$(DeliverMessage resident_id="${arguments.steward}" business_ref="$review_ref" message:={"message_id":${recruit_id},"content_parts":[{"type":"text","text":${brief}}]})
  output target="$chosen" state="waiting_for_steward" scope="out" gap="$gap" steward_message_id="${held.message_id}"
  exit
fi
if [ "${target_state.status}" = "quota_low" ]; then
  brief="为门面 $facade 原请求 $mid 核对额度。所选成员 $chosen，剩余百分比 ${target_state.remaining_percent}。Router 输出：$raw_facts"
  held=$(DeliverMessage resident_id="${arguments.steward}" business_ref="$review_ref" message:={"message_id":${quota_id},"content_parts":[{"type":"text","text":${brief}}]})
  output target="$chosen" state="waiting_for_quota_review" steward_message_id="${held.message_id}"
  exit
fi
if [ "${target_state.status}" != "ready" ] && [ "${target_state.status}" != "unknown" ]; then
  fail "CheckRelayTarget 返回无效状态"
  exit
fi
if [ "$continuation_type" = '[true]' ]; then
  relayed=$(RelayMessage resident_id="$facade" message_id="$mid" member_id="$chosen" continue_previous:=true)
else
  relayed=$(RelayMessage resident_id="$facade" message_id="$mid" member_id="$chosen")
fi
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
  brief="复核门面 $facade 的低置信度原请求 $mid。该原请求已转交 $chosen。Router 输出：$raw_facts"
  DeliverMessage resident_id="${arguments.steward}" business_ref="$review_ref" message:={"message_id":${review_id},"content_parts":[{"type":"text","text":${brief}}]}
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
inputs=$(GetFacadeSummaryInputs resident_id="${arguments.facade}" overview_only:=true)
if [ "${inputs.needs_update}" != "true" ]; then
  output status="unchanged"
  exit
fi
if [ -n "${inputs.notification_delivery.resident_id}" ]; then
  if [ "${inputs.notification_delivery.resident_id}" != "${arguments.steward}" ]; then
    fail "摘要任务目标与当前 Family Steward 不一致"
  fi
  if [ "${inputs.notification_delivery.status}" = "accepted" ]; then
    output status="already_accepted" message_id="${inputs.notification.message_id}"
    exit
  fi
  if [ "${inputs.notification_delivery.status}" != "unsubmitted" ]; then
    fail "摘要任务的固定消息 ID 缺少匹配的受理证据"
  fi
fi
sent=$(DeliverMessage resident_id="${arguments.steward}" business_ref="duty/family-work-summary" message:=${inputs.notification})
output status="requested" message_id="${sent.message_id}"'''

FAMILY_SUMMARY_CHECK_SCRIPT = r'''member="${event.subject_resident_id}"
mid="${event.payload.message_id}"
if [ -z "$member" ] || [ -z "$mid" ]; then
  output status="skipped"
  exit
fi
written=$(GetResidentSummaries resident_ids:=[${member}] include_maintenance:=true)
for missing in ${written.summaries.0.missing_message_ids}; do
  if [ "$missing" = "$mid" ]; then
    if [ -z "${written.summaries.0.reminder.message_id}" ]; then
      output status="reminder_pending"
      exit
    fi
    sent=$(DeliverMessage resident_id="$member" business_ref="duty/work-summary-reminder" activation_policy="attach_to_next" message:=${written.summaries.0.reminder})
    output status="reminder_attached" message_id="${sent.message_id}"
    exit
  fi
done
if [ "${written.summaries.0.consolidation.due}" != "true" ]; then
  output status="covered"
  exit
fi
''' + FAMILY_SUMMARY_ROLLUP_SCRIPT


FAMILY_ROLE_AVAILABILITY_SCRIPT = r'''facade=$(ListResidentStates resident_ids:=[${arguments.facade}] include_viewer_state:=false)
if [ "${facade.states.0.state.intake_state}" != "open" ]; then
  output status="paused"
  exit
fi
for role in ${arguments.roles}; do
  state=$(ListResidentStates resident_ids:=[$role] include_viewer_state:=false)
  if [ "${state.states.0.state.intake_state}" = "closed" ]; then
    SetIntakeOpen resident_ids:=[$role] open:=true
  fi
done
output status="checked"'''


FAMILY_QUEUE_NOTICE_SCRIPT = r'''member="${event.subject_resident_id}"
if [ -z "$member" ]; then
  output status="skipped"
  exit
fi
notice_id="msg_d066000000000006${event.id:6}"
brief="Family 门面 ${arguments.facade}，成员 $member 的队列压力为 ${event.payload.pressure_value}。${arguments.family_tasks.queue.prompt}\n${arguments.family_tasks.queue.guide}"
sent=$(DeliverMessage resident_id="${arguments.steward}" business_ref="duty/family-queue" message:={"message_id":${notice_id},"content_parts":[{"type":"text","text":${brief}}]})
output status="requested" message_id="${sent.message_id}"'''


# Fingerprints identify entire shipped scripts in the current definition
# migration. A customized script is diagnosed by the caller and left intact.
_ORIGINAL_SCRIPTS = {
    "4500a7adf65a4e547e83a7b5a5cada603fbf44490c8e21080c0dc452fa8e922b": FAMILY_ROUTER_SCRIPT,
    "496024dcf5e8f6e780de9e631f02cbf142f9fc337b5704a546186ded4c302419": FAMILY_ROUTER_SCRIPT,
    # The first D066 candidate was also materialized into the release seed.
    "d188f5e86aaf94aad24a2c403be83a0d464055484d075f8dd12d70b801cfe43a": FAMILY_ROUTER_SCRIPT,
    "d458ac34ddc0d6e67ae45ac0439373721aed1e5c13b517cd8ce9c1de4a0e1c44": FAMILY_ROUTE_RELAY_SCRIPT,
    "131b61a132dd3ff51e7ed53941159e1a31a2f6b10d08dd5de04d8c91365c8082": FAMILY_ROUTE_RELAY_SCRIPT,
    "22397c562a12582ba834bb4d9f85372f87b42a14a0bc57f349547c273850f18c": FAMILY_SUMMARY_CHECK_SCRIPT,
    "3e3873fbe45fdab870b4df259039d054f92343f721490a5dfe6e3a6ae1086343": FAMILY_SUMMARY_ROLLUP_SCRIPT,
    "3c29901ad1c2bb3f92ef89de8bebf2d272c8d6a86d01b502842ccb39c77ea6dd": family_recovery_effects.RECOVERY,
    "866f9b8adbe67642327871a0cfa23dbb3094586c6034842376b47312d48cee27": FAMILY_ROLE_AVAILABILITY_SCRIPT,
    "383803173038e47d7ff63dbdda9855e927e1fd6655f29e9dea740c750bc518ba": family_review_effects.NOTICE,
    "b08a2b1a4ca4dc21b3f5d23caf8d2318b2214de4803346e7f437af539553744c": family_review_effects.RECORD_ACTIVITY,
    "13305f83577da86164bd160bd113cba9442577c7050f70a5214d2ea56a3728a0": family_review_effects.PERSONAL_REVIEW,
}

_ORIGINAL_BY_ROLE = {
    "05fe5e611efd770441c0f789919f37cab3cc843a4dea95d009b75b65de51330a": {
        "notify_member_queue_task": FAMILY_QUEUE_NOTICE_SCRIPT,
        "notify_periodic_review_task": family_review_effects.NOTICE,
    },
}


def is_original_family_script(source: str) -> bool:
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return digest in _ORIGINAL_SCRIPTS or digest in _ORIGINAL_BY_ROLE


def migrate_original_script(source: str, *, role: str | None = None) -> str:
    """Upgrade a complete shipped Effect definition without running it."""
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    if digest in _ORIGINAL_BY_ROLE:
        return _ORIGINAL_BY_ROLE[digest].get(role, source)
    return _ORIGINAL_SCRIPTS.get(digest, source)
