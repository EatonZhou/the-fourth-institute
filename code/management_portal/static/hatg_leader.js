(() => {
  "use strict";

  if (window.PortalHatgLeader) return;

  // The Lineage Leader request: which Resident leads, what changed in the graph
  // since the Leader last reviewed it, and sending the Leader its review.
  const DEPENDENCIES = Object.freeze([
    "$id", "closeModal", "commitLineage", "effectiveNodeStatus", "nodeById", "nodeResidentId",
    "sendPromptToNode", "setBusy", "societyService", "state", "toast",
  ]);

  const DEFAULT_LEADER_CHARTER = "持续审视 Lineage 的职责边界、交接质量、验收回路和未决风险。只提出可追踪的结构建议，不自行修改 HATG、创建或停止节点、发送交接或绕过人工确认。";

  function leaderReviewDelta(previousReview, snapshot) {
    const before = previousReview?.snapshot || {};
    const added = (next, prior) => next.filter(id => !new Set(prior || []).has(id));
    const removed = (prior, next) => (prior || []).filter(id => !new Set(next).has(id));
    if (!previousReview) return { baseline: true, since_revision: null, added_nodes: [], removed_nodes: [], added_edges: [], removed_edges: [] };
    return {
      baseline: false,
      since_revision: Number(before.lineage_revision || 0),
      added_nodes: added(snapshot.resident_ids, before.resident_ids),
      removed_nodes: removed(before.resident_ids, snapshot.resident_ids),
      added_edges: added(snapshot.edge_ids, before.edge_ids),
      removed_edges: removed(before.edge_ids, snapshot.edge_ids),
    };
  }

  function leaderDeltaText(delta) {
    if (delta.baseline) return "这是第一份可追踪的 Leader 审视快照。";
    const changes = [
      delta.added_nodes.length ? `新增 ${delta.added_nodes.length} 个节点` : "",
      delta.removed_nodes.length ? `移除 ${delta.removed_nodes.length} 个节点` : "",
      delta.added_edges.length ? `新增 ${delta.added_edges.length} 条关系` : "",
      delta.removed_edges.length ? `移除 ${delta.removed_edges.length} 条关系` : "",
    ].filter(Boolean);
    return changes.length ? `相对 r${delta.since_revision}：${changes.join("；")}。` : `相对 r${delta.since_revision} 没有节点或关系集合变化；仍以当前目标、状态和契约为准。`;
  }

  function createLeader(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalHatgLeader is missing: ${missing.join(", ")}`);
    const {
      $id, closeModal, commitLineage, effectiveNodeStatus, nodeById, nodeResidentId,
      sendPromptToNode, setBusy, societyService, state, toast,
    } = dependencies;

    function effectiveLeaderNode(lineage = state.lineage) {
      const nodes = lineage?.nodes || [];
      return nodes.find(node => node.id === lineage?.leader_resident_id)
        || null;
    }

    function leaderReviewSnapshot() {
      return {
        lineage_revision: Number(state.lineage?.revision || 0),
        resident_ids: (state.lineage?.nodes || []).map(node => node.id).filter(Boolean).sort(),
        edge_ids: (state.lineage?.edges || []).map(edge => edge.id).filter(Boolean).sort(),
        node_count: (state.lineage?.nodes || []).length,
        edge_count: (state.lineage?.edges || []).length,
      };
    }

    function leaderGraphSummary(snapshot, delta, profile) {
      const nodes = state.lineage.nodes.map(node => ({
        id: node.id, title: node.title, resident_id: nodeResidentId(node),
        status: effectiveNodeStatus(node),
      }));
      const edges = state.lineage.edges.map(edge => ({ id: edge.id, source: edge.source, target: edge.target, type: edge.type, label: edge.label, note: edge.note }));
      return JSON.stringify({
        lineage: { id: state.lineage.lineage_id, title: state.lineage.title, revision: snapshot.lineage_revision },
        leader: { resident_id: state.lineage.leader_resident_id, charter: profile.charter, authority: "propose_only" },
        snapshot,
        changes_since_previous_leader_review: delta,
        nodes,
        edges,
      }, null, 2);
    }

    async function submitLeaderRequest(event) {
      event.preventDefault();
      const leader = nodeById($id("leaderNodeSelect").value);
      if (!leader) return;
      const request = $id("leaderRequestInput").value.trim();
      const charter = $id("leaderCharterInput").value.trim() || DEFAULT_LEADER_CHARTER;
      const snapshot = leaderReviewSnapshot();
      const delta = leaderReviewDelta(null, snapshot);
      const profile = { charter };
      const prompt = [
        "[Lineage 持久 Leader 审视]",
        `你是「${state.lineage.title}」当前指定的 Leader 节点。本次审视要求：${charter}`,
        "权限边界：你只能提出建议；不能自行修改 HATG、创建或停止节点、发送交接，或执行任何未获明确授权的操作。",
        `当前图快照为 r${snapshot.lineage_revision}，它覆盖过去对图结构的记忆。${leaderDeltaText(delta)}`,
        request || "请审视当前 HATG 的职责边界、交接质量、验收回路和未决风险。",
        "当前 HATG 的完整快照：",
        "```json",
        leaderGraphSummary(snapshot, delta, profile),
        "```",
        "请按“触发证据、建议操作、影响节点、需要人类确认的部分”逐条输出。若当前快照不足以判断，请明确说明需要读取的工件路径。",
      ].join("\n\n");
      const button = $id("leaderSubmitBtn");
      setBusy(button, true, "设置 Leader 并发送快照…");
      try {
        // UpdateLineage records the Leader; DeliverMessage sends it the snapshot.
        const response = await societyService.updateMetadata({
          lineage_id: state.lineage.lineage_id,
          leader_resident_id: leader.id,
        });
        commitLineage(response.lineage);
        await sendPromptToNode(leader, prompt);
        closeModal("leaderModal");
        toast(window.PortalI18n.t("Leader 已设置；最新快照已发送到左侧 Chat Workspace。"), "success", 6000);
      } catch (error) {
        toast(window.PortalI18n.text`Leader 请求失败：${error.message || error}`, "error", 6000);
      } finally {
        setBusy(button, false);
      }
    }

    return Object.freeze({
      effectiveLeaderNode, submitLeaderRequest,
    });
  }

  window.PortalHatgLeader = Object.freeze({
    createLeader, dependencies: DEPENDENCIES,
  });
})();
