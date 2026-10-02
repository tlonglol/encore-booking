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

  // What Square's style API will actually honour, which is less than it accepts: the validator takes a
  // wider set of selectors and properties than the rendered iframe actually applies, so "no error thrown"
  // is not proof a property did anything. Every line below was checked the only way that is conclusive:
  // setting it to an impossible-to-miss color (red, magenta, green, cyan) on a live page against real
  // Square and screenshotting the result. Re-verified against SDK v1.86.0 in sandbox:
  //
  //   WORKS    .input-container borderColor, borderWidth
  //   WORKS    .input-container.is-focus / .is-error borderColor (is-error loses to is-focus while the
  //            field is actively focused, which is sensible precedence, not a bug: the error border
  //            shows once the parent looks away, confirmed by blurring and re-screenshotting)
  //   WORKS    input::placeholder color
  //   WORKS    .message-text / .message-icon color, and their .is-error variants
  //   WORKS    input fontFamily (validated hard enough to throw on a Google Font, so web-safe only),
  //            fontSize
  //   IGNORED  input backgroundColor, color — accepted, parsed, rendered as if absent every time
  //   IGNORED  .input-container borderRadius — accepted, rendered with square corners every time
  //
  // The two IGNORED input properties are why the field is still white on dark: they live in a
  // cross-origin iframe, and this map is the only way in. The card block around them is dressed in our
  // own CSS to make the white panel read as deliberate rather than as a broken dark theme. The dead
  // properties are left out entirely rather than set and silently doing nothing.
  function styles() {
    return {
      input: {
        // Not var(--body)/Barlow: Square's own validator rejects any non-web-safe font here.
        fontFamily: "Helvetica, Arial, sans-serif",
        fontSize: "16px",
      },
      "input::placeholder": {
        color: "#777777", // --gray
      },
      ".input-container": {
        borderColor: "#2a3a50", // the border every .field input on this page uses
        borderWidth: "1px",
      },
      ".input-container.is-focus": {
        borderColor: "#1a74cc", // --blue-light: matches .field input:focus elsewhere on this page
      },
      ".input-container.is-error": {
        borderColor: "#e5484d", // --danger
      },
      ".message-text": {
        color: "#777777", // --gray
      },
      ".message-icon": {
        color: "#777777", // --gray
      },
      ".message-text.is-error": {
        color: "#e5484d", // --danger
      },
      ".message-icon.is-error": {
        color: "#e5484d", // --danger
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
