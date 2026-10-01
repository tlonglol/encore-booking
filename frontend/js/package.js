(function () {
  const { el, money } = window.Encore;
  const Funnel = window.EncoreFunnel;

  const state = Funnel.require();
  if (!state) return;

  const $ = (id) => document.getElementById(id);
  let catalog = null;

  function renderPackages() {
    $("packages").replaceChildren(
      ...catalog.packages.map((p) =>
        el("div", { class: "pkg-card", role: "group", "aria-label": `${p.label} package` }, [
          p.key === "plus" ? el("span", { class: "popular", text: "Most popular" }) : null,
          el("div", { class: "pkg-name", text: p.label }),
          el("div", { class: "pkg-meta", text: `Up to ${p.included_kids} kids · ${p.hours} hours` }),
          el("div", { class: "pkg-price-row" }, [
            el("span", { class: "cur", text: "$" }),
            el("span", { class: "pkg-price", text: String(p.price_cents / 100) }),
          ]),
          el("div", { class: "pkg-extra" }, [el("b", { text: `+${money(catalog.additional_kid_cents)}` }), " per additional kid"]),
          el("ul", { class: "pkg-features" }, (Funnel.COPY.packages[p.key] || []).map((f) => el("li", { text: f }))),
          el("button", {
            type: "button",
            class: "pkg-select" + (state.package === p.key ? " btn-primary" : ""),
            text: state.package === p.key ? `Selected: ${p.label}` : `Select ${p.label}`,
            onclick: () => select(p),
          }),
        ])
      )
    );
  }

  // The SELECT click advances immediately: the bar updates and the add-ons page opens.
  function select(p) {
    state.package = p.key;
    state.addons = state.addons || {};
    Funnel.save(state);
    Funnel.fetchQuote(p.key, kidsFor(p), state.addons);
    window.location.href = "addons.html";
  }

  function kidsFor(p) {
    return state.kids || p.included_kids;
  }

  async function boot() {
    Funnel.renderSteps(1);
    Funnel.renderHoldLine(state);
    Funnel.barInit();
    catalog = await Funnel.catalog();
    if (!catalog) {
      $("packages").replaceChildren(el("p", { class: "notice error", text: "Couldn't load packages. Refresh to try again." }));
      return;
    }
    renderPackages();
    // Back-navigation: a package already chosen shows its live total; otherwise the floor price.
    const chosen = catalog.packages.find((p) => p.key === state.package);
    if (chosen) Funnel.fetchQuote(chosen.key, kidsFor(chosen), state.addons || {});
    else Funnel.barFrom(Math.min(...catalog.packages.map((p) => p.price_cents)));
  }

  boot();
})();
