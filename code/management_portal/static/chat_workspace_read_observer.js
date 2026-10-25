(() => {
  if (window.PortalChatWorkspaceReadObserver) return;

  // The read receipt of a Resident's conversation: while the viewer can see it,
  // it is marked read in the Conversation store, at most once a frame, and
  // again after a failed attempt.
  const DEPENDENCIES = Object.freeze([]);

  function createReadObserver() {
    class ResidentReadObserver {
      constructor(panel) {
        this.panel = panel;
        this.root = panel.workspace.els.messages;
        this.observation = null;
        this.frame = 0;
        this.pending = false;
        this.confirmAfterPending = false;
        this.retryAfter = 0;
        this.retryTimer = 0;
        this.confirmed = null;
        this.disposed = false;
        this.schedule = () => {
          if (this.disposed || this.frame) return;
          this.frame = window.requestAnimationFrame(() => {
            this.frame = 0;
            this.confirm();
          });
        };
        this.resume = () => {
          this.retryAfter = 0;
          this.schedule();
        };
        this.deferRetry = () => {
          this.retryAfter = Date.now() + 5000;
          if (!this.retryTimer) this.retryTimer = window.setTimeout(() => {
            this.retryTimer = 0;
            this.schedule();
          }, 5000);
        };
        this.unsubscribe = window.PortalConversationStore?.subscribe?.(() => {
          this.schedule();
        });
        this.resizeObserver = new ResizeObserver(this.schedule);
        this.resizeObserver.observe(this.root);
        document.addEventListener("visibilitychange", this.resume);
        window.addEventListener("focus", this.resume);
      }

      update(snapshot) {
        // A conversation view that projects the snapshot differently decides when it is presented.
        const presented = this.panel.conversationView()?.readReceipt?.(snapshot) !== false;
        const observation = snapshot && presented ? {
          residentId: this.panel.target().resident_id,
          cursor: Number(snapshot.cursor || 0),
          unread: snapshot.viewer_state?.unread === true,
        } : null;
        if (observation?.residentId !== this.observation?.residentId) this.confirmed = null;
        this.observation = observation;
        this.schedule();
      }

      confirm() {
        if (this.pending) {
          this.confirmAfterPending = true;
          return;
        }
        if (this.disposed || !this.observation || document.hidden || this.panel.presented === false
          || !this.root.isConnected || this.panel.pendingProtocol
          || this.panel.workspace.isLoading() || Date.now() < this.retryAfter
          || this.observation.residentId !== this.panel.target().resident_id) return;
        if (this.root.checkVisibility && !this.root.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return;
        const store = window.PortalConversationStore;
        if (!store?.observeResidentRead) return;
        const residentId = this.observation.residentId;
        const cursor = this.observation.cursor;
        const actor = String(window.PortalIdentity?.username?.() || "").trim();
        if (!actor) return;
        const item = store.entry?.(`resident:${residentId}`);
        const revision = Number(item?.read_revision || 0);
        if (this.confirmed?.residentId === residentId
          && this.confirmed.actor === actor
          && !item?.unread
          // HATG can present a new completion without loading its inbox entry.
          // Only reuse a receipt for the same unread presentation; an old or
          // absent index entry cannot suppress the next completion's receipt.
          && (!this.observation.unread || this.confirmed.cursor === cursor)
          && this.confirmed.revision >= revision) return;
        this.pending = true;
        Promise.resolve(store.observeResidentRead({
          residentId,
          actor,
        })).then(payload => {
          if (!payload) {
            this.deferRetry();
            return;
          }
          this.confirmed = {
            residentId,
            actor,
            cursor,
            revision: Math.max(revision, Number(payload?.item?.read_revision || 0)),
          };
          this.retryAfter = 0;
          if (this.retryTimer) window.clearTimeout(this.retryTimer);
          this.retryTimer = 0;
        }).catch(error => {
          this.deferRetry();
          console.warn("Resident read receipt failed", error);
        }).finally(() => {
          this.pending = false;
          if (this.confirmAfterPending) {
            this.confirmAfterPending = false;
            this.schedule();
          }
        });
      }

      destroy() {
        this.disposed = true;
        if (this.frame) window.cancelAnimationFrame(this.frame);
        if (this.retryTimer) window.clearTimeout(this.retryTimer);
        this.unsubscribe?.();
        this.resizeObserver.disconnect();
        document.removeEventListener("visibilitychange", this.resume);
        window.removeEventListener("focus", this.resume);
        this.observation = null;
      }
    }

    return Object.freeze({
      ResidentReadObserver,
    });
  }

  window.PortalChatWorkspaceReadObserver = Object.freeze({
    createReadObserver, dependencies: DEPENDENCIES,
  });
})();
