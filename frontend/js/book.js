// The booking funnel: one shell, four steps that swap in the left block.
// Two doors in (package-led and date-led), one merge point at Extras.
(function () {
  const { api, detailText, money, clock, el, store, recall, bindCount } = window.Encore;
  const C = window.EncoreContent;
  const KEY = "encore.draft";
  const STEPS = ["date", "plan", "addons", "info"];
  const STATUS_TEXT = { open: "Open", held: "On hold", booked: "Booked", blocked: "Unavailable", past: "Unavailable", request: "By request" };
  const DRAFT_KEYS = ["mode", "holdId", "slotId", "date", "dayLabel", "timeLabel", "expiresAt", "package", "addons", "kids", "contact", "seenAddons", "seenInfo"];
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
  };

  const ui = {
    step: null,
    catalog: null,
    days: [],
    weekendDefault: "open",
    weekends: null,
    maxWeekends: 52,
    loadingMore: false,
    today: null,
    firstBookable: null,
    quote: null,
    busy: false,
    deadline: null,
    timer: null,
    popover: null,
    picked: null,
    dead: null,
    done: null,
  };

  const $ = (id) => document.getElementById(id);
  const stepRoot = () => $("step");

  // ---------- draft state ----------
  function save() {
    const saved = {};
    DRAFT_KEYS.forEach((key) => { saved[key] = draft[key]; });
    store(KEY, saved);
  }

  function restore() {
    const saved = recall(KEY);
    if (!saved) return;
    DRAFT_KEYS.forEach((key) => {
      if (saved[key] !== undefined && saved[key] !== null) draft[key] = saved[key];
    });
    draft.addons = draft.addons || {};
    draft.contact = draft.contact || {};
  }

  function clearDraft() {
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

  function requesting() {
    return draft.mode === "request";
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
      body: { package: draft.package, kids: kidsValue(), addons: draft.addons },
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
    const { status, data } = await api("/api/quote", {
      method: "POST",
      body: { package: draft.package, kids: kidsValue(), addons: draft.addons },
    });
    if (seq !== quoteSeq) return;
    if (status === 200) ui.quote = data;
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
    ui.popover = null;
    save();
    if (ui.done) return renderDone();
    if (ui.dead) return renderDead();
    if (step === "date") renderDate();
    else if (step === "plan") renderPlan();
    else if (step === "addons") renderAddons();
    else renderInfo();
    renderRail();
    renderMoney();
    renderAdvance();
    document.documentElement.setAttribute("data-tier", draft.package || "none");
    if (step !== "info") stopTimer();
    ensureTimer();
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
        if (number === 2 && draft.package) {
          const unlocks = C.highlights(draft.package).slice(0, 3).map((feature) => C.shortFor(feature, draft.package));
          lines.push(el("div", { class: "rail-sub", text: unlocks.join(" · ") }));
        }
        return el("li", { class: "rail-step" + (number === current ? " current" : "") + (done ? " done" : "") }, lines);
      })
    );
  }

  function renderMoney() {
    const money_ = $("rail-money");
    if (!draft.package || !ui.quote) {
      money_.replaceChildren(
        el("div", { class: "rail-total", text: ui.catalog ? `from ${money(Math.min(...ui.catalog.packages.map((p) => p.price_cents)))}` : "—" }),
        el("div", { class: "rail-split", text: "Private venue party" })
      );
      return;
    }
    money_.replaceChildren(
      el("div", { class: "rail-total", text: money(ui.quote.total_cents) }),
      el("div", { class: "rail-split" }, [
        el("b", { text: `${money(ui.quote.deposit_cents)} today` }),
        ` · ${money(ui.quote.balance_cents)} day of party`,
      ]),
      el("button", {
        type: "button",
        class: "rail-details",
        text: "Details",
        onclick: () => openCompare("summary"),
      })
    );
  }

  function renderAdvance() {
    const button = $("advance");
    const note = $("rail-note");
    note.textContent = "";
    if (ui.done || ui.dead) {
      button.hidden = true;
      return;
    }
    button.hidden = false;
    if (ui.step === "date") {
      button.onclick = confirmDate;
      button.disabled = !ui.picked || ui.busy;
      button.textContent = ui.picked
        ? `Confirm ${shortDate(ui.picked.date)}`
        : draft.package
        ? `Pick a date for ${pkg().label}`
        : "Pick a date";
      note.textContent = ui.picked && ui.picked.status !== "request" ? "Confirming holds this date for 15 minutes." : "";
      return;
    }
    if (ui.step === "plan") {
      button.disabled = true;
      button.textContent = "Choose a package";
      return;
    }
    if (ui.step === "addons") {
      button.disabled = !ui.quote;
      button.textContent = requesting()
        ? `Review request for ${shortDate(draft.date)}`
        : `Lock in ${shortDate(draft.date)} (${money(ui.quote ? ui.quote.deposit_cents : 10000)} today)`;
      button.onclick = () => go("info");
      return;
    }
    button.disabled = ui.busy;
    button.textContent = requesting() ? "Send request" : `Pay ${money(ui.quote ? ui.quote.deposit_cents : 10000)} & lock it in`;
    button.onclick = pay;
    note.textContent = requesting()
      ? "No payment now. Nothing is booked until we confirm with you."
      : "Secure checkout by Square.";
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
  // Coming back to the date step keeps the confirmed date selected, so the rail offers
  // "Confirm {date}" again instead of making the parent pick it twice.
  function restorePicked() {
    if (ui.picked || !draft.date) return;
    ui.picked = {
      date: draft.date,
      day_label: draft.dayLabel,
      time_label: draft.timeLabel,
      slot_id: draft.slotId,
      status: requesting() ? "request" : "open",
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
    ]);

    stepRoot().replaceChildren(
      el("div", { class: "step-head" }, [
        el("h1", { class: "step-title", text: "Pick your date" }),
        el("p", { class: "step-sub", text: "Saturday and Sunday mornings at 10:00 AM. Pick a date, then confirm it to hold it for 15 minutes while you finish." }),
      ]),
      strip,
      el("div", { id: "step-notice", "aria-live": "polite" }),
      el("div", { id: "slots" }, [el("p", { class: "muted", text: "Loading dates…" })]),
      el("div", { class: "asks" }, [
        el("button", {
          type: "button",
          class: "btn btn-ghost btn-block",
          text: "Want a weekday? Tell us your date",
          onclick: () => toggleAsk("weekday"),
        }),
        el("form", { class: "ask-form", id: "weekday-form", hidden: true, onsubmit: submitWeekday }, [
          el("div", { class: "field" }, [
            el("label", { for: "weekday-date", text: "Your date" }),
            el("input", { id: "weekday-date", type: "date", required: true, min: ui.firstBookable || null }),
          ]),
          el("button", { type: "submit", class: "btn btn-primary", text: "Continue" }),
          el("p", { class: "ask-error", id: "weekday-error", role: "alert" }),
        ]),
        el("button", {
          type: "button",
          class: "btn btn-ghost btn-block",
          text: "Don't see your date? Get notified",
          onclick: () => toggleAsk("waitlist"),
        }),
        el("form", { class: "ask-form", id: "waitlist-form", hidden: true, onsubmit: submitWaitlist }, [
          el("div", { class: "field" }, [
            el("label", { for: "waitlist-email", text: "Your email" }),
            el("input", { id: "waitlist-email", type: "email", inputmode: "email", autocomplete: "email", placeholder: "you@email.com", required: true }),
          ]),
          el("button", { type: "submit", class: "btn btn-primary", text: "Notify me" }),
          el("p", { class: "ask-note", id: "waitlist-note", role: "status" }),
        ]),
      ])
    );
    renderDiff(null);
    renderGrid();
    loadGrid();
  }

  function toggleAsk(which) {
    const form = $(which + "-form");
    form.hidden = !form.hidden;
    if (!form.hidden) form.querySelector("input").focus();
  }

  const MORE_STEP = 8;

  async function loadGrid() {
    const query = ui.weekends ? `?weekends=${ui.weekends}` : "";
    const { status, data } = await api(`/api/availability${query}`);
    if (status !== 200) {
      const slots = $("slots");
      if (slots) slots.replaceChildren(el("p", { class: "notice error", text: detailText(data, "Couldn't load dates. Refresh to try again.") }));
      return;
    }
    ui.days = data.days;
    ui.weekendDefault = data.weekend_default;
    ui.weekends = data.weekends;
    ui.maxWeekends = data.max_weekends;
    ui.today = data.today;
    ui.firstBookable = data.first_bookable_date;
    // A date held further out than the list reaches must stay visible: widen the list to cover it.
    const last = data.days[data.days.length - 1].date;
    if (draft.date && draft.date > last && ui.weekends < ui.maxWeekends) {
      const gap = (new Date(draft.date + "T12:00:00") - new Date(last + "T12:00:00")) / DAY_MS;
      ui.weekends = Math.min(ui.maxWeekends, ui.weekends + Math.ceil(gap / 7) + 1);
      return loadGrid();
    }
    const input = $("weekday-date");
    if (input) input.min = data.first_bookable_date;
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

  function renderGrid() {
    const slots = $("slots");
    if (!slots) return;
    const price = tierPriceCents();
    const prefix = draft.package ? "" : "from ";
    slots.replaceChildren(
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
                const mine = holding() && day.slot_id === draft.slotId;
                const picked = Boolean(ui.picked) && ui.picked.date === day.date;
                const tappable = mine || day.status === "open" || day.status === "request";
                const label = picked ? "Selected" : mine ? "Your date" : STATUS_TEXT[day.status];
                const live = day.status === "open" || day.status === "request" || mine;
                return el(
                  "button",
                  {
                    type: "button",
                    class: `wk-card ${picked || mine ? "open yours" : day.status}`,
                    disabled: !tappable,
                    "aria-pressed": String(picked),
                    "aria-label": `${day.day_label} ${day.time_label || "10:00 AM"}, ${label}`,
                    onclick: !tappable ? null : () => selectDate(day),
                  },
                  [
                    el("span", { class: "wk-date", text: day.day_label }),
                    el("span", { class: "wk-time", text: day.time_label || "10:00 AM" }),
                    live
                      ? el("span", { class: "wk-price", "data-cents": String(price) }, [
                          prefix,
                          el("span", { class: "wk-price-num", text: dollars(price) }),
                        ])
                      : null,
                    el("span", { class: "wk-state" }, [el("span", { text: label })]),
                    !mine && day.status === "held" ? el("span", { class: "wk-hint", text: "May free up in a few minutes" }) : null,
                  ]
                );
              })
            ),
          ])
        )
      ),
      moreWeekends()
    );
  }

  // Loads further weekends on demand, MORE_STEP at a time, up to the server's cap.
  function moreWeekends() {
    if (!ui.weekends) return null;
    if (ui.weekends >= ui.maxWeekends) {
      return el("p", { class: "more-end", text: "That's as far ahead as we book online. For later dates, call (646) 203-8087 or get notified below." });
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
    if (!from) {
      const items = C.highlights("standard").map((feature) => C.shortFor(feature, "standard"));
      strip.replaceChildren(
        el("b", { text: `${label} includes: ` }),
        items.join(", "),
        el("span", { class: "diff-delta", text: ` · ${dollars(pkg().price_cents)}` })
      );
      return;
    }
    const change = C.diff(from, draft.package);
    const delta = pkg().price_cents - pkg(from).price_cents;
    strip.replaceChildren(
      el("b", { text: `${label} ${change.up ? "adds" : "drops"}: ` }),
      change.items.join(", "),
      el("span", { class: "diff-delta", text: ` · ${delta >= 0 ? "+" : "−"}${dollars(Math.abs(delta))}` })
    );
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
    if (day.status === "request") {
      draft.mode = "request";
      draft.holdId = null;
      draft.slotId = null;
      draft.date = day.date;
      draft.dayLabel = day.day_label;
      draft.timeLabel = null;
      draft.expiresAt = null;
      ui.picked = null;
      save();
      go(afterDate());
      return;
    }
    if (ui.busy) return;
    ui.busy = true;
    const previous = holding() ? draft.holdId : null;
    const { status, data } = await api("/api/holds", {
      method: "POST",
      body: { slot_id: day.slot_id, replace_hold_id: previous },
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
      ui.deadline = performance.now() + data.seconds_remaining * 1000;
      ui.picked = null;
      save();
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
        el("p", { class: "step-eyebrow", text: requesting() ? `Requesting ${draft.dayLabel}` : `Holding ${draft.dayLabel} · ${draft.timeLabel}` }),
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
        el("p", { class: "step-eyebrow", text: requesting() ? `Requesting ${draft.dayLabel}` : `Holding ${draft.dayLabel} · ${draft.timeLabel}` }),
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
      draft.kids = Math.min(ui.catalog.max_kids, Math.max(ui.catalog.min_kids, value));
      input.value = String(draft.kids);
      sync();
      requote();
      renderRail();
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
    if (quantity > 0) draft.addons[key] = quantity;
    else delete draft.addons[key];
    if (key === "tournament" && !quantity) delete draft.addons.highlight_reel;
    renderOptions();
    requote();
    renderRail();
    touch();
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
    function sync() {
      const value = qty();
      card.classList.toggle("on", value > 0);
      minus.disabled = value <= 0;
      plus.disabled = value >= max;
      if (input.value !== String(value) && document.activeElement !== input) input.value = String(value);
    }
    function set(value) {
      const clamped = Math.min(max, Math.max(0, value));
      if (clamped > 0) draft.addons[addon.key] = clamped;
      else delete draft.addons[addon.key];
      input.value = String(clamped);
      sync();
      requote();
      renderRail();
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
  }

  // ---------- step 4: info + deposit ----------
  function renderInfo() {
    const saved = draft.contact || {};
    draft.seenInfo = true;
    save();
    stepRoot().replaceChildren(
      el("div", { class: "step-head" }, [
        el("p", { class: "step-eyebrow" }, [
          requesting() ? `Requesting ${draft.dayLabel}` : `You're holding ${draft.dayLabel} · ${draft.timeLabel}`,
          requesting() ? null : el("span", { class: "timer", id: "hold-timer", text: "--:--" }),
        ]),
        el("h1", { class: "step-title", text: requesting() ? "Send your request" : "Lock it in" }),
      ]),
      el("form", { class: "form-grid", id: "pay-form", novalidate: true, onsubmit: (e) => e.preventDefault() }, [
        field("email", "Parent email", { type: "email", inputmode: "email", autocomplete: "email", placeholder: "you@email.com", value: saved.email, full: true }),
        field("parent_name", "Parent name", { autocomplete: "name", placeholder: "Your name", value: saved.parentName }),
        field("phone", "Phone", { type: "tel", inputmode: "tel", autocomplete: "tel", placeholder: "(646) 000-0000", value: saved.phone }),
        field("kid_name", "Kid's name", { placeholder: "The birthday kid", value: saved.kidName }),
        field("kid_age", "Kid's age", { inputmode: "numeric", pattern: "[0-9]*", maxlength: "2", placeholder: "e.g. 9", value: saved.kidAge }),
        requesting() ? timeField() : null,
      ]),
      el("div", { class: "pay-summary" }, [
        el("div", { class: "roll" }, [
          el("div", { class: "total", id: "summary-total", text: "—" }),
          el("button", { type: "button", class: "quote-toggle", id: "summary-toggle", text: "Details", onclick: toggleSummary }),
        ]),
        el("ul", { class: "quote-lines", id: "summary-lines", hidden: true }),
        el("p", { class: "deposit-math", id: "deposit-math" }),
      ]),
      requesting() ? null : el("p", { class: "risk-line", text: C.REFUND_PROMISE }),
      el("div", { id: "pay-error", role: "alert" })
    );
    ["email", "parent_name", "phone", "kid_name", "kid_age"].forEach((id) => {
      $(id).addEventListener("blur", id === "email" ? saveEmailOnBlur : rememberForm);
    });
    renderSummary();
    enterInfo();
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

  function timeField() {
    const times = [["09:00", "9:00 AM"], ["10:00", "10:00 AM"], ["11:00", "11:00 AM"], ["12:00", "12:00 PM"],
      ["13:00", "1:00 PM"], ["14:00", "2:00 PM"], ["15:00", "3:00 PM"], ["16:00", "4:00 PM"],
      ["17:00", "5:00 PM"], ["18:00", "6:00 PM"], ["19:00", "7:00 PM"], ["20:00", "8:00 PM"]];
    return el("div", { class: "field" }, [
      el("label", { for: "preferred_time", text: "Preferred start time" }),
      el("select", { id: "preferred_time", name: "preferred_time" },
        times.map(([value, label]) => el("option", { value: value, text: label, selected: value === "16:00" ? "" : null }))),
    ]);
  }

  function toggleSummary() {
    const lines = $("summary-lines");
    lines.hidden = !lines.hidden;
    $("summary-toggle").textContent = lines.hidden ? "Details" : "Hide";
  }

  function renderSummary() {
    const total = $("summary-total");
    if (!total) return;
    if (!ui.quote) {
      total.textContent = "…";
      return;
    }
    total.textContent = money(ui.quote.total_cents);
    $("summary-lines").replaceChildren(
      ...ui.quote.line_items.map((item) =>
        el("li", {}, [
          el("span", { text: item.quantity > 1 ? `${item.label} × ${item.quantity}` : item.label }),
          item.included ? el("span", { class: "inc", text: "Included" }) : el("span", { text: money(item.amount_cents) }),
        ])
      )
    );
    $("deposit-math").replaceChildren(
      `${money(ui.quote.total_cents)} total · `,
      el("b", { text: `${money(ui.quote.deposit_cents)} today` }),
      ` · ${money(ui.quote.balance_cents)} day of party`
    );
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
    button.textContent = requesting() ? "Sending…" : "Starting secure checkout…";
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
    };
    const result = requesting()
      ? await api("/api/requests", { method: "POST", body: { date: draft.date, preferred_time: $("preferred_time").value, ...body } })
      : await api(`/api/holds/${draft.holdId}/checkout`, { method: "POST", body: body });
    const { status, data } = result;
    if (!requesting() && status === 200) {
      window.location.href = data.checkout_url;
      return;
    }
    if (requesting() && status === 201) {
      ui.done = data;
      store(KEY, null);
      render("info");
      return;
    }
    ui.busy = false;
    renderAdvance();
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

  // ---------- countdown ----------
  function stopTimer() {
    clearInterval(ui.timer);
    ui.timer = null;
  }

  function ensureTimer() {
    if (!holding() || !ui.deadline) return;
    tick();
    if (!ui.timer) ui.timer = setInterval(tick, 250);
  }

  function tick() {
    const remaining = (ui.deadline - performance.now()) / 1000;
    const timer = $("hold-timer");
    if (timer) {
      timer.textContent = clock(Math.ceil(remaining));
      timer.classList.toggle("low", remaining < 120);
    }
    if (remaining <= 0 && !ui.busy) showDead("expired");
  }

  async function enterInfo() {
    if (requesting()) return;
    const { status, data } = await api(`/api/holds/${draft.holdId}`);
    if (status !== 200) return showDead("expired");
    if (data.status !== "pending_payment") return showDead(data.status);
    draft.expiresAt = data.expires_at;
    ui.deadline = performance.now() + data.seconds_remaining * 1000;
    save();
    ensureTimer();
  }

  function showDead(status) {
    stopTimer();
    ui.dead = status;
    clearDraft();
    renderDead();
  }

  function renderDead() {
    const copy = {
      expired: "Your 15-minute hold ran out, so the date was released. Nothing was charged.",
      cancelled: "That hold was cancelled, and nothing was charged.",
      booked: "That date is already booked. If that was you, check your confirmation text.",
    };
    stepRoot().replaceChildren(
      el("div", { class: "step-head" }, [el("h1", { class: "step-title", text: "That date was released" })]),
      el("div", { class: "notice error" }, [
        el("div", { text: copy[ui.dead] || copy.expired }),
        el("div", { class: "alts" }, [
          el("button", {
            type: "button",
            class: "chip",
            text: "Pick another date",
            onclick: () => { ui.dead = null; go("date"); },
          }),
        ]),
      ])
    );
    renderRail();
    renderAdvance();
  }

  function renderDone() {
    const data = ui.done;
    stepRoot().replaceChildren(
      el("div", { class: "step-head" }, [
        el("p", { class: "step-eyebrow", text: "Request sent" }),
        el("h1", { class: "step-title", text: "We'll text you" }),
      ]),
      el("p", { class: "summary-line" }, [
        el("b", { text: `${data.package_label} · ${data.day_label} at ${data.time_label}` }),
        ` · ${data.kids} kids · `,
        el("b", { text: money(data.total_cents) }),
      ]),
      el("p", { class: "step-sub", text: "No payment now, and nothing is booked yet. We'll text you back to confirm the date and time. Questions: (646) 203-8087." }),
      el("a", { class: "btn btn-ghost", href: "/index.html", text: "Back to the party page" })
    );
    renderRail();
    renderAdvance();
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
          el("li", {}, [el("span", { text: "Balance due day of party" }), el("span", { text: money(ui.quote.balance_cents) })]),
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

  // ---------- secondary asks ----------
  function submitWeekday(event) {
    event.preventDefault();
    const error = $("weekday-error");
    error.textContent = "";
    const date = $("weekday-date").value;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return void (error.textContent = "Pick the date you have in mind.");
    if (date < ui.firstBookable) return void (error.textContent = "That's too soon to book online. Call (646) 203-8087 and we'll see what we can do.");
    if (date > shiftDate(ui.today, 366)) return void (error.textContent = "That's more than a year out. Check the year, or call (646) 203-8087.");
    const listed = ui.days.find((d) => d.date === date);
    if (listed && listed.status !== "request") {
      if (listed.status === "open") selectDate(listed);
      else error.textContent = `${listed.day_label} is ${listed.status === "booked" ? "booked" : "not available online"}. Call (646) 203-8087 if you need that date.`;
      return;
    }
    if (!listed && isWeekendDate(date) && ui.weekendDefault === "open") {
      error.textContent = "Weekends are booked online for the next 8 weekends. For a later weekend, call (646) 203-8087.";
      return;
    }
    selectDate({ date: date, day_label: dayLabelFor(date), status: "request", slot_id: null, time_label: null });
  }

  async function submitWaitlist(event) {
    event.preventDefault();
    const input = $("waitlist-email");
    const note = $("waitlist-note");
    if (!input.value.trim() || input.value.indexOf("@") < 0) {
      note.textContent = "Enter the email to notify.";
      return;
    }
    const { status, data } = await api("/api/waitlist", { method: "POST", body: { email: input.value.trim() } });
    note.textContent = status === 201 ? data.detail : detailText(data, "Couldn't save that. Try again.");
    if (status === 201) input.value = "";
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

    // A hold from an earlier visit may be gone; never show a step that implies a date we no longer hold.
    if (holding()) {
      const check = await api(`/api/holds/${draft.holdId}`);
      if (check.status !== 200 || check.data.status !== "pending_payment") clearDraft();
      else {
        draft.expiresAt = check.data.expires_at;
        ui.deadline = performance.now() + check.data.seconds_remaining * 1000;
        save();
      }
    }
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
      stopTimer();
      render(ui.step);
    });
    window.addEventListener("popstate", (event) => {
      const step = (event.state && event.state.step) || pathStep();
      go(step, "pop");
    });

    const wanted = pathStep();
    const target = allowed(wanted) ? wanted : earliestIncomplete();
    history.replaceState({ step: target }, "", `/book/${target}`);
    render(target);
  }

  boot();
})();
