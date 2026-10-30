(() => {
  "use strict";

  if (window.PortalResidentMenuIdentity) return;

  // The header's pin and the Resident's avatar: reading and toggling the
  // pin, and the avatar editor that changes the portrait.

  const DEPENDENCIES = Object.freeze([
    "$id", "menuState", "nodeMenuNotice",
  ]);

  function createIdentity(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalResidentMenuIdentity is missing: ${missing.join(", ")}`);
    const {
      $id, menuState, nodeMenuNotice,
    } = dependencies;

    function renderNodeMenuPin(context) {
      const button = $id("nodeMenuPin");
      button.disabled = context.busy || !context.pinReady;
      button.setAttribute("aria-pressed", String(context.pinned));
      button.setAttribute("aria-label", context.pinned ? window.PortalI18n.t("取消置顶") : window.PortalI18n.t("置顶"));
    }

    async function loadNodeMenuPin(context) {
      if (menuState.nodeMenuContext !== context || context.pinLoading) return;
      context.pinLoading = true;
      const store = window.PortalConversationStore;
      const sync = () => {
        if (menuState.nodeMenuContext !== context) return;
        context.pinned = Boolean(store.entry(`resident:${context.residentId}`)?.pinned);
        renderNodeMenuPin(context);
      };
      renderNodeMenuPin(context);
      try {
        context.unobservePin ||= store.subscribe(sync);
        nodeMenuNotice("");
        const result = await store.loadPin(context.residentId);
        if (menuState.nodeMenuContext !== context) return;
        if (!result) throw new Error(window.PortalI18n.t("请重试"));
        context.pinReady = true;
        sync();
      } catch (error) {
        if (menuState.nodeMenuContext === context) nodeMenuNotice(window.PortalI18n.text`置顶状态读取失败：${error.message}`, () => loadNodeMenuPin(context));
      } finally {
        context.pinLoading = false;
      }
    }

    async function loadNodeMenuIdentity(context) {
      await Promise.allSettled([loadNodeMenuPin(context), mountNodeMenuAvatar(context)]);
    }

    // The portrait shows the saved avatar; 编辑头像 in Settings changes it through UpdateResident.
    async function mountNodeMenuAvatar(context) {
      await window.PortalUIPrimitives.loadAsset("/static/portal_avatar_editor.js", {ready: () => window.PortalAvatarEditor});
      if (menuState.nodeMenuContext !== context) return;
      context.avatarEditor = window.PortalAvatarEditor.mount($id("nodeMenuAvatar"), {
        portrait: $id("nodeMenuPortrait"), label: context.node.title || context.residentId,
        load: () => window.PortalAvatarEditor.read("resident", context.residentId, {fresh: true}),
        save: async avatar => (await window.PortalInstructionClient.execute("UpdateResident", {
          lineage_id: context.lineageId, resident_id: context.residentId, avatar,
        }, {idempotencyKey: `resident-avatar:${window.PortalUIPrimitives.randomUUID()}`})).resident.avatar,
        onSaved: avatar => window.PortalAvatarEditor.remember("resident", context.residentId, avatar),
      });
    }

    async function toggleNodeMenuPin() {
      const context = menuState.nodeMenuContext;
      if (!context || !context.pinReady || context.busy || context.saving || menuState.nodeTransferInFlight) return;
      context.busy = true; renderNodeMenuPin(context);
      try {
        const result = await window.PortalConversationStore.setPinned(`resident:${context.residentId}`, !context.pinned);
        if (menuState.nodeMenuContext !== context) return;
        context.pinned = Boolean(result?.item?.pinned);
        nodeMenuNotice("");
      } catch (error) { if (menuState.nodeMenuContext === context) nodeMenuNotice(window.PortalI18n.text`置顶状态更新失败：${error.message}`); }
      finally { if (menuState.nodeMenuContext === context) { context.busy = false; renderNodeMenuPin(context); } }
    }

    return {
      loadNodeMenuIdentity, toggleNodeMenuPin,
    };
  }

  window.PortalResidentMenuIdentity = Object.freeze({ createIdentity, dependencies: DEPENDENCIES });
})();
