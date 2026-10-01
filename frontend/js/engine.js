// The booking engine: four steps that swap in place inside the Book Your Party section.
// Forward clicks, and only these: a date card, SELECT on a package, Lock in, Pay.
(function () {
  const { api, detailText, money, clock, el, store, recall, bindCount } = window.Encore;
  const C = window.EncoreContent;
  const KEY = "encore.funnel";
  const DAY_MS = 86400000;

  const STEPS = ["date", "package", "addons", "confirm"];
  const LABELS = { date: "Date", package: "Package", addons: "Extras", confirm: "Confirm" };
  const PANELS = { date: "step-date", package: "step-package", addons: "step-addons", confirm: "step-confirm" };
  const STATUS_TEXT = { open: "Open", held: "On hold", booked: "Booked", blocked: "Unavailable", past: "Unavailable", request: "By request" };
  const SAVED = ["mode", "holdId", "slotId", "date", "dayLabel", "timeLabel", "expiresAt", "package", "addons", "kids", "contact", "step"];

  const state = {
    step: "date",
    mode: null,
    holdId: null,
    slotId: null,
    date: null,
    dayLabel: null,
    timeLabel: null,
    expiresAt: null,
    package: null,
    addons: {},
    kids: null,
    contact: {},
    days: [],
    weekendDefault: "open",
    today: null,
    firstBookable: null,
    catalog: null,
    quote: null,
    quoteFresh: false,
    quoteError: null,
    busy: false,
    deadline: null,
    timer: null,
  };

  const $ = (id) => document.getElementById(id);

  function save() {
    const saved = {};
    SAVED.forEach((key) => { saved[key] = state[key]; });
    store(KEY, saved);
  }

  function restore() {
    const saved = recall(KEY);
    if (!saved) return;
    SAVED.forEach((key) => {
      if (saved[key] !== undefined && saved[key] !== null) state[key] = saved[key];
    });
    state.addons = state.addons || {};
    state.contact = state.contact || {};
  }

  function clearBooking() {
    state.mode = null;
    state.holdId = null;
    state.slotId = null;
    state.date = null;
    state.dayLabel = null;
    state.timeLabel = null;
    state.expiresAt = null;
    state.step = "date";
    stopTimer();
    save();
  }

  function pkg(key) {
    return state.catalog ? state.catalog.packages.find((p) => p.key === (key || state.package)) : null;
  }

  function kidsValue() {
    const chosen = pkg();
    return state.kids || (chosen ? chosen.included_kids : 0);
  }

  function requesting() {
    return state.mode === "request";
  }

  function shortDate(dateStr) {
    return new Date(dateStr + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" });
  }

  function dayLabelFor(dateStr) {
    return new Date(dateStr + "T12:00:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
  }

  function shiftDate(dateStr, days) {
    return new Date(new Date(dateStr + "T12:00:00").getTime() + days * DAY_MS).toISOString().slice(0, 10);
  }

  function isWeekendDate(dateStr) {
    const dow = new Date(dateStr + "T12:00:00").getDay();
    return dow === 0 || dow === 6;
  }

  // ---------- Step plumbing ----------
  function reachable(step) {
    if (step === "date") return true;
    if (!state.date) return false;
    if (step === "package") return true;
    if (!state.package) return false;
    return true;
  }

  function showStep(name, push) {
    if (!reachable(name)) name = state.date ? "package" : "date";
    state.step = name;
    save();
    STEPS.forEach((step) => { $(PANELS[step]).hidden = step !== name; });
    $("step-done").hidden = true;
    $("step-dead").hidden = true;
    renderSteps();
    renderHoldLine();
    renderBar();
    if (name === "date") loadGrid();
    if (name === "package") renderPicks();
    if (name === "addons") renderOptions();
    if (name === "confirm") enterConfirm();
    ensureTimer();
    if (push) {
      history.pushState({ encoreStep: name }, "", "#book");
      $("book").scrollIntoView({ block: "start" });
    }
  }

  function renderSteps() {
    const index = STEPS.indexOf(state.step);
    $("steps").replaceChildren(
      ...STEPS.map((step, i) => {
        if (i < index && reachable(step)) {
          return el("li", { class: "done" }, [
            el("button", { type: "button", text: LABELS[step], onclick: () => showStep(step, true) }),
          ]);
        }
        return el("li", { class: i === index ? "current" : "", text: LABELS[step] });
      })
    );
  }

  function renderHoldLine() {
    const line = $("hold-line");
    if (!state.date || state.step === "date") {
      line.hidden = true;
      return;
    }
    line.hidden = false;
    const what = requesting() ? "Requesting" : state.step === "confirm" ? "You're holding" : "Holding";
    const when = state.timeLabel ? `${state.dayLabel} at ${state.timeLabel}` : state.dayLabel;
    const children = [el("span", { class: "what", text: what }), el("span", { class: "when", text: when })];
    if (!requesting() && state.holdId) children.push(el("span", { class: "timer", id: "hold-timer", text: "--:--" }));
    line.replaceChildren(...children);
  }

  function showDead(status) {
    const copy = {
      expired: "Your 15-minute hold ran out, so the date was released. Nothing was charged.",
      cancelled: "That hold was cancelled, and nothing was charged.",
      booked: "That date is already booked. If that was you, check your confirmation text.",
    };
    stopTimer();
    STEPS.forEach((step) => { $(PANELS[step]).hidden = true; });
    $("hold-line").hidden = true;
    $("step-done").hidden = true;
    const dead = $("step-dead");
    dead.hidden = false;
    dead.replaceChildren(
      el("div", { class: "notice error" }, [
        el("div", { text: copy[status] || copy.expired }),
        el("div", { class: "alts" }, [
          el("button", {
            type: "button",
            class: "chip",
            text: "Pick a date",
            onclick: () => {
              clearBooking();
              loadGrid();
              showStep("date", true);
            },
          }),
        ]),
      ])
    );
    clearBooking();
    renderSteps();
    renderBar();
  }

  // ---------- Sticky bar ----------
  function barFloor() {
    if (!state.catalog) return;
    $("quote-total").classList.remove("stale");
    $("quote-total").textContent = `from ${money(Math.min(...state.catalog.packages.map((p) => p.price_cents)))}`;
    $("quote-split").textContent = "Private venue party";
    $("quote-toggle").hidden = true;
    $("quote-lines").hidden = true;
  }

  function barQuote(quote) {
    const total = $("quote-total");
    total.classList.remove("stale");
    total.textContent = money(quote.total_cents);
    $("quote-split").replaceChildren(
      el("b", { text: `${money(quote.deposit_cents)} today` }),
      el("br"),
      `${money(quote.balance_cents)} day of party`
    );
    $("quote-toggle").hidden = false;
    const lines = (items) =>
      items.map((item) =>
        el("li", {}, [
          el("span", { text: item.quantity > 1 ? `${item.label} × ${item.quantity}` : item.label }),
          item.included ? el("span", { class: "inc", text: "Included" }) : el("span", { text: money(item.amount_cents) }),
        ])
      );
    $("quote-lines").replaceChildren(
      ...lines(quote.line_items),
      el("li", {}, [el("span", { text: "Deposit due now" }), el("span", { text: money(quote.deposit_cents) })]),
      el("li", {}, [el("span", { text: "Balance due day of party" }), el("span", { text: money(quote.balance_cents) })])
    );
  }

  function renderBar() {
    const cta = $("quote-cta");
    if (state.package && state.quote) barQuote(state.quote);
    else barFloor();
    if (state.step === "addons") {
      cta.hidden = false;
      const label = requesting()
        ? `Review request for ${shortDate(state.date)}`
        : `Lock in ${shortDate(state.date)} ($100 today)`;
      cta.replaceChildren(
        el("button", { type: "button", class: "btn btn-primary", text: label, onclick: () => showStep("confirm", true) })
      );
    } else {
      cta.hidden = true;
      cta.replaceChildren();
    }
  }

  let quoteSeq = 0;
  async function requote() {
    if (!state.package) return;
    save();
    state.quoteFresh = false;
    state.quoteError = null;
    $("quote-total").classList.add("stale");
    const seq = ++quoteSeq;
    const { status, data } = await api("/api/quote", {
      method: "POST",
      body: { package: state.package, kids: kidsValue(), addons: state.addons },
    });
    if (seq !== quoteSeq) return;
    if (status !== 200) {
      state.quoteError = detailText(data, "Couldn't price that. Try again.");
      $("quote-split").replaceChildren(el("span", { class: "quote-error", text: state.quoteError }));
      renderSummary(null);
      return;
    }
    state.quote = data;
    state.quoteFresh = true;
    barQuote(data);
    renderSummary(data);
  }

  // ---------- Step 1: dates ----------
  async function loadGrid() {
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
    renderGrid();
    renderResume();
  }

  function weekendGroups() {
    const groups = [];
    state.days.filter((day) => isWeekendDate(day.date)).forEach((day) => {
      const last = groups[groups.length - 1];
      const sunday = new Date(day.date + "T12:00:00").getDay() === 0;
      if (sunday && last && last[last.length - 1].date === shiftDate(day.date, -1)) last.push(day);
      else groups.push([day]);
    });
    return groups;
  }

  function weekendLabel(group) {
    const first = group[0];
    const saturday = new Date(first.date + "T12:00:00").getDay() === 0 ? shiftDate(first.date, -1) : first.date;
    return `Weekend of ${new Date(saturday + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" })}`;
  }

  function renderGrid() {
    $("slots").replaceChildren(
      el(
        "div",
        { class: "wk-grid" },
        weekendGroups().map((group) =>
          el("div", { class: "wk-group" }, [
            el("div", { class: "wk-label", text: weekendLabel(group) }),
            el(
              "div",
              { class: "wk-cards" },
              group.map((day) => {
                const mine = Boolean(activeHold()) && day.slot_id === state.slotId;
                const tappable = mine || day.status === "open" || day.status === "request";
                const label = mine ? "Your date" : STATUS_TEXT[day.status];
                return el(
                  "button",
                  {
                    type: "button",
                    class: `wk-card ${mine ? "open yours" : day.status}`,
                    disabled: !tappable,
                    "aria-label": `${day.day_label} ${day.time_label || "10:00 AM"}, ${label}`,
                    onclick: !tappable
                      ? null
                      : mine
                      ? () => showStep(state.package ? "addons" : "package", true)
                      : () => pickDate(day),
                  },
                  [
                    el("span", { class: "wk-date", text: day.day_label }),
                    el("span", { class: "wk-time", text: day.time_label || "10:00 AM" }),
                    el("span", { class: "wk-state" }, [el("span", { text: label })]),
                    !mine && day.status === "held" ? el("span", { class: "wk-hint", text: "May free up in a few minutes" }) : null,
                  ]
                );
              })
            ),
          ])
        )
      )
    );
  }

  function activeHold() {
    return state.mode === "hold" && state.holdId && Date.parse(state.expiresAt) > Date.now() ? state : null;
  }

  function renderResume() {
    const notice = $("resume-notice");
    if (!activeHold()) {
      notice.replaceChildren();
      return;
    }
    notice.replaceChildren(
      el("div", { class: "notice" }, [
        el("div", {}, ["You're holding ", el("b", { text: `${state.dayLabel} at ${state.timeLabel}` }), ". Picking another date releases it."]),
        el("div", { class: "alts" }, [
          el("button", { type: "button", class: "chip", text: "Keep going", onclick: () => showStep(state.package ? "addons" : "package", true) }),
        ]),
      ])
    );
  }

  function notice(message, alternatives, isError) {
    const box = $("engine-notice");
    if (!message) {
      box.replaceChildren();
      return;
    }
    const alts = (alternatives || []).map((alt) =>
      el("button", {
        type: "button",
        class: "chip",
        text: `${alt.day_label}${alt.time_label ? ` ${alt.time_label}` : ""}`,
        onclick: () => pickDate(state.days.find((d) => d.date === alt.date) || alt),
      })
    );
    box.replaceChildren(
      el("div", { class: "notice" + (isError ? " error" : "") }, [
        el("div", { text: message }),
        alts.length ? el("div", { class: "alts" }, alts) : null,
      ])
    );
  }

  async function pickDate(day) {
    notice(null);
    if (day.status === "request") {
      startRequest(day.date, day.day_label);
      return;
    }
    if (state.busy) return;
    state.busy = true;
    const previous = activeHold() ? state.holdId : null;
    const { status, data } = await api("/api/holds", {
      method: "POST",
      body: { slot_id: day.slot_id, replace_hold_id: previous },
    });
    state.busy = false;
    if (status === 201) {
      state.mode = "hold";
      state.holdId = data.hold_id;
      state.slotId = day.slot_id;
      state.date = data.date;
      state.dayLabel = data.day_label;
      state.timeLabel = data.time_label;
      state.expiresAt = data.expires_at;
      state.deadline = performance.now() + data.seconds_remaining * 1000;
      save();
      if (state.package) requote();
      showStep("package", true);
      return;
    }
    if (status === 409) {
      await loadGrid();
      const alts = data.alternatives || [];
      notice(`Sorry, ${day.day_label} was just taken. ${alts.length ? "These are still open:" : "No other dates are open right now."}`, alts, true);
      return;
    }
    if (status === 429 && data && data.expires_at) {
      const until = new Date(data.expires_at).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
      notice(`${detailText(data)} Try again after ${until}.`, [], true);
      return;
    }
    notice(detailText(data, "Couldn't hold that date. Try again."), [], true);
  }

  function startRequest(dateStr, label) {
    state.mode = "request";
    state.holdId = null;
    state.slotId = null;
    state.date = dateStr;
    state.dayLabel = label || dayLabelFor(dateStr);
    state.timeLabel = null;
    state.expiresAt = null;
    save();
    if (state.package) requote();
    showStep("package", true);
  }

  // ---------- Step 2: package ----------
  function renderPicks() {
    if (!state.catalog) return;
    $("pick-grid").replaceChildren(
      ...state.catalog.packages.map((p) =>
        el("div", { class: "pick-card" + (p.key === "plus" ? " featured" : "") + (p.key === state.package ? " chosen" : "") }, [
          p.key === "plus" ? el("span", { class: "popular-tag", text: "Most popular" }) : null,
          el("div", { class: "pick-name", text: p.label }),
          el("div", { class: "pick-meta", text: `Up to ${p.included_kids} kids · ${p.hours} hours` }),
          el("div", { class: "pick-price-row" }, [
            el("span", { class: "cur", text: "$" }),
            el("span", { class: "pick-price", text: String(p.price_cents / 100) }),
          ]),
          el("div", { class: "pick-extra" }, [el("b", { text: `+${money(state.catalog.additional_kid_cents)}` }), " per additional kid"]),
          el("button", {
            type: "button",
            class: "btn " + (p.key === state.package ? "btn-primary" : "btn-ghost"),
            text: p.key === state.package ? `Selected: ${p.label}` : `Select ${p.label}`,
            onclick: () => selectPackage(p.key),
          }),
        ])
      )
    );
  }

  function selectPackage(key) {
    state.package = key;
    state.addons = state.addons || {};
    save();
    requote();
    showStep("addons", true);
  }

  // ---------- Step 3: extras ----------
  function packOption(addon, iconBox, body) {
    const max = state.catalog.max_pack_quantity;
    const qty = () => state.addons[addon.key] || 0;
    const input = el("input", {
      type: "text",
      inputmode: "numeric",
      pattern: "[0-9]*",
      enterkeyhint: "done",
      autocomplete: "off",
      maxlength: "2",
      "aria-label": `${addon.label} quantity`,
      value: String(qty()),
    });
    const minus = el("button", { type: "button", "aria-label": `Fewer ${addon.label}`, text: "−" });
    const plus = el("button", { type: "button", "aria-label": `More ${addon.label}`, text: "+" });
    const card = el("div", { class: "opt" }, [
      iconBox,
      body,
      el("div", { class: "opt-side" }, [
        el("span", { class: "opt-price", text: `+${money(addon.price_cents)}` }),
        el("div", { class: "stepper", role: "group", "aria-label": `${addon.label} quantity` }, [minus, input, plus]),
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
      if (clamped > 0) state.addons[addon.key] = clamped;
      else delete state.addons[addon.key];
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

  function setAddon(key, quantity) {
    if (quantity > 0) state.addons[key] = quantity;
    else delete state.addons[key];
    if (key === "tournament" && !quantity) delete state.addons.highlight_reel;
    renderOptions();
    requote();
  }

  function renderOptions() {
    if (!state.catalog || !pkg()) return;
    const included = pkg().included_addons;
    $("opt-grid").replaceChildren(
      ...state.catalog.addons.map((addon) => {
        const copy = C.ADDONS[addon.key] || { icon: "➕", short: "" };
        const blocked = addon.requires && !state.addons[addon.requires];
        const quantity = state.addons[addon.key] || 0;
        const iconBox = el("div", { class: "addon-icon", "aria-hidden": "true", text: copy.icon });
        const body = el("div", { class: "addon-body" }, [
          el("div", { class: "addon-name", text: addon.label }),
          el("div", { class: "addon-desc", text: blocked ? "Add Tournament + Livestream first." : copy.short }),
        ]);
        if (included.includes(addon.key)) {
          return el("div", { class: "opt on" }, [
            iconBox,
            body,
            el("div", { class: "opt-side" }, [el("span", { class: "opt-included", text: "Included" })]),
          ]);
        }
        if (addon.has_quantity) return packOption(addon, iconBox, body);
        return el(
          "button",
          {
            type: "button",
            class: "opt" + (quantity ? " on" : "") + (blocked ? " disabled" : ""),
            role: "switch",
            "aria-checked": String(Boolean(quantity)),
            disabled: Boolean(blocked),
            onclick: () => setAddon(addon.key, quantity ? 0 : 1),
          },
          [
            iconBox,
            body,
            el("div", { class: "opt-side" }, [
              el("span", { class: "opt-price", text: `+${money(addon.price_cents)}` }),
              el("span", { class: "toggle", "aria-hidden": "true" }),
            ]),
          ]
        );
      })
    );
  }

  // ---------- Step 4: confirm ----------
  function renderSummary(quote) {
    if (!quote) {
      $("summary-total").textContent = state.quoteError || "…";
      return;
    }
    $("summary-total").textContent = money(quote.total_cents);
    $("summary-lines").replaceChildren(
      ...quote.line_items.map((item) =>
        el("li", {}, [
          el("span", { text: item.quantity > 1 ? `${item.label} × ${item.quantity}` : item.label }),
          item.included ? el("span", { class: "inc", text: "Included" }) : el("span", { text: money(item.amount_cents) }),
        ])
      )
    );
    $("deposit-math").replaceChildren(
      `${money(quote.total_cents)} total, `,
      el("b", { text: `${money(quote.deposit_cents)} today` }),
      `, ${money(quote.balance_cents)} day of party`
    );
  }

  function restoreForm() {
    const saved = state.contact || {};
    [["email", "email"], ["parent_name", "parentName"], ["phone", "phone"], ["kid_name", "kidName"], ["kid_age", "kidAge"]].forEach(
      ([id, key]) => { if (saved[key]) $(id).value = saved[key]; }
    );
    $("kids").value = state.kids ? String(state.kids) : "";
    $("kids").placeholder = `up to ${pkg().included_kids} included`;
  }

  function readForm() {
    const value = (id) => $(id).value.trim();
    return {
      email: value("email"),
      parentName: value("parent_name"),
      phone: value("phone"),
      kidName: value("kid_name"),
      kidAge: value("kid_age"),
      kids: value("kids"),
    };
  }

  function rememberForm() {
    state.contact = readForm();
    save();
  }

  function payError(message) {
    $("pay-error").replaceChildren(message ? el("p", { class: "notice error", text: message }) : "");
  }

  function validate() {
    const form = readForm();
    const checks = [
      ["email", form.email.includes("@"), "Enter your email."],
      ["parent_name", form.parentName.length > 0, "Enter your name."],
      ["phone", form.phone.length >= 7, "Enter your phone number."],
      ["kid_name", form.kidName.length > 0, "Enter the birthday kid's name."],
      ["kid_age", /^\d{1,2}$/.test(form.kidAge) && Number(form.kidAge) > 0, "Enter the kid's age."],
      [
        "kids",
        /^\d{1,2}$/.test(form.kids) && Number(form.kids) >= state.catalog.min_kids && Number(form.kids) <= state.catalog.max_kids,
        `Estimated headcount: ${state.catalog.min_kids} to ${state.catalog.max_kids} kids.`,
      ],
    ];
    for (const [id, ok, message] of checks) {
      if (!ok) {
        payError(message);
        $(id).focus();
        return null;
      }
    }
    return form;
  }

  function splitName(full) {
    const parts = full.split(/\s+/).filter(Boolean);
    return { first: parts[0] || "", last: parts.slice(1).join(" ") };
  }

  // The countdown runs from the server's seconds_remaining against a monotonic clock, for as long as a hold exists.
  function ensureTimer() {
    if (state.mode !== "hold" || !state.deadline) return;
    tick();
    if (!state.timer) state.timer = setInterval(tick, 250);
  }

  function stopTimer() {
    clearInterval(state.timer);
    state.timer = null;
    state.deadline = null;
  }

  function tick() {
    const remaining = (state.deadline - performance.now()) / 1000;
    const timer = $("hold-timer");
    if (timer) {
      timer.textContent = clock(Math.ceil(remaining));
      timer.classList.toggle("low", remaining < 120);
    }
    if (remaining <= 0 && !state.busy) showDead("expired");
  }

  async function enterConfirm() {
    $("time-field").hidden = !requesting();
    $("risk-line").hidden = requesting();
    $("pay-fine").hidden = requesting();
    $("pay-button").textContent = requesting() ? "Send request" : "Pay $100 & lock it in";
    payError(null);
    restoreForm();
    renderSummary(state.quoteFresh ? state.quote : null);
    if (requesting()) return;
    // The real hold row drives the countdown; a dead hold shows the released panel instead of a form.
    const { status, data } = await api(`/api/holds/${state.holdId}`);
    if (status !== 200) {
      showDead("expired");
      return;
    }
    if (data.status !== "pending_payment") {
      showDead(data.status);
      return;
    }
    state.expiresAt = data.expires_at;
    save();
    state.deadline = performance.now() + data.seconds_remaining * 1000;
    renderHoldLine();
    ensureTimer();
  }

  async function pay() {
    if (state.busy) return;
    payError(null);
    const form = validate();
    if (!form) return;
    const button = $("pay-button");
    state.busy = true;
    button.disabled = true;
    button.textContent = requesting() ? "Sending…" : "Starting secure checkout…";
    const name = splitName(form.parentName);
    const body = {
      package: state.package,
      kids: Number(form.kids),
      addons: state.addons,
      first_name: name.first,
      last_name: name.last,
      phone: form.phone,
      email: form.email,
      kid_name: form.kidName,
      kid_age: Number(form.kidAge),
    };
    const result = requesting()
      ? await api("/api/requests", { method: "POST", body: { date: state.date, preferred_time: $("preferred_time").value, ...body } })
      : await api(`/api/holds/${state.holdId}/checkout`, { method: "POST", body: body });
    const { status, data } = result;
    if (!requesting() && status === 200) {
      window.location.href = data.checkout_url;
      return;
    }
    if (requesting() && status === 201) {
      stopTimer();
      store(KEY, null);
      STEPS.forEach((step) => { $(PANELS[step]).hidden = true; });
      $("hold-line").hidden = true;
      $("step-done").hidden = false;
      $("request-summary").replaceChildren(
        el("b", { text: `${data.package_label} · ${data.day_label} at ${data.time_label}` }),
        ` · ${data.kids} kids · `,
        el("b", { text: money(data.total_cents) })
      );
      return;
    }
    state.busy = false;
    button.disabled = false;
    button.textContent = requesting() ? "Send request" : "Pay $100 & lock it in";
    if (status === 409 && data && data.status) {
      showDead(data.status);
      return;
    }
    if (status === 429 && data && data.expires_at) {
      const until = new Date(data.expires_at).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
      payError(`${detailText(data)} It frees up around ${until}.`);
      return;
    }
    payError(detailText(data));
  }

  function saveEmailOnBlur() {
    rememberForm();
    const email = $("email").value.trim();
    if (!requesting() && state.holdId && email.includes("@")) {
      api(`/api/holds/${state.holdId}/email`, { method: "PATCH", body: { email: email } });
    }
  }

  // ---------- Secondary asks ----------
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
    if (listed && listed.status !== "request") {
      if (listed.status === "open") pickDate(listed);
      else weekdayError(`${listed.day_label} is ${listed.status === "booked" ? "booked" : "not available online"}. Call (646) 203-8087 if you need that date.`);
      return;
    }
    if (!listed && isWeekendDate(date) && state.weekendDefault === "open") {
      weekdayError("Weekends are booked online for the next 8 weekends. For a later weekend, call (646) 203-8087.");
      return;
    }
    startRequest(date, dayLabelFor(date));
  }

  async function submitWaitlist(event) {
    event.preventDefault();
    const input = $("waitlist-email");
    const note = $("waitlist-note");
    if (!input.value.trim() || !input.value.includes("@")) {
      note.textContent = "Enter the email to notify.";
      return;
    }
    const { status, data } = await api("/api/waitlist", { method: "POST", body: { email: input.value.trim() } });
    note.textContent = status === 201 ? data.detail : detailText(data, "Couldn't save that. Try again.");
    if (status === 201) input.value = "";
  }

  function wireToggle(buttonId, formId, focusId) {
    $(buttonId).addEventListener("click", () => {
      const form = $(formId);
      form.hidden = !form.hidden;
      $(buttonId).setAttribute("aria-expanded", String(!form.hidden));
      if (!form.hidden) $(focusId).focus();
    });
  }

  // ---------- Boot ----------
  function preselect(packageKey) {
    state.package = packageKey;
    save();
    requote();
    if (state.step === "package") renderPicks();
    renderBar();
    $("book").scrollIntoView({ block: "start" });
  }

  async function boot(catalog) {
    state.catalog = catalog;
    restore();
    state.step = STEPS.indexOf(state.step) >= 0 ? state.step : "date";

    wireToggle("weekday-toggle", "weekday-form", "weekday-date");
    wireToggle("waitlist-toggle", "waitlist-form", "waitlist-email");
    $("weekday-form").addEventListener("submit", submitWeekday);
    $("waitlist-form").addEventListener("submit", submitWaitlist);
    $("pay-form").addEventListener("submit", (event) => event.preventDefault());
    $("pay-button").addEventListener("click", pay);
    $("email").addEventListener("blur", saveEmailOnBlur);
    ["parent_name", "phone", "kid_name", "kid_age"].forEach((id) => $(id).addEventListener("blur", rememberForm));
    $("kids").addEventListener("input", () => {
      const raw = $("kids").value.trim();
      if (/^\d{1,2}$/.test(raw)) {
        state.kids = Math.min(state.catalog.max_kids, Math.max(state.catalog.min_kids, Number(raw)));
        requote();
      }
    });
    $("quote-toggle").addEventListener("click", () => {
      const lines = $("quote-lines");
      lines.hidden = !lines.hidden;
      $("quote-toggle").setAttribute("aria-expanded", String(!lines.hidden));
      $("quote-toggle").textContent = lines.hidden ? "Details" : "Hide";
    });
    $("summary-toggle").addEventListener("click", () => {
      const lines = $("summary-lines");
      lines.hidden = !lines.hidden;
      $("summary-toggle").setAttribute("aria-expanded", String(!lines.hidden));
      $("summary-toggle").textContent = lines.hidden ? "Details" : "Hide";
    });
    window.addEventListener("popstate", (event) => {
      const step = (event.state && event.state.encoreStep) || "date";
      if (step !== state.step) showStep(step, false);
    });

    // A hold saved in a previous visit may be gone; never show a step that implies a date we no longer hold.
    if (state.mode === "hold" && state.holdId) {
      const { status, data } = await api(`/api/holds/${state.holdId}`);
      if (status !== 200 || data.status !== "pending_payment") clearBooking();
      else {
        state.expiresAt = data.expires_at;
        state.deadline = performance.now() + data.seconds_remaining * 1000;
        save();
      }
    }
    if (state.package) requote();
    showStep(state.step, false);
    history.replaceState({ encoreStep: state.step }, "", location.hash || location.pathname);
    loadGrid();
    setInterval(() => {
      if (document.visibilityState === "visible" && state.step === "date") loadGrid();
    }, 30000);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && state.step === "date") loadGrid();
    });
  }

  window.EncoreEngine = { boot: boot, preselect: preselect };
})();
