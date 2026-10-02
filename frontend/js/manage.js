// The no-login manage page. The token in the URL is the only credential, and every rule shown here was
// decided by the server: this file renders what it was told and never works out eligibility for itself.
(function () {
  const { api, detailText, money, el } = window.Encore;
  window.EncoreShell.mountNav({ ctaHref: "index.html#book" });
  window.EncoreShell.mountFooter();
  const view = document.getElementById("view");
  const token = new URLSearchParams(window.location.search).get("t");

  function title(line1, line2) {
    return el("h1", { class: "hero-title" }, [
      el("span", { class: "line1", text: line1 }),
      el("span", { class: "line2", text: line2 }),
    ]);
  }

  function where() {
    return el("p", { class: "fine" }, [
      "538 Main St, 2nd floor, entrance on Division St · New Rochelle, NY · ",
      el("a", { href: "tel:+16462038087", text: "(646) 203-8087" }),
    ]);
  }

  function details(party) {
    const rows = [
      ["Package", party.package_label],
      ["Date", party.day_label],
      ["Time", `${party.time_label} · ${party.hours} hours`],
      ["Kids", String(party.kids)],
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

  function showCancelled(data) {
    view.replaceChildren(
      el("p", { class: "hold-caption", text: `Confirmation ${data.party.confirmation_code}` }),
      title("Booking", "cancelled"),
      el("p", { class: "hero-sub", style: "margin:0 auto", text: `Your ${data.party.package_label} party on ${data.party.day_label} was cancelled.` }),
      el("a", { class: "btn btn-primary", style: "margin-top:2rem", href: "index.html", text: "Book another date" }),
      where()
    );
  }

  function showBooked(data) {
    const party = data.party;
    view.replaceChildren(
      el("p", { class: "hold-caption", text: party.first_name ? `${party.first_name}'s booking` : "Your booking" }),
      title("Manage", "your party"),
      el("p", { class: "muted", text: "Confirmation code" }),
      el("div", { class: "code-box", text: party.confirmation_code }),
      details(party),
      el("p", {
        class: "fine",
        text: data.inside_free_window
          ? `Free to cancel or move until ${data.free_until_label}.`
          : "Your party is less than a week away, so the deposit is no longer refundable.",
      }),
      where()
    );
  }

  function showMessage(line1, line2, message) {
    view.replaceChildren(
      title(line1, line2),
      el("p", { class: "hero-sub", style: "margin:0 auto", text: message }),
      el("a", { class: "btn btn-primary", style: "margin-top:2rem", href: "index.html", text: "Book a party" }),
      where()
    );
  }

  async function load() {
    if (!token) return showMessage("Nothing", "to manage", "This link is missing its booking. Check the link we texted you.");
    const { status, data } = await api(`/api/manage/${encodeURIComponent(token)}`);
    if (status === 404) return showMessage("Not", "found", detailText(data));
    if (status !== 200) return showMessage("Try", "again", detailText(data, "We couldn't load your booking. Refresh in a moment."));
    if (data.status === "cancelled") return showCancelled(data);
    showBooked(data);
  }

  load();
})();
