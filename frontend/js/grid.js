(function () {
  const { api, detailText, money, el } = window.Encore;
  const Funnel = window.EncoreFunnel;

  const STATUS_TEXT = { open: "Open", held: "On hold", booked: "Booked", blocked: "Unavailable", past: "Unavailable", request: "By request" };
  const DAY_MS = 86400000;

  const state = { days: [], weekendDefault: "open", today: null, firstBookable: null, holding: false };

  const $ = (id) => document.getElementById(id);

  function jsDate(day) {
    return new Date(day.date + "T12:00:00");
  }

  function isWeekendDay(day) {
    const dow = jsDate(day).getDay();
    return dow === 0 || dow === 6;
  }

  function shiftDate(date, days) {
    return new Date(new Date(date + "T12:00:00").getTime() + days * DAY_MS).toISOString().slice(0, 10);
  }

  function dayLabel(date) {
    return new Date(date + "T12:00:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
  }

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
    state.days.filter(isWeekendDay).forEach((day) => {
      const last = groups[groups.length - 1];
      if (jsDate(day).getDay() === 0 && last && last[last.length - 1].date === shiftDate(day.date, -1)) last.push(day);
      else groups.push([day]);
    });
    return groups;
  }

  function weekendLabel(group) {
    const first = group[0];
    const saturday = jsDate(first).getDay() === 0 ? shiftDate(first.date, -1) : first.date;
    return `Weekend of ${new Date(saturday + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" })}`;
  }

  function renderGrid() {
    $("slots").replaceChildren(
      ...weekendGroups().map((group) =>
        el("div", { class: "wk-group" }, [
          el("div", { class: "wk-label", text: weekendLabel(group) }),
          el(
            "div",
            { class: "wk-cards" },
            group.map((day) => {
              const tappable = day.status === "open" || day.status === "request";
              return el(
                "button",
                {
                  type: "button",
                  class: `wk-card ${day.status}`,
                  disabled: !tappable,
                  "aria-label": `${day.day_label} ${day.time_label || "10:00 AM"}, ${STATUS_TEXT[day.status]}`,
                  onclick: tappable ? () => pickDate(day) : null,
                },
                [
                  el("span", { class: "wk-date", text: day.day_label }),
                  el("span", { class: "wk-time", text: day.time_label || "10:00 AM" }),
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

  function activeHold() {
    const saved = Funnel.load();
    if (saved && saved.mode === "hold" && saved.holdId && Date.parse(saved.expiresAt) > Date.now()) return saved;
    return null;
  }

  function renderResume() {
    const saved = activeHold();
    const notice = $("resume-notice");
    if (!saved) {
      notice.replaceChildren();
      return;
    }
    const next = saved.package ? "addons.html" : "package.html";
    notice.replaceChildren(
      el("div", { class: "notice" }, [
        el("div", {}, [`You're holding `, el("b", { text: `${saved.dayLabel} at ${saved.timeLabel}` }), `. Picking another date releases it.`]),
        el("div", { class: "alts" }, [el("a", { class: "chip", href: next, text: "Keep going" })]),
      ])
    );
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

  function advance(stateUpdate) {
    const previous = Funnel.load() || {};
    // Keep selections across a date change so Back never loses work; a fresh visit starts clean.
    Funnel.save({ package: previous.package, addons: previous.addons || {}, kids: previous.kids, contact: previous.contact, ...stateUpdate });
    window.location.href = "package.html";
  }

  async function pickDate(day) {
    if (day.status === "request") {
      advance({ mode: "request", date: day.date, dayLabel: day.day_label, timeLabel: null });
      return;
    }
    if (state.holding) return;
    state.holding = true;
    const saved = activeHold();
    const { status, data } = await api("/api/holds", {
      method: "POST",
      body: { slot_id: day.slot_id, replace_hold_id: saved ? saved.holdId : null },
    });
    state.holding = false;
    if (status === 201) {
      advance({
        mode: "hold",
        holdId: data.hold_id,
        slotId: day.slot_id,
        date: data.date,
        dayLabel: data.day_label,
        timeLabel: data.time_label,
        expiresAt: data.expires_at,
      });
      return;
    }
    if (status === 409) {
      await loadGrid();
      const alts = data.alternatives || [];
      showSlotNotice(`Sorry, ${day.day_label} was just taken. ${alts.length ? "These are still open:" : "No other dates are open right now."}`, alts, true);
      return;
    }
    if (status === 429 && data && data.expires_at) {
      const until = new Date(data.expires_at).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
      showSlotNotice(`${detailText(data)} Try again after ${until}.`, [], true);
      return;
    }
    showSlotNotice(detailText(data, "Couldn't hold that date. Try again."), [], true);
  }

  // ---------- Weekday request ----------
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
    if (!listed && isWeekendDay({ date: date }) && state.weekendDefault === "open") {
      weekdayError("Weekends are booked online for the next 8 weekends. For a later weekend, call (646) 203-8087.");
      return;
    }
    advance({ mode: "request", date: date, dayLabel: dayLabel(date), timeLabel: null });
  }

  // ---------- Waitlist ----------
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

  async function boot() {
    Funnel.renderSteps(0);
    Funnel.barInit();
    wireToggle("weekday-toggle", "weekday-form", "weekday-date");
    wireToggle("waitlist-toggle", "waitlist-form", "waitlist-email");
    $("weekday-form").addEventListener("submit", submitWeekday);
    $("waitlist-form").addEventListener("submit", submitWaitlist);

    const catalog = await Funnel.catalog();
    if (catalog) {
      const packages = catalog.packages;
      $("tier-strip").replaceChildren(
        ...packages.map((p) => el("span", {}, [`${p.label}`, el("b", { text: money(p.price_cents) })]))
      );
      Funnel.barFrom(Math.min(...packages.map((p) => p.price_cents)));
    }
    loadGrid();
    setInterval(() => {
      if (document.visibilityState === "visible") loadGrid();
    }, 30000);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") loadGrid();
    });
  }

  boot();
})();
