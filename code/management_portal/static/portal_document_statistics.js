(() => {
  "use strict";

  function characterCount(text) {
    let count = 0;
    for (const character of text) count += 1;
    return count;
  }

  function wordCount(text) {
    let count = 0;
    const words = text.replace(/\p{Script=Han}/gu, () => {
      count += 1;
      return " ";
    });
    for (const match of words.matchAll(/[\p{L}\p{N}][\p{L}\p{M}\p{N}]*(?:['’][\p{L}\p{M}\p{N}]+)*/gu)) count += 1;
    return count;
  }

  window.PortalDocumentStatistics = Object.freeze({ characterCount, wordCount });
})();
