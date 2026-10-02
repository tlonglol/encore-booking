// The booking funnel: one shell, four steps that swap in the left block.
// Two doors in (package-led and date-led), one merge point at Extras.
(function () {
  const { api, detailText, money, el, store, recall, keep, kept, bindCount } = window.Encore;
  const C = window.EncoreContent;
  const KEY = "encore.draft";
  // The hold itself is browser-wide, not per-tab: see save() and restore().
  const HOLD_KEY = "encore.hold";
  const HOLD_FIELDS = ["mode", "holdId", "slotId", "date", "dayLabel", "timeLabel", "expiresAt", "step"];
  const STEPS = ["date", "plan", "addons", "info"];
  // Display copy only, mirroring "feeds about 10 kids" on the Pizza Pack card; never used for pricing.
  const KIDS_PER_PIZZA_PACK = 10;
  const STATUS_TEXT = { open: "Open", held: "On hold", booked: "Booked", blocked: "Unavailable", past: "Unavailable", request: "By request" };
  const DRAFT_KEYS = ["mode", "holdId", "slotId", "date", "dayLabel", "timeLabel", "expiresAt", "package", "addons", "kids", "contact", "seenAddons", "seenInfo", "birthday", "step"];
  const DAY_MS = 86400000;
  const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)");

  const draft = {
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
    seenAddons: false,
    seenInfo: false,
    birthday: null,
    step: null,
  };

  const ui = {
    step: null,
    catalog: null,
    days: [],
    weekends: null,
    maxWeekends: 52,
    loadingMore: false,
    today: null,
    firstBookable: null,
    quote: null,
    busy: false,
    deadline: null,
    timer: null,
    gridTimer: null,
    popover: null,
    // Which date's waitlist panel is open, and the dates this page view has already signed up for.
    // Both are view state only: the server holds the signup, this just stops the panel reopening.
    waitlistOpen: null,
    waitlisted: {},
    picked: null,
    birthdayNote: "",
    policyOpen: false,
    holdModal: null,
    delta: null,
    pendingDelta: null,
    deltaTimer: null,
    dead: null,
  };

  const $ = (id) => document.getElementById(id);
  const stepRoot = () => $("step");

  // ---------- draft state ----------
  function save() {
    const saved = {};
    DRAFT_KEYS.forEach((key) => { saved[key] = draft[key]; });
    store(KEY, saved);
  }

  // The contact details stay in the tab that typed them; only the hold travels, because every tab in this
  // browser has to know a date is already being held or confirming another one would hold a second date.
  // Written only where the server has just confirmed this hold is live, so a tab sitting on a dead hold
  // can never overwrite the pointer of a tab that has a real one.
  function rememberHold() {
    const pointer = {};
    HOLD_FIELDS.forEach((key) => { pointer[key] = draft[key]; });
    keep(HOLD_KEY, pointer);
  }

  // Dropping a hold clears the pointer only while it still names that hold: another tab may own a newer one.
  function forgetHold(dropped) {
    const pointer = kept(HOLD_KEY);
    if (pointer && (!dropped || pointer.holdId === dropped)) keep(HOLD_KEY, null);
  }

  function noteStep() {
    const pointer = kept(HOLD_KEY);
    if (!pointer || pointer.holdId !== draft.holdId) return;
    pointer.step = draft.step;
    keep(HOLD_KEY, pointer);
  }

  function restore() {
    const saved = recall(KEY);
    if (saved) {
      DRAFT_KEYS.forEach((key) => {
        if (saved[key] !== undefined && saved[key] !== null) draft[key] = saved[key];
      });
      draft.addons = draft.addons || {};
      draft.contact = draft.contact || {};
    }
    // A new tab has no draft of its own, but the browser may still be holding a date. Boot checks it with
    // the server, so a stale pointer costs one request and is dropped.
    if (draft.holdId) return;
    const pointer = kept(HOLD_KEY);
    if (!pointer || !pointer.holdId) return;
    HOLD_FIELDS.forEach((key) => {
      if (pointer[key] !== undefined && pointer[key] !== null) draft[key] = pointer[key];
    });
  }

  function clearDraft() {
    const dropped = draft.holdId;
    draft.mode = null;
    draft.seenAddons = false;
    draft.seenInfo = false;
    draft.holdId = null;
    draft.slotId = null;
    draft.date = null;
    draft.dayLabel = null;
    draft.timeLabel = null;
    draft.expiresAt = null;
    stopTimer();
    save();
    forgetHold(dropped);
  }

  function pkg(key) {
    return ui.catalog ? ui.catalog.packages.find((p) => p.key === (key || draft.package)) : null;
  }

  function cap() {
    const chosen = pkg();
    return chosen ? chosen.included_kids : 0;
  }

  function kidsValue() {
    return draft.kids || cap();
  }

  function holding() {
    return draft.mode === "hold" && Boolean(draft.holdId);
  }

  // Mirror the draft onto the hold row, priced by the server. Never blocks the UI.
  let draftSeq = 0;
  function pushDraft() {
    if (!holding() || !draft.package) return;
    const seq = ++draftSeq;
    api(`/api/holds/${draft.holdId}/draft`, {
      method: "PATCH",
      body: { package: draft.package, kids: kidsValue(), addons: draft.addons, birthday: draft.birthday || null },
    }).then((result) => {
      if (seq !== draftSeq || result.status !== 200) return;
      ui.quote = result.data;
      renderMoney();
    });
  }

  // ---------- pricing ----------
  let quoteSeq = 0;
  async function requote() {
    save();
    if (!draft.package) {
      ui.quote = null;
      renderMoney();
      renderAdvance();
      return;
    }
    const seq = ++quoteSeq;
    $("rail-money").classList.add("pending");
    const { status, data } = await api("/api/quote", {
      method: "POST",
      body: { package: draft.package, kids: kidsValue(), addons: draft.addons },
    });
    if (seq !== quoteSeq) return;
    $("rail-money").classList.remove("pending");
    if (status === 200) ui.quote = data;
    if (ui.pendingDelta) {
      ui.delta = ui.pendingDelta;
      ui.pendingDelta = null;
      clearTimeout(ui.deltaTimer);
      ui.deltaTimer = setTimeout(hideDelta, DELTA_MS);
    }
    renderMoney();
    renderAdvance();
    if (ui.step === "addons") renderOverage();
    if (ui.step === "info") renderSummary();
    pushDraft();
  }

  // ---------- routing ----------
  function pathStep() {
    const match = location.pathname.match(/^\/book\/([a-z]+)/);
    return match && STEPS.indexOf(match[1]) >= 0 ? match[1] : "date";
  }

  function allowed(step) {
    if (step === "date") return true;
    if (!draft.date) return false;
    if (step === "plan") return true;
    return Boolean(draft.package);
  }

  function earliestIncomplete() {
    if (!draft.date) return "date";
    if (!draft.package) return "plan";
    return "addons";
  }

  function go(step, mode) {
    const target = allowed(step) ? step : earliestIncomplete();
    const url = `/book/${target}`;
    if (mode === "replace" || target === ui.step) history.replaceState({ step: target }, "", url);
    else if (mode !== "pop") history.pushState({ step: target }, "", url);
    render(target);
  }

  // Sub-changes inside a step never add a history entry, so Back walks steps and never un-toggles an add-on.
  function touch() {
    history.replaceState({ step: ui.step }, "", `/book/${ui.step}`);
  }

  function render(step) {
    ui.step = step;
    draft.step = step;
    ui.popover = null;
    ui.policyOpen = false;
    save();
    if (step === "date") startGridPoll();
    else stopGridPoll();
    if (step === "date") renderDate();
    else if (step === "plan") renderPlan();
    else if (step === "addons") renderAddons();
    else renderInfo();
    renderRail();
    renderMoney();
    renderAdvance();
    document.documentElement.setAttribute("data-tier", draft.package || "none");
    noteStep();
    renderHoldHead();
    window.scrollTo({ top: 0, behavior: REDUCED.matches ? "auto" : "smooth" });
  }

  // ---------- persistent frame: the rail ----------
  function railValue(step) {
    if (step === 1) return draft.date ? `${draft.dayLabel}${draft.timeLabel ? ` · ${draft.timeLabel}` : ""}` : null;
    if (step === 2) return draft.package ? pkg().label : null;
    if (step === 3) {
      // Not "completed" until they have actually been there; defaults are not choices.
      if (!draft.package || !draft.seenAddons) return null;
      const names = Object.keys(draft.addons).map((key) => {
        const addon = ui.catalog.addons.find((a) => a.key === key);
        const quantity = draft.addons[key];
        return quantity > 1 ? `${addon.label} ×${quantity}` : addon.label;
      });
      return `${kidsValue()} kids${names.length ? ` · ${names.join(", ")}` : " · no add-ons"}`;
    }
    return draft.seenInfo && draft.contact && draft.contact.email ? draft.contact.email : null;
  }

  function railEditTarget(step) {
    if (step === 1) return "date";
    if (step === 2) return "plan";
    if (step === 3) return "addons";
    return "info";
  }

  function renderRail() {
    const current = STEPS.indexOf(ui.step) + 1;
    const labels = ["Date", "Package", "Extras", "Info & Deposit"];
    $("rail-steps").replaceChildren(
      ...labels.map((label, index) => {
        const number = index + 1;
        const value = railValue(number);
        const done = Boolean(value) && number < current;
        const lines = [
          el("div", { class: "rail-step-head" }, [
            el("span", { class: "rail-num", text: String(number) }),
            el("span", { class: "rail-label", text: label }),
            done
              ? el("button", {
                  type: "button",
                  class: "rail-edit",
                  "aria-label": `Edit ${label}`,
                  text: "Edit",
                  onclick: () => go(railEditTarget(number)),
                })
              : null,
          ]),
          value ? el("div", { class: "rail-value", text: value }) : null,
        ];
        // The chosen package explains itself: its top unlocks ride along in the rail.
        if (number === 1 && draft.birthday) {
          lines.push(el("div", { class: "rail-sub rail-sub-muted", text: `Birthday ${shortDate(draft.birthday)}` }));
        }
        if (number === 2 && draft.package) {
          const unlocks = C.highlights(draft.package).slice(0, 3).map((feature) => C.shortFor(feature, draft.package));
          lines.push(el("div", { class: "rail-sub", text: unlocks.join(" · ") }));
        }
        return el("li", { class: "rail-step" + (number === current ? " current" : "") + (done ? " done" : "") }, lines);
      })
    );
  }

  // The payment step is the info step of a booking that takes a deposit. Same source of truth as the rail's
  // step highlight (ui.step): there is no second copy of the step logic.
  function onPaymentStep() {
    return ui.step === "info";
  }

  const DELTA_MS = 1500;
  const FREE_CANCEL_DAYS = 7;

  // The policy promises "up to 7 days before your party"; naming the actual date is more concrete than
  // asking a parent to count backwards. The precise hour lives behind "details", since the free window
  // really closes at the party's start time on that day.
  function policyCutoff() {
    if (!draft.date) return null;
    const cutoff = new Date(new Date(draft.date + "T12:00:00").getTime() - FREE_CANCEL_DAYS * DAY_MS);
    return cutoff.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  }

  function renderPolicy() {
    const box = $("rail-policy");
    if (!box) return;
    if (!onPaymentStep()) {
      box.hidden = true;
      box.replaceChildren();
      return;
    }
    const by = policyCutoff();
    const full = by
      ? `Free cancel or reschedule up to ${FREE_CANCEL_DAYS} days before your party, until ${by} at ${draft.timeLabel || "10:00 AM"}. After that your deposit isn't refundable, but you can still reschedule once.`
      : C.REFUND_PROMISE;
    box.hidden = false;
    box.replaceChildren(
      ...[
        el("span", { text: by ? `Free cancel or reschedule until ${by}` : "Free cancel or reschedule up to 7 days before" }),
        " · ",
        el("button", {
          type: "button",
          class: "policy-more",
          "aria-expanded": String(ui.policyOpen),
          text: ui.policyOpen ? "less" : "details",
          onclick: () => {
            ui.policyOpen = !ui.policyOpen;
            renderPolicy();
          },
        }),
        ui.policyOpen ? el("span", { class: "policy-full", text: full }) : null,
      ].filter(Boolean)
    );
  }

  function catalogAddon(key) {
    return ui.catalog.addons.find((addon) => addon.key === key);
  }

  function ordinal(n) {
    const teens = n % 100;
    if (teens >= 11 && teens <= 13) return `${n}th`;
    return n + (["th", "st", "nd", "rd"][n % 10] || "th");
  }

  // "+$60 VR Gaming", "−$40 Pizza Pack", "+$35 · 26th kid". The sign is always shown.
  function deltaText(cents, name, dotted) {
    return `${cents > 0 ? "+" : "−"}${money(Math.abs(cents))}${dotted ? " · " : " "}${name}`;
  }

  // Held until the new total lands, so the chip and the number it explains appear together.
  function queueDelta(cents, name, dotted) {
    if (!cents) return;
    ui.pendingDelta = { text: deltaText(cents, name, dotted), add: cents > 0 };
  }

  function hideDelta() {
    const chip = $("rail-delta");
    if (chip) chip.classList.remove("show");
    ui.delta = null;
  }

  // The payment step's receipt: line items, then a divider, then Total / Due today / Due at the party.
  // Tax is never left unsaid: either the server priced a Sales tax line, or the note says it is included.
  function receipt(q) {
    const row = (label, amount, cls) =>
      el("div", { class: "rc-row" + (cls ? " " + cls : "") }, [
        el("span", { class: "rc-label", text: label }),
        el("span", { class: "rc-amt", text: amount }),
      ]);
    const items = q.line_items.filter((item) => !item.included);
    const taxed = items.some((item) => item.key === "sales_tax");
    return el("div", { class: "receipt" }, [
      ...items.map((item) => row(item.quantity > 1 ? `${item.label} × ${item.quantity}` : item.label, money(item.amount_cents))),
      row("Total", money(q.total_cents), "rc-total"),
      taxed ? null : el("div", { class: "rc-note", text: "Includes sales tax" }),
      row("Due today", money(q.deposit_cents), "rc-due-today"),
      row("Due at the party", money(q.balance_cents)),
    ]);
  }

  // The money block, step-aware. Pre-payment: the total leads (the decision number), "$100 today" is the
  // only accent, and the remainder sits on its own muted line. Payment step: "Due now" leads in the accent.
  function renderMoney() {
    const box = $("rail-money");
    if (!draft.package || !ui.quote) {
      ui.moneyShown = null;
      box.replaceChildren(
        el("div", { class: "rail-total", text: ui.catalog ? `from ${money(Math.min(...ui.catalog.packages.map((p) => p.price_cents)))}` : "—" }),
        el("div", { class: "rail-due", text: "Private venue party" })
      );
      return;
    }
    const q = ui.quote;
    const payment = onPaymentStep();
    const next = { mode: payment ? "pay" : "pre", total: q.total_cents, deposit: q.deposit_cents, balance: q.balance_cents };
    const prev = ui.moneyShown || {};
    // A changed amount fades in; a layout switch (pre-payment to payment step) does not.
    const fade = (key) => (prev.mode === next.mode && prev[key] !== next[key] ? " amt-fade" : "");
    ui.moneyShown = next;

    const lines = payment
      ? [
          // The phone bar is too small for a ledger, so it carries the lead number; the receipt is the rail.
          el("div", { class: "rail-lead" + fade("deposit"), text: `Due today: ${money(q.deposit_cents)}` }),
          receipt(q),
        ]
      : [
          el("div", { class: "rail-total-line" }, [
            el("span", { class: "rail-total" + fade("total"), text: money(q.total_cents) }),
            ui.delta ? el("span", { class: `rail-delta show ${ui.delta.add ? "add" : "remove"}`, id: "rail-delta", text: ui.delta.text }) : null,
          ]),
          // "$100 today" in the accent, then the remainder muted on the same line: the two amounts say it all.
          el("div", { class: "rail-today" }, [
            el("b", { class: "rail-today-amt" + fade("deposit"), text: `${money(q.deposit_cents)} today` }),
            el("span", { class: "rail-reassure" + fade("balance"), text: ` · ${money(q.balance_cents)} due at the party` }),
          ]),
        ];
    // replaceChildren stringifies a null child, so the DETAILS button is filtered out, not passed as null.
    const priced = q.line_items.filter((item) => !item.included).length;
    const details = payment
      ? null
      : el("button", {
          type: "button",
          class: "rail-details",
          text: `Details · ${priced} item${priced === 1 ? "" : "s"}`,
          onclick: () => openCompare("summary"),
        });
    box.replaceChildren(...[...lines, details].filter(Boolean));
  }

  function renderAdvance() {
    const button = $("advance");
    const note = $("rail-note");
    note.textContent = "";
    button.hidden = false;
    renderPolicy();
    if (ui.step === "date") {
      button.onclick = confirmDate;
      button.disabled = !ui.picked || ui.busy;
      button.textContent = ui.picked
        ? `Confirm ${shortDate(ui.picked.date)}`
        : draft.package
        ? `Pick a date for ${pkg().label}`
        : "Pick a date";
      note.textContent = ui.picked ? "Confirming holds this date for 15 minutes." : "";
      return;
    }
    if (ui.step === "plan") {
      button.disabled = true;
      button.textContent = "Choose a package";
      return;
    }
    if (ui.step === "addons") {
      button.disabled = !ui.quote;
      button.textContent = `Lock in ${shortDate(draft.date)} (${money(ui.quote ? ui.quote.deposit_cents : 10000)} today)`;
      button.onclick = () => go("info");
      return;
    }
    button.disabled = ui.busy;
    button.textContent = `Pay ${money(ui.quote ? ui.quote.deposit_cents : 10000)} & lock it in`;
    renderPolicy();
    button.onclick = pay;
    note.textContent = "Card secured by Square. You stay on this page.";
  }

  // ---------- helpers ----------
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

  function dollars(cents) {
    return "$" + Math.round(cents / 100).toLocaleString("en-US");
  }

  function notice(message, alternatives, isError) {
    const box = $("step-notice");
    if (!box) return;
    if (!message) {
      box.replaceChildren();
      return;
    }
    const alts = (alternatives || []).map((alt) =>
      el("button", {
        type: "button",
        class: "chip",
        text: `${alt.day_label}${alt.time_label ? ` ${alt.time_label}` : ""}`,
        onclick: () => selectDate(ui.days.find((d) => d.date === alt.date) || alt),
      })
    );
    box.replaceChildren(
      el("div", { class: "notice" + (isError ? " error" : "") }, [
        el("div", { text: message }),
        alts.length ? el("div", { class: "alts" }, alts) : null,
      ])
    );
  }

  // ---------- step 1: date ----------
  // Lucide "cake" (stroke icon, 24px grid), static trusted markup.
  function cakeIcon() {
    const span = el("span", { class: "bday-icon", "aria-hidden": "true" });
    span.innerHTML =
      '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M20 21v-8a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8"/><path d="M4 16s.5-1 2-1 2.5 2 4 2 2.5-2 4-2 2.5 2 4 2 2-1 2-1"/>' +
      '<path d="M2 21h20"/><path d="M7 8v3"/><path d="M12 8v3"/><path d="M17 8v3"/><path d="M7 4h.01"/><path d="M12 4h.01"/><path d="M17 4h.01"/></svg>';
    return span;
  }

  // Coming back to the date step keeps the confirmed date selected, so the rail offers
  // "Confirm {date}" again instead of making the parent pick it twice.
  function restorePicked() {
    if (ui.picked || !draft.date) return;
    ui.picked = {
      date: draft.date,
      day_label: draft.dayLabel,
      time_label: draft.timeLabel,
      slot_id: draft.slotId,
      status: "open",
    };
  }

  function renderDate() {
    restorePicked();
    const strip = el("div", { class: "tabs-wrap" }, [
      el("div", { class: "tabs-head" }, [
        el("button", { type: "button", class: "compare-btn", text: "Compare all plans", onclick: () => openCompare() }),
      ]),
      el(
        "div",
        { class: "tab-strip", role: "tablist", "aria-label": "Package" },
        ui.catalog.packages.map((p) =>
          el("div", { class: "tab-cell" + (p.key === draft.package ? " active" : ""), "data-tier": p.key }, [
            el("button", {
              type: "button",
              class: "tab-btn",
              role: "tab",
              "aria-selected": String(p.key === draft.package),
              onclick: () => chooseTier(p.key),
            }, [
              el("span", { class: "tab-name", text: p.label }),
              el("span", { class: "tab-price", text: dollars(p.price_cents) }),
            ]),
            el("button", {
              type: "button",
              class: "tab-info",
              "aria-label": `What ${p.label} includes`,
              text: "i",
              onclick: (event) => { event.stopPropagation(); togglePopover(p.key); },
            }),
          ])
        )
      ),
      el("div", { class: "diff-strip", id: "diff-strip" }),
      el("div", { class: "tab-pop", id: "tab-pop", hidden: true }),
      el("div", { class: "bday-card" }, [
        cakeIcon(),
        el("div", { class: "bday-text" }, [
          el("label", { for: "bday-m", class: "bday-title" }, ["When's the big day? ", el("span", { class: "bday-opt", text: "(optional)" })]),
          el("div", { class: "bday-benefit", text: "We'll line up the closest open weekends." }),
        ]),
        el("div", { class: "bday-pick" }, [
          dateControl(),
          el("button", { type: "button", class: "link-btn", id: "bday-clear", hidden: !draft.birthday, text: "Clear", onclick: clearBirthday }),
        ]),
      ]),
      el("p", { class: "bday-msg", id: "bday-msg", role: "alert" }),
    ]);

    stepRoot().replaceChildren(
      el("div", { class: "step-head" }, [
        el("h1", { class: "step-title", text: "Pick your date" }),
        el("p", { class: "step-sub", text: "Saturday and Sunday mornings at 10:00 AM. Pick a date, then confirm it to hold it for 15 minutes while you finish." }),
      ]),
      strip,
      el("div", { id: "step-notice", "aria-live": "polite" }),
      el("div", { id: "slots" }, [el("p", { class: "muted", text: "Loading dates…" })]),
      el("p", { class: "grid-foot" }, [
        "Don't see your date? Pick any booked date above to get notified, or call ",
        el("a", { href: "tel:+16462038087", text: "(646) 203-8087" }),
        ".",
      ])
    );
    renderDiff(null);
    renderGrid();
    setSegments(draft.birthday);
    loadGrid();
  }

  const MORE_STEP = 8;
  const GRID_POLL_MS = 30000;

  // Someone else's hold lapses, or another tab of this browser swaps a date away: either way the grid in
  // front of the parent is wrong until it asks again. Only while the grid is the thing on screen.
  function startGridPoll() {
    if (ui.gridTimer) return;
    ui.gridTimer = setInterval(() => {
      if (ui.step === "date" && !document.hidden && !ui.busy) loadGrid();
    }, GRID_POLL_MS);
  }

  function stopGridPoll() {
    clearInterval(ui.gridTimer);
    ui.gridTimer = null;
  }

  async function loadGrid() {
    const query = ui.weekends ? `?weekends=${ui.weekends}` : "";
    const { status, data } = await api(`/api/availability${query}`);
    if (status !== 200) {
      const slots = $("slots");
      if (slots) slots.replaceChildren(el("p", { class: "notice error", text: detailText(data, "Couldn't load dates. Refresh to try again.") }));
      return;
    }
    ui.days = data.days;
    ui.weekends = data.weekends;
    ui.maxWeekends = data.max_weekends;
    ui.today = data.today;
    ui.firstBookable = data.first_bookable_date;
    if (draft.birthday && draft.birthday < data.today) {
      draft.birthday = null;
      save();
    }
    const bday = $("bday");
    if (bday) {
      bday.min = data.today;
      bday.max = shiftDate(data.today, 366);
    }
    if (draft.birthday) {
      // Cover the birthday and the weekend after it, on demand: this is what keeps the default list at 8.
      const needed = Math.min(ui.maxWeekends, Math.ceil(window.EncoreFinder.diffDays(draft.birthday, data.today) / 7) + 2);
      if (ui.weekends < needed) {
        ui.weekends = needed;
        return loadGrid();
      }
      // Nothing bookable near it: look further out for the nearest open weekend, up to the cap.
      if (ui.weekends < ui.maxWeekends && window.EncoreFinder.suggest(weekendGroups(), draft.birthday).exhausted) {
        ui.weekends = Math.min(ui.maxWeekends, ui.weekends + MORE_STEP);
        return loadGrid();
      }
    }
    ui.birthdayNote =
      draft.birthday && draft.birthday > data.days[data.days.length - 1].date
        ? "Weekends that far out aren't open for online booking yet. Call (646) 203-8087."
        : "";
    // A date held further out than the list reaches must stay visible: widen the list to cover it.
    const last = data.days[data.days.length - 1].date;
    if (draft.date && draft.date > last && ui.weekends < ui.maxWeekends) {
      const gap = (new Date(draft.date + "T12:00:00") - new Date(last + "T12:00:00")) / DAY_MS;
      ui.weekends = Math.min(ui.maxWeekends, ui.weekends + Math.ceil(gap / 7) + 1);
      return loadGrid();
    }
    // We believe we hold a date and the grid just said it is free: the hold is gone, expired or swapped
    // away in another tab. The server settles which, and shows the modal if the parent needs telling.
    if (holding()) {
      const ours = ui.days.find((day) => day.slot_id === draft.slotId);
      if (ours && ours.status !== "held") await syncHold();
    }
    if (ui.picked && ui.picked.slot_id) {
      const fresh = ui.days.find((d) => d.date === ui.picked.date);
      const mine = holding() && ui.picked.slot_id === draft.slotId;
      if (!mine && (!fresh || fresh.status !== "open")) {
        notice(`${ui.picked.day_label} just became unavailable. Pick another date.`, [], true);
        ui.picked = null;
      }
    }
    renderGrid();
    renderAdvance();
  }

  function weekendGroups() {
    const groups = [];
    ui.days.filter((day) => isWeekendDate(day.date)).forEach((day) => {
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

  function tierPriceCents() {
    const chosen = pkg();
    if (chosen) return chosen.price_cents;
    return Math.min(...ui.catalog.packages.map((p) => p.price_cents));
  }

  // One date card, with its live state in words. Used by every layout of the grid.
  //
  // A taken date is still clickable, but for a different reason than an open one: it opens the waitlist
  // panel. It keeps its BOOKED or ON HOLD word and styling throughout, because a date someone else has
  // must never look even slightly available. The panel is a sibling of the button, not a child: a form
  // cannot live inside a <button>.
  function dayCard(day, price, prefix) {
    const mine = holding() && day.slot_id === draft.slotId;
    const picked = Boolean(ui.picked) && ui.picked.date === day.date;
    const tappable = mine || day.status === "open";
    const waitable = !mine && (day.status === "booked" || day.status === "held");
    const label = picked ? "Selected" : mine ? "Your date" : STATUS_TEXT[day.status];
    const live = day.status === "open" || mine;
    const joined = ui.waitlisted[day.slot_id];
    const open = ui.waitlistOpen === day.slot_id && !joined;

    const card = el(
      "button",
      {
        type: "button",
        class: `wk-card ${picked || mine ? "open yours" : day.status}`,
        disabled: !tappable && !waitable,
        "aria-pressed": String(picked),
        "aria-expanded": waitable ? String(open) : null,
        "aria-label": waitable
          ? `${day.day_label} ${day.time_label || "10:00 AM"}, ${label}. Get notified if it opens up.`
          : `${day.day_label} ${day.time_label || "10:00 AM"}, ${label}`,
        onclick: tappable ? () => selectDate(day) : waitable ? () => toggleWaitlist(day) : null,
      },
      [
        el("span", { class: "wk-date", text: day.day_label }),
        el("span", { class: "wk-time", text: day.time_label || "10:00 AM" }),
        live
          ? el("span", { class: "wk-price", "data-cents": String(price) }, [prefix, el("span", { class: "wk-price-num", text: dollars(price) })])
          : null,
        el("span", { class: "wk-state" }, [el("span", { text: label })]),
        !mine && day.status === "held" ? el("span", { class: "wk-hint", text: "May free up in a few minutes" }) : null,
        waitable && !joined ? el("span", { class: "wk-notify", text: open ? "Close" : "Notify me" }) : null,
      ]
    );

    if (!waitable) return card;
    return el("div", { class: "wk-cell" }, [
      card,
      joined ? el("p", { class: "wk-joined", role: "status", text: joined }) : null,
      open ? waitlistPanel(day) : null,
    ]);
  }

  // The ask, inline on the card that was clicked.
  function waitlistPanel(day) {
    const held = day.status === "held";
    const noteId = `wl-note-${day.slot_id}`;
    return el("form", { class: "wk-ask", onsubmit: (event) => submitWaitlist(event, day) }, [
      el("p", {
        class: "wk-ask-lede",
        text: held
          ? `${day.day_label} is on hold and may free up in a few minutes. Want a text if it does?`
          : `${day.day_label} is booked. Want a text if it opens up?`,
      }),
      el("div", { class: "field" }, [
        el("label", { for: `wl-email-${day.slot_id}`, text: "Your email" }),
        el("input", {
          id: `wl-email-${day.slot_id}`,
          name: "email",
          type: "email",
          inputmode: "email",
          autocomplete: "email",
          placeholder: "you@email.com",
          required: true,
        }),
      ]),
      el("div", { class: "field" }, [
        el("label", { for: `wl-phone-${day.slot_id}`, text: "Phone (optional)" }),
        el("input", {
          id: `wl-phone-${day.slot_id}`,
          name: "phone",
          type: "tel",
          inputmode: "tel",
          autocomplete: "tel",
          placeholder: "(646) 000-0000",
        }),
      ]),
      el("button", { type: "submit", class: "btn btn-primary btn-sm", text: "Notify me" }),
      el("p", { class: "ask-note", id: noteId, role: "status" }),
    ]);
  }

  function toggleWaitlist(day) {
    ui.waitlistOpen = ui.waitlistOpen === day.slot_id ? null : day.slot_id;
    renderGrid();
    const field = $(`wl-email-${day.slot_id}`);
    if (field) field.focus();
  }

  function weekendBlock(group, price, prefix, note) {
    return el("div", { class: "wk-group" }, [
      el("div", { class: "wk-label" }, [weekendLabel(group), note ? el("span", { class: "wk-note", text: ` · ${note}` }) : null]),
      el("div", { class: "wk-cards" }, group.map((day) => dayCard(day, price, prefix))),
    ]);
  }

  function renderGrid() {
    const slots = $("slots");
    if (!slots) return;
    const price = tierPriceCents();
    const prefix = draft.package ? "" : "from ";
    const groups = weekendGroups();

    if (!draft.birthday) {
      slots.replaceChildren(
        el("div", { class: "wk-grid" }, groups.map((group) => weekendBlock(group, price, prefix))),
        moreWeekends()
      );
      return;
    }

    // Birthday set: the closest weekends are pinned on top; every other weekend is still here, under a fold.
    const found = window.EncoreFinder.suggest(groups, draft.birthday);
    const children = [
      el("div", { class: "closest" }, [
        el("div", { class: "closest-title" }, [
          "Closest to the birthday",
          el("span", { text: ` · ${dayLabelFor(draft.birthday)}` }),
        ]),
        el("div", { class: "wk-grid" }, found.pinned.map((entry) => weekendBlock(entry.group, price, prefix, entry.note))),
        found.noneNearby
          ? el("p", { class: "closest-none" }, [
              found.exhausted ? "No open weekends near that date. " : "The weekends right around that date are taken. ",
              el("button", {
                type: "button",
                class: "link-btn",
                text: "Get notified",
                onclick: () => openWaitlistFor(found.pinned),
              }),
              " if one opens up.",
            ])
          : null,
        ui.birthdayNote ? el("p", { class: "closest-none", text: ui.birthdayNote }) : null,
      ]),
    ];
    if (found.rest.length) {
      children.push(
        el("details", { class: "all-wk" }, [
          el("summary", { text: `All weekends (${found.rest.length} more)` }),
          el("div", { class: "wk-grid" }, found.rest.map((group) => weekendBlock(group, price, prefix))),
        ])
      );
    }
    children.push(moreWeekends());
    slots.replaceChildren(...children);
  }

  // Opens the panel on the nearest date the copy is actually referring to, so the signup names a date
  // rather than being the blind "notify me about something" this replaced.
  function openWaitlistFor(pinned) {
    const taken = (pinned || [])
      .flatMap((entry) => entry.group)
      .find((day) => day.status === "booked" || day.status === "held");
    if (!taken) return;
    ui.waitlistOpen = taken.slot_id;
    renderGrid();
    const field = $(`wl-email-${taken.slot_id}`);
    if (field) {
      field.scrollIntoView({ block: "center", behavior: REDUCED.matches ? "auto" : "smooth" });
      field.focus();
    }
  }

  // Loads further weekends on demand, MORE_STEP at a time, up to the server's cap.
  function moreWeekends() {
    if (!ui.weekends) return null;
    if (ui.weekends >= ui.maxWeekends) {
      return el("p", { class: "more-end", text: "That's as far ahead as we book online. For later dates, call (646) 203-8087." });
    }
    return el("div", { class: "more-wrap" }, [
      el("button", {
        type: "button",
        class: "more-btn",
        id: "more-weekends",
        disabled: ui.loadingMore,
        "aria-expanded": "false",
        text: ui.loadingMore ? "Loading…" : "Show more weekends",
        onclick: showMore,
      }),
    ]);
  }

  async function showMore() {
    if (ui.loadingMore) return;
    ui.loadingMore = true;
    renderGrid();
    ui.weekends = Math.min(ui.maxWeekends, (ui.weekends || 8) + MORE_STEP);
    await loadGrid();
    ui.loadingMore = false;
    renderGrid();
    // Put the first newly loaded weekend in view so it is obvious something appeared.
    const groups = document.querySelectorAll(".wk-group");
    const firstNew = groups[Math.max(0, groups.length - MORE_STEP)];
    if (firstNew) firstNew.scrollIntoView({ block: "center", behavior: REDUCED.matches ? "auto" : "smooth" });
  }

  // Tab switch: the prices change colour (a CSS crossfade) and the numbers swap at once.
  function tweenPrices(toCents) {
    const prefix = draft.package ? "" : "from ";
    document.querySelectorAll(".wk-price").forEach((node) => {
      node.dataset.cents = String(toCents);
      node.firstChild.textContent = prefix;
      const num = node.querySelector(".wk-price-num");
      if (num) num.textContent = dollars(toCents);
    });
  }

  function chooseTier(key) {
    const previous = draft.package;
    if (previous === key) return;
    setPackage(key);
    document.querySelectorAll(".tab-cell").forEach((cell) => {
      const active = cell.dataset.tier === key;
      cell.classList.toggle("active", active);
      cell.querySelector(".tab-btn").setAttribute("aria-selected", String(active));
    });
    renderDiff(previous);
    tweenPrices(pkg().price_cents);
    ui.popover = null;
    $("tab-pop").hidden = true;
    renderRail();
    renderMoney();
    renderAdvance();
    touch();
  }

  function renderDiff(previous) {
    const strip = $("diff-strip");
    if (!strip) return;
    if (!draft.package) {
      strip.replaceChildren(el("span", { class: "diff-muted", text: "Every plan is a private full-venue buyout. Pick a tier to see what changes, or just pick a date." }));
      return;
    }
    const from = previous || (draft.package === "standard" ? null : "standard");
    const label = pkg().label;
    // Up a tier: what it adds. Anywhere else (the base tier, or a move down): just what the tier includes.
    // No price (the tabs carry that), never a "drops" line and never a minus amount.
    const movingUp = from && C.TIERS.indexOf(draft.package) > C.TIERS.indexOf(from);
    if (!movingUp) {
      const items = C.highlights(draft.package).map((feature) => C.shortFor(feature, draft.package));
      strip.replaceChildren(el("b", { text: `${label} includes: ` }), items.join(", "));
      return;
    }
    const change = C.diff(from, draft.package);
    strip.replaceChildren(el("b", { text: `${label} adds: ` }), change.items.join(", "));
  }

  function togglePopover(key) {
    const pop = $("tab-pop");
    if (ui.popover === key) {
      ui.popover = null;
      pop.hidden = true;
      return;
    }
    ui.popover = key;
    pop.hidden = false;
    pop.setAttribute("data-tier", key);
    const label = pkg(key).label;
    pop.replaceChildren(
      el("div", { class: "pop-title", text: `${label} · ${dollars(pkg(key).price_cents)}` }),
      el(
        "ul",
        { class: "pop-list" },
        C.FEATURES.map((feature) => {
          const state = C.featureFor(feature, key);
          const unlocked = C.highlights(key).indexOf(feature) >= 0;
          return el("li", { class: "pop-line" + (state.on ? "" : " off") + (unlocked ? " unlock" : ""), text: state.text });
        })
      ),
      el("button", { type: "button", class: "link-btn", text: "Compare all plans", onclick: () => openCompare() })
    );
  }

  // ---------- birthday finder ----------
  function birthdayMsg(message) {
    const node = $("bday-msg");
    if (node) node.textContent = message || "";
  }

  // Month / day / year boxes. They exist so that Backspace in an empty box steps back to the previous one and
  // editing one box never wipes the rest, neither of which the browser's built-in date field allows.
  const SEG_LENGTHS = [2, 2, 4];

  function segments() {
    return [...document.querySelectorAll(".date-seg .seg")];
  }

  function setSegments(value) {
    const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || "");
    const [m, d, y] = segments();
    if (!m) return;
    m.value = parts ? parts[2] : "";
    d.value = parts ? parts[3] : "";
    y.value = parts ? parts[1] : "";
    const native = $("bday");
    if (native) native.value = value || "";
  }

  function realDate(value) {
    const when = new Date(value + "T12:00:00Z");
    return !Number.isNaN(when.getTime()) && when.toISOString().slice(0, 10) === value;
  }

  // Called on every keystroke in a box. Only a complete real date applies; a half-edited date changes nothing,
  // and only emptying every box removes the birthday.
  function commitSegments() {
    const [mm, dd, yy] = segments().map((box) => box.value);
    birthdayMsg("");
    if (!mm && !dd && !yy) {
      if (draft.birthday) clearBirthday();
      return;
    }
    if (mm.length === 2 && dd.length === 2 && yy.length === 4) {
      const value = `${yy}-${mm}-${dd}`;
      if (!realDate(value)) {
        birthdayMsg("That isn't a real date. Check the month and day.");
        return;
      }
      if (value !== draft.birthday) applyBirthday(value);
    }
  }

  function applyBirthday(value) {
    if (ui.today && value < ui.today) {
      birthdayMsg("That date has already passed. Pick the upcoming birthday.");
      setSegments(draft.birthday);
      return;
    }
    if (ui.today && value > shiftDate(ui.today, 366)) {
      birthdayMsg("That's more than a year away. Check the year, or call (646) 203-8087.");
      setSegments(draft.birthday);
      return;
    }
    birthdayMsg("");
    draft.birthday = value;
    setSegments(value);
    save();
    $("bday-clear").hidden = false;
    renderRail();
    pushDraft();
    loadGrid();
  }

  // Clearing restores the full default grid: back to 8 weekends, nothing pinned.
  function clearBirthday() {
    draft.birthday = null;
    ui.weekends = null;
    ui.birthdayNote = "";
    save();
    setSegments("");
    const clear = $("bday-clear");
    if (clear) clear.hidden = true;
    birthdayMsg("");
    renderRail();
    pushDraft();
    loadGrid();
  }

  function dateControl() {
    const box = (cls, placeholder, label) =>
      el("input", {
        class: `seg ${cls}`,
        type: "text",
        inputmode: "numeric",
        autocomplete: "off",
        enterkeyhint: "next",
        maxlength: String(cls === "seg-y" ? 4 : 2),
        placeholder: placeholder,
        "aria-label": label,
      });
    const boxes = [box("seg-m", "mm", "Birthday month"), box("seg-d", "dd", "Birthday day"), box("seg-y", "yyyy", "Birthday year")];
    boxes[0].id = "bday-m";

    boxes.forEach((input, i) => {
      input.addEventListener("input", () => {
        input.value = input.value.replace(/\D/g, "").slice(0, SEG_LENGTHS[i]);
        // A first digit that can only start a two-digit month or day pads itself and moves on.
        if (i === 0 && input.value.length === 1 && Number(input.value) > 1) input.value = "0" + input.value;
        if (i === 1 && input.value.length === 1 && Number(input.value) > 3) input.value = "0" + input.value;
        if (input.value.length === SEG_LENGTHS[i] && i < 2) boxes[i + 1].focus();
        commitSegments();
      });
      input.addEventListener("keydown", (event) => {
        if (event.key === "Backspace" && input.value === "" && i > 0) {
          // Already empty: step back to the previous box, ready for the next Backspace to clear it.
          event.preventDefault();
          boxes[i - 1].focus();
        } else if (event.key === "ArrowLeft" && input.selectionStart === 0 && i > 0) {
          event.preventDefault();
          boxes[i - 1].focus();
        } else if (event.key === "ArrowRight" && input.selectionEnd === input.value.length && i < 2) {
          event.preventDefault();
          boxes[i + 1].focus();
        } else if ((event.key === "/" || event.key === "-" || event.key === ".") && i < 2) {
          event.preventDefault();
          if (input.value) boxes[i + 1].focus();
        }
      });
      input.addEventListener("focus", () => input.select());
    });

    // The native calendar, one tap away. It lives off-screen but real so the browser can open its picker.
    const native = el("input", {
      id: "bday",
      type: "date",
      class: "seg-native",
      tabindex: "-1",
      "aria-hidden": "true",
      min: ui.today || null,
      max: ui.today ? shiftDate(ui.today, 366) : null,
      onchange: (event) => {
        if (event.target.value) applyBirthday(event.target.value);
      },
    });
    // The one thing that opens the calendar. Everywhere else in the control is for typing.
    const calendar = el("button", {
      type: "button",
      class: "seg-cal",
      "aria-label": "Pick from calendar",
      onclick: () => {
        boxes.forEach((box) => box.blur());
        if (native.showPicker) {
          try {
            native.showPicker();
            return;
          } catch (error) {
            /* some browsers refuse outside a trusted gesture; fall through to focusing it */
          }
        }
        native.focus();
      },
    });
    calendar.innerHTML =
      '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="M8 2v4"/><path d="M16 2v4"/><rect width="18" height="18" x="3" y="4" rx="2"/><path d="M3 10h18"/></svg>';

    const wrap = el("div", { class: "date-seg", role: "group", "aria-label": "Birthday" }, [
      el("div", { class: "seg-fields" }, [
        boxes[0],
        el("span", { class: "seg-sep", text: "/", "aria-hidden": "true" }),
        boxes[1],
        el("span", { class: "seg-sep", text: "/", "aria-hidden": "true" }),
        boxes[2],
      ]),
      calendar,
      native,
    ]);

    // Clicking the typing area puts the caret in the nearest box rather than doing nothing.
    wrap.querySelector(".seg-fields").addEventListener("click", (event) => {
      if (!event.target.closest(".seg")) boxes[0].focus();
    });
    return wrap;
  }

  // Tapping a date only selects it. Nothing is held and the page stays put until Confirm.
  function selectDate(day) {
    notice(null);
    ui.picked = day;
    renderGrid();
    renderAdvance();
  }

  async function confirmDate() {
    const day = ui.picked;
    if (!day || ui.busy) return;
    notice(null);
    if (holding() && day.slot_id === draft.slotId) {
      ui.picked = null;
      go(afterDate());
      return;
    }
    if (ui.busy) return;
    ui.busy = true;
    const pointer = kept(HOLD_KEY);
    const previous = (pointer && pointer.holdId) || (holding() ? draft.holdId : null);
    // Resolves to null while Turnstile is off, which is the default and what the server expects.
    const turnstileToken = await window.EncoreTurnstile.token();
    const { status, data } = await api("/api/holds", {
      method: "POST",
      body: { slot_id: day.slot_id, replace_hold_id: previous, turnstile_token: turnstileToken },
    });
    ui.busy = false;
    if (status === 201) {
      draft.mode = "hold";
      draft.holdId = data.hold_id;
      draft.slotId = day.slot_id;
      draft.date = data.date;
      draft.dayLabel = data.day_label;
      draft.timeLabel = data.time_label;
      draft.expiresAt = data.expires_at;
      ui.picked = null;
      save();
      rememberHold();
      setDeadline(data.seconds_remaining);
      pushDraft();
      go(afterDate());
      return;
    }
    if (status === 409) {
      ui.picked = null;
      await loadGrid();
      const alts = data.alternatives || [];
      notice(`Sorry, ${day.day_label} was just taken. ${alts.length ? "These are still open:" : "No other dates are open right now."}`, alts, true);
      return;
    }
    if (status === 429 && data && data.expires_at) {
      const until = new Date(data.expires_at).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
      await loadGrid();
      notice(`${detailText(data)} Try again after ${until}.`, [], true);
      return;
    }
    notice(detailText(data, "Couldn't hold that date. Try again."), [], true);
  }

  // Door A (or a Door B parent who picked a tab) skips Select Plan.
  function afterDate() {
    return draft.package ? "addons" : "plan";
  }

  // ---------- step 2: select plan ----------
  function renderPlan() {
    stepRoot().replaceChildren(
      el("div", { class: "step-head" }, [
        el("h1", { class: "step-title", text: "Choose your package" }),
      ]),
      el("div", { id: "step-notice" }),
      el("div", { class: "tabs-head" }, [
        el("button", { type: "button", class: "compare-btn", text: "Compare all plans", onclick: () => openCompare() }),
      ]),
      el(
        "div",
        { class: "pick-grid" },
        ui.catalog.packages.map((p) =>
          el("div", { class: "pick-card" + (p.key === "plus" ? " featured" : ""), "data-tier": p.key }, [
            p.key === "plus" ? el("span", { class: "popular-tag", text: "Most popular" }) : null,
            el("div", { class: "pick-name", text: p.label }),
            el("div", { class: "pick-meta", text: `Up to ${p.included_kids} kids · ${p.hours} hours` }),
            el("div", { class: "pick-price-row" }, [el("span", { class: "pick-price", text: dollars(p.price_cents) })]),
            el("div", { class: "pick-extra" }, [el("b", { text: `+${money(ui.catalog.additional_kid_cents)}` }), " per additional kid"]),
            el(
              "ul",
              { class: "pick-features" },
              C.highlights(p.key).slice(0, 3).map((feature) => el("li", { text: C.featureFor(feature, p.key).text }))
            ),
            el("button", { type: "button", class: "btn btn-primary btn-block pick-go", text: `Select ${p.label}`, onclick: () => selectPackage(p.key) }),
          ])
        )
      )
    );
  }

  // Changing tier keeps the add-ons that still cost something and drops the ones the new tier includes free.
  function pruneAddons() {
    const included = pkg() ? pkg().included_addons : [];
    included.forEach((key) => { delete draft.addons[key]; });
    if (!draft.addons.tournament) delete draft.addons.highlight_reel;
  }

  function setPackage(key) {
    draft.package = key;
    pruneAddons();
    save();
    requote();
    document.documentElement.setAttribute("data-tier", key);
  }

  function selectPackage(key) {
    setPackage(key);
    go("addons");
  }

  // ---------- step 3: extras ----------
  function renderAddons() {
    const chosen = pkg();
    draft.seenAddons = true;
    save();
    stepRoot().replaceChildren(
      el("div", { class: "step-head" }, [
        el("h1", { class: "step-title", text: "Add extras" }),
        el("p", { class: "step-sub", text: "All optional. Nothing here moves you forward — the total updates as you pick." }),
      ]),
      el("div", { class: "headcount" }, [
        el("div", { class: "headcount-main" }, [
          el("div", { class: "headcount-label", text: "How many kids" }),
          el("div", { class: "headcount-note", id: "overage" }),
        ]),
        headcountStepper(chosen),
      ]),
      el("div", { class: "opt-grid", id: "opt-grid" })
    );
    renderOptions();
    renderOverage();
  }

  function headcountStepper(chosen) {
    const minus = el("button", { type: "button", "aria-label": "One fewer kid", text: "−" });
    const plus = el("button", { type: "button", "aria-label": "One more kid", text: "+" });
    const input = el("input", {
      type: "text",
      inputmode: "numeric",
      pattern: "[0-9]*",
      autocomplete: "off",
      maxlength: "2",
      "aria-label": "Number of kids",
      value: String(kidsValue()),
    });
    function sync() {
      const value = kidsValue();
      minus.disabled = value <= ui.catalog.min_kids;
      plus.disabled = value >= ui.catalog.max_kids;
      if (input.value !== String(value) && document.activeElement !== input) input.value = String(value);
    }
    function set(value) {
      const from = kidsValue();
      const to = Math.min(ui.catalog.max_kids, Math.max(ui.catalog.min_kids, value));
      const billable = (n) => Math.max(0, n - cap());
      const extra = billable(to) - billable(from);
      if (extra) {
        const name = Math.abs(extra) === 1 ? `${ordinal(Math.max(from, to))} kid` : `${Math.abs(extra)} kids`;
        queueDelta(extra * ui.catalog.additional_kid_cents, name, true);
      }
      draft.kids = to;
      input.value = String(draft.kids);
      sync();
      requote();
      renderRail();
      refreshDrinksNote();
      touch();
    }
    minus.addEventListener("click", () => set(kidsValue() - 1));
    plus.addEventListener("click", () => set(kidsValue() + 1));
    bindCount(input, ui.catalog.min_kids, ui.catalog.max_kids, kidsValue, set);
    sync();
    return el("div", { class: "stepper", role: "group", "aria-label": "Number of kids" }, [minus, input, plus]);
  }

  function renderOverage() {
    const node = $("overage");
    if (!node) return;
    const chosen = pkg();
    const extra = Math.max(0, kidsValue() - chosen.included_kids);
    node.replaceChildren(
      `${chosen.label} includes up to ${chosen.included_kids}. `,
      extra
        ? el("b", { class: "overage", text: `+${extra} kid${extra === 1 ? "" : "s"} · +${money(extra * ui.catalog.additional_kid_cents)}` })
        : `Each kid after that is +${money(ui.catalog.additional_kid_cents)}.`
    );
  }

  function setAddon(key, quantity) {
    const addon = catalogAddon(key);
    const dropsReel = key === "tournament" && !quantity && Boolean(draft.addons.highlight_reel);
    const cents = addon.price_cents + (dropsReel ? catalogAddon("highlight_reel").price_cents : 0);
    queueDelta(quantity > 0 ? cents : -cents, addon.label, false);
    if (quantity > 0) draft.addons[key] = quantity;
    else delete draft.addons[key];
    if (key === "tournament" && !quantity) delete draft.addons.highlight_reel;
    renderOptions();
    requote();
    renderRail();
    touch();
  }

  // The Pizza Pack already includes drinks. When one is in the order, the Drinks Pack card says so, with
  // capacity worked out from the headcount. It is only a note: neither stepper is ever changed for the parent.
  function drinksNoteText() {
    const packs = draft.addons.pizza_pack || 0;
    if (packs < 1) return "";
    const kids = kidsValue();
    const covered = packs * KIDS_PER_PIZZA_PACK;
    const one = packs === 1;
    const what = one ? "Your Pizza Pack includes drinks." : `Your ${packs} Pizza Packs include drinks.`;
    const covers = one ? "It covers" : "They cover";
    if (covered >= kids) return `${what} ${covers} all ${kids} of your kids.`;
    return `${what} ${covers} ~${covered} of your ${kids} kids. Add a Drinks Pack to cover everyone.`;
  }

  function refreshDrinksNote() {
    const node = $("drinks-note");
    if (!node) return;
    const text = drinksNoteText();
    node.textContent = text;
    node.hidden = !text;
  }

  function packOption(addon, iconBox, body) {
    const max = ui.catalog.max_pack_quantity;
    const qty = () => draft.addons[addon.key] || 0;
    const input = el("input", {
      type: "text",
      inputmode: "numeric",
      pattern: "[0-9]*",
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
        el("div", { class: "stepper small", role: "group", "aria-label": `${addon.label} quantity` }, [minus, input, plus]),
      ]),
    ]);
    // The note spans the whole card under the controls, instead of squeezing into the text column.
    if (addon.key === "drinks_pack") card.append(el("div", { class: "opt-note", id: "drinks-note", "aria-live": "polite", hidden: true }));
    function sync() {
      const value = qty();
      card.classList.toggle("on", value > 0);
      minus.disabled = value <= 0;
      plus.disabled = value >= max;
      if (input.value !== String(value) && document.activeElement !== input) input.value = String(value);
    }
    function set(value) {
      const clamped = Math.min(max, Math.max(0, value));
      queueDelta((clamped - qty()) * addon.price_cents, addon.label, false);
      if (clamped > 0) draft.addons[addon.key] = clamped;
      else delete draft.addons[addon.key];
      input.value = String(clamped);
      sync();
      requote();
      renderRail();
      refreshDrinksNote();
      touch();
    }
    minus.addEventListener("click", () => set(qty() - 1));
    plus.addEventListener("click", () => set(qty() + 1));
    bindCount(input, 0, max, qty, set);
    sync();
    return card;
  }

  function renderOptions() {
    const grid = $("opt-grid");
    if (!grid) return;
    const included = pkg().included_addons;
    grid.replaceChildren(
      ...ui.catalog.addons.map((addon) => {
        const copy = C.ADDONS[addon.key] || { icon: "➕", short: "" };
        const blocked = addon.requires && !draft.addons[addon.requires];
        const quantity = draft.addons[addon.key] || 0;
        const iconBox = el("div", { class: "addon-icon", "aria-hidden": "true", text: copy.icon });
        const body = el("div", { class: "addon-body" }, [
          el("div", { class: "addon-name", text: addon.label }),
          el("div", { class: "addon-desc", text: blocked ? "Add Tournament + Livestream first." : copy.short }),
        ]);
        if (included.indexOf(addon.key) >= 0) {
          return el("div", { class: "opt on included" }, [
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
    refreshDrinksNote();
  }

  // ---------- step 4: info + deposit ----------
  function renderInfo() {
    const saved = draft.contact || {};
    draft.seenInfo = true;
    save();
    stepRoot().replaceChildren(
      el("div", { class: "step-head" }, [
        el("h1", { class: "step-title", text: "Lock it in" }),
      ]),
      el("form", { class: "form-grid", id: "pay-form", novalidate: true, onsubmit: (e) => e.preventDefault() }, [
        field("email", "Parent email", { type: "email", inputmode: "email", autocomplete: "email", placeholder: "you@email.com", value: saved.email, full: true }),
        field("parent_name", "Parent name", { autocomplete: "name", placeholder: "Your name", value: saved.parentName }),
        field("phone", "Phone", { type: "tel", inputmode: "tel", autocomplete: "tel", placeholder: "(646) 000-0000", value: saved.phone }),
        field("kid_name", "Kid's name", { placeholder: "The birthday kid", value: saved.kidName }),
        field("kid_age", "Kid's age", { inputmode: "numeric", pattern: "[0-9]*", maxlength: "2", placeholder: "e.g. 9", value: saved.kidAge }),
      ]),
      el("div", { class: "card-block", id: "card-block" }, [
        el("div", { class: "card-label", text: "Card" }),
        el("div", { class: "card-host", id: "card-host" }),
        el("div", { class: "card-note", id: "card-note", text: "Secured by Square. We never see your card number." }),
      ]),
      el("div", { class: "pay-summary", id: "pay-summary" }),
      el("div", { id: "pay-error", role: "alert" })
    );
    ["email", "parent_name", "phone", "kid_name", "kid_age"].forEach((id) => {
      $(id).addEventListener("blur", id === "email" ? saveEmailOnBlur : rememberForm);
    });
    renderSummary();
    mountCard();
  }

  // The card fields live in Square's iframes inside our page, so the parent never leaves the funnel.
  // If they cannot be mounted (local dev with no Square credentials, or Square's CDN unreachable) the
  // block says so rather than leaving an unexplained gap above the pay button.
  async function mountCard() {
    const host = $("card-host");
    if (!host) return;
    if (!(await window.EncoreCard.enabled())) {
      // No Square application id, so there is nothing to mount. That is local dev with no credentials,
      // where the deposit is faked server-side; saying so beats an unexplained gap above the button.
      const note = $("card-note");
      if (note) note.textContent = "No card fields: SQUARE_APPLICATION_ID is not set, so the deposit is faked locally.";
      $("card-host").hidden = true;
      return;
    }
    try {
      const ok = await window.EncoreCard.mount(host);
      if (!ok) throw new Error("not mounted");
    } catch (error) {
      const note = $("card-note");
      if (note) {
        note.textContent = "The card form didn't load. Refresh the page, or call (646) 203-8087 and we'll take it by phone.";
        note.classList.add("card-note-error");
      }
    }
  }

  function field(id, label, options) {
    const opts = options || {};
    return el("div", { class: "field" + (opts.full ? " full" : "") }, [
      el("label", { for: id, text: label }),
      el("input", {
        id: id,
        name: id,
        type: opts.type || "text",
        inputmode: opts.inputmode || null,
        pattern: opts.pattern || null,
        maxlength: opts.maxlength || null,
        autocomplete: opts.autocomplete || null,
        placeholder: opts.placeholder || null,
        value: opts.value || null,
        required: true,
      }),
    ]);
  }

  // The same receipt as the rail. CSS shows this one only where the rail is collapsed (phones), so there
  // is exactly one money block per viewport.
  function renderSummary() {
    const box = $("pay-summary");
    if (!box) return;
    if (!ui.quote) {
      box.replaceChildren(el("div", { class: "rc-row", text: "…" }));
      return;
    }
    box.replaceChildren(receipt(ui.quote));
  }

  function readForm() {
    const value = (id) => ($(id) ? $(id).value.trim() : "");
    return {
      email: value("email"),
      parentName: value("parent_name"),
      phone: value("phone"),
      kidName: value("kid_name"),
      kidAge: value("kid_age"),
    };
  }

  function rememberForm() {
    draft.contact = readForm();
    save();
    renderRail();
  }

  function saveEmailOnBlur() {
    rememberForm();
    const email = $("email").value.trim();
    if (holding() && email.indexOf("@") > 0) {
      api(`/api/holds/${draft.holdId}/email`, { method: "PATCH", body: { email: email } });
    }
  }

  function payError(message) {
    $("pay-error").replaceChildren(message ? el("p", { class: "notice error", text: message }) : "");
  }

  function validate() {
    const form = readForm();
    const checks = [
      ["email", form.email.indexOf("@") > 0, "Enter your email."],
      ["parent_name", form.parentName.length > 0, "Enter your name."],
      ["phone", form.phone.length >= 7, "Enter your phone number."],
      ["kid_name", form.kidName.length > 0, "Enter the birthday kid's name."],
      ["kid_age", /^\d{1,2}$/.test(form.kidAge) && Number(form.kidAge) > 0, "Enter the kid's age."],
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

  async function pay() {
    if (ui.busy) return;
    payError(null);
    const form = validate();
    if (!form) return;
    const button = $("advance");
    rememberForm();
    ui.busy = true;
    button.disabled = true;
    button.textContent = "Starting secure checkout…";
    const name = splitName(form.parentName);
    const body = {
      package: draft.package,
      kids: kidsValue(),
      addons: draft.addons,
      first_name: name.first,
      last_name: name.last,
      phone: form.phone,
      email: form.email,
      kid_name: form.kidName,
      kid_age: Number(form.kidAge),
      birthday: draft.birthday || null,
    };
    const snapshot = await api(`/api/holds/${draft.holdId}/checkout`, { method: "POST", body: body });
    if (snapshot.status !== 200) {
      ui.busy = false;
      renderAdvance();
      const data = snapshot.data;
      if (snapshot.status === 409 && data && data.status) return showHoldModal(data.status);
      if (snapshot.status === 429 && data && data.expires_at) {
        const until = new Date(data.expires_at).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
        return payError(`${detailText(data)} It frees up around ${until}.`);
      }
      return payError(detailText(data));
    }

    // The card is read by Square inside its own iframe; this page only ever sees the token.
    let cardToken = "dev-token";
    if (await window.EncoreCard.enabled()) {
      button.textContent = "Checking your card…";
      const tokenized = await window.EncoreCard.tokenize();
      if (tokenized.error) {
        ui.busy = false;
        renderAdvance();
        return payError(tokenized.error);
      }
      cardToken = tokenized.token;
    }

    button.textContent = "Taking your deposit…";
    const charged = await api(`/api/holds/${draft.holdId}/pay`, { method: "POST", body: { token: cardToken } });
    if (charged.status === 200) {
      // Booked. The hold became a booking, so the browser-wide pointer is dropped before leaving the
      // funnel: there is nothing left to count down and nothing left to resume.
      await window.EncoreCard.destroy();
      forgetHold(draft.holdId);
      window.location.href = `/confirm.html?code=${encodeURIComponent(charged.data.confirmation_code)}`;
      return;
    }
    ui.busy = false;
    renderAdvance();
    if (charged.status === 409 && charged.data && charged.data.status) {
      showHoldModal(charged.data.status);
      return;
    }
    // 402 is a declined card and 502 is Square being unreachable. Both keep the date, and both are worth
    // trying again, so the card fields are left mounted and filled.
    payError(detailText(charged.data));
  }

  // ---------- hold header, timer and expiry ----------
  // The hold row's expiry on the server is the only clock that matters. The client keeps one number, the
  // moment (on its own monotonic clock) the server said the hold would end, and only ever sets it from a
  // server response: at hold creation, on boot, on return to the tab, on a Re-hold. It is never restarted locally.
  function setDeadline(secondsRemaining) {
    ui.deadline = performance.now() + secondsRemaining * 1000;
    startTimer();
    renderHoldHead();
  }

  function startTimer() {
    if (!ui.timer) ui.timer = setInterval(tick, 250);
  }

  function stopTimer() {
    clearInterval(ui.timer);
    ui.timer = null;
    ui.deadline = null;
  }

  function remainingSeconds() {
    return ui.deadline === null ? null : (ui.deadline - performance.now()) / 1000;
  }

  function tick() {
    const left = remainingSeconds();
    if (left !== null && left <= 0 && !ui.busy && !ui.holdModal) return showHoldModal("expired");
    renderHoldHead();
  }

  // A hold that has run out, or that the modal is already explaining, is not a status worth showing.
  function holdLive() {
    const left = remainingSeconds();
    return holding() && !ui.holdModal && (left === null || left > 0);
  }

  // The pill is a shell element shared with the rest of the site: the funnel owns the clock and hands it
  // the current state, so the timer never unmounts or resets on step navigation.
  function renderHoldHead() {
    if (!draft.date || !holdLive()) return window.EncoreHoldPill.hide();
    window.EncoreHoldPill.render({
      seconds: remainingSeconds(),
      dayLabel: draft.dayLabel,
      timeLabel: draft.timeLabel,
      shortLabel: shortDate(draft.date),
      // The way out sits beside the clock on every step but the one it leads to.
      actionText: ui.step === "date" ? null : "Change date",
      actionShort: "Change",
      onAction: () => go("date"),
    });
  }

  // Another tab of this browser may hold a date that this tab has never heard of, or may have swapped the
  // one this tab was using. Adopting it beats telling the parent their hold is gone when it plainly is not.
  async function adoptBrowserHold() {
    const pointer = kept(HOLD_KEY);
    if (!pointer || !pointer.holdId || pointer.holdId === draft.holdId) return false;
    const { status, data } = await api(`/api/holds/${pointer.holdId}`);
    if (status !== 200 || data.status !== "pending_payment") {
      forgetHold(pointer.holdId);
      return false;
    }
    HOLD_FIELDS.forEach((key) => { if (pointer[key] !== undefined && pointer[key] !== null) draft[key] = pointer[key]; });
    draft.expiresAt = data.expires_at;
    ui.picked = null;
    save();
    setDeadline(data.seconds_remaining);
    return true;
  }

  // Ask the server how long is left. Used on boot, when the tab comes back and when a cached page is restored.
  async function syncHold() {
    if (!holding()) return;
    const { status, data } = await api(`/api/holds/${draft.holdId}`);
    if (status === 404) return (await adoptBrowserHold()) ? renderHoldHead() : showHoldModal("gone");
    if (status !== 200) return;
    if (data.status === "pending_payment") {
      draft.expiresAt = data.expires_at;
      save();
      rememberHold();
      setDeadline(data.seconds_remaining);
      return;
    }
    if (await adoptBrowserHold()) return renderHoldHead();
    showHoldModal(data.status);
  }

  function holdModalClose() {
    ui.holdModal = null;
    const dialog = $("hold-modal");
    if (dialog.open) dialog.close();
  }

  // "Still there?" when the hold expired; a plain notice when the date is gone for good. The modal has no other
  // way out: Re-hold (only for an expired hold) or back to the dates.
  function showHoldModal(status) {
    if (!holding()) return;
    const label = draft.dayLabel;
    const expired = status === "expired";
    ui.holdModal = status;
    stopTimer();
    renderHoldHead();
    $("hold-modal-title").textContent = expired ? "Still there?" : "That date is no longer held";
    $("hold-modal-text").textContent = expired
      ? `Your hold on ${label} expired. Nothing was charged. Re-hold it to get another 15 minutes; your package and details are saved.`
      : status === "booked"
      ? `${label} has been booked. If that was you, check your confirmation text. Otherwise, pick another date; your package and details are saved.`
      : `${label} is no longer held for you, and nothing was charged. Pick a date to continue; your package and details are saved.`;
    $("hold-modal-error").replaceChildren();
    const primary = $("hold-modal-primary");
    primary.hidden = !expired;
    primary.disabled = false;
    primary.textContent = `Re-hold ${shortDate(draft.date)}`;
    primary.onclick = reHold;
    const secondary = $("hold-modal-secondary");
    secondary.textContent = expired ? "Pick another date" : "Back to the dates";
    secondary.onclick = leaveHold;
    if (!$("hold-modal").open) $("hold-modal").showModal();
  }

  // The only thing that restarts the clock: a fresh hold on the same slot, through the same endpoint and the
  // same database constraint as any hold, so availability is re-checked for real.
  async function reHold() {
    const primary = $("hold-modal-primary");
    primary.disabled = true;
    primary.textContent = "Checking…";
    const { status, data } = await api("/api/holds", {
      method: "POST",
      body: { slot_id: draft.slotId, replace_hold_id: draft.holdId },
    });
    if (status === 201) {
      draft.holdId = data.hold_id;
      draft.expiresAt = data.expires_at;
      save();
      rememberHold();
      holdModalClose();
      setDeadline(data.seconds_remaining);
      renderHoldHead();
      pushDraft();
      const email = (draft.contact && draft.contact.email) || "";
      if (email.indexOf("@") > 0) api(`/api/holds/${draft.holdId}/email`, { method: "PATCH", body: { email: email } });
      return;
    }
    primary.disabled = false;
    primary.textContent = `Re-hold ${shortDate(draft.date)}`;
    if (status === 409) {
      // Someone else has it. The way back to the dates is the only option left.
      ui.holdModal = "taken";
      $("hold-modal-title").textContent = "That date was just taken";
      $("hold-modal-text").textContent = `Someone else grabbed ${draft.dayLabel} while it was unheld. Your package and details are saved; pick another date.`;
      primary.hidden = true;
      $("hold-modal-secondary").textContent = "Pick another date";
      return;
    }
    $("hold-modal-error").replaceChildren(el("p", { class: "notice error", text: detailText(data, "Couldn't re-hold that date. Try again.") }));
  }

  function leaveHold() {
    holdModalClose();
    ui.picked = null;
    clearDraft();
    go("date");
  }

  // ---------- compare drawer ----------
  function openCompare(mode) {
    const body = $("compare-body");
    const tiers = ui.catalog.packages;
    if (mode === "summary" && ui.quote) {
      body.replaceChildren(
        el("ul", { class: "quote-lines drawer-lines" }, [
          ...ui.quote.line_items.map((item) =>
            el("li", {}, [
              el("span", { text: item.quantity > 1 ? `${item.label} × ${item.quantity}` : item.label }),
              item.included ? el("span", { class: "inc", text: "Included" }) : el("span", { text: money(item.amount_cents) }),
            ])
          ),
          el("li", {}, [el("span", { text: "Deposit due now" }), el("span", { text: money(ui.quote.deposit_cents) })]),
          el("li", {}, [el("span", { text: "Due at the party" }), el("span", { text: money(ui.quote.balance_cents) })]),
        ])
      );
      $("compare-title").textContent = "Your party";
    } else {
      $("compare-title").textContent = "Compare all plans";
      body.replaceChildren(
        el("table", { class: "compare" }, [
          el("thead", {}, [
            el("tr", {}, [
              el("th", { text: "" }),
              ...tiers.map((p) => el("th", { "data-tier": p.key }, [
                el("span", { class: "compare-name", text: p.label }),
                el("span", { class: "compare-price", text: dollars(p.price_cents) }),
              ])),
            ]),
          ]),
          el("tbody", {},
            C.FEATURES.map((feature) =>
              el("tr", {}, [
                el("th", { scope: "row", text: C.rowLabel(feature) }),
                ...tiers.map((p) => {
                  const state = C.featureFor(feature, p.key);
                  const spell = state.on && C.varies(feature);
                  return el("td", { "data-tier": p.key, class: state.on ? "yes" : "no" }, [
                    spell
                      ? el("span", { class: "compare-says", text: C.shortFor(feature, p.key) })
                      : el("span", { class: "compare-mark", "aria-hidden": "true", text: state.on ? "✓" : "—" }),
                    spell ? null : el("span", { class: "compare-detail", text: state.on ? state.text : "" }),
                  ]);
                }),
              ])
            )
          ),
        ])
      );
    }
    $("compare").showModal();
  }

  // ---------- waitlist ----------
  async function submitWaitlist(event, day) {
    event.preventDefault();
    const email = $(`wl-email-${day.slot_id}`);
    const phone = $(`wl-phone-${day.slot_id}`);
    const note = $(`wl-note-${day.slot_id}`);
    if (!email.value.trim() || email.value.indexOf("@") < 0) {
      note.textContent = "Enter the email to notify.";
      return;
    }
    const body = { slot_id: day.slot_id, email: email.value.trim() };
    if (phone.value.trim()) body.phone = phone.value.trim();
    note.textContent = "Adding you…";
    const { status, data } = await api("/api/waitlist", { method: "POST", body: body });
    if (status === 201) {
      // Collapses to a line on the card. Kept only for this page view: it is a courtesy, not a record,
      // and the server already holds the real one.
      ui.waitlisted[day.slot_id] = data.detail;
      ui.waitlistOpen = null;
      renderGrid();
      return;
    }
    note.textContent = detailText(data, "Couldn't save that. Try again.");
  }

  // ---------- boot ----------
  async function boot() {
    const { status, data } = await api("/api/catalog");
    if (status !== 200) {
      stepRoot().replaceChildren(el("p", { class: "notice error", text: "Couldn't load the booking page. Refresh to try again." }));
      return;
    }
    ui.catalog = data;
    restore();

    const entryTier = new URLSearchParams(location.search).get("pkg");
    if (entryTier && ui.catalog.packages.some((p) => p.key === entryTier)) {
      draft.package = entryTier;
      pruneAddons();
      save();
    }

    // A hold from an earlier visit: ask the server how long is left (never restart locally). A hold the server
    // has never heard of is dropped; one that merely expired is kept so the parent can re-hold it.
    let staleStatus = null;
    let live = false;
    if (holding()) {
      const check = await api(`/api/holds/${draft.holdId}`);
      if (check.status === 404) clearDraft();
      else if (check.status === 200 && check.data.status === "pending_payment") {
        draft.expiresAt = check.data.expires_at;
        save();
        rememberHold();
        setDeadline(check.data.seconds_remaining);
        live = true;
      } else if (check.status === 200) staleStatus = check.data.status;
    }
    // This tab has no live hold of its own, but another tab of this browser may be holding a date right now.
    if (!live && (await adoptBrowserHold())) staleStatus = null;
    if (draft.package) requote();

    $("rail-handle").addEventListener("click", () => {
      const rail = $("rail");
      const open = rail.classList.toggle("open");
      $("rail-handle").setAttribute("aria-expanded", String(open));
    });
    $("compare-close").addEventListener("click", () => $("compare").close());
    $("compare").addEventListener("click", (event) => { if (event.target === $("compare")) $("compare").close(); });
    document.addEventListener("click", (event) => {
      const pop = $("tab-pop");
      if (ui.popover && pop && !pop.hidden && !pop.contains(event.target) && !event.target.closest(".tab-info")) {
        ui.popover = null;
        pop.hidden = true;
      }
    });
    // Back from the payment page can restore this page frozen mid-"Starting checkout…" from the browser's
    // back/forward cache. Clear that, and re-check the hold, so every control works again.
    window.addEventListener("pageshow", (event) => {
      if (!event.persisted || !ui.catalog) return;
      ui.busy = false;
      render(ui.step);
      syncHold();
    });
    // A tab that slept can have a stale local clock: the server's number is the only one that counts.
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState !== "visible" || !ui.catalog) return;
      if (!ui.holdModal) syncHold();
      if (ui.step === "date") loadGrid();
    });
    // The modal has no other way out than its two buttons.
    $("hold-modal").addEventListener("cancel", (event) => event.preventDefault());
    window.addEventListener("popstate", (event) => {
      const step = (event.state && event.state.step) || pathStep();
      go(step, "pop");
    });

    const wanted = pathStep();
    const target = allowed(wanted) ? wanted : earliestIncomplete();
    history.replaceState({ step: target }, "", `/book/${target}`);
    render(target);
    if (staleStatus) showHoldModal(staleStatus);
  }

  window.EncoreShell.mountFooter();
  boot();
})();
