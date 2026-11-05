(() => {
  "use strict";

  // The manifest is content, never executable host configuration. ResourceService
  // owns authorization; this adapter owns bounded reads, integrity and lifetime.
  const MiB = 1024 * 1024;
  const limits = Object.freeze({ manifest: MiB, video: 64 * MiB, text: 2 * MiB, interaction: 2 * MiB });
  const hashPattern = /^[a-f0-9]{64}$/;
  const t = value => window.PortalI18n?.t?.(value) || value;
  const node = (tag, text, className) => {
    const el = document.createElement(tag);
    if (text != null) el.textContent = text;
    if (className) el.className = className;
    return el;
  };
  const resourceUrl = address => `/api/resources/content?address=${encodeURIComponent(address)}`;
  function asset(value) {
    if (!value || typeof value !== "object" || typeof value.address !== "string"
      || !/^workspace_[a-f0-9]{12}\/file\/.+/.test(value.address)
      || value.address.length > 4096 || /[\x00-\x1f\x7f\\?#]/.test(value.address)
      || value.address.split("/").some(part => part === "." || part === ".." || !part)
      || !hashPattern.test(value.sha256 || "")) throw new Error(t("文件地址或内容摘要无效。"));
    return { address: value.address, sha256: value.sha256 };
  }
  const identity = () => {
    const current = window.PortalIdentity?.peek?.() || {};
    return JSON.stringify([current.principal?.kind || "user", current.principal?.id || current.username || window.PortalIdentity?.username?.() || ""]);
  };
  const abortError = () => new DOMException("Explanation view cancelled", "AbortError");
  async function digest(bytes, signal) {
    if (signal.aborted) throw abortError();
    if (globalThis.crypto?.subtle || bytes.byteLength < MiB) {
      const { sha256 } = await import("/static/portal_sha256.js");
      return sha256(bytes);
    }
    // LAN HTTP has no WebCrypto. Keep the large-video fallback off the UI thread.
    const code = `import {sha256} from ${JSON.stringify(new URL("/static/portal_sha256.js", location.href).href)};onmessage=async e=>{try{postMessage({hash:await sha256(e.data)})}catch(e){postMessage({error:String(e)})}}`;
    const url = URL.createObjectURL(new Blob([code], { type: "text/javascript" }));
    const worker = new Worker(url, { type: "module" });
    try {
      return await new Promise((resolve, reject) => {
        const cancel = () => reject(abortError());
        signal.addEventListener("abort", cancel, { once: true });
        worker.onmessage = event => { signal.removeEventListener("abort", cancel); event.data.error ? reject(new Error(event.data.error)) : resolve(event.data.hash); };
        worker.onerror = () => { signal.removeEventListener("abort", cancel); reject(new Error(t("无法核对文件内容摘要。"))); };
        // The original bytes remain available for the authorized Blob.
        const copy = bytes.slice(); worker.postMessage(copy, [copy.buffer]);
      });
    } finally { worker.terminate(); URL.revokeObjectURL(url); }
  }
  async function readBytes(url, maxBytes, signal, expectedHash) {
    const response = await window.PortalAPI.raw(url, { signal, cache: "no-store", credentials: "same-origin", redirect: "error" });
    if (!response.ok) {
      await response.body?.cancel?.();
      throw Object.assign(new Error(t(response.status === 403 ? "当前账号无权读取此内容。" : response.status === 404 ? "找不到此文件。" : "读取失败，请重试。")), { status: response.status });
    }
    const tooLarge = () => new Error(t("文件超过解释页的读取上限。"));
    if (Number(response.headers.get("Content-Length")) > maxBytes) { await response.body?.cancel?.(); throw tooLarge(); }
    if (!response.body?.getReader) throw new Error(t("浏览器不支持受限文件读取。"));
    const reader = response.body.getReader(), chunks = [];
    let length = 0;
    try {
      while (true) {
        if (signal.aborted) throw abortError();
        const part = await reader.read();
        if (part.done) break;
        length += part.value.byteLength;
        if (length > maxBytes) throw tooLarge();
        chunks.push(part.value);
      }
    } catch (error) { await reader.cancel().catch(() => {}); throw error; }
    finally { reader.releaseLock(); }
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    if (expectedHash && await digest(bytes, signal) !== expectedHash) throw new Error(t("文件内容已改变，无法打开这个固定版本。"));
    if (signal.aborted) throw abortError();
    return { bytes, type: (response.headers.get("Content-Type") || "").split(";")[0] };
  }
  const decode = bytes => new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  function chapters(value) {
    if (!Array.isArray(value)) return [];
    return value.slice(0, 80).filter(item => item && Number.isFinite(item.time) && item.time >= 0 && typeof item.title === "string")
      .map(item => ({ time: item.time, title: item.title.slice(0, 240) })).sort((a, b) => a.time - b.time);
  }
  function mediaUrl(value) {
    const url = new URL(String(value || ""), location.href);
    if (!value || url.origin !== location.origin || !url.pathname.startsWith("/api/") || url.username || url.password) throw new Error(t("媒体地址无效。"));
    return url.href;
  }

  // Runs inside the opaque document. Only its layout size crosses the boundary;
  // it receives no host credentials, resource API or document access.
  function observeInteractionLayout(channel) {
    let pending = false, previous = 0;
    const measure = () => {
      pending = false;
      const body = document.body;
      if (!body) return;
      // Do not measure documentElement.scrollHeight: it includes the old iframe
      // viewport, preventing the document from shrinking when details collapse.
      const bounds = body.getBoundingClientRect();
      const height = Math.max(1, Math.ceil(Math.max(bounds.height, body.scrollHeight) + Math.max(0, bounds.top + scrollY)));
      if (height !== previous) {
        previous = height;
        parent.parent.postMessage({ type: "portal-explanation-layout", channel, height }, "*");
      }
    };
    const schedule = () => {
      if (!pending) { pending = true; requestAnimationFrame(measure); }
    };
    document.addEventListener("DOMContentLoaded", () => {
      new ResizeObserver(schedule).observe(document.body);
      new MutationObserver(schedule).observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });
      document.addEventListener("load", schedule, true);
      document.fonts?.ready.then(schedule);
      schedule();
    }, { once: true });
    window.addEventListener("resize", schedule);
  }

  // Interactive prose uses the same opaque-inner / trusted-empty-outer boundary as
  // projection_host. The outer frame's CSP also blocks inner self-navigation.
  // No SDK, host callbacks, credentials or business APIs enter this document.
  function mountInteraction(container, html, title) {
    // Template contents are inert even for image loads during parsing.
    const template = document.createElement("template"); template.innerHTML = html;
    const parsed = document.implementation.createHTMLDocument(""); parsed.body.append(template.content);
    if (parsed.querySelector("base,iframe,frame,object,embed,script[src],link")) throw new Error(t("交互文件必须是自包含页面，不能嵌套页面或加载外部脚本。"));
    parsed.querySelectorAll("meta[http-equiv]").forEach(el => el.remove());
    const policy = parsed.createElement("meta");
    policy.httpEquiv = "Content-Security-Policy";
    policy.content = "default-src 'none'; script-src 'unsafe-inline'; script-src-attr 'none'; style-src 'unsafe-inline'; img-src data:; media-src 'none'; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-src 'none'; object-src 'none'; worker-src 'none'; font-src 'none'";
    const style = parsed.createElement("style");
    const computed = getComputedStyle(container.isConnected ? container : document.body);
    const color = name => computed.getPropertyValue(name).trim().replace(/[;{}<>]/g, "");
    style.textContent = `:root{font-size:${computed.fontSize};color-scheme:${computed.colorScheme || "light"};--bg:${color("--panel") || "#fff"};--panel:${color("--panel") || "#fff"};--text:${color("--text") || "#192234"};--muted:${color("--muted") || "#536078"};--border:${color("--border") || "#d6dde8"};--accent:${color("--accent") || "#365edb"}}*{box-sizing:border-box}body{display:flow-root;margin:0;background:var(--panel);color:var(--text);font:${computed.fontSize} / 1.6 system-ui,sans-serif}button,input,select{font:inherit}button,input[type=range],select{min-height:44px}button:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid var(--accent);outline-offset:2px}img,svg,canvas{max-width:100%}@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important;scroll-behavior:auto!important}}`;
    parsed.head.prepend(policy, style);
    // The report owns vertical scrolling. The authored document contributes its
    // full natural height instead of introducing another viewport or frame border.
    const flow = parsed.createElement("style");
    flow.textContent = "html,body{height:auto!important;min-height:0!important;max-height:none!important}html{overflow:hidden!important}body{overflow:visible!important;margin:0!important;padding:0!important}";
    parsed.body.append(flow);
    const channel = Array.from(crypto.getRandomValues(new Uint32Array(4)), value => value.toString(16)).join("-");
    const layout = parsed.createElement("script");
    layout.textContent = `(${observeInteractionLayout.toString()})(${JSON.stringify(channel)});`;
    parsed.head.append(layout);
    const surface = node("iframe", null, "homeExplanationInteraction");
    surface.title = title; surface.referrerPolicy = "no-referrer";
    surface.setAttribute("scrolling", "no");
    surface.style.height = "1px";
    // Preview may finish loading before its host is connected, and a host can
    // later be reparented. Serialize the trusted wrapper instead of touching an
    // about:blank contentDocument that is absent (or discarded) in those states.
    const outer = document.implementation.createHTMLDocument("");
    const navigation = outer.createElement("meta");
    navigation.httpEquiv = "Content-Security-Policy"; navigation.content = "frame-src 'none'";
    outer.head.append(navigation);
    outer.documentElement.style.height = "100%";
    outer.body.style.cssText = "margin:0;height:100%;overflow:hidden";
    const inner = outer.createElement("iframe"); inner.title = title;
    inner.setAttribute("sandbox", "allow-scripts"); inner.referrerPolicy = "no-referrer";
    inner.setAttribute("scrolling", "no");
    inner.style.cssText = "display:block;width:100%;height:100%;border:0";
    outer.body.append(inner); inner.srcdoc = "<!doctype html>" + parsed.documentElement.outerHTML;
    surface.srcdoc = "<!doctype html>" + outer.documentElement.outerHTML;
    let pending = 0, nextHeight = 1;
    const receiveLayout = event => {
      const message = event.data;
      if (event.source !== surface.contentWindow?.[0] || message?.type !== "portal-explanation-layout"
        || message.channel !== channel || !Number.isFinite(message.height) || message.height < 1 || message.height > 10000000) return;
      nextHeight = Math.ceil(message.height);
      if (!pending) pending = requestAnimationFrame(() => {
        pending = 0;
        surface.style.height = `${nextHeight}px`;
      });
    };
    window.addEventListener("message", receiveLayout);
    container.append(surface);
    return () => {
      window.removeEventListener("message", receiveLayout);
      cancelAnimationFrame(pending);
      surface.remove();
    };
  }

  function create({ root, onOpen, onSelection, onChange }) {
    const owner = identity(), urls = new Set(), disposers = new Set();
    let presented = true, destroyed = false, token = 0, controller = null, externalSignal = null;
    let target = null, manifest = null, status = "idle", video = null, playbackTime = 0, scrollTop = 0;
    let mediaTimer = null, loading = null, requestId = "", externalBlur = false;
    root.classList.add("homeExplanation");
    const snapshot = () => ({ target, payload: manifest, accessStatus: status, hasDraft: false, requestId });
    const notify = () => { if (!destroyed) onChange?.(snapshot()); };
    const current = key => !destroyed && presented && !document.hidden && key === token && !controller?.signal.aborted && !externalSignal?.aborted && identity() === owner;
    const button = (label, action) => { const el = node("button", t(label), "homeExplanationButton"); el.type = "button"; el.onclick = action; return el; };
    const blob = (bytes, type) => { const url = URL.createObjectURL(new Blob([bytes], { type })); urls.add(url); return url; };
    function cleanup() {
      clearTimeout(mediaTimer); mediaTimer = null;
      if (video) { playbackTime = Number.isFinite(video.currentTime) ? video.currentTime : playbackTime; video.pause(); video.removeAttribute("src"); video.load(); video = null; }
      for (const dispose of disposers) dispose(); disposers.clear();
      for (const url of urls) URL.revokeObjectURL(url); urls.clear();
    }
    function invalidate() {
      token++; controller?.abort(); loading = null;
      if (root.childElementCount) scrollTop = root.scrollTop;
      cleanup(); root.replaceChildren(); manifest = null; status = "hidden"; notify();
    }
    function failure(container, error, retry = revalidate) {
      const box = node("div", null, "homeExplanationError"); box.setAttribute("role", "status");
      box.append(node("p", error.message || t("读取失败，请重试。")));
      box.append(button("重试", () => { void retry(); })); container.append(box);
    }
    async function read(ref, limit, key) {
      if (!current(key)) throw abortError();
      const fixed = asset(ref), result = await readBytes(resourceUrl(fixed.address), limit, controller.signal, fixed.sha256);
      if (!current(key)) throw abortError();
      return result;
    }
    function section(title, label) {
      const region = node("section", null, "homeExplanationSection");
      const heading = node("div", null, "homeExplanationSectionHead");
      heading.append(node("span", label, "homeExplanationStep"), node("h2", t(title)));
      region.append(heading); root.append(region); return region;
    }
    async function moduleTask(region, task, key) {
      const progress = node("p", t("正在读取…"), "homeExplanationMuted"); progress.setAttribute("role", "status"); region.append(progress);
      try { await task(); }
      catch (error) { if (current(key) && error.name !== "AbortError") failure(region, error); }
      finally { progress.remove(); }
    }
    async function renderVideo(region, spec, key, generated = false) {
      const source = generated
        ? await readBytes(mediaUrl(spec.video_url), limits.video, controller.signal, spec.video_sha256 || undefined)
        : await read(spec, limits.video, key);
      if (!current(key)) return;
      const type = source.type.startsWith("video/") ? source.type : "video/mp4";
      const player = node("video"); player.controls = true; player.playsInline = true; player.preload = "metadata";
      player.setAttribute("aria-label", t("讲解视频")); player.src = blob(source.bytes, type);
      video = player;
      const frame = node("div", null, "homeExplanationVideo"); frame.append(player); region.append(frame);
      if (document.fullscreenEnabled && frame.requestFullscreen) {
        const controls = node("div", null, "homeExplanationVideoControls");
        const transport = node("div", null, "homeExplanationTransport"); transport.hidden = true;
        const play = button("播放", async () => {
          if (!player.paused) player.pause();
          else try { await player.play(); } catch (error) { if (current(key)) failure(region, error); }
        });
        const seek = node("input"); seek.type = "range"; seek.min = "0"; seek.step = "0.1";
        seek.setAttribute("aria-label", t("播放位置"));
        seek.oninput = () => { if (Number.isFinite(player.duration)) player.currentTime = Number(seek.value); };
        const time = node("span", null, "homeExplanationTime");
        const mute = button("静音", () => { player.muted = !player.muted; });
        const volume = node("input"); volume.type = "range"; volume.min = "0"; volume.max = "1"; volume.step = "0.05";
        volume.className = "homeExplanationVolume"; volume.setAttribute("aria-label", t("音量"));
        volume.oninput = () => { player.volume = Number(volume.value); player.muted = player.volume === 0; };
        const cc = button("字幕", () => { const track = player.textTracks[0]; if (track) track.mode = track.mode === "showing" ? "disabled" : "showing"; });
        const clock = seconds => `${Math.floor((seconds || 0) / 60)}:${String(Math.floor((seconds || 0) % 60)).padStart(2, "0")}`;
        const syncTransport = () => {
          play.textContent = t(player.paused ? "播放" : "暂停");
          seek.disabled = !Number.isFinite(player.duration); seek.max = String(seek.disabled ? 0 : player.duration); seek.value = String(player.currentTime);
          time.textContent = `${clock(player.currentTime)} / ${clock(seek.disabled ? 0 : player.duration)}`;
          seek.setAttribute("aria-valuetext", time.textContent);
          mute.textContent = t(player.muted ? "取消静音" : "静音"); mute.setAttribute("aria-pressed", String(player.muted));
          volume.value = String(player.muted ? 0 : player.volume);
          cc.disabled = !player.textTracks.length; cc.setAttribute("aria-pressed", String(player.textTracks[0]?.mode === "showing"));
        };
        transport.append(play, seek, time, mute, volume, cc); controls.append(transport);
        const expand = button(t("全屏观看"), async () => {
          try {
            if (document.fullscreenElement === frame) await document.exitFullscreen();
            else await frame.requestFullscreen();
          } catch (error) { if (current(key)) failure(region, error); }
        });
        const syncFullscreen = () => {
          const expanded = document.fullscreenElement === frame;
          expand.textContent = t(expanded ? "退出全屏" : "全屏观看");
          expand.setAttribute("aria-pressed", String(expanded));
          transport.hidden = !expanded; player.controls = !expanded;
          syncTransport();
        };
        expand.classList.add("homeExplanationExpand");
        controls.append(expand); frame.prepend(controls);
        // Fullscreen the picture and its captions together, so the enlarged
        // diagram keeps the same unobscured caption layout as the page.
        player.controlsList?.add("nofullscreen");
        document.addEventListener("fullscreenchange", syncFullscreen);
        const transportEvents = [[player, "timeupdate"], [player, "durationchange"], [player, "loadedmetadata"],
          [player, "play"], [player, "pause"], [player, "volumechange"], [player.textTracks, "change"], [player.textTracks, "addtrack"]];
        for (const [target, event] of transportEvents) target.addEventListener(event, syncTransport);
        disposers.add(() => {
          document.removeEventListener("fullscreenchange", syncFullscreen);
          for (const [target, event] of transportEvents) target.removeEventListener(event, syncTransport);
        });
        syncFullscreen();
      }
      player.addEventListener("loadedmetadata", () => { if (current(key) && playbackTime < player.duration) player.currentTime = playbackTime; });
      player.addEventListener("error", () => { if (current(key)) failure(region, new Error(t("视频无法播放，请重试或阅读下方图文。"))); }, { once: true });
      const items = chapters(spec.chapters);
      if (items.length) {
        const list = node("nav", null, "homeExplanationChapters"); list.setAttribute("aria-label", t("视频章节"));
        for (const chapter of items) {
          const time = `${Math.floor(chapter.time / 60)}:${String(Math.floor(chapter.time % 60)).padStart(2, "0")}`;
          const jump = button(chapter.title, () => { if (current(key)) { player.currentTime = chapter.time; player.focus(); } });
          jump.prepend(node("span", time)); list.append(jump);
        }
        region.append(list);
      }
      const captions = generated ? spec.captions_url : spec.captions;
      const transcript = generated ? spec.transcript_url : spec.transcript;
      await Promise.all([
        captions && (async () => {
          try {
            const content = generated ? await readBytes(mediaUrl(captions), limits.text, controller.signal, spec.captions_sha256) : await read(captions, limits.text, key);
            if (!current(key)) return;
            if (!/^\uFEFF?WEBVTT(?:\s|$)/.test(decode(content.bytes))) throw new Error(t("字幕文件不是有效的 WebVTT。"));
            const track = node("track"); track.kind = "captions"; track.label = t("字幕"); track.srclang = spec.language || "zh"; track.default = true;
            track.src = blob(content.bytes, "text/vtt"); player.append(track);
            // Keep the authenticated native track and its CC controls. Inline
            // captions use document text sizing below the picture, so long
            // sentences cannot cover the diagram or shrink with the video.
            const captionText = node("div", null, "homeExplanationCaptions");
            captionText.setAttribute("aria-label", t("字幕")); captionText.hidden = true; frame.append(captionText);
            const syncCaptions = () => {
              if (!current(key)) return;
              const nativePresentation = document.fullscreenElement === player || player.webkitDisplayingFullscreen || document.pictureInPictureElement === player;
              frame.classList.toggle("homeExplanationInlineCaptions", !nativePresentation);
              const cues = track.track.mode === "showing" ? Array.from(track.track.activeCues || []) : [];
              const caption = cues.map(cue => cue.getCueAsHTML ? cue.getCueAsHTML().textContent : cue.text).join("\n");
              if (captionText.textContent !== caption) { captionText.textContent = caption; captionText.scrollTop = 0; }
              captionText.hidden = !!nativePresentation || !captionText.textContent;
            };
            const listeners = [[track, "load"], [track.track, "cuechange"], [player.textTracks, "change"],
              [document, "fullscreenchange"], [player, "webkitbeginfullscreen"], [player, "webkitendfullscreen"],
              [player, "enterpictureinpicture"], [player, "leavepictureinpicture"]];
            for (const [target, event] of listeners) target.addEventListener(event, syncCaptions);
            disposers.add(() => { for (const [target, event] of listeners) target.removeEventListener(event, syncCaptions); });
            syncCaptions();
          } catch (error) { if (current(key) && error.name !== "AbortError") failure(region, error); }
        })(),
        transcript && (async () => {
          try {
            const content = generated ? await readBytes(mediaUrl(transcript), limits.text, controller.signal, spec.transcript_sha256) : await read(transcript, limits.text, key);
            if (!current(key)) return;
            const details = node("details", null, "homeExplanationTranscript"); details.append(node("summary", t("阅读讲稿")), node("div", decode(content.bytes)));
            region.append(details);
          } catch (error) { if (current(key) && error.name !== "AbortError") failure(region, error); }
        })(),
      ]);
    }
    function videoReview(region, review, key, quality, page = false, mediaTarget = target) {
      if (!current(key)) return;
      const area = node("div", null, page ? "homeExplanationPageReview" : "homeExplanationVideoReview"); region.append(area);
      const endpoint = `/api/home-explanation/${page ? "reviews" : "media"}?address=${encodeURIComponent(mediaTarget.address)}&sha256=${mediaTarget.sha256}&quality=${quality}`;
      const unpack = result => page ? result?.review : (result?.media === undefined ? result : result.media)?.review;
      let releaseEvidence = [];
      const clearEvidence = () => { releaseEvidence.forEach(dispose => dispose()); releaseEvidence = []; };
      disposers.add(clearEvidence);
      const draw = value => {
        if (!current(key)) return;
        if (!page) { clearTimeout(mediaTimer); mediaTimer = null; }
        clearEvidence();
        area.replaceChildren();
        const state = value?.status || "not_requested";
        const can = name => value?.capabilities ? value.capabilities[name] === true : !value?.id;
        const labels = {
          pending: "视频质量检查等待提交。",
          submitted: "视频已提交独立检查，结果待回读。",
          submission_failed: "视频检查暂未提交，已保存的视频仍可播放。",
          not_requested: "视频尚未经过独立检查。",
          not_required: "此质量档位无需独立审核。",
          passed: "此固定版本已通过独立审核。",
          changes_requested: "审核要求修改；请保存新固定版本后重新送审。",
          blocked: "审核受阻，尚未通过。",
          cancelled: "审核已取消，尚未通过。",
          invalidated: "固定产物已变化，原审核通过结论失效；请保存新固定版本后重新送审。",
          unavailable: "审核状态暂不可读，尚未通过。",
          awaiting_author: "缺少经核验的负责Resident，审核等待正式作者关联。",
        };
        const pageLabels = { pending: "解释页质量检查等待提交。", submitted: "解释页已提交独立检查，尚未通过。",
          submission_failed: "解释页检查暂未提交。", not_requested: "解释页尚未经过独立检查，不能视为高质量达标。" };
        const status = node("p", t((page && pageLabels[state]) || labels[state] || labels.not_requested), "homeExplanationMuted");
        status.setAttribute("role", "status"); area.append(status);
        if (value?.feedback) area.append(node("p", value.feedback, "homeExplanationMuted"));
        if (value?.error) area.append(node("p", value.error, "homeExplanationMuted"));
        for (const evidence of can("can_read_evidence") ? value?.evidence || [] : []) {
          if (typeof evidence?.address === "string" && hashPattern.test(evidence.sha256 || "")) {
            const fixed = value?.handoff?.evidence?.entries?.find(entry => entry.address === evidence.address && entry.sha256 === evidence.sha256);
            if (!fixed || !hashPattern.test(value.id || "")) {
              area.append(button("查看审核证据", () => onOpen?.({ view: "preview", target: { address: evidence.address } })));
              continue;
            }
            const body = node("div", null, "homeExplanationSupplement");
            let evidenceUrl = "";
            const clearBody = () => { window.PortalPreview.dispose(body); body.replaceChildren();
              if (evidenceUrl) { URL.revokeObjectURL(evidenceUrl); urls.delete(evidenceUrl); evidenceUrl = ""; } };
            releaseEvidence.push(clearBody);
            const open = button("查看审核证据", async () => {
              if (!current(key)) return;
              open.disabled = true;
              clearBody();
              try {
                const url = `/api/home-explanation/reviews/file?id=${value.id}&sha256=${fixed.sha256}&group=evidence`;
                const maxBytes = /^(image|audio|video)\//.test(fixed.content_type || "") ? limits.video : limits.text;
                const content = await readBytes(url, maxBytes, controller.signal, fixed.sha256);
                if (!current(key) || !area.contains(open)) return;
                const type = fixed.content_type || content.type;
                const kind = /^image\//.test(type) ? "image" : /^audio\//.test(type) ? "audio" : /^video\//.test(type) ? "video"
                  : /\.md$/i.test(fixed.name) ? "markdown" : /\.json$/i.test(fixed.name) ? "json" : "code";
                const media = ["image", "audio", "video"].includes(kind);
                evidenceUrl = media ? blob(content.bytes, type) : "";
                window.PortalPreview.render({ root: body, document: { source: "review", path: fixed.name,
                  title: fixed.name.split("/").pop(), contentKind: kind, content: media ? "" : decode(content.bytes),
                  previewUrl: evidenceUrl, capabilities: { edit: false, save: false } } });
              } catch (error) {
                if (current(key) && area.contains(open) && error.name !== "AbortError") failure(body, error, () => open.click());
              } finally { if (current(key)) open.disabled = false; }
            });
            area.append(open, body);
          }
        }
        if (state === "blocked" && can("can_resume") && hashPattern.test(value?.id || "")) {
          const resume = button("恢复独立检查", async () => {
            if (!current(key)) return;
            resume.disabled = true;
            try {
              const result = await window.PortalAPI.post("/api/home-explanation/reviews/resume", { review_id: value.id }, { signal: controller.signal });
              if (current(key)) draw(result.review);
            } catch (error) { if (current(key) && error.name !== "AbortError") failure(area, error, () => resume.click()); }
            finally { if (current(key)) resume.disabled = false; }
          });
          area.append(resume);
        }
        if (["submitted", "pending"].includes(state)) {
          area.append(button("刷新审核结果", async () => {
            if (!current(key)) return;
            try {
              const result = await window.PortalAPI.get(endpoint, { signal: controller.signal, cache: "no-store" });
              if (current(key)) draw(unpack(result));
            } catch (error) {
              if (current(key) && error.name !== "AbortError") {
                draw(value); failure(area, error);
              }
            }
          }));
          if (value?.id && can("can_cancel")) area.append(button("取消审核", async () => {
            try {
              const result = await window.PortalAPI.post("/api/home-explanation/reviews/cancel", { review_id: value.id }, { signal: controller.signal });
              if (current(key)) draw(result.review);
            } catch (error) { if (current(key)) failure(area, error); }
          }));
          return;
        }
        if (quality !== "high" || !can("can_retry") || ["passed", "changes_requested", "blocked", "cancelled", "invalidated", "awaiting_author"].includes(state)) return;
        area.append(button(page ? "提交解释页独立检查" : state === "submission_failed" ? "重试视频检查" : "检查视频质量", async () => {
          if (!current(key)) return;
          area.querySelectorAll("button").forEach(el => { el.disabled = true; });
          try {
            const result = await window.PortalAPI.post(page ? "/api/home-explanation/reviews" : "/api/home-explanation/media/review", {
              address: mediaTarget.address, sha256: mediaTarget.sha256, quality,
            }, { signal: controller.signal });
            if (current(key)) draw(unpack(result));
          } catch (error) {
            if (current(key) && error.name !== "AbortError") {
              draw({ ...value, status: "submission_failed" }); failure(area, error);
            }
          }
        }));
      };
      draw(review);
    }
    async function plannedVideo(region, plan, key) {
      // Reading a plan only verifies provenance. A POST requires a button click.
      if (plan) await read(plan, limits.text, key);
      const content = node("div", null, "homeExplanationMediaState"); region.append(content);
      let busy = false, attempts = 0, mediaTarget = target;
      const qualityControl = window.PortalUIPrimitives.radioGroup({label: t("视频质量"),
        value: ["low", "medium", "high"].includes(manifest?.quality) ? manifest.quality : "medium",
        choices: [["low", t("低")], ["medium", t("中")], ["high", t("高")]],
        onChange: () => { attempts = 0; mediaTarget = target; clearTimeout(mediaTimer); void fetchState(); }});
      qualityControl.root.classList.add("homeExplanationQuality"); region.insertBefore(qualityControl.root, content);
      async function fetchState(generate = false, initial = false) {
        if (!current(key) || busy) return;
        busy = true; qualityControl.disabled = true; clearTimeout(mediaTimer);
        let quality = qualityControl.value;
        const endpoint = `/api/home-explanation/media?address=${encodeURIComponent(target.address)}&sha256=${target.sha256}&quality=${quality}`;
        content.querySelectorAll("button").forEach(el => { el.disabled = true; });
        try {
          const result = generate
            ? await window.PortalAPI.post("/api/home-explanation/media", { ...mediaTarget, quality }, { signal: controller.signal })
            : await window.PortalAPI.get(endpoint, { signal: controller.signal, cache: "no-store" });
          if (!current(key)) return;
          let media = result?.media === undefined ? result : result.media;
          // Page quality and video quality are independent. On first open,
          // discover an existing job if the preferred tier has no job at all.
          // Explicit tier changes remain exact reads and never trigger generation.
          if (initial && !media) {
            const alternatives = await Promise.all(["low", "medium", "high"].filter(value => value !== quality).map(async value => {
              const response = await window.PortalAPI.get(`/api/home-explanation/media?address=${encodeURIComponent(target.address)}&sha256=${target.sha256}&quality=${value}`, { signal: controller.signal, cache: "no-store" });
              return {quality:value, media:response?.media === undefined ? response : response.media};
            }));
            if (!current(key)) return;
            const existing = alternatives.find(value => value.media?.status === "ready") || alternatives.find(value => ["generating", "authored", "awaiting_authoring"].includes(value.media?.status));
            if (existing) { quality = existing.quality; media = existing.media; qualityControl.value = quality; }
          }
          mediaTarget = media?.result_target || mediaTarget;
          content.replaceChildren();
          if (media?.result_target) content.append(button("查看视频对应版本", () => onOpen?.({ view: "explanation", target: media.result_target })));
          if (media?.status === "ready") {
            await renderVideo(content, media, key, true);
            if (current(key)) videoReview(content, media.review, key, quality, false, mediaTarget);
          } else if (media?.status === "generating") {
            const progress = node("p", t("讲解视频正在生成，可以先探索交互和图文。"), "homeExplanationMuted"); progress.setAttribute("role", "status");
            content.append(progress);
            if (++attempts < 120) mediaTimer = setTimeout(() => { void fetchState(); }, 2500);
            else content.append(button("刷新生成状态", () => { attempts = 0; void fetchState(); }));
            content.append(button("取消生成", async () => {
              clearTimeout(mediaTimer);
              try { await window.PortalAPI.post("/api/home-explanation/media/cancel", { ...mediaTarget, quality }, { signal: controller.signal }); if (current(key)) void fetchState(); }
              catch (error) { if (current(key)) failure(content, error); }
            }));
          } else if (media?.status === "awaiting_authoring") {
            content.append(node("p", media.authoring?.error || t(media.authoring?.status === "submitted"
              ? "已将质量要求送交负责Resident修改分镜；尚未生成新视频。"
              : "分镜创作等待正式负责Resident处理，尚未生成新视频。"), "homeExplanationMuted"));
            content.append(button("刷新生成状态", () => { void fetchState(); }));
            if (media.authoring?.status === "submission_failed") content.append(button("重试分镜创作请求", () => { void fetchState(true); }));
            content.append(button("取消生成", async () => {
              try { await window.PortalAPI.post("/api/home-explanation/media/cancel", { ...target, quality }, { signal: controller.signal }); if (current(key)) void fetchState(); }
              catch (error) { if (current(key)) failure(content, error); }
            }));
          } else {
            content.append(node("p", media?.error || t(media?.status === "cancelled" ? "视频生成已取消。" : media?.status === "authored" ? "分镜已完成，可以生成讲解视频。" : "需要连续讲解时，可以生成带字幕的视频。"), "homeExplanationMuted"));
            content.append(button(media?.status === "failed" ? "重试生成视频" : "生成讲解视频", () => { void fetchState(true); }));
          }
        } catch (error) {
          if (current(key) && error.name !== "AbortError") {
            content.replaceChildren(); failure(content, error, () => fetchState(false));
          }
        } finally { busy = false; qualityControl.disabled = false; }
      }
      await fetchState(false, true);
    }
    async function load(key) {
      try {
        if (identity() !== owner) throw new Error(t("身份已切换，请重新打开解释页。"));
        const data = await read(target, limits.manifest, key);
        const value = JSON.parse(decode(data.bytes));
        if (value.schema !== "portal-explanation/v1" || !Number.isSafeInteger(value.revision) || value.revision < 1
          || typeof value.title !== "string" || !value.title.trim() || value.title.length > 300
          || ![value.video, value.video_plan, value.interaction, value.supplement].some(Boolean)) throw new Error(t("解释页内容包格式无效。"));
        manifest = value; root.replaceChildren();
        const header = node("header", null, "homeExplanationHeader");
        header.append(node("p", t("探索与理解"), "homeExplanationEyebrow"), node("h1", value.title));
        if (typeof value.summary === "string" && value.summary.trim()) header.append(node("p", value.summary.slice(0, 4000), "homeExplanationSummary"));
        root.append(header);
        const tasks = []; let index = 0;
        if (value.quality === "high") {
          const reviewArea = node("div"); header.append(reviewArea);
          tasks.push(moduleTask(reviewArea, async () => {
            const result = await window.PortalAPI.get(`/api/home-explanation/reviews?address=${encodeURIComponent(target.address)}&sha256=${target.sha256}`, { signal: controller.signal, cache: "no-store" });
            if (current(key)) videoReview(reviewArea, result?.review, key, "high", true);
          }, key));
        }
        if (value.video || value.video_plan || value.quality !== "low") {
          const region = section("视频讲解", String(++index).padStart(2, "0"));
          tasks.push(moduleTask(region, () => value.video ? renderVideo(region, value.video, key) : plannedVideo(region, value.video_plan, key), key));
        }
        if (value.interaction || value.supplement) {
          const region = section("图文解释", String(++index).padStart(2, "0"));
          tasks.push(moduleTask(region, async () => {
            // The interactive document owns the complete illustrated narrative.
            // Markdown is its static reading alternative, not a second chapter.
            if (value.interaction) {
              let panel;
              try {
                const content = await read(value.interaction, limits.interaction, key);
                if (!current(key)) return;
                panel = node("div", null, "homeExplanationDemo"); region.append(panel);
                const dispose = mountInteraction(panel, decode(content.bytes), value.interaction.title || t("图文解释"));
                disposers.add(dispose);
                return;
              } catch (error) {
                panel?.remove();
                if (!current(key) || error.name === "AbortError" || !value.supplement) throw error;
                failure(region, error);
              }
            }
            const content = await read(value.supplement, limits.text, key);
            if (!current(key)) return;
            const body = node("div", null, "homeExplanationSupplement"); region.append(body);
            disposers.add(() => window.PortalPreview.dispose(body));
            window.PortalPreview.render({ root: body, document: { source: "resource", address: value.supplement.address,
              path: value.supplement.address, title: value.title, contentKind: "markdown", content: decode(content.bytes),
              version: value.supplement.sha256, capabilities: { edit: false, save: false } },
              onPreviewRequest: request => onOpen?.({ view: "preview", target: { address: request.resourceAddress || request.address || request.path } }) });
            body.classList.add("homeExplanationSupplement");
          }, key));
        }
        if (Array.isArray(value.sources) && value.sources.length) {
          const sources = node("footer", null, "homeExplanationSources"); sources.append(node("h2", t("来源与延伸阅读")));
          for (const source of value.sources.slice(0, 40)) {
            if (!source || typeof source.label !== "string") continue;
            if (source.address && typeof source.address === "string" && /^[a-z]+_[a-f0-9]{12}(?:\/|$)/.test(source.address)) {
              sources.append(button(source.label, () => { if (current(key)) onOpen?.({ view: "preview", target: { address: source.address } }); }));
            } else if (source.url) {
              try {
                const url = new URL(source.url);
                if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) continue;
                const link = node("a", source.label); link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer"; sources.append(link);
              } catch (_) { /* Invalid source links are not navigation capabilities. */ }
            }
          }
          if (sources.childElementCount > 1) root.append(sources);
        }
        notify();
        await Promise.all(tasks);
        if (current(key)) { status = "ready"; root.scrollTop = scrollTop; notify(); }
      } catch (error) {
        if (key !== token || destroyed || !presented || error.name === "AbortError") return;
        cleanup(); root.replaceChildren(); status = "error"; failure(root, error); notify();
      }
    }
    function revalidate() {
      if (!presented || destroyed || document.hidden || !target || externalSignal?.aborted) return Promise.resolve();
      if (loading) return loading;
      invalidate(); controller = new AbortController(); const key = token;
      status = "checking"; root.append(node("p", t("正在读取解释页…"), "homeExplanationMuted")); notify();
      const promise = load(key); loading = promise;
      return promise.finally(() => { if (loading === promise) loading = null; });
    }
    const abort = () => invalidate();
    async function show(next, options = {}) {
      if (destroyed) return;
      const fixed = asset(next);
      if (target && (fixed.address !== target.address || fixed.sha256 !== target.sha256)) throw new Error(t("每个历史条目只绑定一个固定解释版本。"));
      target = fixed; requestId = options.requestId || "";
      externalSignal?.removeEventListener("abort", abort); externalSignal = options.signal;
      externalSignal?.addEventListener("abort", abort, { once: true });
      if (externalSignal?.aborted) { invalidate(); return; }
      return revalidate();
    }
    const accessChanged = () => { invalidate(); if (presented) void revalidate(); };
    const focus = () => { if (externalBlur) { externalBlur = false; if (presented) void revalidate(); } };
    const blur = () => { externalBlur = !document.hasFocus(); };
    const resume = () => { if (presented) void revalidate(); };
    const visibility = () => { if (document.hidden) invalidate(); else resume(); };
    const selection = () => {
      const selected = window.getSelection();
      if (current(token) && selected && root.contains(selected.anchorNode) && root.contains(selected.focusNode) && selected.toString().trim())
        onSelection?.({ view: "explanation", target, address: target.address, title: manifest?.title || "", text: selected.toString().slice(0, 12000) });
    };
    const events = ["portal:identity-changed", "portal:permissions-changed"];
    for (const event of events) window.addEventListener(event, accessChanged);
    window.addEventListener("focus", focus); window.addEventListener("blur", blur); window.addEventListener("pagehide", abort); window.addEventListener("pageshow", resume);
    document.addEventListener("visibilitychange", visibility);
    root.addEventListener("mouseup", selection); root.addEventListener("keyup", selection);
    return { show, snapshot, handlesReadErrors: true, hasDraft: () => false, canClose: () => true,
      setPresented(value) {
        if (destroyed || presented === !!value) return;
        presented = !!value;
        if (!presented) { invalidate(); return; }
        return revalidate();
      },
      destroy() {
        if (destroyed) return;
        destroyed = true; invalidate(); externalSignal?.removeEventListener("abort", abort);
        for (const event of events) window.removeEventListener(event, accessChanged);
        window.removeEventListener("focus", focus); window.removeEventListener("blur", blur); window.removeEventListener("pagehide", abort); window.removeEventListener("pageshow", resume);
        document.removeEventListener("visibilitychange", visibility);
        root.removeEventListener("mouseup", selection); root.removeEventListener("keyup", selection); root.classList.remove("homeExplanation");
      },
    };
  }
  window.PortalHomeExplanationView = Object.freeze({ create });
})();
