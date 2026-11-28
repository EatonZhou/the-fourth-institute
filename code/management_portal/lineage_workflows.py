"""Business workflows drawn into a team's Lineage: manual Event → DeliverMessage Effect → Resident.

A business page (WALL, Paper, Test, Studio) keeps each AI function as a
visible flow in its team's Lineage: a ``manual.triggered`` Event Source feeds
an Effect that calls ``DeliverMessage`` with the function's fixed prompt; the
Effect's ``target`` endpoint points at the Resident doing the work.  Each run
is an ordinary ``TriggerEvent`` whose input is appended to the fixed prompt
(decision_4adcc1ff2f2c “Effect：用脚本组合指令”).
"""
from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import isa_client
from .hatg.contracts import InstructionError
from .society_service import SocietyServiceUnavailable
from .society_service.lineage_effect_script import deliver_message_script, deliver_script_parts
from .resources.errors import ResourceServiceUnavailable
from .workflow_bindings_store import get_workflow_binding_store


HANDLE_FIELDS = ("resident_id", "lineage_id", "event_source_id", "effect_id", "request_id",
                 "idempotency_key", "instruction_id", "event_id", "trigger_path_id", "instruction")
OBSERVATION_PAGES = 20


def submission_execution(handle, *, actor, instruction, entry="web"):
    """Read the original receipt by ID or the durably prepared request key."""
    if handle.get("instruction_id"):
        selector = {"instruction_id": handle["instruction_id"]}
    elif handle.get("idempotency_key"):
        selector = {"instruction": instruction, "idempotency_key": handle["idempotency_key"]}
    else:
        return {}
    try:
        record = isa_client.call(actor, "GetInstructionExecution", selector, entry=entry)
    except InstructionError as error:
        if error.code != "instruction.not_found":
            raise
        return {}
    actual = record.get("instruction_id")
    if (record.get("instruction") != instruction or not actual or
            (handle.get("instruction_id") and handle["instruction_id"] != actual)):
        raise InstructionError("instruction.conflict", f"指令执行与原 {instruction} 不匹配")
    return record


def submission_was_rejected(record):
    error = record.get("error") or {}
    details = error.get("details") or {}
    return (record.get("status") == "failed" and error.get("commit_state") == "not_committed"
            and error.get("code") != "instruction.interrupted" and details.get("reason") != "interrupted"
            and not details.get("domain_committed") and not record.get("references"))


def submission_handle(value, *, state="pending", reason="", message_id=""):
    """Keep prepared request identity separate from a confirmed input receipt."""
    result = {key: str(value.get(key) or "") for key in HANDLE_FIELDS if value.get(key)}
    result["submission_state"] = state
    if reason:
        result["submission_reason"] = reason
    if state == "submitted" and message_id:
        result["message_id"] = message_id
    return result


def submission_error(handle, error):
    """Retain the owner execution locator, never mistake an entry receipt for it."""
    result = dict(handle)
    if isinstance(error, InstructionError):
        details = error.details or {}
        instruction_id = details.get("instruction_id") or (details.get("instruction_result") or {}).get("instruction_id")
        if str(instruction_id or "").startswith("inst_lin_"):
            result["instruction_id"] = instruction_id
        for key in ("event_id", "trigger_path_id"):
            if details.get(key):
                result[key] = details[key]
        result["submission_error"] = error.to_dict()
    result["submission_reason"] = str(error)
    return result


class WorkflowPending(RuntimeError):
    """Acceptance is unconfirmed; retain the original submission locator."""

    def __init__(self, binding, receipt):
        self.handle = {**binding, **receipt}
        self.handle.pop("message_id", None)
        self.handle["submission_state"] = "pending"
        super().__init__(f"工作流提交待确认：{self.handle.get('event_id', '')}；"
                         f"{self.handle.get('submission_reason', '')}。请恢复此请求，勿重复提交。")


class WorkflowFailed(RuntimeError):
    """The original submission is positively known not to have been accepted."""

    def __init__(self, binding, receipt):
        self.handle = {**binding, **receipt}
        self.handle.pop("message_id", None)
        self.handle["submission_state"] = "not_submitted"
        super().__init__(f"工作流未受理：{self.handle.get('submission_reason', '')}")


