(function () {
  const { api, detailText, money, clock, el, store, recall } = window.Encore;
  const HOLD_KEY = "encore.hold";

  const COPY = {
    packages: {
      standard: ["4 games pre-loaded", "Private full-venue buyout", "Switch, PS5 & PC stations", "Dedicated staff host", "Birthday banner on every screen"],
      plus: ["6 games pre-loaded", "Everything in Standard", "Gamer decorations + balloon tower", "Party favor bags"],
      premium: ["Full catalog, every station", "Everything in Plus", "VR gaming & DDR pad, full party", "Premium favor bags + Encore item"],
    },
    addons: {
      tournament: ["🏆", "Bracket, host MC, YouTube livestream, printed bracket, trophy for the birthday champion."],
      highlight_reel: ["🎬", "Edited highlight Reel of the tournament. Needs Tournament + Livestream."],
      extra_hour: ["⏱️", "Extend the party. Venue, host, and games stay yours."],
      vr: ["🥽", "Beat Saber, Superhot & more, full party duration."],
      ddr: ["🕹️", "DDR / Stepmania pad for the whole group, full party."],
      decorations: ["🎈", "Balloon tower, tabletop décor, themed setup."],
      pizza_pack: ["🍕", "2 large pizzas + drinks. Feeds about 10 kids."],
      drinks_pack: ["🥤", "Soda + juice boxes for the whole group."],
    },
  };
  const STATUS_TEXT = { open: "Open", held: "On hold", booked: "Booked", blocked: "Unavailable", past: "Too soon" };

  const state = {
    catalog: null,
    packageKey: "standard",
    kids: 15,
    addons: {},
    slots: [],
    slotId: null,
    quote: null,
    quoteSeq: 0,
    hold: null,
    timer: null,
  };

  const $ = (id) => document.getElementById(id);

  function pkg(key) {
    return state.catalog.packages.find((p) => p.key === key);
  }

  function isIncluded(addonKey) {
    return pkg(state.packageKey).included_addons.includes(addonKey);
  }

  // ---------- Packages ----------
  function renderPackages() {
    const root = $("packages");
    root.replaceChildren(
      ...state.catalog.packages.map((p) =>
        el(
          "button",
          {
            type: "button",
            class: "pkg-card",
            role: "radio",
            "aria-checked": String(p.key === state.packageKey),
            onclick: () => {
              state.packageKey = p.key;
              renderPackages();
              renderKids();
              renderAddons();
              requestQuote();
            },
          },
          [
            p.key === "plus" ? el("span", { class: "popular", text: "Most popular" }) : null,
            el("div", { class: "pkg-name", text: p.label }),
            el("div", { class: "pkg-meta", text: `Up to ${p.included_kids} kids · ${p.hours} hours` }),
            el("div", { class: "pkg-price-row" }, [
              el("span", { class: "cur", text: "$" }),
              el("span", { class: "pkg-price", text: String(p.price_cents / 100) }),
            ]),
            el("div", { class: "pkg-extra" }, [el("b", { text: `+${money(state.catalog.additional_kid_cents)}` }), " per additional kid"]),
            el("ul", { class: "pkg-features" }, (COPY.packages[p.key] || []).map((f) => el("li", { text: f }))),
            el("span", { class: "pkg-select", text: p.key === state.packageKey ? "Selected" : `Select ${p.label}` }),
          ]
        )
      )
    );
  }

  // ---------- Kids ----------
  function renderKids() {
    const p = pkg(state.packageKey);
    $("kids-count").textContent = String(state.kids);
    $("kids-minus").disabled = state.kids <= state.catalog.min_kids;
    $("kids-plus").disabled = state.kids >= state.catalog.max_kids;
    const extra = Math.max(0, state.kids - p.included_kids);
    $("kids-note").replaceChildren(
      `${p.label} includes up to ${p.included_kids}. `,
      extra
        ? el("b", { text: `${extra} extra × ${money(state.catalog.additional_kid_cents)} = ${money(extra * state.catalog.additional_kid_cents)}` })
        : `Each kid after that is +${money(state.catalog.additional_kid_cents)}`,
      `. Max ${state.catalog.max_kids} per party.`
    );
  }

  function changeKids(delta) {
    state.kids = Math.min(state.catalog.max_kids, Math.max(state.catalog.min_kids, state.kids + delta));
    renderKids();
    requestQuote();
  }

  // ---------- Add-ons ----------
  function setAddon(key, quantity) {
    if (quantity > 0) state.addons[key] = quantity;
    else delete state.addons[key];
    if (key === "tournament" && !quantity) delete state.addons.highlight_reel;
    renderAddons();
    requestQuote();
  }

  function renderAddons() {
    const root = $("addons");
    root.replaceChildren(
      ...state.catalog.addons.map((a) => {
        const [icon, desc] = COPY.addons[a.key] || ["➕", ""];
        const included = isIncluded(a.key);
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
        if (a.has_quantity) {
          return el("div", { class: "addon" + (qty ? " on" : "") }, [
            iconBox,
            body,
            el("div", { class: "addon-side" }, [
              el("span", { class: "addon-price", text: `+${money(a.price_cents)}` }),
              el("div", { class: "stepper small", role: "group", "aria-label": `${a.label} quantity` }, [
                el("button", { type: "button", "aria-label": `Fewer ${a.label}`, disabled: qty <= 0, onclick: () => setAddon(a.key, qty - 1), text: "−" }),
                el("output", { "aria-live": "polite", text: String(qty) }),
                el("button", {
                  type: "button",
                  "aria-label": `More ${a.label}`,
                  disabled: qty >= state.catalog.max_pack_quantity,
                  onclick: () => setAddon(a.key, qty + 1),
                  text: "+",
                }),
              ]),
            ]),
          ]);
        }
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

  // ---------- Quote ----------
  let quoteDelay = null;
  function requestQuote() {
    $("quote-total").classList.add("stale");
    clearTimeout(quoteDelay);
    quoteDelay = setTimeout(fetchQuote, 120);
  }

  async function fetchQuote() {
    const seq = ++state.quoteSeq;
    const { status, data } = await api("/api/quote", {
      method: "POST",
      body: { package: state.packageKey, kids: state.kids, addons: state.addons },
    });
    if (seq !== state.quoteSeq || state.hold) return;
    if (status !== 200) {
      renderQuote(null, detailText(data, "Couldn't price that. Try again."));
      return;
    }
    state.quote = data;
    renderQuote(data);
    renderSummary();
  }

  function renderQuote(quote, error) {
    const total = $("quote-total");
    const split = $("quote-split");
    const lines = $("quote-lines");
    if (!quote) {
      total.textContent = "—";
      split.replaceChildren(el("span", { class: "quote-error", text: error }));
      return;
    }
    total.classList.remove("stale");
    total.textContent = money(quote.total_cents);
    split.replaceChildren(
      el("b", { text: `${money(quote.deposit_cents)} today` }),
      el("br"),
      `${money(quote.balance_cents)} day of party`
    );
    lines.replaceChildren(
      ...quote.line_items.map((item) =>
        el("li", {}, [
          el("span", { text: item.quantity > 1 ? `${item.label} × ${item.quantity}` : item.label }),
          item.included ? el("span", { class: "inc", text: "Included" }) : el("span", { text: money(item.amount_cents) }),
        ])
      ),
      el("li", {}, [el("span", { text: "Deposit due now" }), el("span", { text: money(quote.deposit_cents) })]),
      el("li", {}, [el("span", { text: "Balance due day of party" }), el("span", { text: money(quote.balance_cents) })])
    );
  }

  // ---------- Slots ----------
  async function loadSlots() {
    const { status, data } = await api("/api/availability");
    if (status !== 200) {
      $("slots").replaceChildren(el("p", { class: "notice error", text: detailText(data, "Couldn't load dates. Refresh to try again.") }));
      return;
    }
    state.slots = data.slots;
    if (state.slotId && !state.slots.some((s) => s.id === state.slotId && s.status === "open")) state.slotId = null;
    renderSlots();
    renderSummary();
  }

  function weekendKey(slot) {
    const d = new Date(slot.date + "T12:00:00");
    if (d.getDay() === 0) d.setDate(d.getDate() - 1);
    return d.toISOString().slice(0, 10);
  }

  function renderSlots() {
    const groups = new Map();
    state.slots.forEach((slot) => {
      const key = weekendKey(slot);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(slot);
    });
    const rows = [];
    groups.forEach((slots) => {
      const cells = slots.map((slot) =>
        el(
          "button",
          {
            type: "button",
            class: `slot ${slot.status}` + (slot.id === state.slotId ? " selected" : ""),
            "aria-pressed": String(slot.id === state.slotId),
            "aria-label": `${slot.day_label} ${slot.time_label}, ${STATUS_TEXT[slot.status]}`,
            onclick: () => pickSlot(slot),
          },
          [
            el("div", { class: "day", text: slot.day_label }),
            el("div", { class: "time", text: slot.time_label }),
            el("span", { class: "state", text: slot.id === state.slotId ? "Selected" : STATUS_TEXT[slot.status] }),
          ]
        )
      );
      if (cells.length === 1 && new Date(slots[0].date + "T12:00:00").getDay() === 0) cells.unshift(el("div", { class: "slot slot-placeholder" }));
      rows.push(el("div", { class: "weekend" }, cells));
    });
    $("slots").replaceChildren(...rows);
  }

  function nextOpenAfter(slot) {
    return state.slots.filter((s) => s.status === "open" && s.date > slot.date).slice(0, 2);
  }

  function showSlotNotice(message, alternatives, isError) {
    const notice = $("slot-notice");
    if (!message) {
      notice.replaceChildren();
      return;
    }
    const alts = (alternatives || []).map((alt) =>
      el("button", { type: "button", class: "chip", onclick: () => pickSlot(alt), text: `${alt.day_label} ${alt.time_label}` })
    );
    notice.replaceChildren(
      el("div", { class: "notice" + (isError ? " error" : "") }, [
        el("div", { text: message }),
        alts.length ? el("div", { class: "alts" }, alts) : null,
      ])
    );
    notice.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function pickSlot(slot) {
    const current = state.slots.find((s) => s.id === slot.id) || slot;
    if (current.status !== "open") {
      if (current.status === "past") {
        showSlotNotice(`${current.day_label} is too soon to book online. Call (646) 203-8087 and we'll see what we can do.`, nextOpenAfter(current));
      } else {
        const alts = nextOpenAfter(current);
        showSlotNotice(`${current.day_label} is ${STATUS_TEXT[current.status].toLowerCase()}. ${alts.length ? "Next open:" : "No later dates are open right now."}`, alts);
      }
      return;
    }
    state.slotId = current.id;
    showSlotNotice(null);
    renderSlots();
    renderSummary();
    $("details-panel").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderSummary() {
    const slot = state.slots.find((s) => s.id === state.slotId);
    const line = $("summary-line");
    $("hold-button").disabled = !slot || !state.quote;
    if (!slot) {
      line.textContent = "Pick a date above";
      return;
    }
    line.replaceChildren(
      el("b", { text: `${pkg(state.packageKey).label} · ${slot.day_label} ${slot.time_label}` }),
      ` · ${state.kids} kids · `,
      el("b", { text: state.quote ? money(state.quote.total_cents) : "…" })
    );
  }

  // ---------- Hold ----------
  function formError(message) {
    $("form-error").replaceChildren(message ? el("p", { class: "notice error", text: message }) : "");
  }

  function localTime(iso) {
    return new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  }

  async function submitHold(event) {
    event.preventDefault();
    formError(null);
    const form = event.target;
    const slot = state.slots.find((s) => s.id === state.slotId);
    if (!slot) {
      formError("Pick a date first.");
      return;
    }
    const values = Object.fromEntries(new FormData(form).entries());
    const missing = ["first_name", "last_name", "phone", "email"].filter((k) => !String(values[k] || "").trim());
    if (missing.length) {
      formError("Please fill in your name, phone, and email.");
      form.elements[missing[0]].focus();
      return;
    }
    const button = $("hold-button");
    button.disabled = true;
    button.textContent = "Holding your date…";
    const { status, data } = await api("/api/holds", {
      method: "POST",
      body: {
        slot_id: slot.id,
        package: state.packageKey,
        kids: state.kids,
        addons: state.addons,
        first_name: values.first_name.trim(),
        last_name: values.last_name.trim(),
        phone: values.phone.trim(),
        email: values.email.trim(),
        notes: String(values.notes || "").trim() || null,
      },
    });
    button.textContent = "Hold my date";
    if (status === 201) {
      startHold({ id: data.hold_id, checkoutUrl: data.checkout_url, deadline: Date.now() + data.seconds_remaining * 1000 });
      return;
    }
    button.disabled = false;
    if (status === 409) {
      state.slotId = null;
      await loadSlots();
      const alts = data.alternatives || [];
      showSlotNotice(`Sorry, ${slot.day_label} was just taken. ${alts.length ? "These are still open:" : "No other dates are open right now."}`, alts, true);
      return;
    }
    if (status === 429 && data && data.expires_at) {
      formError(`${detailText(data)} It's held until ${localTime(data.expires_at)}. Finish that checkout, or try again after it expires.`);
      return;
    }
    if (status === 422 || status === 404) await loadSlots();
    formError(detailText(data));
  }

  async function startHold(hold) {
    state.hold = hold;
    store(HOLD_KEY, hold);
    $("builder").hidden = true;
    $("details-panel").hidden = true;
    $("hold-panel").hidden = false;
    $("pay-button").href = hold.checkoutUrl || "#";
    $("hold-panel").scrollIntoView({ behavior: "smooth", block: "start" });
    tick();
    clearInterval(state.timer);
    state.timer = setInterval(tick, 250);
    const { status, data } = await api(`/api/holds/${hold.id}`);
    if (status === 200) {
      const party = data.party;
      $("hold-summary").replaceChildren(
        el("b", { text: `${party.package_label} · ${party.day_label} ${party.time_label}` }),
        ` · ${party.kids} kids · `,
        el("b", { text: money(party.total_cents) })
      );
      state.quote = { total_cents: party.total_cents, deposit_cents: party.deposit_cents, balance_cents: party.balance_cents, line_items: party.line_items };
      renderQuote(state.quote);
    }
  }

  function tick() {
    const remaining = (state.hold.deadline - Date.now()) / 1000;
    const countdown = $("countdown");
    countdown.textContent = clock(Math.ceil(remaining));
    countdown.classList.toggle("low", remaining < 120);
    if (remaining <= 0) {
      clearInterval(state.timer);
      store(HOLD_KEY, null);
      $("pay-button").hidden = true;
      $("hold-summary").replaceChildren("This hold expired. ", el("a", { href: "index.html", text: "Start again" }), " to pick a date.");
    }
  }

  async function restoreHold() {
    const saved = recall(HOLD_KEY);
    if (!saved) return false;
    const { status, data } = await api(`/api/holds/${saved.id}`);
    if (status !== 200) {
      store(HOLD_KEY, null);
      return false;
    }
    if (data.status === "pending_payment") {
      startHold({ id: saved.id, checkoutUrl: data.checkout_url, deadline: Date.now() + data.seconds_remaining * 1000 });
      return true;
    }
    store(HOLD_KEY, null);
    if (data.status === "booked" || data.status === "paid_after_expiry") {
      window.location.replace(`confirm.html?hold=${encodeURIComponent(saved.id)}`);
      return true;
    }
    return false;
  }

  // ---------- Boot ----------
  async function boot() {
    $("quote-toggle").addEventListener("click", () => {
      const lines = $("quote-lines");
      lines.hidden = !lines.hidden;
      $("quote-toggle").setAttribute("aria-expanded", String(!lines.hidden));
      $("quote-toggle").textContent = lines.hidden ? "Details" : "Hide";
    });
    $("kids-minus").addEventListener("click", () => changeKids(-1));
    $("kids-plus").addEventListener("click", () => changeKids(1));
    $("hold-form").addEventListener("submit", submitHold);

    const { status, data } = await api("/api/catalog");
    if (status !== 200) {
      $("packages").replaceChildren(el("p", { class: "notice error", text: detailText(data, "Couldn't load packages. Refresh to try again.") }));
      return;
    }
    state.catalog = data;
    state.kids = pkg(state.packageKey).included_kids;
    if (await restoreHold()) return;
    renderPackages();
    renderKids();
    renderAddons();
    requestQuote();
    loadSlots();
    setInterval(() => {
      if (!state.hold && document.visibilityState === "visible") loadSlots();
    }, 30000);
  }

  boot();
})();
