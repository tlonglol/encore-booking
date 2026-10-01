(function () {
  const { el, money } = window.Encore;
  const Funnel = window.EncoreFunnel;

  const state = Funnel.require((s) => s.package);
  if (!state) return;
  state.addons = state.addons || {};

  const $ = (id) => document.getElementById(id);
  let catalog = null;

  function pkg() {
    return catalog.packages.find((p) => p.key === state.package);
  }

  function requote() {
    Funnel.save(state);
    Funnel.fetchQuote(state.package, state.kids || pkg().included_kids, state.addons);
  }

  function setAddon(key, quantity) {
    if (quantity > 0) state.addons[key] = quantity;
    else delete state.addons[key];
    if (key === "tournament" && !quantity) delete state.addons.highlight_reel;
    renderAddons();
    requote();
  }

  // Pack cards update in place instead of re-rendering the grid, so a focused quantity input keeps focus while typing.
  function packAddon(a, iconBox, body) {
    const { bindCount } = window.Encore;
    const max = catalog.max_pack_quantity;
    const qty = () => state.addons[a.key] || 0;
    const input = el("input", {
      type: "text",
      inputmode: "numeric",
      pattern: "[0-9]*",
      enterkeyhint: "done",
      autocomplete: "off",
      maxlength: "2",
      "aria-label": `${a.label} quantity`,
      value: String(qty()),
    });
    const minus = el("button", { type: "button", "aria-label": `Fewer ${a.label}`, text: "−" });
    const plus = el("button", { type: "button", "aria-label": `More ${a.label}`, text: "+" });
    const card = el("div", { class: "addon" }, [
      iconBox,
      body,
      el("div", { class: "addon-side" }, [
        el("span", { class: "addon-price", text: `+${money(a.price_cents)}` }),
        el("div", { class: "stepper small", role: "group", "aria-label": `${a.label} quantity` }, [minus, input, plus]),
      ]),
    ]);
    function sync() {
      const value = qty();
      card.classList.toggle("on", value > 0);
      minus.disabled = value <= 0;
      plus.disabled = value >= max;
      if (input.value !== String(value) && document.activeElement !== input) input.value = String(value);
    }
    function set(value) {
      const clamped = Math.min(max, Math.max(0, value));
      if (clamped > 0) state.addons[a.key] = clamped;
      else delete state.addons[a.key];
      input.value = String(clamped);
      sync();
      requote();
    }
    minus.addEventListener("click", () => set(qty() - 1));
    plus.addEventListener("click", () => set(qty() + 1));
    bindCount(input, 0, max, qty, set);
    sync();
    return card;
  }

  function renderAddons() {
    $("addons").replaceChildren(
      ...catalog.addons.map((a) => {
        const [icon, desc] = Funnel.COPY.addons[a.key] || ["➕", ""];
        const included = pkg().included_addons.includes(a.key);
        const blocked = a.requires && !state.addons[a.requires];
        const qty = state.addons[a.key] || 0;
        const body = el("div", { class: "addon-body" }, [
          el("div", { class: "addon-name", text: a.label }),
          el("div", { class: "addon-desc", text: blocked ? "Add Tournament + Livestream first." : desc }),
        ]);
        const iconBox = el("div", { class: "addon-icon", "aria-hidden": "true", text: icon });

        if (included) {
          return el("div", { class: "addon on" }, [
            iconBox,
            body,
            el("div", { class: "addon-side" }, [el("span", { class: "addon-included", text: "Included" })]),
          ]);
        }
        if (a.has_quantity) return packAddon(a, iconBox, body);
        return el(
          "button",
          {
            type: "button",
            class: "addon" + (qty ? " on" : "") + (blocked ? " disabled" : ""),
            role: "switch",
            "aria-checked": String(Boolean(qty)),
            disabled: Boolean(blocked),
            onclick: () => setAddon(a.key, qty ? 0 : 1),
          },
          [
            iconBox,
            body,
            el("div", { class: "addon-side" }, [el("span", { class: "addon-price", text: `+${money(a.price_cents)}` }), el("span", { class: "toggle", "aria-hidden": "true" })]),
          ]
        );
      })
    );
  }

  async function boot() {
    Funnel.renderSteps(2);
    Funnel.renderHoldLine(state);
    Funnel.barInit();
    catalog = await Funnel.catalog();
    if (!catalog || !pkg()) {
      $("addons").replaceChildren(el("p", { class: "notice error", text: "Couldn't load add-ons. Refresh to try again." }));
      return;
    }
    // The sticky-bar button is the advancing click on this page.
    const cta = $("quote-cta");
    cta.hidden = false;
    const label =
      state.mode === "request"
        ? `Request ${Funnel.shortDate(state.date)}`
        : `Lock in ${Funnel.shortDate(state.date)} ($100 today)`;
    cta.replaceChildren(
      el("button", {
        type: "button",
        class: "btn btn-primary",
        text: label,
        onclick: () => {
          Funnel.save(state);
          window.location.href = "checkout.html";
        },
      })
    );
    renderAddons();
    requote();
  }

  boot();
})();
