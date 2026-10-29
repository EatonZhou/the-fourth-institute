(() => {
  "use strict";

  if (window.PortalArticleWorkspacePreferences) return;

  // Editor preferences (bracket closing, cursor, font and tab size) and word
  // wrap: their controls, applying them to the editor, keeping them.
  const DEPENDENCIES = Object.freeze([
    "$", "activeEditorModel", "captureActiveDocument", "compileSettingValue", "EDITOR_PREFERENCE_DEFAULTS",
    "EDITOR_PREFERENCE_KEY", "renderEditorLayers", "showToast", "state", "syncEditorScroll",
    "WORD_WRAP_STORAGE_KEY",
  ]);

  function createPreferences(dependencies) {
    const missing = DEPENDENCIES.filter(name => dependencies?.[name] === undefined);
    if (missing.length) throw new Error(`PortalArticleWorkspacePreferences is missing: ${missing.join(", ")}`);
    const {
      $, activeEditorModel, captureActiveDocument, compileSettingValue, EDITOR_PREFERENCE_DEFAULTS,
      EDITOR_PREFERENCE_KEY, renderEditorLayers, showToast, state, syncEditorScroll, WORD_WRAP_STORAGE_KEY,
    } = dependencies;

    function normalizedEditorPreferences(value = {}) {
      const fontSize = [11, 12, 13, 14, 16].includes(Number(value.fontSize))
        ? Number(value.fontSize)
        : EDITOR_PREFERENCE_DEFAULTS.fontSize;
      const tabSize = [2, 4, 8].includes(Number(value.tabSize))
        ? Number(value.tabSize)
        : EDITOR_PREFERENCE_DEFAULTS.tabSize;
      return {
        autoCloseBrackets: typeof value.autoCloseBrackets === "boolean"
          ? value.autoCloseBrackets
          : EDITOR_PREFERENCE_DEFAULTS.autoCloseBrackets,
        solidCursor: typeof value.solidCursor === "boolean"
          ? value.solidCursor
          : EDITOR_PREFERENCE_DEFAULTS.solidCursor,
        fontSize,
        tabSize,
      };
    }

    function persistEditorPreferences() {
      window.PortalState.storage.writeJson(EDITOR_PREFERENCE_KEY, state.editorPreferences);
      window.PortalState.storage.writeText(WORD_WRAP_STORAGE_KEY, state.wordWrap ? "1" : "0");
    }

    function renderEditorPreferenceControls() {
      if (!$("articleEditorAutoClose")) return;
      $("articleEditorAutoClose").checked = state.editorPreferences.autoCloseBrackets;
      $("articleEditorSolidCursor").checked = state.editorPreferences.solidCursor;
      $("articleEditorCodeCheck").checked = Boolean(compileSettingValue("syntax_check"));
      $("articleEditorCodeCheck").disabled = !state.selectedId;
      $("articleEditorWordWrap").checked = state.wordWrap;
      $("articleEditorFontSize").value = String(state.editorPreferences.fontSize);
      $("articleEditorTabSize").value = String(state.editorPreferences.tabSize);
    }

    function applyEditorPreferences() {
      const editorPane = document.querySelector(".articleEditorPane");
      editorPane.style.setProperty("--article-editor-font-size", `${state.editorPreferences.fontSize}px`);
      editorPane.style.setProperty("--article-editor-tab-size", String(state.editorPreferences.tabSize));
      $("articleCodeEditor").classList.toggle("solidCursor", state.editorPreferences.solidCursor);
      state.editor?.updateOptions?.({
        fontSize: state.editorPreferences.fontSize,
        lineHeight: Math.round(state.editorPreferences.fontSize * 1.68),
        tabSize: state.editorPreferences.tabSize,
        cursorBlinking: state.editorPreferences.solidCursor ? "solid" : "blink",
        autoClosingBrackets: state.editorPreferences.autoCloseBrackets ? "languageDefined" : "never",
        autoClosingQuotes: state.editorPreferences.autoCloseBrackets ? "languageDefined" : "never",
      });
      activeEditorModel()?.updateOptions?.({
        tabSize: state.editorPreferences.tabSize,
        insertSpaces: true,
      });
      renderEditorPreferenceControls();
      if (state.openFile) renderEditorLayers();
    }

    function updateEditorPreference(key, value) {
      state.editorPreferences = normalizedEditorPreferences({
        ...state.editorPreferences,
        [key]: value,
      });
      persistEditorPreferences();
      applyEditorPreferences();
    }

    function setSettingsSection(section) {
      const target = section === "compiler" ? "compiler" : "editor";
      state.settingsSection = target;
      document.querySelectorAll("[data-settings-section]").forEach(button => {
        const active = button.dataset.settingsSection === target;
        button.classList.toggle("active", active);
        button.setAttribute("aria-selected", active ? "true" : "false");
        button.tabIndex = active ? 0 : -1;
      });
      document.querySelectorAll("[data-settings-panel]").forEach(panel => {
        const active = panel.dataset.settingsPanel === target;
        panel.classList.toggle("active", active);
        panel.hidden = !active;
      });
    }

    function applyWordWrap() {
      const editor = $("articleSourceEditor");
      $("articleCodeEditor").classList.toggle("wordWrap", state.wordWrap);
      $("articleWordWrap").setAttribute("aria-pressed", state.wordWrap ? "true" : "false");
      $("articleWordWrap").title = state.wordWrap ? window.PortalI18n.t("关闭自动换行") : window.PortalI18n.t("自动换行");
      editor.wrap = state.wordWrap ? "soft" : "off";
      if (state.wordWrap) editor.scrollLeft = 0;
      state.editor?.updateOptions?.({
        wordWrap: state.wordWrap ? "on" : "off",
        scrollbar: { horizontal: state.wordWrap ? "hidden" : "auto" },
      });
      renderEditorPreferenceControls();
      if (state.openFile) renderEditorLayers();
    }

    function toggleWordWrap() {
      captureActiveDocument();
      state.wordWrap = !state.wordWrap;
      window.PortalState.storage.writeText(WORD_WRAP_STORAGE_KEY, state.wordWrap ? "1" : "0");
      applyWordWrap();
      window.requestAnimationFrame(() => {
        if (state.monacoReady) state.editor?.layout?.();
        else syncEditorScroll();
      });
    }

    function resetEditorPreferences() {
      state.editorPreferences = { ...EDITOR_PREFERENCE_DEFAULTS };
      state.wordWrap = true;
      persistEditorPreferences();
      applyEditorPreferences();
      applyWordWrap();
      showToast(window.PortalI18n.t("编辑器设置已恢复默认值。"), "success");
    }

    return Object.freeze({
      applyEditorPreferences, applyWordWrap, normalizedEditorPreferences, renderEditorPreferenceControls,
      resetEditorPreferences, setSettingsSection, toggleWordWrap, updateEditorPreference,
    });
  }

  window.PortalArticleWorkspacePreferences = Object.freeze({ createPreferences, dependencies: DEPENDENCIES });
})();
