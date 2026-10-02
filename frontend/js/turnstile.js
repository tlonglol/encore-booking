// Cloudflare Turnstile on the Confirm action. Scaffolded and off.
//
// While the flag is off, which is the default, token() resolves to null immediately, no Cloudflare script
// is ever loaded and nothing here touches the network: the funnel behaves exactly as it did before this
// file existed. Turning it on is a decision for the day holds start being abused by something the session
// cookie and the IP caps cannot see.
(function () {
  const origin = (window.ENCORE_CONFIG && window.ENCORE_CONFIG.apiOrigin) || "";
  const SCRIPT = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

  const state = { enabled: false, siteKey: "", loading: null, widget: null };

  async function configure() {
    try {
      const response = await fetch(origin + "/api/config");
      if (!response.ok) return;
      const data = await response.json();
      state.enabled = Boolean(data.turnstile_enabled && data.turnstile_site_key);
      state.siteKey = data.turnstile_site_key || "";
    } catch (error) {
      /* an unreachable config endpoint leaves the challenge off, which is how it ships anyway */
    }
  }

  const ready = configure();

  function loadScript() {
    if (state.loading) return state.loading;
    state.loading = new Promise(function (resolve, reject) {
      const tag = document.createElement("script");
      tag.src = SCRIPT;
      tag.async = true;
      tag.onload = resolve;
      tag.onerror = reject;
      document.head.appendChild(tag);
    });
    return state.loading;
  }

  function container() {
    let node = document.getElementById("turnstile-host");
    if (!node) {
      node = document.createElement("div");
      node.id = "turnstile-host";
      node.className = "turnstile-host";
      document.body.appendChild(node);
    }
    return node;
  }

  // One widget, rendered on first use and re-executed after that: Turnstile tokens are single-use.
  function execute() {
    return new Promise(function (resolve) {
      const turnstile = window.turnstile;
      if (!turnstile) return resolve(null);
      if (state.widget === null) {
        state.widget = turnstile.render(container(), {
          sitekey: state.siteKey,
          appearance: "execute",
          callback: function (token) {
            resolve(token);
          },
          "error-callback": function () {
            resolve(null);
          },
          "timeout-callback": function () {
            resolve(null);
          },
        });
      } else {
        turnstile.reset(state.widget);
      }
      turnstile.execute(state.widget);
    });
  }

  // null means "no challenge needed", which is what the server expects while the flag is off.
  async function token() {
    await ready;
    if (!state.enabled) return null;
    try {
      await loadScript();
      return await execute();
    } catch (error) {
      return null;
    }
  }

  window.EncoreTurnstile = { token: token };
})();
