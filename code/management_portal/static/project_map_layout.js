(function () {
  "use strict";

  // Where each direction of a research map sits (decision_9d15ae2053c4): a
  // radial tree around the Project's goal, each subtree given a share of the
  // circle by its number of leaves, and positions people dragged kept as saved.
  if (window.PortalProjectMapLayout) return;

  const FIRST_RING = 320;
  const RING_STEP = 250;
  const STANCE_ORDER = Object.freeze({ focus: 0, explore: 1, candidate: 2, watch: 3, concluded: 4, dropped: 5 });

  function tree(directions) {
    const known = new Set(directions.map(item => item.direction_id));
    const children = new Map();
    for (const item of directions) {
      const parent = known.has(item.parent_id) ? item.parent_id : "";
      if (!children.has(parent)) children.set(parent, []);
      children.get(parent).push(item);
    }
    return children;
  }

  function leaves(children, id, memo = new Map()) {
    if (memo.has(id)) return memo.get(id);
    const below = children.get(id) || [];
    const count = below.length ? below.reduce((sum, child) => sum + leaves(children, child.direction_id, memo), 0) : 1;
    memo.set(id, count);
    return count;
  }

  // {direction_id: {x, y, depth, angle, saved}}; the goal sits at (0, 0).
  function radial(directions, saved = {}) {
    const children = tree(directions);
    const memo = new Map();
    const positions = {};
    const place = (items, start, span, depth) => {
      const total = items.reduce((sum, item) => sum + leaves(children, item.direction_id, memo), 0) || 1;
      let cursor = start;
      for (const item of items) {
        const share = span * leaves(children, item.direction_id, memo) / total;
        const angle = cursor + share / 2;
        const radius = FIRST_RING + (depth - 1) * RING_STEP;
        const own = saved[item.direction_id];
        positions[item.direction_id] = own
          ? { x: own.x, y: own.y, depth, angle, saved: true }
          : { x: Math.round(Math.cos(angle) * radius), y: Math.round(Math.sin(angle) * radius), depth, angle, saved: false };
        place(children.get(item.direction_id) || [], cursor, share, depth + 1);
        cursor += share;
      }
    };
    place(children.get("") || [], -Math.PI / 2, Math.PI * 2, 1);
    return positions;
  }

  function bounds(positions, padding = 180) {
    const points = Object.values(positions);
    if (!points.length) return { minX: -padding, minY: -padding, maxX: padding, maxY: padding };
    const xs = points.map(point => point.x);
    const ys = points.map(point => point.y);
    return { minX: Math.min(0, ...xs) - padding, minY: Math.min(0, ...ys) - padding,
      maxX: Math.max(0, ...xs) + padding, maxY: Math.max(0, ...ys) + padding };
  }

  // 1 for something that happened just now, fading to 0 over about three weeks.
  function heat(times, now = Date.now()) {
    const latest = Math.max(0, ...times.map(value => new Date(String(value || "")).getTime()).filter(Number.isFinite));
    if (!latest) return 0;
    const hours = Math.max(0, (now - latest) / 3_600_000);
    return Math.max(0, Math.min(1, 1 - Math.log1p(hours) / Math.log1p(24 * 21)));
  }

  // The directions shown: dropped ones only when asked; a hidden direction hides its subtree.
  function visible(directions, { showDropped = false } = {}) {
    if (showDropped) return directions;
    const hidden = new Set();
    let changed = true;
    while (changed) {
      changed = false;
      for (const item of directions) {
        if (hidden.has(item.direction_id)) continue;
        if (item.stance === "dropped" || hidden.has(item.parent_id)) {
          hidden.add(item.direction_id);
          changed = true;
        }
      }
    }
    return directions.filter(item => !hidden.has(item.direction_id));
  }

  function counts(directions) {
    const result = {};
    for (const item of directions) result[item.stance] = (result[item.stance] || 0) + 1;
    return result;
  }

  // A one-line account of a revision's changes, for the replay timeline.
  function describe(entry, titleOf) {
    const t = value => window.PortalI18n.t(value);
    const labels = { focus: "主攻", explore: "探索", watch: "观望", candidate: "候选", concluded: "已收束", dropped: "放弃" };
    const parts = (entry.changes || []).map(change => {
      const title = change.title || titleOf(change.direction_id) || change.direction_id || "";
      switch (change.op) {
        case "add_direction": return window.PortalI18n.text`新增「${title}」（${t(labels[change.stance] || change.stance)}）`;
        case "update_direction": return window.PortalI18n.text`修改「${title}」`;
        case "move_direction": return window.PortalI18n.text`移动「${title}」`;
        case "remove_direction": return window.PortalI18n.text`删除「${title}」`;
        case "add_relation": return t("连接两个方向");
        case "remove_relation": return t("去掉一条关系");
        case "add_note": return window.PortalI18n.text`为「${title}」记了一条${t({ progress: "进展", decision: "决定", note: "批注" }[change.kind] || "记录")}`;
        case "set_goal": return t("修改目标");
        default: return change.op;
      }
    });
    return parts.join("；");
  }

  function sortByStance(items) {
    return [...items].sort((left, right) => (STANCE_ORDER[left.stance] ?? 9) - (STANCE_ORDER[right.stance] ?? 9));
  }

  window.PortalProjectMapLayout = Object.freeze({ tree, radial, bounds, heat, visible, counts, describe, sortByStance,
    FIRST_RING, RING_STEP });
})();
