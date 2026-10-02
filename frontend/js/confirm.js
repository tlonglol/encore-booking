(function () {
  const { api, detailText, money, el, paint, store, keep } = window.Encore;
  window.EncoreShell.mountNav({ ctaHref: "index.html#book" });
  window.EncoreShell.mountFooter();
  const view = document.getElementById("view");
  const params = new URLSearchParams(window.location.search);
  const FAST_POLL_MS = 2000;
  const SLOW_POLL_MS = 5000;
  const FAST_POLL_FOR_MS = 120000;
  const startedAt = Date.now();

  function title(line1, line2) {
    return el("h1", { class: "hero-title" }, [el("span", { class: "line1", text: line1 }), el("span", { class: "line2", text: line2 })]);
  }

  // "Leo turns 8 · Oct 14" when a birthday was given; nothing otherwise.
  function birthdayRow(party) {
    if (!party.birthday) return [];
    const day = new Date(party.birthday + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" });
    const who = party.kid_name || "Birthday";
    const turns = party.kid_age ? `${who} turns ${party.kid_age}` : `${who}'s birthday`;
    return [["Birthday", `${turns} · ${day}`]];
  }

  function details(party) {
    const rows = [
      ["Package", party.package_label],
      ["Date", party.day_label],
      ["Time", `${party.time_label} · ${party.hours} hours`],
      ["Kids", String(party.kids)],
      ...birthdayRow(party),
      ...party.line_items
        .filter((item) => item.key !== "package" && item.key !== "additional_kids")
        .map((item) => [item.quantity > 1 ? `${item.label} × ${item.quantity}` : item.label, item.included ? "Included" : money(item.amount_cents)]),
      ["Total", money(party.total_cents)],
      ["Deposit paid", money(party.deposit_cents)],
    ];
    return el("div", { class: "details" }, [
      el("dl", {}, [
        ...rows.flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: v })]),
        el("div", { class: "balance", style: "display:contents" }, [
          el("dt", { text: "Due at the party" }),
          el("dd", { text: money(party.balance_cents) }),
        ]),
      ]),
    ]);
  }

  function utcStamp(iso, addHours) {
    const d = new Date(new Date(iso).getTime() + (addHours || 0) * 3600000);
    return d.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z");
  }

  function calendarButtons(party) {
    const google = new URL("https://calendar.google.com/calendar/render");
    google.searchParams.set("action", "TEMPLATE");
    google.searchParams.set("text", `Encore Esports ${party.package_label} party`);
    google.searchParams.set("dates", `${utcStamp(party.starts_at)}/${utcStamp(party.starts_at, party.hours)}`);
    google.searchParams.set("location", "Encore Esports, 538 Main St, 2nd floor, New Rochelle, NY");
    google.searchParams.set("details", `Confirmation ${party.confirmation_code}. Balance ${money(party.balance_cents)} due at the party.`);
    const apiOrigin = (window.ENCORE_CONFIG && window.ENCORE_CONFIG.apiOrigin) || "";
    return el("div", { class: "cal-buttons" }, [
      el("a", { class: "btn btn-ghost btn-sm", href: google.toString(), target: "_blank", rel: "noopener", text: "Add to Google Calendar" }),
      el("a", { class: "btn btn-ghost btn-sm", href: `${apiOrigin}/api/bookings/${encodeURIComponent(party.confirmation_code)}/calendar.ics`, text: "Apple / .ics" }),
    ]);
  }

  function renderBar(party) {
    const bar = document.getElementById("confirm-bar");
    bar.hidden = false;
    bar.classList.add("show");
    document.body.classList.add("bar-on");
    document.getElementById("quote-total").textContent = money(party.total_cents);
    document.getElementById("quote-split").replaceChildren(
      el("b", { text: `${money(party.deposit_cents)} paid` }),
      el("br"),
      `${money(party.balance_cents)} due at the party`
    ]);
    document.getElementById("quote-lines").replaceChildren(
      ...party.line_items.map((item) =>
        el("li", {}, [
          el("span", { text: item.quantity > 1 ? `${item.label} × ${item.quantity}` : item.label }),
          item.included ? el("span", { class: "inc", text: "Included" }) : el("span", { text: money(item.amount_cents) }),
        ])
      )
    ]);
    const toggle = document.getElementById("quote-toggle");
    toggle.addEventListener("click", () => {
      const lines = document.getElementById("quote-lines");
      lines.hidden = !lines.hidden;
      toggle.textContent = lines.hidden ? "Details" : "Hide";
    });
  }

  function where() {
    return el("p", { class: "fine" }, ["538 Main St, 2nd floor, entrance on Division St · New Rochelle, NY · ", el("a", { href: "tel:+16462038087", text: "(646) 203-8087" })]);
  }

  function manageLinks(party) {
    if (!party.manage_token) return [];
    const href = `manage.html?t=${encodeURIComponent(party.manage_token)}`;
    return [
      el("p", { class: "fine" }, [
        "You can add Pizza Packs, Drinks, or VR anytime before the party: ",
        el("a", { href: href, text: "manage this booking" }),
        ". The same page cancels or moves your date.",
      ]),
    ];
  }

  function showBooked(party) {
    store("encore.draft", null);
    // The hold became a booking, so the browser-wide pointer every tab watches is finished with.
    keep("encore.hold", null);
    paint(view, [
      el("p", { class: "hold-caption", text: party.first_name ? `You're all set, ${party.first_name}` : "You're all set" }),
      title("You're", "booked!"),
      el("p", { class: "muted", text: "Confirmation code" }),
      el("div", { class: "code-box", text: party.confirmation_code }),
      el("p", { class: "muted small", text: "A text and an email with these details are on their way. Save this page: its link is your confirmation." }),
      details(party),
      calendarButtons(party),
      el("p", { class: "fine", text: "What happens next: Encore will text you the week of your party." }),
      ...manageLinks(party),
      where()
    ]);
    renderBar(party);
  }

  function showMessage(line1, line2, message, actions) {
    paint(view, [title(line1, line2), el("p", { class: "hero-sub", style: "margin:0 auto", text: message }), ...(actions || []), where()]);
  }

  function bookAgain(label) {
    return el("a", { class: "btn btn-primary", style: "margin-top:2rem", href: "index.html", text: label || "Pick another date" });
  }

  function showPending(data) {
    const minutes = Math.ceil(data.seconds_remaining / 60);
    paint(view, [
      el("p", { class: "hold-caption", text: "One moment" }),
      title("Confirming", "your payment"),
      el("div", { class: "spinner", role: "status", "aria-label": "Waiting for payment confirmation" }),
      el("p", { class: "hero-sub", style: "margin:0 auto", text: "This usually takes a few seconds. Keep this page open." }),
      data.checkout_url
        ? el("p", { class: "fine" }, [
            `Haven't paid yet? Your date is held for about ${minutes} more minute${minutes === 1 ? "" : "s"}. `,
            el("a", { href: data.checkout_url, text: "Return to checkout" }),
          ])
        : null
    ]);
  }

  async function pollHold(holdId) {
    const { status, data } = await api(`/api/holds/${encodeURIComponent(holdId)}`);
    if (status === 200) {
      switch (data.status) {
        case "booked":
          showBooked(data.party);
          if (data.party.confirmation_code) {
            history.replaceState(null, "", `confirm.html?code=${encodeURIComponent(data.party.confirmation_code)}`);
          }
          return;
        case "paid_after_expiry":
          store("encore.draft", null);
          showMessage(
            "Payment",
            "received",
            "We received your payment. This slot's hold had expired, so we'll call you within the hour to lock in your date."
          );
          return;
        case "expired":
          store("encore.draft", null);
          showMessage("Hold", "expired", "Your 15-minute hold ran out before payment arrived, so the date was released. If you did pay, we'll call you.", [bookAgain()]);
          return;
        case "cancelled":
          store("encore.draft", null);
          showMessage("Hold", "cancelled", "This hold was cancelled, and no booking was made. Questions? Call (646) 203-8087.", [bookAgain()]);
          return;
        default:
          showPending(data);
      }
    } else if (status === 404) {
      showMessage("Not", "found", "We couldn't find that hold. Check the link, or call us.", [bookAgain("Book a party")]);
      return;
    }
    const delay = Date.now() - startedAt < FAST_POLL_FOR_MS ? FAST_POLL_MS : SLOW_POLL_MS;
    setTimeout(() => pollHold(holdId), delay);
  }

  async function loadBooking(code) {
    const { status, data } = await api(`/api/bookings/${encodeURIComponent(code)}`);
    if (status === 200 && data.status === "booked") {
      showBooked(data.party);
    } else if (status === 200) {
      showMessage("Booking", "cancelled", `Booking ${data.party.confirmation_code} for ${data.party.day_label} was cancelled. Questions? Call (646) 203-8087.`, [bookAgain("Book a party")]);
    } else if (status === 404) {
      showMessage("Not", "found", "We couldn't find that confirmation code. Check the link in your text, or call us.", [bookAgain("Book a party")]);
    } else {
      showMessage("Try", "again", detailText(data, "We couldn't load your booking. Refresh in a moment."));
    }
  }

  if (params.get("code")) loadBooking(params.get("code"));
  else if (params.get("hold")) pollHold(params.get("hold"));
  else showMessage("Book a", "party", "Nothing to confirm yet.", [bookAgain("Book a party")]);
})();
