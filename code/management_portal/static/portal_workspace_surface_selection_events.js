(() => {
  "use strict";

  if (window.PortalWorkspaceSurfaceSelectionEvents) return;

  // The events a Preview selection follows: pointer and key releases and
  // selections in its roots, presses outside them, and the toolbar's own
  // presses and clicks.

  const DEPENDENCIES = Object.freeze([
    "addToChat", "hide", "roots", "scheduleShow", "toolbar",
  ]);

  function createSelectionEvents(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalWorkspaceSurfaceSelectionEvents is missing: ${missing.join(", ")}`);
    const {
      addToChat, hide, roots, scheduleShow, toolbar,
    } = dependencies;

    const handleMouseDown = event => {
      if (event.target === toolbar || toolbar.contains(event.target)) return;
      hide();
    };
    const handleMouseUp = event => scheduleShow(event);
    const handleKeyUp = event => {
      if (event.shiftKey || ["Shift", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
        scheduleShow(event);
      }
    };
    const handleSelect = event => scheduleShow(event);
    const handleOutsideMouseDown = event => {
      if (toolbar.contains(event.target) || roots.some(root => root.contains(event.target))) return;
      hide();
    };
    const handleToolbarMouseDown = event => event.preventDefault();
    const handleToolbarClick = event => {
      event.preventDefault();
      addToChat();
    };

    roots.forEach(root => {
      root.addEventListener("mousedown", handleMouseDown);
      root.addEventListener("mouseup", handleMouseUp);
      root.addEventListener("keyup", handleKeyUp);
      root.addEventListener("select", handleSelect, true);
    });
    document.addEventListener("mousedown", handleOutsideMouseDown);
    toolbar.addEventListener("mousedown", handleToolbarMouseDown);
    toolbar.addEventListener("click", handleToolbarClick);

    return {
      handleKeyUp, handleMouseDown, handleMouseUp, handleOutsideMouseDown, handleSelect, handleToolbarClick,
      handleToolbarMouseDown,
    };
  }

  window.PortalWorkspaceSurfaceSelectionEvents = Object.freeze({ createSelectionEvents, dependencies: DEPENDENCIES });
})();
