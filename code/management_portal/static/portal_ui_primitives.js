(function (global) {
  "use strict";

  if (global.PortalUIPrimitives) {
    return;
  }

  const STATUS_TONES = Object.freeze({
    active: "success",
    approved: "success",
    completed: "success",
    connected: "success",
    done: "success",
    failed: "danger",
    blocked: "danger",
    error: "danger",
    rejected: "danger",
    queued: "neutral",
    pending: "warning",
    paused: "warning",
    awaiting_approval: "warning",
    running: "running",
    processing: "running",
    syncing: "running",
    draft: "neutral",
    archived: "neutral",
  });

  const strokeIcon = (body, options = {}) => {
    const viewBox = options.viewBox || "0 0 24 24";
    const strokeWidth = options.strokeWidth || "2";
    return `<svg class="portalIcon" viewBox="${viewBox}" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="${strokeWidth}" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;
  };

  const fillIcon = (body, options = {}) => {
    const viewBox = options.viewBox || "0 0 24 24";
    return `<svg class="portalIcon" viewBox="${viewBox}" aria-hidden="true" focusable="false" fill="currentColor" stroke="none">${body}</svg>`;
  };

  /*
   * Portal icon vocabulary.
   *
   * Consumers choose a semantic name; they never own SVG geometry. Keep this
   * catalog independent of page structure so changing an icon cannot require
   * coordinated edits across business pages.
   */
  const ICONS = Object.freeze({
    search: strokeIcon('<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>'),
    semantic: strokeIcon('<path d="m12 3 1.3 4.2 4.2 1.3-4.2 1.3L12 14l-1.3-4.2-4.2-1.3 4.2-1.3Z"/><path d="m18.5 14 .7 2.3 2.3.7-2.3.7-.7 2.3-.7-2.3-2.3-.7 2.3-.7Z"/>'),
    filter: strokeIcon('<path d="M3 5h18l-7 8v5l-4 2v-7Z"/>'),
    selector: strokeIcon('<circle cx="5" cy="12" r="2"/><path d="M7 12h3c3 0 3-5 6-5h4M10 12c3 0 3 5 6 5h4"/><path d="m18 4 3 3-3 3M18 14l3 3-3 3"/>'),
    refresh: strokeIcon('<path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/>'),
    plus: strokeIcon('<path d="M12 5v14M5 12h14"/>'),
    minus: strokeIcon('<path d="M5 12h14"/>'),
    close: strokeIcon('<path d="M18 6 6 18M6 6l12 12"/>', { strokeWidth: "2.2" }),
    check: strokeIcon('<path d="m5 12 4 4L19 6"/>', { strokeWidth: "2.4" }),
    "more-horizontal": fillIcon('<circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/>'),
    "chevron-left": strokeIcon('<path d="m15 18-6-6 6-6"/>', { strokeWidth: "2.2" }),
    "chevron-right": strokeIcon('<path d="m9 18 6-6-6-6"/>', { strokeWidth: "2.2" }),
    "chevron-up": strokeIcon('<path d="m6 15 6-6 6 6"/>', { strokeWidth: "2.2" }),
    "chevron-down": strokeIcon('<path d="m6 9 6 6 6-6"/>', { strokeWidth: "2.2" }),
    "arrow-left": strokeIcon('<path d="M19 12H5m6-6-6 6 6 6"/>'),
    "arrow-right": strokeIcon('<path d="M5 12h14m-6-6 6 6-6 6"/>'),
    "arrow-up": strokeIcon('<path d="M12 19V5m-6 6 6-6 6 6"/>'),
    "arrow-down": strokeIcon('<path d="M12 5v14m6-6-6 6-6-6"/>'),
    "arrow-up-right": strokeIcon('<path d="M7 17 17 7M8 7h9v9"/>'),
    "panel-left-close": strokeIcon('<path d="M4 4h16v16H4zM9 4v16m6-5-3-3 3-3"/>'),
    "panel-right-close": strokeIcon('<path d="M4 4h16v16H4zM15 4v16m-6-5 3-3-3-3"/>'),
    "split-right": strokeIcon('<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M12 4v16M16 9l3 3-3 3"/>'),
    "fit-view": strokeIcon('<path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5"/>'),
    "auto-layout": strokeIcon('<rect x="3" y="4" width="6" height="5"/><rect x="15" y="4" width="6" height="5"/><rect x="9" y="15" width="6" height="5"/><path d="M6 9v3h12V9M12 12v3"/>'),
    pin: strokeIcon('<path d="M8 3.5h8l-1 5.75 3 3v2.25H6v-2.25l3-3-1-5.75Z"/><path d="M12 14.5v6"/>', { strokeWidth: "1.8" }),
    edit: strokeIcon('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>'),
    pen: strokeIcon('<path d="m4 20 4.5-1 10-10a2.1 2.1 0 0 0-3-3l-10 10Z"/><path d="m13.5 8 3 3M4 20h5"/>'),
    square: strokeIcon('<rect x="4" y="4" width="16" height="16" rx="1"/>'),
    type: strokeIcon('<path d="M5 5h14M12 5v14M8 19h8"/>'),
    mosaic: strokeIcon('<rect x="3" y="3" width="18" height="18" rx="1"/><path d="M9 3v18M15 3v18M3 9h18M3 15h18"/>', { strokeWidth: "1.6" }),
    trash: strokeIcon('<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v5M14 11v5"/>'),
    copy: strokeIcon('<rect x="8" y="8" width="11" height="11" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>'),
    save: strokeIcon('<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"/><path d="M17 21v-8H7v8M7 3v5h8"/>'),
    undo: strokeIcon('<path d="m9 7-5 5 5 5"/><path d="M20 17a7 7 0 0 0-7-7H4"/>'),
    redo: strokeIcon('<path d="m15 7 5 5-5 5"/><path d="M4 17a7 7 0 0 1 7-7h9"/>'),
    "word-wrap": strokeIcon('<path d="M4 6h13a3 3 0 0 1 0 6H9"/><path d="m12 9-3 3 3 3M4 18h7"/>'),
    download: strokeIcon('<path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>'),
    upload: strokeIcon('<path d="M12 16V4M7 9l5-5 5 5M5 20h14"/>'),
    "external-link": strokeIcon('<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 13v7H4V6h7"/>'),
    "file-open-new": strokeIcon('<path d="M7 3h8l4 4v14H7z"/><path d="M14 3v5h5M3 12h9M8 8l4 4-4 4"/>'),
    file: strokeIcon('<path d="M6 3h8l4 4v14H6Z"/><path d="M14 3v5h5"/>'),
    "file-text": strokeIcon('<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 12h7M9 16h7"/>'),
    "file-plus": strokeIcon('<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M12 11v6M9 14h6"/>'),
    folder: strokeIcon('<path d="M3 6h6l2 2h10v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>'),
    "folder-plus": strokeIcon('<path d="M3 6h6l2 2h10v11H3z"/><path d="M12 11v5M9.5 13.5h5"/>'),
    crown: strokeIcon('<path d="m3 6 4 4 5-7 5 7 4-4-2 12H5L3 6ZM5 21h14"/>'),
    archive: strokeIcon('<path d="M4 7h16v13H4z"/><path d="M3 3h18v4H3zM9 11h6"/>'),
    book: strokeIcon('<path d="M5 4h12a2 2 0 0 1 2 2v14H7a2 2 0 0 1-2-2Z"/><path d="M7 20a2 2 0 0 1 0-4h12M9 8h6M9 11h6"/>'),
    list: strokeIcon('<path d="M4 5h16M4 12h16M4 19h16"/><path d="M7 5h.01M7 12h.01M7 19h.01"/>'),
    code: strokeIcon('<path d="m8 8-4 4 4 4M16 8l4 4-4 4M14 4l-4 16"/>'),
    settings: strokeIcon('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-2.9 1.2v.1h-4v-.1a1.7 1.7 0 0 0-2.9-1.2l-.1.1L4.2 17l.1-.1A1.7 1.7 0 0 0 3.1 14H3v-4h.1A1.7 1.7 0 0 0 4.3 7.1L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 10 3.1V3h4v.1a1.7 1.7 0 0 0 2.9 1.2l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0 1.2 2.9h.1v4h-.1a1.7 1.7 0 0 0-1.5 1Z"/>'),
    network: strokeIcon('<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="8" r="2.5"/><circle cx="11" cy="18" r="2.5"/><path d="m8.3 6.5 7.2 1M7.4 8l2.5 7.5m6.5-5.2-3.8 5.5"/>'),
    user: strokeIcon('<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>'),
    users: strokeIcon('<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>'),
    chat: strokeIcon('<path d="M7 8h10M7 12h7"/><path d="M5 4h14a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H9l-4 3v-3a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z"/>'),
    "chat-room": strokeIcon('<path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4Z"/><path d="M8 8h8M8 12h5"/>'),
    octopus: strokeIcon('<path d="M5.5 14v-3a6.5 6.5 0 0 1 13 0v3M5.5 14c0 3.5-1.5 5-3 5.5M9.75 14.5c0 3-.5 5-2 6M14.25 14.5c0 3 .5 5 2 6M18.5 14c0 3.5 1.5 5 3 5.5"/><circle cx="9.5" cy="10.5" r="1" fill="currentColor"/><circle cx="14.5" cy="10.5" r="1" fill="currentColor"/>'),
    "chat-check": strokeIcon('<path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4Z"/><path d="m9 12 2 2 4-5"/>'),
    send: strokeIcon('<path d="m4 4 16 8-16 8 3-8z"/><path d="M7 12h13"/>'),
    "send-forward": strokeIcon('<path d="M20 5 9 16"/><path d="M20 5 13 21l-4-5-6-2 17-9Z"/>'),
    star: strokeIcon('<path d="m12 3 2.7 5.5 6 .9-4.4 4.2 1.1 6-5.4-2.8-5.4 2.8 1.1-6-4.4-4.2 6-.9Z"/>'),
    "star-filled": fillIcon('<path d="m12 2.8 2.85 5.77 6.37.93-4.61 4.49 1.09 6.34L12 17.34l-5.7 2.99 1.09-6.34L2.78 9.5l6.37-.93Z"/>'),
    bot: strokeIcon('<rect x="4" y="8" width="16" height="12" rx="2"/><path d="M12 8V4H8M2 14h2M20 14h2M9 13v2M15 13v2"/>'),
    server: strokeIcon('<rect x="4" y="4" width="16" height="6" rx="2"/><rect x="4" y="14" width="16" height="6" rx="2"/><path d="M8 7h.01M8 17h.01M12 7h4M12 17h4"/>'),
    history: strokeIcon('<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/>'),
    window: strokeIcon('<rect x="4" y="5" width="16" height="14" rx="2"/><path d="M4 9h16M8 7h.01M11 7h.01"/>'),
    terminal: strokeIcon('<path d="m5 7 4 4-4 4M11 17h8"/>'),
    "eye": strokeIcon('<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>'),
    "eye-off": strokeIcon('<path d="m3 3 18 18"/><path d="M10.6 10.6a3 3 0 0 0 3.8 3.8"/><path d="M9.9 5.2A10 10 0 0 1 12 5c6.5 0 10 7 10 7a18 18 0 0 1-2.8 3.8M6.6 6.7C3.7 8.7 2 12 2 12s3.5 7 10 7a9.7 9.7 0 0 0 4.1-.9"/>'),
    lock: strokeIcon('<rect x="4" y="10" width="16" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v2"/>'),
    "shield-check": strokeIcon('<path d="M12 3 4.5 6v5.5c0 4.4 3 7.8 7.5 9.5 4.5-1.7 7.5-5.1 7.5-9.5V6Z"/><path d="m8.5 12 2.2 2.2 4.8-5"/>'),
    "alert-circle": strokeIcon('<circle cx="12" cy="12" r="9"/><path d="M12 7.75v5.5M12 16.5h.01"/>'),
    "alert-triangle": strokeIcon('<path d="M12 3 2.7 19h18.6L12 3Z"/><path d="M12 9v4M12 16.5h.01"/>'),
    inbox: strokeIcon('<path d="M4 6.5h16v11H4z"/><path d="M4 13h4l2 2h4l2-2h4"/>', { strokeWidth: "1.8" }),
    repository: strokeIcon('<path d="M5 3.5h10.5a2 2 0 0 1 2 2V20H7a2 2 0 0 1-2-2Z"/><path d="M7 3.5v16M9.5 7h5"/>'),
    branch: strokeIcon('<circle cx="6" cy="5" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="6" cy="19" r="2"/><path d="M6 7v10M8 12h4a6 6 0 0 0 6-4"/>'),
    symlink: strokeIcon('<path d="M10 13a5 5 0 0 0 7.5.5l2-2a5 5 0 0 0-7-7l-1.1 1.1"/><path d="M14 11a5 5 0 0 0-7.5-.5l-2 2a5 5 0 0 0 7 7l1.1-1.1"/>'),
    changes: strokeIcon('<path d="M7 3v14M17 7v14"/><circle cx="7" cy="19" r="2"/><circle cx="17" cy="5" r="2"/>'),
    "repository-sparkles": strokeIcon('<path d="M5 3.5h10.5a2 2 0 0 1 2 2V20H7a2 2 0 0 1-2-2Z"/><path d="M7 3.5v16M10 8h5M10 12h5"/><path d="m19 2 .7 2.3L22 5l-2.3.7L19 8l-.7-2.3L16 5l2.3-.7Z"/>'),
    "keyboard": strokeIcon('<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M7 10h.01M11 10h.01M15 10h.01M18 10h.01M7 14h.01M17 14h.01M10 14h4"/>'),
    "roadmap": strokeIcon('<path d="M5 4h14v16H5z"/><path d="M8 8h8M8 12h8M8 16h5"/>'),
    "channels": strokeIcon('<circle cx="5" cy="12" r="2"/><circle cx="19" cy="7" r="2"/><circle cx="19" cy="17" r="2"/><path d="m7 11 10-3M7 13l10 3"/>'),
    mail: strokeIcon('<path d="M4 5h16v14H4z"/><path d="m4 7 8 6 8-6"/>'),
    receipt: strokeIcon('<path d="M7 3h10v18l-2-1.5-3 1.5-3-1.5L7 21Z"/><path d="M10 8h4M10 12h4"/>'),
    fork: strokeIcon('<circle cx="6" cy="5" r="2"/><circle cx="18" cy="8" r="2"/><circle cx="18" cy="18" r="2"/><path d="M8 5h3a4 4 0 0 1 4 4v7M15 9h1"/>'),
    handoff: strokeIcon('<path d="M4 7h11M12 4l3 3-3 3M20 17H9M12 14l-3 3 3 3"/>'),
    trigger: strokeIcon('<circle cx="5" cy="12" r="2"/><circle cx="19" cy="12" r="2"/><path d="M7 12h8M12 8l4 4-4 4"/><path d="M9 5h6v4H9z"/>'),
    role: strokeIcon('<path d="M5 13c2-5 6-7 11-6-1 2-1 4 1 6-3 4-7 5-12 2Z"/><path d="m15 9 4-2-2 4M8 15l-2 4M11 15l1 4"/>'),
    event: strokeIcon('<path d="M12 3 21 12 12 21 3 12Z"/><path d="M12 7v5l3 2"/>'),
    reducer: strokeIcon('<path d="M3 6h4l5 6 5-6h4M3 18h4l5-6 5 6h4"/><circle cx="12" cy="12" r="2.2"/>'),
    effect: strokeIcon('<path d="M4 5h11v9H8l-4 4Z"/><path d="M12 18h8M17 14l4 4-4 4"/>'),
    stop: fillIcon('<rect x="6" y="6" width="12" height="12" rx="1.5"/>'),
    play: fillIcon('<path d="M7 4.5v15l12-7.5Z"/>'),
    pulse: strokeIcon('<path d="M3 12h4l2.5-6 4.5 12 2.5-6H21"/>'),
    compact: strokeIcon('<path d="m9 3 3 3 3-3M12 6v5m-3 10 3-3 3 3M12 18v-5M3 9l3 3-3 3M6 12h5m10-3-3 3 3 3M18 12h-5"/>'),
    eraser: strokeIcon('<path d="m7 21-4.3-4.3a2 2 0 0 1 0-2.8l9.6-9.6a2 2 0 0 1 2.8 0l4.3 4.3a2 2 0 0 1 0 2.8L9.8 21Z"/><path d="M22 21H7M5 13l6 6"/>'),
    idea: strokeIcon('<path d="M9 18h6M10 22h4M8.5 14.5A6 6 0 1 1 15.5 14.5c-.9.7-1.5 1.6-1.5 2.5h-4c0-.9-.6-1.8-1.5-2.5Z"/>'),
    plan: strokeIcon('<path d="M5 4h14v5H5zM5 15h6v5H5zM15 15h4v5h-4zM12 9v3m-4 0h9M8 12v3m9-3v3"/>'),
    seed: strokeIcon('<path d="M12 21v-9M8 21h8"/><path d="M12 12C12 8 9.5 5 4 5c0 4.5 3 7 8 7Z"/><path d="M12 14.5c0-3.5 2.5-6 7.5-6 0 4-2.5 6-7.5 6Z"/>'),
    verify: strokeIcon('<path d="M9 11l2 2 4-5"/><circle cx="12" cy="13" r="9"/>'),
    component: strokeIcon('<path d="M5 5h14v14H5zM9 9h6M9 13h6"/>'),
    "theme-auto": strokeIcon('<path d="M12 3a9 9 0 1 0 9 9M12 3v18M12 7a5 5 0 0 1 0 10"/>'),
    "theme-light": strokeIcon('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2m-7.1-17.1 1.4 1.4m11.4 11.4 1.4 1.4M2 12h2M20 12h2M6.3 17.7l-1.4 1.4M19.1 4.9l-1.4 1.4"/>'),
    "theme-dark": strokeIcon('<path d="M20.5 14.5A8.5 8.5 0 0 1 9.5 3.5 7 7 0 1 0 20.5 14.5Z"/>'),
    "theme-fire": strokeIcon('<path d="M12 22c4 0 7-2.7 7-6.5 0-2.5-1.3-4.4-3.2-6.3-.9 2.1-2.3 3.1-4.1 3.1 1.4-3.4.4-6.8-2.7-10.3C8.5 6.1 5 8.8 5 13.8 5 18.6 8.1 22 12 22Z"/><path d="M12 22c1.8 0 3-1.2 3-2.8 0-1.2-.7-2.1-1.7-3.1-.3 1.1-1.1 1.7-2.2 1.7.7-1.6.2-3.2-1.1-4.8-.4 2.1-1.5 3.5-1.5 5.2C8.5 20.4 10 22 12 22Z"/>'),
    "theme-deepsea": strokeIcon('<path d="M7.2 11.2c0-3.7 1.9-6.2 4.8-6.2s4.8 2.5 4.8 6.2v2.1H7.2Z"/><circle cx="10.4" cy="9.8" r=".65" fill="currentColor" stroke="none"/><circle cx="13.6" cy="9.8" r=".65" fill="currentColor" stroke="none"/><path d="M7.7 13.1c-2 1-2.4 3.1-.8 4.5M9.7 13.1c-.9 1.6-.7 3.4.6 5.1M12 13.1c-.4 1.8 0 3.6 0 5.4M14.3 13.1c.9 1.6.7 3.4-.6 5.1M16.3 13.1c2 1 2.4 3.1.8 4.5"/>', { strokeWidth: "1.9" }),
    "theme-forest": strokeIcon('<path d="M5 20c8-1 13-6 14-15-8 1-13 6-14 15Z"/><path d="M6 19c4-5 7-8 12-13M9 15c-1.8-.2-3.2-.9-4.2-2.1M13 11c-.4-1.9-1.1-3.4-2.3-4.5"/>'),
    "theme-scifi": strokeIcon('<path d="M12 3 20 8v8l-8 5-8-5V8l8-5ZM12 8v8M8.5 10.2 12 8l3.5 2.2M8.5 13.8 12 16l3.5-2.2"/><circle cx="12" cy="12" r="1.4"/>'),
    "theme-moonwalker": strokeIcon('<circle cx="12" cy="12" r="7.5"/><path d="M15.6 5.4a7.5 7.5 0 0 0 0 13.2M3.6 15.1c3.9 3.3 10.5 4.1 16.8 1.7"/><circle cx="18.9" cy="7.2" r=".8" fill="currentColor" stroke="none"/>'),
    cursor: fillIcon('<path d="M3 2.5 23.5 20l-9.2 1.4-4.8 8.1L3 2.5Z"/>', { viewBox: "0 0 28 32" }),
    "status-dot": fillIcon('<circle cx="12" cy="12" r="8"/>'),
  });

  const ICON_ALIASES = Object.freeze({
    add: "plus",
    board: "list",
    map: "network",
    graph: "network",
    explorer: "folder",
    favorite: "star",
    background: "server",
    "actual-prompt": "code",
    "chat-context": "chat-check",
    "auto-commit": "repository-sparkles",
    "circle-plus": "plus",
  });

  const ASYNC_STATE_DEFAULTS = Object.freeze({
    loading: Object.freeze({
      title: window.PortalI18n.t("正在加载"),
      message: window.PortalI18n.t("请稍候。"),
      icon: "loading",
    }),
    empty: Object.freeze({
      title: window.PortalI18n.t("暂无内容"),
      message: window.PortalI18n.t("这里还没有可以显示的内容。"),
      icon: "inbox",
    }),
    error: Object.freeze({
      title: window.PortalI18n.t("加载失败"),
      message: window.PortalI18n.t("无法取得内容，请重试。"),
      icon: "alert-triangle",
    }),
  });

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // An element given as an Element, an id or a selector.
  function elementOf(ref) {
    if (!ref) return null;
    if (typeof ref === "string") return document.getElementById(ref) || document.querySelector(ref);
    return ref instanceof Element ? ref : null;
  }

  // A field that should hold an object; a missing or malformed value reads as {}.
  function objectOf(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  // A static asset URL with the page's release tag, so a lazily loaded asset matches the page.
  function staticAssetUrl(path) {
    const tagged = [...document.scripts].find(script => script.src.includes("portal_v="));
    const version = tagged ? new URL(tagged.src).searchParams.get("portal_v") : "";
    return version ? `${path}?portal_v=${encodeURIComponent(version)}` : path;
  }

  // Lazily loaded scripts and stylesheets by file path. Every lazy loader of a
  // page goes through loadAsset, so a file gets one tag and one promise.
  const assetLoads = new Map();

  function assetPath(url) {
    return new URL(url, location.href).pathname;
  }

  // Loads a script or stylesheet once, with the page's release tag. A script
  // whose ready() holds counts as loaded. Without ready(), so does a file the
  // page's own markup declares: those tags ran before any lazy loader asks.
  // With ready() failing, the declared copy did not arrive and is loaded again.
  function loadAsset(path, { ready } = {}) {
    if (ready?.()) return Promise.resolve();
    const key = assetPath(path);
    if (assetLoads.has(key)) return assetLoads.get(key);
    const css = key.endsWith(".css");
    const declared = !ready && (css
      ? [...document.querySelectorAll('link[rel="stylesheet"][href]')].some(link => assetPath(link.href) === key)
      : [...document.scripts].some(script => script.src && assetPath(script.src) === key));
    if (declared) return Promise.resolve();
    const element = document.createElement(css ? "link" : "script");
    const promise = new Promise((resolve, reject) => {
      element.addEventListener("load", () => resolve(), { once: true });
      element.addEventListener("error", () => {
        element.remove();
        assetLoads.delete(key);
        reject(new Error(`无法加载 ${key}`));
      }, { once: true });
    });
    if (css) {
      element.rel = "stylesheet";
      element.href = staticAssetUrl(key);
    } else {
      // async=false keeps execution in request order while downloads overlap.
      element.src = staticAssetUrl(key);
      element.async = false;
    }
    assetLoads.set(key, promise);
    document.head.append(element);
    return promise;
  }

  function toneForStatus(status, fallback = "neutral") {
    const key = String(status || "").trim().toLowerCase().replace(/[\s-]+/g, "_");
    return STATUS_TONES[key] || String(fallback || "neutral");
  }

  function canonicalIconName(name) {
    const key = String(name || "").trim().toLowerCase();
    return ICON_ALIASES[key] || key;
  }

  function iconHtml(name, options = {}) {
    const key = canonicalIconName(name);
    const html = ICONS[key] || "";
    if (!html || !options.className) return html;
    const classes = String(options.className)
      .split(/\s+/)
      .map(value => value.replace(/[^a-zA-Z0-9_-]/g, ""))
      .filter(Boolean)
      .join(" ");
    return classes
      ? html.replace('class="portalIcon"', `class="portalIcon ${classes}"`)
      : html;
  }

  function createIcon(name, className = "") {
    const template = document.createElement("template");
    template.innerHTML = iconHtml(name, { className });
    const svg = template.content.firstElementChild;
    return svg;
  }

  function mountIcon(target) {
    if (!(target instanceof Element)) return null;
    const name = canonicalIconName(target.dataset.portalIcon);
    const iconClass = String(target.dataset.portalIconClass || "");
    const current = target.querySelector(":scope > [data-portal-icon-node]");
    if (!name || !ICONS[name]) {
      current?.remove();
      return null;
    }
    if (
      current?.dataset.portalIconNode === name
      && current.dataset.portalIconClasses === iconClass
    ) return current;
    current?.remove();
    const svg = createIcon(name, iconClass);
    if (!svg) return null;
    svg.dataset.portalIconNode = name;
    svg.dataset.portalIconClasses = iconClass;
    target.prepend(svg);
    return svg;
  }

  function mountIcons(root = document) {
    const targets = [];
    if (root instanceof Element && root.matches("[data-portal-icon]")) targets.push(root);
    root.querySelectorAll?.("[data-portal-icon]").forEach(target => targets.push(target));
    return targets.map(mountIcon).filter(Boolean);
  }

  function statusHtml(status, options = {}) {
    const label = String(options.label ?? status ?? "").trim();
    const tone = String(options.tone || toneForStatus(status, options.fallbackTone));
    return window.PortalI18n.html`<span class="portalStatus${options.className ? ` ${escapeHtml(options.className)}` : ""}" data-tone="${escapeHtml(tone)}"${options.title ? ` title="${escapeHtml(options.title)}"` : ""}>${escapeHtml(label)}</span>`;
  }

  function buttonHtml(options = {}) {
    const tag = options.href ? "a" : "button";
    const variant = String(options.variant || "").trim();
    const classes = [
      "portalButton",
      variant ? `is-${variant}` : "",
      options.compact ? "is-compact" : "",
      options.iconOnly ? "is-icon" : "",
      options.className || "",
    ].filter(Boolean).join(" ");
    const attributes = [
      `class="${escapeHtml(classes)}"`,
      options.id ? `id="${escapeHtml(options.id)}"` : "",
      options.href ? `href="${escapeHtml(options.href)}"` : `type="${escapeHtml(options.type || "button")}"`,
      options.disabled ? "disabled" : "",
      options.ariaLabel ? `aria-label="${escapeHtml(options.ariaLabel)}"` : "",
      options.title ? `title="${escapeHtml(options.title)}"` : "",
    ].filter(Boolean).join(" ");
    const content = options.html === undefined ? escapeHtml(options.label || "") : String(options.html);
    return `<${tag} ${attributes}>${content}</${tag}>`;
  }

  function fieldHtml(options = {}) {
    const id = String(options.id || "").trim();
    const tag = options.multiline ? "textarea" : options.options ? "select" : "input";
    const label = String(options.label || "").trim();
    const inputAttributes = [
      id ? `id="${escapeHtml(id)}"` : "",
      options.name ? `name="${escapeHtml(options.name)}"` : "",
      tag === "input" ? `type="${escapeHtml(options.type || "text")}"` : "",
      options.placeholder ? `placeholder="${escapeHtml(options.placeholder)}"` : "",
      options.required ? "required" : "",
      options.disabled ? "disabled" : "",
      options.value !== undefined && tag === "input" ? `value="${escapeHtml(options.value)}"` : "",
    ].filter(Boolean).join(" ");
    let control = "";
    if (tag === "select") {
      control = window.PortalI18n.html`<select ${inputAttributes}>${(options.options || []).map(option => {
        const value = typeof option === "object" ? option.value : option;
        const text = typeof option === "object" ? option.label : option;
        return window.PortalI18n.html`<option value="${escapeHtml(value)}"${String(value) === String(options.value) ? " selected" : ""}>${escapeHtml(text)}</option>`;
      }).join("")}</select>`;
    } else if (tag === "textarea") {
      control = window.PortalI18n.html`<textarea ${inputAttributes}>${escapeHtml(options.value || "")}</textarea>`;
    } else {
      control = window.PortalI18n.html`<input ${inputAttributes} />`;
    }
    return window.PortalI18n.html`<label class="portalField"${id ? ` for="${escapeHtml(id)}"` : ""}>
      ${label ? window.PortalI18n.html`<span>${escapeHtml(label)}</span>` : ""}
      ${control}
      ${options.hint ? window.PortalI18n.html`<small class="portalFieldHint">${escapeHtml(options.hint)}</small>` : ""}
      ${options.error ? window.PortalI18n.html`<small class="portalFieldError">${escapeHtml(options.error)}</small>` : ""}
    </label>`;
  }

  function asyncStateHtml(state = {}) {
    const type = ["loading", "empty", "error"].includes(String(state.state))
      ? String(state.state)
      : "empty";
    const defaults = ASYNC_STATE_DEFAULTS[type];
    const density = ["compact", "inline"].includes(String(state.density))
      ? String(state.density)
      : "panel";
    const classes = [
      "portalAsyncState",
      density !== "panel" ? `is-${density}` : "",
      state.className || "",
    ].filter(Boolean).join(" ");
    const ownsTitle = Object.prototype.hasOwnProperty.call(state, "title");
    const ownsMessage = Object.prototype.hasOwnProperty.call(state, "message");
    const title = ownsTitle ? String(state.title || "") : defaults.title;
    const message = ownsMessage ? String(state.message || "") : defaults.message;
    const icon = state.icon === undefined ? defaults.icon : state.icon;
    const iconKey = String(icon || "");
    const iconContent = iconKey === "loading"
      ? ""
      : iconHtml(iconKey) || escapeHtml(iconKey);
    const iconMarkup = icon === false
      ? ""
      : window.PortalI18n.html`<span class="portalAsyncStateIcon" aria-hidden="true">${iconContent}</span>`;
    const actionHtml = state.actionLabel
      ? buttonHtml({
        label: state.actionLabel,
        variant: state.actionVariant || "primary",
        compact: true,
        className: "portalAsyncStateAction",
      })
      : "";
    const actionsHtml = state.actionsHtml || actionHtml;
    return window.PortalI18n.html`<div class="${escapeHtml(classes)}"${state.id ? ` id="${escapeHtml(state.id)}"` : ""}${state.hidden ? " hidden" : ""} data-state="${type}" data-density="${density}" role="${type === "error" ? "alert" : "status"}">
      ${iconMarkup}
      ${title ? window.PortalI18n.html`<strong class="portalAsyncStateTitle">${escapeHtml(title)}</strong>` : ""}
      ${message ? window.PortalI18n.html`<span class="portalAsyncStateMessage">${escapeHtml(message)}</span>` : ""}
      ${actionsHtml ? window.PortalI18n.html`<div class="portalAsyncStateActions">${actionsHtml}</div>` : ""}
    </div>`;
  }

  function renderAsyncState(rootRef, state = {}) {
    const root = typeof rootRef === "string"
      ? document.getElementById(rootRef) || document.querySelector(rootRef)
      : rootRef;
    if (!root) return null;
    root.innerHTML = asyncStateHtml({
      ...state,
      actionLabel: typeof state.onRetry === "function"
        ? state.actionLabel || window.PortalI18n.t("重试")
        : "",
    });
    const action = root.querySelector(".portalAsyncStateAction");
    if (typeof state.onRetry === "function") {
      action?.addEventListener("click", event => state.onRetry(event));
    }
    return root.firstElementChild;
  }

  function randomUUID() {
    // getRandomValues is available on HTTP LAN origins as well as HTTPS.
    // Keep UUID entropy and format without relying on secure-context-only APIs.
    const bytes = global.crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }

  // All document-wide insertion discovery belongs here. Component callbacks
  // receive matching elements, never a subtree that they must scan again.
  const enhancers = new Map();
  const initialEnhancers = new Set();
  const insertedRoots = new Set();
  const removedRoots = new Set();
  const changedTargets = new Map();
  const contentTargets = new Set();
  let enhancementObserver = null;
  let enhancementQueued = false;

  function outerRoots(roots, connected) {
    return [...roots].filter(node => {
      if (node.isConnected !== connected || node.ownerDocument !== document) return false;
      for (let parent = node.parentElement; parent; parent = parent.parentElement) {
        if (roots.has(parent)) return false;
      }
      return true;
    });
  }

  function invokeEnhancer(entry, node, removing = false) {
    if (enhancers.get(entry.id) !== entry) return;
    try {
      if (removing) entry.remove?.(node);
      else {
        entry.seen.add(node);
        entry.enhance(node);
      }
    } catch (error) {
      console.error(`Portal enhancer ${entry.id} failed`, error);
    }
  }

  function flushEnhancements() {
    enhancementQueued = false;
    const entries = [...enhancers.values()];
    const initial = [...initialEnhancers].filter(entry => enhancers.get(entry.id) === entry);
    const added = outerRoots(insertedRoots, true);
    const removed = outerRoots(removedRoots, false);
    const attributes = new Map(changedTargets);
    const content = [...contentTargets];
    insertedRoots.clear();
    removedRoots.clear();
    changedTargets.clear();
    contentTargets.clear();
    initialEnhancers.clear();
    if (!entries.length) return;

    const pending = new Map(entries.map(entry => [entry, new Set()]));
    const discover = (roots, candidates, visit) => {
      if (!candidates.length) return;
      const selector = candidates.map(entry => entry.selector).join(",");
      for (const root of roots) {
        const nodes = [...(root.matches(selector) ? [root] : []), ...root.querySelectorAll(selector)];
        for (const node of nodes) {
          for (const entry of candidates) if (node.matches(entry.selector)) visit(entry, node);
        }
      }
    };
    discover(removed, entries.filter(entry => entry.remove), (entry, node) => invokeEnhancer(entry, node, true));
    discover([document.documentElement], initial, (entry, node) => pending.get(entry).add(node));
    discover(added, entries, (entry, node) => pending.get(entry).add(node));
    for (const [node, names] of attributes) {
      if (!node.isConnected) continue;
      for (const entry of entries) {
        if (entry.attributes.some(name => names.has(name)) && (entry.seen.has(node) || node.matches(entry.selector))) {
          pending.get(entry).add(node);
        }
      }
    }
    for (const target of content) {
      if (!target.isConnected) continue;
      for (const entry of entries) {
        if (!entry.content) continue;
        const node = target.closest(entry.selector);
        if (node) pending.get(entry).add(node);
      }
    }
    for (const [entry, nodes] of pending) {
      for (const node of nodes) if (node.isConnected) invokeEnhancer(entry, node);
    }
  }

  function queueEnhancements() {
    if (enhancementQueued) return;
    enhancementQueued = true;
    queueMicrotask(flushEnhancements);
  }

  function observeEnhancements() {
    if (!document.documentElement || !enhancers.size) return;
    if (!enhancementObserver) enhancementObserver = new MutationObserver(records => {
      for (const record of records) {
        if (record.type === "attributes") {
          if (!changedTargets.has(record.target)) changedTargets.set(record.target, new Set());
          changedTargets.get(record.target).add(record.attributeName);
        } else if (record.type === "characterData") {
          if (record.target.parentElement) contentTargets.add(record.target.parentElement);
        } else {
          record.addedNodes.forEach(node => { if (node instanceof Element) insertedRoots.add(node); });
          record.removedNodes.forEach(node => { if (node instanceof Element) removedRoots.add(node); });
          contentTargets.add(record.target);
        }
      }
      queueEnhancements();
    });
    const entries = [...enhancers.values()];
    const attributeFilter = [...new Set(entries.flatMap(entry => entry.attributes))];
    enhancementObserver.observe(document.documentElement, {
      childList: true, subtree: true,
      characterData: entries.some(entry => entry.content),
      ...(attributeFilter.length ? { attributes: true, attributeFilter } : { attributes: false }),
    });
  }

  function registerEnhancer({ id, selector, enhance, remove, attributes = [], content = false }) {
    if (!id || !selector || typeof enhance !== "function") throw new TypeError("An enhancer requires id, selector and enhance.");
    if (enhancers.has(id)) throw new Error(`Portal enhancer already registered: ${id}`);
    document.documentElement.matches(selector); // Validate before registering.
    const entry = { id, selector, enhance, remove, attributes, content, seen: new WeakSet() };
    enhancers.set(id, entry);
    observeEnhancements();
    initialEnhancers.add(entry);
    queueEnhancements();
    return () => {
      if (enhancers.get(id) !== entry) return;
      enhancers.delete(id);
      initialEnhancers.delete(entry);
      if (enhancers.size) observeEnhancements();
      else {
        enhancementObserver?.disconnect();
        enhancementObserver = null;
        insertedRoots.clear();
        removedRoots.clear();
        changedTargets.clear();
        contentTargets.clear();
      }
    };
  }

  const api = Object.freeze({
    ICONS,
    ICON_ALIASES,
    STATUS_TONES,
    asyncStateHtml,
    buttonHtml,
    escapeHtml,
    createIcon,
    elementOf,
    fieldHtml,
    iconHtml,
    canonicalIconName,
    loadAsset,
    mountIcon,
    mountIcons,
    objectOf,
    randomUUID,
    registerEnhancer,
    renderAsyncState,
    staticAssetUrl,
    statusHtml,
    toneForStatus,
  });
  global.PortalUIPrimitives = api;

  const initializeIcons = () => {
    registerEnhancer({
      id: "icons", selector: "[data-portal-icon]", enhance: mountIcon,
      attributes: ["data-portal-icon", "data-portal-icon-class"],
    });
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeIcons, { once: true });
  } else {
    initializeIcons();
  }
})(window);
