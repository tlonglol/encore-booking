// The landing page: the live Birthday Parties page, rebuilt, with the booking engine as its last section.
(function () {
  const { api, el, money } = window.Encore;
  const C = window.EncoreContent;
  const $ = (id) => document.getElementById(id);

  function hero() {
    $("hero").replaceChildren(
      el("p", { class: "hero-eyebrow", text: C.HERO.eyebrow }),
      el("h1", { class: "hero-title" }, [
        el("span", { class: "line1", text: C.HERO.line1 }),
        el("span", { class: "line2", text: C.HERO.line2 }),
      ]),
      el("p", { class: "hero-sub", text: C.HERO.sub }),
      el(
        "div",
        { class: "badges" },
        C.HERO.chips.map((chip, index) => el("span", { class: "badge " + (index % 2 ? "badge-white" : "badge-blue"), text: chip }))
      )
    );
  }

  function stats() {
    $("info-strip").replaceChildren(
      ...C.STATS.map(([icon, label, value]) =>
        el("div", { class: "info-cell" }, [
          el("div", { class: "info-icon", "aria-hidden": "true", text: icon }),
          el("div", { class: "info-label", text: label }),
          el("div", { class: "info-value", text: value }),
        ])
      )
    );
  }

  function reviews() {
    const badge = $("reviews");
    badge.href = C.REVIEWS.href;
    badge.replaceChildren(
      el("span", { class: "stars", "aria-hidden": "true", text: "★★★★★" }),
      el("span", { class: "reviews-rating", text: C.REVIEWS.rating }),
      el("span", { class: "reviews-tail", text: C.REVIEWS.tail })
    );
  }

  // "Premium favor bags + Encore branded item" -> the highlighted part in the tier accent, the rest plain.
  function featureText(state) {
    if (!state.hl || state.text.indexOf(state.hl) < 0) return [state.text];
    const [before, after] = state.text.split(state.hl);
    return [before, el("span", { class: "feature-highlight", text: state.hl }), after];
  }

  function packages(catalog) {
    $("pkg-grid").replaceChildren(
      ...catalog.packages.map((p) => {
        const featured = p.key === "plus";
        return el("div", { class: "pkg-card" + (featured ? " featured" : ""), "data-tier": p.key }, [
          el("div", { class: "pkg-bar" }),
          el("div", { class: "pkg-header" }, [
            featured ? el("div", {}, [el("span", { class: "popular-tag", text: "Most Popular" })]) : null,
            el("div", { class: "pkg-name", text: p.label }),
            el("div", { class: "pkg-capacity", text: `Up to ${p.included_kids} kids · ${p.hours} hours` }),
            el("div", { class: "pkg-price-row" }, [
              el("span", { class: "pkg-currency", text: "$" }),
              el("span", { class: "pkg-price", text: String(p.price_cents / 100) }),
            ]),
            el("div", { class: "pkg-overage" }, [el("span", { text: `+${money(catalog.additional_kid_cents)}` }), " per additional kid"]),
          ]),
          el(
            "div",
            { class: "pkg-body" },
            C.FEATURES.map((feature) => {
              const state = C.featureFor(feature, p.key);
              return el("div", { class: "feature-row" + (state.on ? "" : " inactive") }, [
                el("span", { class: "check-wrap" + (state.on ? "" : " off"), "aria-hidden": "true", text: "✓" }),
                el("span", {}, featureText(state)),
              ]);
            })
          ),
          el("div", { class: "pkg-cta-wrap" }, [
            el("a", {
              class: "pkg-cta" + (featured ? " primary" : ""),
              href: `/book/date?pkg=${p.key}`,
              text: "Book Now",
            }),
          ]),
        ]);
      })
    );
  }

  function buyout() {
    $("buyout").replaceChildren(
      el("div", { class: "buyout-inner" }, [
        el("div", { class: "buyout-main" }, [
          el("div", { class: "buyout-eyebrow", text: C.BUYOUT.eyebrow }),
          el("div", { class: "buyout-title", text: C.BUYOUT.title }),
          el("p", { text: C.BUYOUT.body }),
          el("div", { class: "buyout-facts", text: C.BUYOUT.facts }),
        ]),
        el("div", { class: "buyout-side" }, [
          el("div", { class: "buyout-from", text: C.BUYOUT.fromLabel }),
          el("div", { class: "buyout-price", text: C.BUYOUT.fromPrice }),
          el("div", { class: "buyout-note", text: C.BUYOUT.fromNote }),
          el("a", { class: "btn btn-ghost", href: C.BUYOUT.href, text: C.BUYOUT.cta }),
        ]),
      ])
    );
  }

  function addons(catalog) {
    const byKey = {};
    catalog.addons.forEach((addon) => { byKey[addon.key] = addon; });
    const reel = byKey.highlight_reel ? money(byKey.highlight_reel.price_cents) : "";
    $("addon-grid").replaceChildren(
      ...C.ADDON_ORDER.filter((key) => byKey[key]).map((key) => {
        const addon = byKey[key];
        const copy = C.ADDONS[key];
        return el("div", { class: "addon-card" + (copy.featured ? " featured" : "") }, [
          el("div", { class: "addon-icon", "aria-hidden": "true", text: copy.icon }),
          el("div", { class: "addon-body" }, [
            el("div", { class: "addon-name" }, [
              addon.label,
              copy.featured ? el("span", { class: "addon-tag", text: "Featured" }) : null,
            ]),
            el("div", { class: "addon-desc", text: copy.desc.replace("{reel}", reel) }),
          ]),
          el("div", { class: "addon-price", text: `+${money(addon.price_cents)}` }),
        ]);
      })
    );
  }

  function faq() {
    $("faq-grid").replaceChildren(
      ...C.FAQ.map(([question, answer]) =>
        el("div", { class: "faq-item" }, [
          el("div", { class: "faq-q", text: question }),
          el("div", { class: "faq-a", text: answer }),
        ])
      )
    );
  }

  function quotes() {
    $("quotes-grid").replaceChildren(
      ...C.QUOTES.map(([text, who]) =>
        el("div", { class: "quote-card" }, [
          el("div", { class: "stars", "aria-hidden": "true", text: "★★★★★" }),
          el("div", { class: "quote-text", text: text }),
          el("div", { class: "quote-who" }, [who, el("span", { class: "quote-src", text: "Verified Google review" })]),
        ])
      )
    );
    const all = $("reviews-all");
    all.href = C.REVIEWS.href;
    all.textContent = "Read all 161 Google reviews →";
  }

  // Door B: no tier chosen, price visible before the click.
  function bookPanel(catalog) {
    const floor = money(Math.min(...catalog.packages.map((p) => p.price_cents)));
    $("book").replaceChildren(
      el("h2", { class: "book-title", text: "Book Your Party" }),
      el("p", { class: "book-sub", text: `Weekend morning slots fill fast. Private venue parties from ${floor}, and a $100 deposit holds your date.` }),
      el("div", { class: "book-actions" }, [
        el("a", { class: "btn btn-primary", href: "/book/date", text: "See open weekends" }),
        el("a", { class: "btn btn-ghost", href: "tel:+16462038087", text: "Call (646) 203-8087" }),
      ]),
      el("p", { class: "fine", text: C.REFUND_PROMISE })
    );
  }

  async function boot() {
    window.EncoreShell.mountNav({ ctaHref: "#book" });
    window.EncoreShell.mountFooter();
    hero();
    stats();
    reviews();
    faq();
    quotes();
    buyout();

    const { status, data } = await api("/api/catalog");
    if (status !== 200) {
      $("pkg-grid").replaceChildren(el("p", { class: "notice error", text: "Couldn't load packages. Refresh to try again." }));
      return;
    }
    packages(data);
    addons(data);
    bookPanel(data);
  }

  boot();
})();
