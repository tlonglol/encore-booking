// Square Web Payments SDK: the card fields on our own Info + Deposit page.
//
// The card number never reaches this page's JavaScript. Square renders the inputs inside its own
// iframes; all we ever hold is the single-use token tokenize() hands back, and that goes straight to our
// server and is never stored. The styling below is why this exists rather than a redirect: the fields
// are dressed from the site's own tokens so the deposit step looks like the rest of the funnel.
//
// Without an application id (local dev with no Square credentials) enabled() is false and the funnel
// falls back to paying with a dev token, exactly as the server does.
(function () {
  const origin = (window.ENCORE_CONFIG && window.ENCORE_CONFIG.apiOrigin) || "";
  const CDN = {
    sandbox: "https://sandbox.web.squarecdn.com/v1/square.js",
    production: "https://web.squarecdn.com/v1/square.js",
  };

  const state = { appId: "", locationId: "", environment: "sandbox", loading: null, card: null, host: null };

  async function configure() {
    try {
      const response = await fetch(origin + "/api/config");
      if (!response.ok) return;
      const data = await response.json();
      state.appId = data.square_application_id || "";
      state.locationId = data.square_location_id || "";
      state.environment = data.square_environment === "production" ? "production" : "sandbox";
    } catch (error) {
      /* leaves appId empty, which is the dev fallback rather than a broken page */
    }
  }

  const ready = configure();

  function loadScript() {
    if (state.loading) return state.loading;
    state.loading = new Promise(function (resolve, reject) {
      const tag = document.createElement("script");
      tag.src = CDN[state.environment];
      tag.async = true;
      tag.onload = resolve;
      tag.onerror = reject;
      document.head.appendChild(tag);
    });
    return state.loading;
  }

  // Read from the stylesheet rather than hardcoded, so the card fields follow the design tokens.
  function token(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  }

  function styles() {
    return {
      ".input-container": {
        borderColor: token("--border", "#1e1e1e"),
        borderRadius: "6px",
      },
      ".input-container.is-focus": {
        borderColor: token("--blue", "#0959a7"),
      },
      ".input-container.is-error": {
        borderColor: token("--warn", "#f59e0b"),
      },
      input: {
        backgroundColor: token("--card2", "#161616"),
        color: token("--white", "#f0f0f0"),
        fontFamily: "Barlow, sans-serif",
        fontSize: "16px",
      },
      "input::placeholder": {
        color: token("--gray", "#777777"),
      },
      ".message-text": {
        color: token("--gray-light", "#bbbbbb"),
      },
      ".message-text.is-error": {
        color: token("--warn", "#f59e0b"),
      },
    };
  }

  async function mount(host) {
    await ready;
    if (!state.appId || !state.locationId) return false;
    if (state.card && state.host === host) return true;
    await destroy();
    await loadScript();
    if (!window.Square) return false;
    const payments = window.Square.payments(state.appId, state.locationId);
    const card = await payments.card({ style: styles() });
    await card.attach(host);
    state.card = card;
    state.host = host;
    return true;
  }

  async function destroy() {
    if (!state.card) return;
    const card = state.card;
    state.card = null;
    state.host = null;
    try {
      await card.destroy();
    } catch (error) {
      /* already gone */
    }
  }

  // { token } on success, { error } with something a parent can act on otherwise.
  async function tokenize() {
    if (!state.card) return { error: "The card form didn't load. Refresh the page and try again." };
    let result;
    try {
      result = await state.card.tokenize();
    } catch (error) {
      return { error: "We couldn't read that card. Check the details and try again." };
    }
    if (result && result.status === "OK" && result.token) return { token: result.token };
    const first = result && result.errors && result.errors[0];
    return { error: (first && first.message) || "Check the card details and try again." };
  }

  async function enabled() {
    await ready;
    return Boolean(state.appId && state.locationId);
  }

  window.EncoreCard = { mount: mount, destroy: destroy, tokenize: tokenize, enabled: enabled };
})();
