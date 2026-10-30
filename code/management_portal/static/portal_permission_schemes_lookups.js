(() => {
  "use strict";

  if (window.PortalPermissionSchemesLookups) return;

  // What the permission scheme page reads off its state: whether the draft
  // has unsaved changes, whether the permission system is on, which
  // instructions the caller may run, the vocabulary's instructions and A
  // elements, the unclassified elements and who passes them, labels, and the
  // notice above the editor.

  const DEPENDENCIES = Object.freeze([
    "state", "t", "tx", "unmappedCallers",
  ]);

  function createLookups(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalPermissionSchemesLookups is missing: ${missing.join(", ")}`);
    const {
      state, t, tx, unmappedCallers,
    } = dependencies;

    const dirty = () => Boolean(state.draft) && JSON.stringify(state.draft) !== state.base;
    // With the permission system off the schemes stay editable; the active one is the scheme used when it is turned on.
    const enabled = () => state.system?.enabled === true;
    // Each button asks for the one instruction it runs (GetPermissionSystem.allows).
    const may = name => state.system?.allows?.[name] === true;
    const activeLabel = () => typeof state.system?.enabled !== "boolean" ? t("已选择") : enabled() ? t("启用中") : t("开启时使用");
    const editable = () => may("UpdatePermissionScheme") && !state.busy && Boolean(state.current) && !state.current.builtin;
    const instructionNames = () => (state.vocabulary.instructions || []).map(item => item.name);
    const instructionInfo = name => (state.vocabulary.instructions || []).find(item => item.name === name);
    // The A elements instructions declare (vocabulary.instructions[].elements): element -> {instruction, slot}.
    let elementCache = {vocabulary: null, map: new Map()};
    const elementMap = () => {
      if (elementCache.vocabulary !== state.vocabulary) {
        const map = new Map();
        for (const info of state.vocabulary.instructions || []) for (const slot of info.elements || []) map.set(slot.element, {instruction: info.name, slot});
        elementCache = {vocabulary: state.vocabulary, map};
      }
      return elementCache.map;
    };
    const elementsOf = name => instructionInfo(name)?.elements || [];
    const isFixed = element => Boolean(elementMap().get(element)?.slot.fixed);
    // The server lists the A elements a scheme does not classify (GetPermissionScheme and each dry_run);
    // until it answers, the editor counts them from the vocabulary.
    const unmappedNames = () => state.unmapped || [...elementMap().keys()].filter(element => !state.draft.instructions?.[element]);
    const principalLabel = value => state.draft.trees.P.nodes.find(node => node.id === value)?.title || value;
    const relationTitle = relation => t((state.vocabulary.relations || []).find(item => item.relation === relation)?.title || relation);
    // Who passes an unclassified element: a fixed slot on the A and R default positions, an argument slot
    // on the A default position over the objects its argument names.
    const unmappedText = (fixed = true) => {
      const where = state.draft.trees.A.default_parent;
      if (!fixed) return tx`未归类：按 A 默认位置 ${where} 判定参数指向的对象，放行取决于对象在 R 树中的位置`;
      const callers = unmappedCallers(state.draft, principalLabel);
      if (callers === null) return t("未归类：所有主体都能调用");
      return callers.length ? tx`未归类：只有${callers.join("、")}能调用` : t("未归类：没有表项放行，任何人都不能调用");
    };
    const setNotice = (message, tone = "") => { state.notice = message; state.noticeTone = tone; };

    return {
      activeLabel, dirty, editable, elementMap, elementsOf, enabled, instructionInfo, instructionNames, isFixed, may,
      principalLabel, relationTitle, setNotice, unmappedNames, unmappedText,
    };
  }

  window.PortalPermissionSchemesLookups = Object.freeze({ createLookups, dependencies: DEPENDENCIES });
})();
