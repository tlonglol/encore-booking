(function () {
  const { api, detailText, money, el, store } = window.Encore;
  const view = document.getElementById("view");
  const params = new URLSearchParams(window.location.search);
  const FAST_POLL_MS = 2000;
  const SLOW_POLL_MS = 5000;
  const FAST_POLL_FOR_MS = 120000;
  const startedAt = Date.now();

  function title(line1, line2) {
    return el("h1", { class: "hero-title" }, [el("span", { class: "line1", text: line1 }), el("span", { class: "line2", text: line2 })]);
  }

  function details(party) {
    const rows = [
      ["Package", party.package_label],
      ["Date", party.day_label],
      ["Time", `${party.time_label} · ${party.hours} hours`],
      ["Kids", String(party.kids)],
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
          el("dt", { text: "Balance due day of party" }),
          el("dd", { text: money(party.balance_cents) }),
        ]),
      ]),
    ]);
  }

  function where() {
    return el("p", { class: "fine" }, ["538 Main St, 2nd floor, entrance on Division St · New Rochelle, NY · ", el("a", { href: "tel:+16462038087", text: "(646) 203-8087" })]);
  }

  function showBooked(party) {
    store("encore.hold", null);
    view.replaceChildren(
      el("p", { class: "hold-caption", text: party.first_name ? `You're all set, ${party.first_name}` : "You're all set" }),
      title("You're", "booked!"),
      el("p", { class: "muted", text: "Confirmation code" }),
      el("div", { class: "code-box", text: party.confirmation_code }),
      el("p", { class: "muted small", text: "A text with these details is on its way. Save this page: its link is your confirmation." }),
      details(party),
      where()
    );
  }

  function showMessage(line1, line2, message, actions) {
    view.replaceChildren(title(line1, line2), el("p", { class: "hero-sub", style: "margin:0 auto", text: message }), ...(actions || []), where());
  }

  function bookAgain(label) {
    return el("a", { class: "btn btn-primary", style: "margin-top:2rem", href: "index.html", text: label || "Pick another date" });
  }

  function showPending(data) {
    const minutes = Math.ceil(data.seconds_remaining / 60);
    view.replaceChildren(
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
    );
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
          store("encore.hold", null);
          showMessage(
            "Payment",
            "received",
            "We received your payment. This slot's hold had expired, so we'll call you within the hour to lock in your date."
          );
          return;
        case "expired":
          store("encore.hold", null);
          showMessage("Hold", "expired", "Your 15-minute hold ran out before payment arrived, so the date was released. If you did pay, we'll call you.", [bookAgain()]);
          return;
        case "cancelled":
          store("encore.hold", null);
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
