export default (() => {
  "use strict";

  const REALM_URL = "/static/portal_mermaid_realm.mjs";
  const MAX_SOURCE_LENGTH = 50000;
  let libraryPromise = null;
  let initialized = false;
  let diagramSequence = 0;
  let renderQueue = Promise.resolve();
  let libraryFrame = null;

  function loadIsolatedLibrary() {
    return new Promise((resolve, reject) => {
      const frame = document.createElement("iframe");
      frame.tabIndex = -1;
      frame.setAttribute("aria-hidden", "true");
      frame.setAttribute("data-portal-mermaid-realm", "");
      frame.style.position = "fixed";
      frame.style.left = "-100000px";
      frame.style.top = "0";
      frame.style.width = `${Math.max(1600, document.documentElement.clientWidth || 0)}px`;
      frame.style.height = "1200px";
      frame.style.border = "0";
      frame.style.opacity = "0";
      frame.style.pointerEvents = "none";
      frame.src = "about:blank";
      document.documentElement.appendChild(frame);

      const realmWindow = frame.contentWindow;
      const realmDocument = frame.contentDocument;
      if (!realmWindow || !realmDocument?.head) {
        frame.remove();
        reject(new Error("Unable to create an isolated Mermaid realm."));
        return;
      }

      const script = realmDocument.createElement("script");
      script.type = "module";
      script.src = REALM_URL;
      script.onload = () => {
        const mermaid = realmWindow.PortalMermaidRealm?.mermaid;
        if (typeof mermaid?.initialize !== "function" || typeof mermaid?.render !== "function") {
          frame.remove();
          reject(new Error("Mermaid realm did not expose its API."));
          return;
        }
        libraryFrame = frame;
        resolve(mermaid);
      };
      script.onerror = () => {
        frame.remove();
        reject(new Error("Unable to load the isolated Mermaid realm."));
      };
      realmDocument.head.appendChild(script);
    });
  }

  function loadLibrary() {
    if (typeof window.mermaid?.initialize === "function" && typeof window.mermaid?.render === "function") {
      return Promise.resolve(window.mermaid);
    }
    if (libraryPromise) return libraryPromise;
    libraryPromise = loadIsolatedLibrary().catch(error => {
      libraryFrame?.remove();
      libraryFrame = null;
      libraryPromise = null;
      throw error;
    });
    return libraryPromise;
  }

  function portalUsesDarkTheme() {
    return getComputedStyle(document.documentElement).colorScheme === "dark";
  }

  async function initializedLibrary() {
    const mermaid = await loadLibrary();
    if (!initialized) {
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        suppressErrorRendering: true,
        maxTextSize: MAX_SOURCE_LENGTH,
        maxEdges: 500,
        theme: portalUsesDarkTheme() ? "dark" : "default",
        flowchart: { htmlLabels: false, useMaxWidth: true },
      });
      initialized = true;
    }
    return mermaid;
  }

  function sanitizedSvg(markup) {
    const template = document.createElement("template");
    template.innerHTML = String(markup || "").trim();
    template.content.querySelectorAll("script, iframe, object, embed, form, input, button, textarea, select, link, meta, base, img, audio, video, source, track").forEach(node => node.remove());
    template.content.querySelectorAll("*").forEach(node => {
      [...node.attributes].forEach(attribute => {
        if (/^on/i.test(attribute.name)) node.removeAttribute(attribute.name);
        if (/^(?:href|xlink:href|src|srcset|action|formaction)$/i.test(attribute.name) && !String(attribute.value || "").startsWith("#")) {
          node.removeAttribute(attribute.name);
        }
        if (attribute.name.toLowerCase() === "style" && /(?:url\s*\(|expression\s*\()/i.test(attribute.value || "")) {
          node.removeAttribute(attribute.name);
        }
      });
    });
    template.content.querySelectorAll("foreignObject").forEach(container => {
      container.style.overflow = "visible";
      container.querySelectorAll("div, p, span").forEach(label => {
        label.style.overflowWrap = "anywhere";
        label.style.wordBreak = "break-word";
      });
    });
    if (!template.content.querySelector("svg")) throw new Error("Mermaid returned no SVG.");
    return template.content;
  }

  function queueRender(callback) {
    const queued = renderQueue.then(callback, callback);
    renderQueue = queued.catch(() => undefined);
    return queued;
  }

  const states = new WeakMap();
  const subscriptions = new WeakMap();
  let subscriptionSequence = 0;
  const visibility = "IntersectionObserver" in window ? new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      visibility.unobserve(entry.target);
      renderBlock(entry.target, states.get(entry.target));
    });
  }, { rootMargin: "480px 0px" }) : null;

  function isCurrent(block, state) {
    return state && block.isConnected && states.get(block) === state;
  }

  function showError(block, state, error) {
    if (!isCurrent(block, state)) return;
    const canvas = block.querySelector("[data-portal-mermaid-canvas]");
    if (!canvas) return;
    block.dataset.mermaidState = "error";
    canvas.replaceChildren();
    const message = document.createElement("span");
    message.className = "portalMermaidError";
    message.textContent = "Mermaid 图无法渲染，请展开源码检查语法。";
    canvas.appendChild(message);
    const source = block.querySelector(".portalMermaidSource");
    if (source) source.open = true;
    console.warn("Mermaid render failed", error);
  }

  function renderBlock(block, state) {
    if (!isCurrent(block, state) || state.started) return;
    state.started = true;
    block.dataset.mermaidState = "loading";
    const canvas = block.querySelector("[data-portal-mermaid-canvas]");
    const source = state.source;
    if (canvas) canvas.innerHTML = '<span class="portalMermaidStatus">正在渲染 Mermaid 图…</span>';
    const diagramId = `portal-mermaid-${Date.now().toString(36)}-${++diagramSequence}`;
    return queueRender(async () => {
      if (!isCurrent(block, state)) return;
      if (!source.trim()) throw new Error("Empty Mermaid source.");
      if (source.length > MAX_SOURCE_LENGTH) throw new Error("Mermaid source is too large.");
      const mermaid = await initializedLibrary();
      if (!isCurrent(block, state)) return;
      const result = await mermaid.render(diagramId, source);
      if (!isCurrent(block, state) || !canvas) return;
      canvas.replaceChildren(sanitizedSvg(result?.svg));
      block.dataset.mermaidState = "ready";
    }).catch(error => showError(block, state, error));
  }

  function visit(root, callback) {
    if (root.matches?.("[data-portal-mermaid]")) callback(root);
    root.querySelectorAll?.("[data-portal-mermaid]").forEach(callback);
  }

  function mount(block) {
    if (!block.isConnected) return;
    const source = block.querySelector("[data-portal-mermaid-source]")?.textContent || "";
    if (states.get(block)?.source === source) return;
    const state = { source, started: false };
    states.set(block, state);
    block.dataset.mermaidState = "pending";
    if (visibility) visibility.observe(block);
    else renderBlock(block, state);
  }

  function unmount(block) {
    if (block.isConnected) return;
    visibility?.unobserve(block);
    states.delete(block);
  }

  function observe(root = document) {
    if (!subscriptions.has(root)) {
      const unregister = window.PortalUIPrimitives.registerEnhancer({
        id: `markdown-mermaid-${++subscriptionSequence}`, selector: "[data-portal-mermaid]", content: true,
        enhance(block) { if (root.contains(block)) mount(block); },
        remove: unmount,
      });
      subscriptions.set(root, () => {
        unregister();
        subscriptions.delete(root);
        visit(root, block => { visibility?.unobserve(block); states.delete(block); });
      });
    }
    visit(root, mount);
    return subscriptions.get(root);
  }

  return Object.freeze({ observe });
})();
