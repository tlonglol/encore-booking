// The no-login manage page. The token in the URL is the only credential, and every rule shown here was
// decided by the server: this file renders what it was told and never works out eligibility for itself.
(function () {
  const { api, detailText, money, el, paint } = window.Encore;
  const C = window.EncoreContent;
  window.EncoreShell.mountNav({ ctaHref: "index.html#book" });
  window.EncoreShell.mountFooter();
  const view = document.getElementById("view");
  const token = new URLSearchParams(window.location.search).get("t");
  const PHONE = "(646) 203-8087";

  const ui = { busy: false, adding: null, picking: false, confirmingCancel: false, days: [], catalog: [] };
  // The booking as the server last described it; every panel renders from this and nothing else.
  let latest = null;

  function title(line1, line2) {
    return el("h1", { class: "hero-title" }, [
      el("span", { class: "line1", text: line1 }),
      el("span", { class: "line2", text: line2 }),
    ]);
  }

  function where() {
    return el("p", { class: "fine" }, [
      "538 Main St, 2nd floor, entrance on Division St · New Rochelle, NY · ",
      el("a", { href: "tel:+16462038087", text: PHONE }),
    ]);
  }

  function sectionLabel(text) {
    return el("div", { class: "section-label" }, [el("span", { text: text }), el("i")]);
  }

  function summaryNotesRow(party) {
    return party.notes ? [["Notes", party.notes]] : [];
  }

  function details(party) {
    const rows = [
      ["Package", party.package_label],
      ["Date", party.day_label],
      ["Time", `${party.time_label} · ${party.hours} hours`],
      ["Kids", String(party.kids)],
      ...summaryNotesRow(party),
      ...party.line_items
        .filter((item) => item.key !== "package" && item.key !== "additional_kids")
        .map((item) => [
          item.quantity > 1 ? `${item.label} × ${item.quantity}` : item.label,
          item.included ? "Included" : money(item.amount_cents),
        ]),
      ["Total", money(party.total_cents)],
      ["Deposit paid", money(party.deposit_cents)],
    ];
    return el("div", { class: "details" }, [
      el("dl", {}, [
        ...rows.flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: v })]),
        el("div", { style: "display:contents" }, [
          el("dt", { text: "Due at the party" }),
          el("dd", { text: money(party.balance_cents) }),
        ]),
      ]),
    ]);
  }

  function error(message) {
    const box = document.getElementById("manage-error");
    if (box) box.textContent = message || "";
  }

  async function send(path, body) {
    if (ui.busy) return null;
    ui.busy = true;
    error(null);
    const result = await api(`/api/manage/${encodeURIComponent(token)}${path}`, { method: "POST", body: body });
    ui.busy = false;
    return result;
  }

  // ---------- add extras ----------
  // Prices come from /api/catalog, the same source the landing page and the funnel use, so there is never a
  // second copy of a price in JavaScript. content.js supplies the wording and the display order only.
  function addableAddons() {
    const have = currentAddons();
    const order = C.ADDON_ORDER.concat(["highlight_reel"]);
    return (ui.catalog || [])
      .filter((addon) => have[addon.key] !== "included")
      .sort((a, b) => order.indexOf(a.key) - order.indexOf(b.key));
  }

  // What the booking already has: a quantity, or "included" for an add-on the package covers for free.
  function currentAddons() {
    const have = {};
    latest.party.line_items.forEach((item) => {
      if (item.key === "package" || item.key === "additional_kids" || item.key === "sales_tax") return;
      have[item.key] = item.included ? "included" : item.quantity;
    });
    return have;
  }

  // Highlight Reel needs the tournament, exactly as the funnel and the pricing function say it does.
  function blockedReason(addon) {
    if (addon.requires && !wantedQuantity(addon.requires)) {
      const parent = (ui.catalog.find((entry) => entry.key === addon.requires) || {}).label;
      return `Needs ${parent}`;
    }
    return null;
  }

  function wantedQuantity(key) {
    const have = currentAddons();
    const owned = have[key] === "included" ? 1 : have[key] || 0;
    return Math.max(owned, ui.adding.addons[key] || 0);
  }

  function addPanel(data) {
    const can = data.can;
    if (!can.can_add_addons) {
      return el("div", { class: "manage-card" }, [
        el("h3", { text: "Add extras" }),
        el("p", { class: "muted small", text: can.addons_reason }),
      ]);
    }
    if (!ui.adding) {
      return el("div", { class: "manage-card" }, [
        el("h3", { text: "Add extras" }),
        el("p", { class: "muted small", text: `Pizza, drinks, VR and more, right up to ${can.addons_close_label}. Nothing is charged now: extras go on the balance you pay at the party.` }),
        el("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: () => start(data), text: "Add extras" }),
      ]);
    }
    const rows = addableAddons().map((addon) => addonRow(addon));
    return el("div", { class: "manage-card" }, [
      el("h3", { text: "Add extras" }),
      el("div", { class: "manage-rows" }, rows),
      kidsRow(data),
      notesRow(),
      el("div", { class: "manage-actions" }, [
        el("button", { class: "btn btn-primary btn-sm", type: "button", onclick: () => submitAddons(data), text: "Save changes" }),
        el("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: () => cancelAdding(data), text: "Never mind" }),
      ]),
    ]);
  }

  // Edited under the same panel, the same cutoff, as the add-ons: posting it is its own reason to submit,
  // not an afterthought tacked onto an addon change.
  function notesRow() {
    return el("div", { class: "field" }, [
      el("label", { for: "manage-notes", text: "Anything we should know? (optional)" }),
      el(
        "textarea",
        {
          id: "manage-notes",
          rows: "3",
          maxlength: "500",
          placeholder: "Theme, favorite game, anything the host should know",
          oninput: (event) => {
            ui.adding.notes = event.target.value;
          },
        },
        [ui.adding.notes || ""]
      ),
    ]);
  }

  function start(data) {
    ui.adding = { addons: {}, kids: data.party.kids, notes: data.party.notes || "" };
    render(data);
  }

  function cancelAdding(data) {
    ui.adding = null;
    render(data);
  }

  function addonRow(addon) {
    const have = currentAddons();
    const owned = have[addon.key] || 0;
    const adding = ui.adding.addons[addon.key] || 0;
    const blocked = blockedReason(addon);
    const note = blocked || (owned ? `${owned} already on your party` : `+${money(addon.price_cents)}${addon.has_quantity ? " each" : ""}`);
    const control = blocked
      ? el("span", { class: "manage-price", text: "—" })
      : addon.has_quantity
        ? stepper(adding, (value) => setAddon(addon.key, value))
        : el("button", {
            class: "btn btn-sm " + (adding || owned ? "btn-primary" : "btn-ghost"),
            type: "button",
            disabled: Boolean(owned),
            onclick: () => setAddon(addon.key, adding ? 0 : 1),
            text: owned ? "On your party" : adding ? "Adding" : "Add",
          });
    return el("div", { class: "manage-row" }, [
      el("div", {}, [el("div", { class: "manage-row-name", text: addon.label }), el("span", { class: "manage-price", text: note })]),
      control,
    ]);
  }

  function stepper(value, set) {
    return el("div", { class: "manage-stepper" }, [
      el("button", { class: "step", type: "button", "aria-label": "Fewer", onclick: () => set(Math.max(0, value - 1)), text: "−" }),
      el("span", { class: "step-value", text: String(value) }),
      el("button", { class: "step", type: "button", "aria-label": "More", onclick: () => set(value + 1), text: "+" }),
    ]);
  }

  function kidsRow(data) {
    return el("div", { class: "manage-row" }, [
      el("div", {}, [
        el("div", { class: "manage-row-name", text: "Kids" }),
        el("span", { class: "manage-price", text: `${data.party.kids} booked · more kids are priced at the party rate` }),
      ]),
      stepper(ui.adding.kids, (value) => setKids(data, value)),
    ]);
  }

  function setAddon(key, value) {
    ui.adding.addons[key] = value;
    reload();
  }

  function setKids(data, value) {
    ui.adding.kids = Math.max(data.party.kids, value);
    reload();
  }

  async function submitAddons(data) {
    // The server keeps the larger of each quantity, so a "2 more pizza packs" has to be sent as the total.
    const have = currentAddons();
    const wanted = {};
    Object.entries(ui.adding.addons).forEach(([key, extra]) => {
      if (!extra) return;
      const owned = have[key] === "included" ? 0 : have[key] || 0;
      wanted[key] = owned + extra;
    });
    const result = await send("/addons", { addons: wanted, kids: ui.adding.kids, notes: ui.adding.notes });
    if (!result) return;
    if (result.status !== 200) return error(detailText(result.data));
    ui.adding = null;
    render(result.data, "Saved. Your balance due at the party has been updated.");
  }

  // ---------- reschedule ----------
  function reschedulePanel(data) {
    const can = data.can;
    if (!can.can_reschedule) {
      return el("div", { class: "manage-card" }, [
        el("h3", { text: "Move your date" }),
        el("p", { class: "muted small", text: can.reschedule_reason }),
      ]);
    }
    if (!ui.picking) {
      return el("div", { class: "manage-card" }, [
        el("h3", { text: "Move your date" }),
        el("p", { class: "muted small", text: data.inside_free_window ? "Pick any open weekend. Your deposit comes with you and nothing is charged." : "Your party is inside the 7-day window, so this is your one courtesy move. Your deposit comes with you." }),
        el("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: () => openPicker(data), text: "Pick a new date" }),
      ]);
    }
    const open = ui.days.filter((day) => day.status === "open");
    return el("div", { class: "manage-card" }, [
      el("h3", { text: "Move your date" }),
      open.length
        ? el("div", { class: "manage-dates" }, open.map((day) =>
            el("button", { class: "manage-date", type: "button", onclick: () => move(day), text: `${day.day_label} · ${day.time_label}` })
          ))
        : el("p", { class: "muted small", text: `No open weekends right now. Call ${PHONE} and we'll sort it out.` }),
      el("div", { class: "manage-actions" }, [
        el("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: () => closePicker(data), text: "Never mind" }),
      ]),
    ]);
  }

  async function openPicker(data) {
    const { status, data: grid } = await api("/api/availability");
    ui.days = status === 200 ? grid.days : [];
    ui.picking = true;
    render(data);
  }

  function closePicker(data) {
    ui.picking = false;
    render(data);
  }

  async function move(day) {
    const result = await send("/reschedule", { slot_id: day.slot_id });
    if (!result) return;
    if (result.status !== 200) return error(detailText(result.data));
    ui.picking = false;
    render(result.data, `Moved. Your party is now ${day.day_label} at ${day.time_label}.`);
  }

  // ---------- cancel ----------
  function cancelPanel(data) {
    const can = data.can;
    if (!can.can_cancel) return null;
    if (!ui.confirmingCancel) {
      return el("div", { class: "manage-card" }, [
        el("h3", { text: "Cancel your party" }),
        el("p", { class: "muted small", text: can.refund_on_cancel ? `Free until ${data.free_until_label}: your $100 deposit goes back to the card that paid it.` : can.cancel_reason }),
        el("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: () => askCancel(data), text: "Cancel my party" }),
      ]);
    }
    return el("div", { class: "manage-card manage-card-warn" }, [
      el("h3", { text: can.refund_on_cancel ? "Cancel and refund the deposit?" : "Cancel without a refund?" }),
      el("p", { class: "muted small", text: can.refund_on_cancel ? "Your date goes back on the calendar and your $100 deposit is returned. This can't be undone." : `Your party is less than a week away, so the $100 deposit stays with Encore. Your date goes back on the calendar. This can't be undone. If you'd rather move the date, close this and use "Move your date".` }),
      el("div", { class: "manage-actions" }, [
        el("button", { class: "btn btn-primary btn-sm", type: "button", onclick: () => doCancel(data), text: can.refund_on_cancel ? "Yes, cancel and refund" : "Yes, cancel without a refund" }),
        el("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: () => stopCancel(data), text: "Keep my party" }),
      ]),
    ]);
  }

  function askCancel(data) {
    ui.confirmingCancel = true;
    render(data);
  }

  function stopCancel(data) {
    ui.confirmingCancel = false;
    render(data);
  }

  async function doCancel(data) {
    const result = await send("/cancel", { forfeit_deposit: !data.can.refund_on_cancel });
    if (!result) return;
    if (result.status !== 200) return error(detailText(result.data));
    showCancelled(result.data);
  }

  // ---------- rendering ----------
  function showCancelled(result) {
    paint(view, [
      title("Party", "cancelled"),
      el("p", { class: "hero-sub", style: "margin:0 auto", text: result.message }),
      result.refunded ? el("p", { class: "fine", text: result.settlement_notice }) : null,
      el("a", { class: "btn btn-primary", style: "margin-top:2rem", href: "index.html", text: "Book another date" }),
      where()
    ]);
  }

  function showAlreadyCancelled(data) {
    paint(view, [
      el("p", { class: "hold-caption", text: `Confirmation ${data.party.confirmation_code}` }),
      title("Booking", "cancelled"),
      el("p", { class: "hero-sub", style: "margin:0 auto", text: `Your ${data.party.package_label} party on ${data.party.day_label} was cancelled.` }),
      el("a", { class: "btn btn-primary", style: "margin-top:2rem", href: "index.html", text: "Book another date" }),
      where()
    ]);
  }

  // Every path that re-renders also refreshes what the page is rendering from, so a stepper tap and a
  // server response both go through one place.
  function render(data, flash) {
    latest = data;
    const party = data.party;
    paint(view, [
      el("p", { class: "hold-caption", text: party.first_name ? `${party.first_name}'s booking` : "Your booking" }),
      title("Manage", "your party"),
      flash ? el("p", { class: "manage-flash", role: "status", text: flash }) : null,
      el("p", { class: "muted", text: "Confirmation code" }),
      el("div", { class: "code-box", text: party.confirmation_code }),
      details(party),
      el("p", {
        class: "fine",
        text: data.inside_free_window
          ? `Free to cancel or move until ${data.free_until_label}.`
          : "Your party is less than a week away, so the deposit is no longer refundable.",
      }),
      sectionLabel("What you can change"),
      el("div", { id: "manage-error", class: "manage-error", role: "alert" }),
      addPanel(data),
      reschedulePanel(data),
      cancelPanel(data),
      where()
    ]);
  }

  function showMessage(line1, line2, message) {
    paint(view, [
      title(line1, line2),
      el("p", { class: "hero-sub", style: "margin:0 auto", text: message }),
      el("a", { class: "btn btn-primary", style: "margin-top:2rem", href: "index.html", text: "Book a party" }),
      where()
    ]);
  }

  function reload() {
    if (latest) render(latest);
  }

  async function load() {
    if (!token) return showMessage("Nothing", "to manage", "This link is missing its booking. Check the link we emailed you.");
    const [{ status, data }, catalog] = await Promise.all([
      api(`/api/manage/${encodeURIComponent(token)}`),
      api("/api/catalog"),
    ]);
    ui.catalog = catalog.status === 200 ? catalog.data.addons : [];
    if (status === 404) return showMessage("Not", "found", detailText(data));
    if (status !== 200) return showMessage("Try", "again", detailText(data, "We couldn't load your booking. Refresh in a moment."));
    if (data.status === "cancelled") return showAlreadyCancelled(data);
    render(data);
  }

  load();
})();