def lineage_graph(actor: str, lineage_id: str) -> dict:
    return dict(isa_client.call(actor, "GetLineage", {"lineage_id": lineage_id}).get("lineage") or {})


def _deliver_effect_config(prompt: str, *, execution_constraints=None) -> dict:
    constants = {} if execution_constraints is None else {'execution_constraints': execution_constraints, 'context_policy': 'fresh'}
    return {"script": deliver_message_script(prompt, **constants), "endpoints": ["target"]}


class WorkflowBindings:
    @property
    def store(self):
        return get_workflow_binding_store()

    def lineage_lock(self, lineage_id):
        return self.store.lineage_lock(lineage_id)

    def delete_resident(self, *, actor: str, lineage_id: str, resident_id: str, preserve_paths=()):
        """Remove an identity and its owned workflows in one ordinary graph edit.

        The binding records are ownership evidence; a title or an arbitrary
        incoming wire is not. Refuse deletion if someone shared those objects.
        The ordinary Resident lifecycle finalizes sessions, folders and receipts.
        """
        # Re-read authoritative state on each explicit recovery. A definitive
        # failure can be retried after its cause is fixed, even if the graph
        # revision did not advance. Transport retries retain these attempt keys.
        attempt = secrets.token_hex(8)

        def finalize(target_lineage):
            try:
                isa_client.call(actor, "ApplyLineageChanges", {
                    "lineage_id": target_lineage, "operations": [{"op": "delete", "kind": "resident", "id": resident_id}],
                }, key=f"release:{resident_id}:finalize:{attempt}")
            except InstructionError as error:
                if error.code not in {"instruction.forbidden", "instruction.not_found"}:
                    raise
                from .society_service import get_society_service

                # Terminal deletion removes the target's permission facts.
                # Only the canonical owner can confirm this exact cleanup.
                if not get_society_service().resident.resident_deletion_complete(resident_id, target_lineage):
                    raise

        try:
            record = isa_client.call(actor, 'GetResident', {'resident_id': resident_id})['resident']
        except InstructionError as exc:
            if exc.code != 'instruction.not_found':
                raise
            # A previous attempt may have committed the deletion but not yet
            # completed folder/session finalization.
            # Each explicit recovery is a new finalization attempt. A prior
            # definitive failure must remain in history, rather than being
            # replayed forever by the old key. The owner reconciles the same
            # immutable deletion; projection retries inside this call retain
            # this attempt's key.
            return finalize(lineage_id)
        lineage_id = record['lineage_id']
        keep = {Path(path).resolve(strict=False) for path in preserve_paths}
        bindings = record.get('workspaces', [])
        # A business source must not be inside the system folder being deleted,
        # or contain that Resident's owned directory. Such a request needs a move.
        for binding in bindings:
            if not binding.get('is_owned'):
                continue
            owned = Path(binding['path']).resolve(strict=False)
            if any(path == owned.parent or owned.parent in path.parents or path in owned.parents
                   for path in keep):
                raise ValueError('Project source overlaps the Resident folder; move it before releasing this Resident')
        for binding in bindings:
            path = Path(binding['path']).resolve(strict=False)
            if not any(path == root or root in path.parents or path in root.parents for root in keep):
                continue
            wid = binding['workspace_id']
            isa_client.call(actor, 'UnbindWorkspace', {'resident_id': resident_id, 'workspace_id': wid},
                            key=f'release:{resident_id}:unbind:{wid}:{attempt}')
        # Unbinding can advance the Lineage revision. Read it only afterwards;
        # on lost replies a fresh GetResident resumes the remaining bindings.
        with self.lineage_lock(lineage_id):
            graph = lineage_graph(actor, lineage_id)
            flows = self.store.for_resident(lineage_id, resident_id)
            components = {item['id'] for item in graph.get('program', {}).get('components', [])}
            owned = {flow['steps'][step] for flow in flows for step in ('event', 'effect')
                     if flow['steps'].get(step) in components}
            wires = graph.get('program', {}).get('wires', [])
            for wire in wires:
                endpoints = {wire.get('source'), wire.get('target')}
                if endpoints & owned and endpoints - owned - {resident_id}:
                    raise ValueError('Resident workflow is shared; detach its other consumers before deletion')
            operations = [{'op': 'delete', 'kind': 'component', 'id': cid} for cid in sorted(owned)]
            if resident_id in {node['id'] for node in graph.get('nodes', [])}:
                operations.append({'op': 'delete', 'kind': 'resident', 'id': resident_id})
            if operations:
                digest = hashlib.sha256(json.dumps(operations, sort_keys=True).encode()).hexdigest()[:24]
                isa_client.call(actor, 'ApplyLineageChanges', {
                    'lineage_id': lineage_id, 'operations': operations,
                }, version=graph['revision'], key=f'release:{resident_id}:{graph["revision"]}:{digest}:{attempt}')
            # This also resumes lifecycle finalization after a committed graph
            # edit whose response was lost. Do not drop associations on failure.
            finalize(lineage_id)

    def install(self, *, actor, lineage_id, resident_id, name, title, prompt,
                context_policy=None, execution_constraints=None, effect_config: Mapping[str, Any] | None = None,
                is_owned_effect: Callable[[str], bool] | None = None, include_envelope: bool | None = None):
        """Install the default delivery Effect or a domain's strictly owned template."""
        if (effect_config is None) != (is_owned_effect is None):
            raise ValueError("领域 Effect 需要同时提供配置和原配脚本校验")
        if effect_config is not None:
            if execution_constraints is not None:
                raise ValueError("领域 Effect 直接声明其凭证和执行限制")
            desired_effect = deepcopy(dict(effect_config))
            if not is_owned_effect(str(desired_effect.get("script") or "")):
                raise ValueError("目标 Effect 配置不属于此业务模板")
        else:
            desired_effect = _deliver_effect_config(prompt, execution_constraints=execution_constraints)
        if execution_constraints is not None and (context_policy != 'fresh' or execution_constraints != {'version': 1, 'text_only': True, 'context': 'fresh'}):
            raise ValueError('Constrained workflow requires v1 text_only and fresh')
        if context_policy not in (None, "session", "fresh"):
            raise ValueError("工作流 context_policy 必须为 session 或 fresh")
        identity = hashlib.sha256(json.dumps([lineage_id, resident_id, name]).encode()).hexdigest()
        state = self.store.get(lineage_id, resident_id, name)
        if (state and set(state["steps"]) == {"event", "effect", "target", "invoke"}
                and state.get("prompt_sha256") == hashlib.sha256(prompt.encode()).hexdigest()):
            # Reusing an unchanged workflow is a read. It must not queue behind
            # installations for every other Resident in the same team.
            current = lineage_graph(actor, lineage_id)
            binding = self._validated_binding(state, current)
            effect = next(item for item in current["program"]["components"] if item["id"] == binding["effect_id"])
            current_effect = effect.get("config") or {}
            source = str(current_effect.get("script") or "")
            invoke = next(wire for wire in current["program"]["wires"] if wire["id"] == state["steps"]["invoke"])
            wire_config = invoke.get("config") or {}
            policy_current = (context_policy is None or wire_config.get("context_policy") == context_policy) and (
                include_envelope is None or wire_config.get("include_envelope", True) is include_envelope
            )
            if is_owned_effect is not None:
                if not is_owned_effect(source):
                    raise ValueError("工作流 Effect 已自定义，拒绝由业务模板覆盖")
                effect_current = all(current_effect.get(key) == value for key, value in desired_effect.items())
            else:
                parts = deliver_script_parts(source)
                effect_current = execution_constraints is None or (
                    parts and parts[2].get("execution_constraints") == execution_constraints
                )
            if policy_current and effect_current:
                return binding
        with self.lineage_lock(lineage_id):
            graph = lineage_graph(actor, lineage_id)
            if resident_id not in {node.get("id") for node in graph.get("nodes", [])}:
                raise ValueError(f"Resident 未绑定：{resident_id}")
            state = (self.store.get(lineage_id, resident_id, name)
                     or self.store.follow_moved_resident(lineage_id, resident_id, name, graph)) or {
                "lineage_id": lineage_id, "resident_id": resident_id, "name": name, "steps": {},
            }

            def step(label, kind, value):
                if label in state["steps"]:
                    return state["steps"][label]
                arguments = {"lineage_id": lineage_id, "operations": [
                    {"op": "create", "kind": kind, "ref": label, "value": value},
                ]}
                if kind == "component":
                    arguments["near_resident_id"] = resident_id
                result = isa_client.call(
                    actor, "ApplyLineageChanges", arguments, key=f"workflow:{identity}:{label}",
                )
                object_id = (result.get("refs") or {}).get(label)
                if not isinstance(object_id, str) or not object_id:
                    raise RuntimeError("工作流创建指令未返回对象 ID")
                state["steps"][label] = object_id
                self.store.save(state)
                return object_id

            event = step("event", "component", {
                "kind": "event_source", "title": title, "config": {"event_type": "manual.triggered"},
            })
            effect = step("effect", "component", {
                "kind": "effect", "title": title + " · 执行",
                "config": desired_effect,
            })
            step("target", "wire", {
                "kind": "target", "source": effect, "source_port": "target", "target": resident_id,
            })
            step("invoke", "wire", {
                "kind": "route", "source": event, "target": effect,
                "config": {"include_envelope": bool(include_envelope), "unavailable_policy": "queue", "queue_limit": 100,
                           **({"context_policy": context_policy} if context_policy is not None else {})},
            })
            current = lineage_graph(actor, lineage_id)
            binding = self._validated_binding(state, current)
            components = {item["id"]: item for item in current.get("program", {}).get("components", [])}
            if is_owned_effect is not None:
                prompt_sha256 = hashlib.sha256(prompt.encode()).hexdigest()
                self._keep_owned_effect_current(
                    actor, lineage_id, identity, current["revision"], components[effect], desired_effect, is_owned_effect,
                )
                if state.get("prompt_sha256") != prompt_sha256:
                    state["prompt_sha256"] = prompt_sha256
                    self.store.save(state)
            else:
                if execution_constraints is not None:
                    fixed = deliver_script_parts(str((components[effect].get("config") or {}).get("script") or ""))
                    if not fixed or fixed[2].get("execution_constraints") != execution_constraints:
                        raise ValueError("Existing Effect must explicitly declare execution_constraints before use")
                self._keep_prompt_current(actor, lineage_id, identity, state, components[effect], prompt)
            if include_envelope is not None or context_policy is not None:
                # Preserve the other wire controls.
                current = lineage_graph(actor, lineage_id)
                invoke = next(wire for wire in current["program"]["wires"] if wire["id"] == state["steps"]["invoke"])
                config = dict(invoke.get("config") or {})
                original = dict(config)
                if include_envelope is not None:
                    config["include_envelope"] = include_envelope
                if context_policy is not None:
                    config["context_policy"] = context_policy
                if config != original:
                    digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
                    isa_client.call(actor, "ApplyLineageChanges", {
                        "lineage_id": lineage_id, "operations": [
                            {"op": "update", "kind": "wire", "id": invoke["id"], "changes": {"config": config}},
                        ],
                    }, version=current["revision"], key=f"workflow:{identity}:config:{current['revision']}:{digest[:32]}")
        return binding

    @staticmethod
    def _keep_owned_effect_current(actor, lineage_id, identity, revision, effect, desired, is_owned_effect):
        current = effect.get("config") or {}
        if not is_owned_effect(str(current.get("script") or "")):
            raise ValueError("工作流 Effect 已自定义，拒绝由业务模板覆盖")
        config = {**current, **desired}
        if config == current:
            return
        digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        isa_client.call(actor, "ApplyLineageChanges", {
            "lineage_id": lineage_id, "operations": [
                {"op": "update", "kind": "component", "id": effect["id"], "changes": {"config": config}},
            ],
        }, version=revision, key=f"workflow:{identity}:owned-effect:{revision}:{digest[:32]}")

    @staticmethod
    def _validated_binding(state, graph):
        resident_id = state["resident_id"]
        if resident_id not in {node.get("id") for node in graph.get("nodes", [])}:
            raise ValueError(f"Resident 未绑定：{resident_id}")
        event, effect = state["steps"]["event"], state["steps"]["effect"]
        components = {item["id"] for item in graph.get("program", {}).get("components", [])}
        if not {event, effect}.issubset(components):
            raise ValueError("工作流 Event / Effect 已被删除；请显式修复绑定，不会自动重建")
        wires = graph.get("program", {}).get("wires", [])
        for label, kind, source, target in (("target", "target", effect, resident_id), ("invoke", "route", event, effect)):
            if not any(wire.get("id") == state["steps"][label] and wire.get("kind") == kind and wire.get("source") == source and wire.get("target") == target for wire in wires):
                raise ValueError("工作流连接已解除；请显式修复绑定")
        return {"lineage_id": state["lineage_id"], "resident_id": resident_id,
                "event_source_id": event, "effect_id": effect}

    def _keep_prompt_current(self, actor, lineage_id, identity, state, effect, prompt):
        """The Effect keeps the business's current fixed prompt while it still holds the text this workflow wrote.

        A message someone changed in the Lineage after installation stays as it is.
        """
        parts = deliver_script_parts(str((effect.get("config") or {}).get("script") or ""))
        if parts is None:
            return
        endpoint, message, constants = parts
        if constants.get("lend_temporary_credentials") or "temporary_credentials" in constants:
            return
        wanted = hashlib.sha256(prompt.encode()).hexdigest()
        if message != prompt:
            if not state.get("prompt_sha256") or hashlib.sha256(message.encode()).hexdigest() != state["prompt_sha256"]:
                return
            script = deliver_message_script(prompt, endpoint=endpoint, **constants)
            digest = hashlib.sha256(script.encode()).hexdigest()
            isa_client.call(actor, "ApplyLineageChanges", {
                "lineage_id": lineage_id, "operations": [
                    {"op": "update", "kind": "component", "id": effect["id"], "changes": {"config.script": script}},
                ],
            }, key=f"workflow:{identity}:script:{digest[:32]}")
        if state.get("prompt_sha256") != wanted:
            state["prompt_sha256"] = wanted
            self.store.save(state)

    @staticmethod
    def prepare(binding, *, request_id):
        if not request_id:
            raise ValueError("工作流请求必须有稳定的 request_id")
        for key in ("lineage_id", "resident_id", "event_source_id", "effect_id"):
            if not binding.get(key):
                raise ValueError(f"工作流绑定缺少 {key}")
        digest = hashlib.sha256(str(request_id).encode()).hexdigest()
        return submission_handle({**binding, "request_id": request_id, "instruction": "TriggerEvent",
                                  "idempotency_key": f"workflow:{binding['event_source_id']}:{digest}"})

    @staticmethod
    def _resolved(handle):
        if handle["submission_state"] == "pending":
            raise WorkflowPending(handle, {})
        if handle["submission_state"] == "not_submitted":
            raise WorkflowFailed(handle, {})
        return {**handle, "status": "running"}

    def trigger(self, binding, *, actor, prompt, request_id, content_parts=None,
                payload: Mapping[str, Any] | None = None, message_metadata=None, entry="web"):
        handle = self.prepare(binding, request_id=request_id)
        arguments = {"lineage_id": handle["lineage_id"], "event_source_id": handle["event_source_id"],
                     "prompt": prompt}
        if content_parts:
            arguments["content_parts"] = list(content_parts)
        if payload is not None and not isinstance(payload, Mapping):
            raise ValueError("工作流领域 payload 必须是对象")
        data = deepcopy(dict(payload or {}))
        reserved = set(data) & {"actor", "caller", "initiated_by", "principal", "issuer",
                               "temporary_credentials", "message_metadata"}
        if reserved:
            raise ValueError("工作流领域 payload 包含保留字段：" + "、".join(sorted(reserved)))
        if message_metadata:
            from .business_context import message_metadata as validate_metadata
            data["message_metadata"] = validate_metadata(message_metadata)
        if data:
            arguments["payload"] = data
        try:
            # A lost receipt is recovered by reads, never another TriggerEvent.
            receipt = isa_client.call(actor, "TriggerEvent", arguments,
                                      key=handle["idempotency_key"], entry=entry)
        except PermissionError:
            raise
        except (InstructionError, SocietyServiceUnavailable, ResourceServiceUnavailable, OSError) as error:
            handle = submission_error(handle, error)
            return self._resolved(self.resolve(handle, actor=actor, entry=entry))
        handle.update({key: str(receipt.get(key) or "") for key in ("event_id", "trigger_path_id")})
        delivery = receipt.get("delivery") or {}
        results = [item for item in delivery.get("results") or []
                   if isinstance(item, dict) and item.get("target_id") == binding["effect_id"]]
        if len(results) == 1 and results[0].get("status") == "executed":
            outputs = results[0].get("typed_outputs") or {}
            message_id = str(outputs.get("message_id") or "")
            if outputs.get("resident_id") == binding["resident_id"] and message_id and handle.get("event_id"):
                return self._resolved(submission_handle(handle, state="submitted", message_id=message_id))
        return self._resolved(self.resolve(handle, actor=actor, entry=entry))

    def observe(self, handle, *, actor, entry="web"):
        """Continue an original submission using only authenticated ISA reads."""
        return self._resolved(self.resolve(handle, actor=actor, entry=entry))

    def resolve(self, handle, *, actor, entry="web"):
        current = submission_handle(handle)
        if not all(current.get(key) for key in ("lineage_id", "resident_id", "event_source_id", "effect_id")):
            return submission_handle(current, reason="恢复句柄缺少完整工作流绑定")
        record = {}
        try:
            if current.get("instruction_id") or not (current.get("event_id") or current.get("trigger_path_id")):
                record = submission_execution(current, actor=actor, instruction="TriggerEvent", entry=entry)
            if record:
                current["instruction_id"] = record["instruction_id"]
                references = record.get("references") or {}
                for key in ("event_id", "trigger_path_id"):
                    if references.get(key):
                        if current.get(key) and current[key] != references[key]:
                            return submission_handle(current, reason="原请求与指令执行引用冲突")
                        current[key] = references[key]
            if not current.get("trigger_path_id"):
                if not (current.get("instruction_id") or current.get("event_id")):
                    return submission_handle(current, reason="原幂等提交尚无可查询执行记录或事件；未找到不代表未受理")
                matches = {}
                cursor = ""
                seen_cursors = set()
                for _ in range(OBSERVATION_PAGES):
                    page = isa_client.call(actor, "ListTriggerPaths", {
                        "lineage_id": current["lineage_id"], "limit": 200, **({"cursor": cursor} if cursor else {}),
                    }, entry=entry)
                    for path in page.get("paths") or []:
                        if self._matches_path(current, path):
                            matches[path["trigger_path_id"]] = path
                    cursor = str(page.get("next_cursor") or "")
                    if not cursor:
                        break
                    if cursor in seen_cursors:
                        return submission_handle(current, reason="触发路径分页未向前推进")
                    seen_cursors.add(cursor)
                else:
                    return submission_handle(current, reason="触发路径查询达到本次观察预算，尚未确认唯一原路径")
                if len(matches) > 1:
                    return submission_handle(current, reason="原请求对应多个触发路径，不能选择某个输入")
                if not matches:
                    error = record.get("error") or {}
                    if submission_was_rejected(record):
                        return submission_handle(current, state="not_submitted", reason=error.get("message") or "原指令明确未提交")
                    return submission_handle(current, reason="原请求尚无可确认的触发路径；未找到不代表未受理")
                current["trigger_path_id"] = next(iter(matches))
            return self._read_submission_path(current, actor=actor, entry=entry)
        except (InstructionError, SocietyServiceUnavailable, ResourceServiceUnavailable, OSError) as error:
            return submission_handle(current, reason=f"原提交观察暂不可确认：{error}")

    @staticmethod
    def _matches_path(handle, path):
        if (path.get("lineage_id") != handle["lineage_id"] or path.get("root_kind") != "event"
                or path.get("root_node_id") != handle["event_source_id"]):
            return False
        if handle.get("event_id") and path.get("occurrence_id") != handle["event_id"]:
            return False
        if handle.get("instruction_id") and (path.get("cause_kind") != "instruction" or
                                              path.get("cause_id") != handle["instruction_id"]):
            return False
        return True

    def _read_submission_path(self, handle, *, actor, entry):
        steps, edges = {}, set()
        anchored = bool(handle.get("event_id") or handle.get("instruction_id"))
        complete_rejection_evidence = anchored
        revision = None
        offset = 0
        for _ in range(OBSERVATION_PAGES):
            page = isa_client.call(actor, "GetTriggerPath", {
                "lineage_id": handle["lineage_id"], "trigger_path_id": handle["trigger_path_id"],
                "offset": offset, "limit": 200,
            }, entry=entry)
            path = page.get("path") or {}
            if not self._matches_path(handle, path) or path.get("trigger_path_id") != handle["trigger_path_id"]:
                return submission_handle(handle, reason="Trigger Path 与原绑定、事件或指令不匹配")
            if offset == 0:
                revision = path.get("revision")
            # Establish one stable terminal snapshot. Any linked Execution
            # rules out rejection; its further pages cannot change that fact.
            complete_rejection_evidence = (
                complete_rejection_evidence and revision is not None and path.get("revision") == revision
                and path.get("status") == "failed" and bool(path.get("finished_at"))
                and all(isinstance((page.get(key) or {}).get("items"), list) for key in ("steps", "edges"))
                and all((page.get(key) or {}).get("items") == [] and (page.get(key) or {}).get("next_offset") is None
                        for key in ("execution_links", "executions"))
            )
            handle = {**handle, "event_id": path["occurrence_id"]}
            for step in (page.get("steps") or {}).get("items") or []:
                steps[step["step_id"]] = step
            for edge in (page.get("edges") or {}).get("items") or []:
                edges.add((edge["source_step_id"], edge["target_step_id"]))
            offsets = [(page.get(key) or {}).get("next_offset") for key in ("steps", "edges")]
            if all(value is None for value in offsets):
                break
            next_offset = min(value for value in offsets if value is not None)
            if next_offset <= offset:
                return submission_handle(handle, reason="触发步骤分页未向前推进")
            offset = next_offset
        else:
            return submission_handle(handle, reason="原路径观察预算已用完，尚未确认唯一输入")
        effects = {key: step for key, step in steps.items() if step.get("kind") == "effect"}
        reachable = {key for key, step in effects.items() if step.get("node_id") == handle["effect_id"]}
        while True:
            expanded = reachable | {target for source, target in edges if source in reachable}
            if expanded == reachable:
                break
            reachable = expanded
        candidates = {str(step.get("input_message_id") or "") for key, step in steps.items()
                      if key in reachable and step.get("kind") == "resident" and
                      step.get("node_id") == handle["resident_id"] and step.get("input_message_id")}
        if len(candidates) == 1:
            message_id = candidates.pop()
            state = isa_client.call(actor, "GetInputState", {"resident_id": handle["resident_id"],
                                   "message_id": message_id}, entry=entry).get("input_state") or {}
            if state.get("message_id") != message_id or not state.get("state"):
                return submission_handle(handle, reason="原路径输入回执尚不可确认")
            return submission_handle(handle, state="submitted", message_id=message_id)
        if (not candidates and complete_rejection_evidence and len(effects) == 1
                and path.get("cause_kind") == "instruction" and path.get("cause_id") and handle.get("event_id")
                and all(step.get("finished_at") and not step.get("input_message_id") for step in steps.values())):
            effect = next(iter(effects.values()))
            result = effect.get("result") or {}
            calls = result.get("calls") or {}
            failure = result.get("failure") or {}
            outputs = result.get("outputs") or {}
            if (effect.get("node_id") == handle["effect_id"] and effect.get("status") == "failed"
                    and effect.get("activation_key") and not result.get("disposition")
                    and failure.get("completed_calls") == 0 and failure.get("retryable") is False
                    and not any(outputs.get(key) for key in ("message_id", "execution_id", "accepted"))
                    and isinstance(calls, dict) and len(calls) == 1):
                call = next(iter(calls.values()))
                error = call.get("error") or {}
                if (call.get("instruction") == "DeliverMessage" and call.get("business_ids") == {}
                        and call.get("instruction_id") and call["instruction_id"] != path["cause_id"]
                        and error.get("code") and error.get("retryable") is False and submission_was_rejected(call)):
                    return {
                        **submission_handle({**handle, "instruction_id": path["cause_id"]}, state="not_submitted",
                                            reason=error.get("message") or failure.get("diagnostic") or error["code"]),
                        "submission_failure_kind": "delivery_rejected",
                        "submission_failure_instruction_id": call["instruction_id"],
                        "submission_failure_code": error["code"],
                        "submission_failure_commit_state": error["commit_state"],
                    }
        return submission_handle(handle, reason="原绑定 Effect 尚无唯一的已受理 Resident 输入；路径失败也不能证明未投递")
