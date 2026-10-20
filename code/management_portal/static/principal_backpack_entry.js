(() => {
  "use strict";
  const button = document.querySelector("[data-open-principal-backpack]");
  const open = () => window.PortalNav.openPrincipalBackpack({ principalId: new URLSearchParams(location.search).get("principal") || "" });
  button.addEventListener("click", open);
  open();
})();
