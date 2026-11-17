(() => {
  "use strict";

  if (window.PortalResidentMenuIdentity) return;

  // The Resident's portrait and avatar editor.

  const DEPENDENCIES = Object.freeze([
    "$id", "menuState", "nodeMenuNotice",
  ]);

  function createIdentity(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalResidentMenuIdentity is missing: ${missing.join(", ")}`);
    const {
      $id, menuState, nodeMenuNotice,
    } = dependencies;

    async function loadNodeMenuIdentity(context) {
      await mountNodeMenuAvatar(context);
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

    return {
      loadNodeMenuIdentity,
    };
  }

  window.PortalResidentMenuIdentity = Object.freeze({ createIdentity, dependencies: DEPENDENCIES });
})();
