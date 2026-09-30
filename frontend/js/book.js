(function () {
  const { api, detailText, money, clock, el, store, recall, bindCount } = window.Encore;
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
  const STATUS_TEXT = { open: "Open", held: "On hold", booked: "Booked", blocked: "Unavailable", past: "Unavailable", request: "By request" };

  const state = {
    catalog: null,
    packageKey: "standard",
    kids: 15,
    addons: {},
    days: [],
    weekendDefault: "open",
    today: null,
    firstBookable: null,
    sheetDay: null,
    busy: false,
    quote: null,
    quoteFresh: false,
    quoteError: null,
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
    const input = $("kids-count");
    if (input.value !== String(state.kids)) input.value = String(state.kids);
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

  function setKids(value) {
    state.kids = Math.min(state.catalog.max_kids, Math.max(state.catalog.min_kids, value));
    renderKids();
    requestQuote();
  }

  function changeKids(delta) {
    setKids(state.kids + delta);
  }

  // ---------- Add-ons ----------
  function setAddon(key, quantity) {
    if (quantity > 0) state.addons[key] = quantity;
    else delete state.addons[key];
    if (key === "tournament" && !quantity) delete state.addons.highlight_reel;
    renderAddons();
    requestQuote();
  }

  // Pack cards update in place instead of re-rendering the grid, so a focused quantity input keeps focus while typing.
  function packAddon(a, iconBox, body) {
    const max = state.catalog.max_pack_quantity;
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
      requestQuote();
    }
    minus.addEventListener("click", () => set(qty() - 1));
    plus.addEventListener("click", () => set(qty() + 1));
    bindCount(input, 0, max, qty, set);
    sync();
    return card;
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

  // ---------- Quote ----------
  let quoteDelay = null;
  function requestQuote() {
    state.quoteFresh = false;
    state.quoteError = null;
    renderSheet();
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
      state.quoteError = detailText(data, "Couldn't price that. Try again.");
      renderQuote(null, state.quoteError);
      renderSheet();
      return;
    }
    state.quote = data;
    state.quoteFresh = true;
    renderQuote(data);
    renderSheet();
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

  // ---------- Weekend list ----------
  const DAY_MS = 86400000;

  function jsDate(day) {
    return new Date(day.date + "T12:00:00");
  }

  function isWeekendDay(day) {
    const dow = jsDate(day).getDay();
    return dow === 0 || dow === 6;
  }

  function dayLabel(date) {
    return new Date(date + "T12:00:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
  }

  async function loadCalendar() {
    const { status, data } = await api("/api/availability");
    if (status !== 200) {
      $("slots").replaceChildren(el("p", { class: "notice error", text: detailText(data, "Couldn't load dates. Refresh to try again.") }));
      return;
    }
    state.days = data.days;
    state.weekendDefault = data.weekend_default;
    state.today = data.today;
    state.firstBookable = data.first_bookable_date;
    $("weekday-date").min = data.first_bookable_date;
    const open = state.sheetDay;
    const fresh = open && state.days.find((d) => d.date === open.date);
    if (fresh && fresh.status !== "open" && fresh.status !== "request" && !state.busy) {
      closeSheet();
      showSlotNotice(`${fresh.day_label} just became unavailable. Pick another date.`, nextOpenAfter(fresh), true);
    }
    renderCalendar();
  }

  function weekendGroups() {
    const groups = [];
    state.days.filter(isWeekendDay).forEach((day) => {
      const last = groups[groups.length - 1];
      if (jsDate(day).getDay() === 0 && last && last[last.length - 1].date === shiftDate(day.date, -1)) last.push(day);
      else groups.push([day]);
    });
    return groups;
  }

  function shiftDate(date, days) {
    return new Date(new Date(date + "T12:00:00").getTime() + days * DAY_MS).toISOString().slice(0, 10);
  }

  function weekendLabel(group) {
    const first = group[0];
    const saturday = jsDate(first).getDay() === 0 ? shiftDate(first.date, -1) : first.date;
    return `Weekend of ${new Date(saturday + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" })}`;
  }

  function renderCalendar() {
    $("slots").replaceChildren(
      ...weekendGroups().map((group) =>
        el("div", { class: "wk-group" }, [
          el("div", { class: "wk-label", text: weekendLabel(group) }),
          el(
            "div",
            { class: "wk-cards" },
            group.map((day) => {
              const time = day.time_label || "10:00 AM";
              return el(
                "button",
                {
                  type: "button",
                  class: `wk-card ${day.status}`,
                  "aria-label": `${day.day_label} ${time}, ${STATUS_TEXT[day.status]}`,
                  onclick: () => pickDate(day),
                },
                [
                  el("span", { class: "wk-date", text: day.day_label }),
                  el("span", { class: "wk-time", text: time }),
                  el("span", { class: "wk-state" }, [el("span", { text: STATUS_TEXT[day.status] })]),
                  day.status === "held" ? el("span", { class: "wk-hint", text: "May free up in a few minutes" }) : null,
                ]
              );
            })
          ),
        ])
      )
    );
  }

  function nextOpenAfter(day) {
    return state.days.filter((d) => d.status === "open" && d.date > day.date).slice(0, 2);
  }

  function showSlotNotice(message, alternatives, isError) {
    const notice = $("slot-notice");
    if (!message) {
      notice.replaceChildren();
      return;
    }
    const alts = (alternatives || []).map((alt) =>
      el("button", {
        type: "button",
        class: "chip",
        onclick: () => pickDate(state.days.find((d) => d.date === alt.date) || alt),
        text: `${alt.day_label}${alt.time_label ? ` ${alt.time_label}` : ""}`,
      })
    );
    notice.replaceChildren(
      el("div", { class: "notice" + (isError ? " error" : "") }, [
        el("div", { text: message }),
        alts.length ? el("div", { class: "alts" }, alts) : null,
      ])
    );
    notice.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function pickDate(day) {
    const current = state.days.find((d) => d.date === day.date) || day;
    if (current.status === "open" || current.status === "request") {
      showSlotNotice(null);
      openSheet(current);
      return;
    }
    const alts = nextOpenAfter(current);
    const nextOpen = alts.length ? "Next open:" : "No open weekend dates right now.";
    if (current.status === "held") {
      showSlotNotice(`${current.day_label} is on hold for another family. It may free up in a few minutes, and this page checks again on its own. ${nextOpen}`, alts);
    } else if (current.status === "booked") {
      showSlotNotice(`${current.day_label} is booked. ${nextOpen}`, alts);
    } else {
      showSlotNotice(`${current.day_label} isn't available online. Call (646) 203-8087 if you need that date. ${nextOpen}`, alts);
    }
  }

  // ---------- Weekday request form ----------
  function weekdayError(message) {
    $("weekday-error").textContent = message || "";
  }

  function submitWeekday(event) {
    event.preventDefault();
    weekdayError(null);
    const date = $("weekday-date").value;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
      weekdayError("Pick the date you have in mind.");
      return;
    }
    if (date < state.firstBookable) {
      weekdayError("That's too soon to book online. Call (646) 203-8087 and we'll see what we can do.");
      return;
    }
    if (date > shiftDate(state.today, 366)) {
      weekdayError("That's more than a year out. Check the year, or call (646) 203-8087.");
      return;
    }
    const listed = state.days.find((d) => d.date === date);
    if (listed) {
      pickDate(listed);
      return;
    }
    if (isWeekendDay({ date }) && state.weekendDefault === "open") {
      weekdayError("Weekends are booked online for the next 8 weekends. For a later weekend, call (646) 203-8087.");
      return;
    }
    openSheet({ date, day_label: dayLabel(date), status: "request", slot_id: null, time_label: null });
  }

  // ---------- Confirm sheet ----------
  function requestMode() {
    return Boolean(state.sheetDay && state.sheetDay.status === "request");
  }

  function sheetRow(label, value, className) {
    return [el("dt", { text: label }), el("dd", { class: className || "", text: value })];
  }

  function renderSheet() {
    const day = state.sheetDay;
    if (!day) return;
    const requesting = requestMode();
    const quote = state.quoteFresh ? state.quote : null;
    const p = pkg(state.packageKey);
    $("sheet-eyebrow").textContent = requesting ? "Confirm your request" : "Confirm your date";
    $("sheet-title").textContent = jsDate(day).toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
    $("sheet-time-field").hidden = !requesting;
    const rows = [];
    if (!requesting) rows.push(...sheetRow("Time", day.time_label || "10:00 AM"));
    rows.push(...sheetRow("Package", `${p.label} · ${state.kids} kids`));
    if (quote) {
      rows.push(...sheetRow("Duration", `${quote.hours} hours`));
      rows.push(...sheetRow("Total", money(quote.total_cents), "money"));
      rows.push(...sheetRow(requesting ? "Deposit, after we confirm" : "Deposit due now", money(quote.deposit_cents), "money strong"));
      rows.push(...sheetRow("Balance due day of party", money(quote.balance_cents), "money"));
    } else {
      rows.push(...sheetRow("Total", state.quoteError || "Updating…", "stale"));
    }
    $("sheet-rows").replaceChildren(...rows);
    $("sheet-note").textContent = requesting
      ? "No payment now, and nothing is booked until we confirm with you. We'll text you back. Questions: (646) 203-8087."
      : "Your date is held for 15 minutes while you pay the $100 deposit. Nobody else can take it in that time. Full refund with 7+ days notice.";
    $("sheet-confirm").textContent = requesting ? "Send request" : "Hold my date";
    $("sheet-confirm").disabled = !quote || state.busy;
    $("sheet-dismiss").disabled = state.busy;
  }

  function openSheet(day) {
    state.sheetDay = day;
    state.busy = false;
    sheetError(null);
    $("preferred_time").value = isWeekendDay(day) ? "10:00" : "16:00";
    renderSheet();
    if (!$("sheet").open) $("sheet").showModal();
  }

  function closeSheet() {
    if ($("sheet").open) $("sheet").close();
    state.sheetDay = null;
    state.busy = false;
  }

  function sheetError(message) {
    $("sheet-error").replaceChildren(message ? el("p", { class: "notice error sheet-error-text", text: message }) : "");
  }

  // ---------- Hold and request ----------
  function formError(message) {
    $("form-error").replaceChildren(message ? el("p", { class: "notice error", text: message }) : "");
  }

  function localTime(iso) {
    return new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  }

  function readDetails() {
    const values = Object.fromEntries(new FormData($("details-form")).entries());
    const missing = ["first_name", "last_name", "phone", "email"].filter((k) => !String(values[k] || "").trim());
    return { values, missing };
  }

  async function confirmSheet() {
    const day = state.sheetDay;
    if (!day || state.busy) return;
    sheetError(null);
    formError(null);
    const { values, missing } = readDetails();
    if (missing.length) {
      closeSheet();
      formError("Please fill in your name, phone, and email, then pick your date again.");
      $("details-form").elements[missing[0]].focus();
      $("details-panel").scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    state.busy = true;
    renderSheet();
    $("sheet-confirm").textContent = requestMode() ? "Sending…" : "Holding your date…";
    if (requestMode()) await submitRequest(day, values);
    else await submitHold(day, values);
    if (state.sheetDay) {
      state.busy = false;
      renderSheet();
    }
  }

  function contact(values) {
    return {
      first_name: values.first_name.trim(),
      last_name: values.last_name.trim(),
      phone: values.phone.trim(),
      email: values.email.trim(),
      notes: String(values.notes || "").trim() || null,
    };
  }

  async function submitHold(day, values) {
    const { status, data } = await api("/api/holds", {
      method: "POST",
      body: { slot_id: day.slot_id, package: state.packageKey, kids: state.kids, addons: state.addons, ...contact(values) },
    });
    if (status === 201) {
      closeSheet();
      startHold({ id: data.hold_id, checkoutUrl: data.checkout_url, deadline: Date.now() + data.seconds_remaining * 1000 });
      return;
    }
    if (status === 409) {
      closeSheet();
      await loadCalendar();
      const alts = data.alternatives || [];
      showSlotNotice(`Sorry, ${day.day_label} was just taken. ${alts.length ? "These are still open:" : "No other dates are open right now."}`, alts, true);
      return;
    }
    if (status === 429 && data && data.expires_at) {
      sheetError(`${detailText(data)} It's held until ${localTime(data.expires_at)}. Finish that checkout, or try again after it expires.`);
      return;
    }
    if (status === 422 || status === 404) {
      state.busy = false;
      await loadCalendar();
    }
    if (state.sheetDay) sheetError(detailText(data));
  }

  async function submitRequest(day, values) {
    const { status, data } = await api("/api/requests", {
      method: "POST",
      body: {
        date: day.date,
        preferred_time: $("preferred_time").value,
        package: state.packageKey,
        kids: state.kids,
        addons: state.addons,
        ...contact(values),
      },
    });
    if (status === 201) {
      closeSheet();
      $("builder").hidden = true;
      $("request-done").hidden = false;
      $("request-summary").replaceChildren(
        el("b", { text: `${data.package_label} · ${data.day_label} at ${data.time_label}` }),
        ` · ${data.kids} kids · `,
        el("b", { text: money(data.total_cents) })
      );
      $("request-done").scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    if (status === 422) {
      state.busy = false;
      await loadCalendar();
    }
    if (state.sheetDay) sheetError(detailText(data));
  }

  async function startHold(hold) {
    state.hold = hold;
    store(HOLD_KEY, hold);
    $("builder").hidden = true;
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
    $("details-form").addEventListener("submit", (event) => event.preventDefault());
    $("weekday-form").addEventListener("submit", submitWeekday);
    $("weekday-toggle").addEventListener("click", () => {
      const form = $("weekday-form");
      form.hidden = !form.hidden;
      $("weekday-toggle").setAttribute("aria-expanded", String(!form.hidden));
      if (!form.hidden) $("weekday-date").focus();
    });
    const sheet = $("sheet");
    $("sheet-confirm").addEventListener("click", confirmSheet);
    $("sheet-dismiss").addEventListener("click", closeSheet);
    $("preferred_time").addEventListener("change", renderSheet);
    sheet.addEventListener("cancel", (event) => {
      if (state.busy) event.preventDefault();
    });
    sheet.addEventListener("click", (event) => {
      if (event.target === sheet && !state.busy) closeSheet();
    });
    sheet.addEventListener("close", () => {
      state.sheetDay = null;
    });

    const { status, data } = await api("/api/catalog");
    if (status !== 200) {
      $("packages").replaceChildren(el("p", { class: "notice error", text: detailText(data, "Couldn't load packages. Refresh to try again.") }));
      return;
    }
    state.catalog = data;
    bindCount($("kids-count"), data.min_kids, data.max_kids, () => state.kids, setKids);
    state.kids = pkg(state.packageKey).included_kids;
    if (await restoreHold()) return;
    renderPackages();
    renderKids();
    renderAddons();
    requestQuote();
    loadCalendar();
    setInterval(() => {
      if (!state.hold && document.visibilityState === "visible") loadCalendar();
    }, 30000);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && !state.hold) loadCalendar();
    });
  }

  boot();
})();
