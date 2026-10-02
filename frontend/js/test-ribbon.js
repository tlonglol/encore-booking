// The test-mode ribbon. Every page loads this; the API decides whether it shows, so sandbox credentials can
// never be demoed without the notice and going live needs no edit here. Nothing is hardcoded but the words.
(function () {
  const origin = (window.ENCORE_CONFIG && window.ENCORE_CONFIG.apiOrigin) || "";
  const NOTICE = "Test mode · this is a demo booking system · no real cards are charged";
  const SHORT = "Test mode · no real cards are charged";

  function mount() {
    const ribbon = document.createElement("div");
    ribbon.className = "test-ribbon";
    ribbon.id = "test-ribbon";
    ribbon.setAttribute("role", "status");
    ribbon.innerHTML =
      '<span class="ribbon-long"></span><span class="ribbon-short"></span>';
    ribbon.querySelector(".ribbon-long").textContent = NOTICE;
    ribbon.querySelector(".ribbon-short").textContent = SHORT;
    document.body.prepend(ribbon);
    document.body.classList.add("has-ribbon");
    // The hold pill floats under whichever header the page has, and the ribbon just moved it.
    window.dispatchEvent(new Event("resize"));
  }

  async function check() {
    try {
      const response = await fetch(origin + "/api/config");
      if (!response.ok) return;
      const data = await response.json();
      if (data.test_mode) mount();
    } catch (error) {
      /* the ribbon is a notice, not a gate: an unreachable API leaves the page alone */
    }
  }

  check();
})();
