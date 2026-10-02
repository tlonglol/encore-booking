(function () {
  const { api, detailText, money, el, paint } = window.Encore;
  const $ = (id) => document.getElementById(id);
  const panel = $("panel");
  window.EncoreShell.mountFooter();
  window.EncoreShell.mountNav({ accent: "ADMIN", ctaTag: "button", ctaId: "logout", ctaLabel: "Log out", ctaHidden: true });
  let current = "bookings";

  function notesCallout(notes) {
    if (!notes) return null;
    return el("div", { class: "notes-callout" }, [
      el("span", { class: "notes-label", text: "Notes:" }),
      notes,
    ]);
  }

  const FLAG_TEXT = {
    paid_after_expiry: "Paid after hold expired: call parent",
    duplicate_payment: "Second payment on a booked order: refund in Square",
    amount_mismatch: "Paid the wrong amount: check in Square",
    unmatched_order: "Payment with no matching hold: check in Square",
  };

  function when(iso) {
    return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  }

  function empty(text) {
    return el("p", { class: "muted", text });
  }

  function problem(data) {
    panel.replaceChildren(el("p", { class: "notice error", text: detailText(data) }));
  }

  async function guarded(path, options) {
    const result = await api(path, options);
    if (result.status === 401) showLogin();
    return result;
  }

  function confirmButton(label, confirmLabel, action, tone) {
    const button = el("button", { type: "button", class: `btn ${tone || "btn-danger"} btn-sm`, text: label });
    let armed = false;
    button.addEventListener("click", async () => {
      if (!armed) {
        armed = true;
        button.textContent = confirmLabel;
        setTimeout(() => {
          armed = false;
          button.textContent = label;
        }, 4000);
        return;
      }
      button.disabled = true;
      await action();
    });
    return button;
  }

  function contact(value, scheme) {
    return el("a", { href: `${scheme}:${value}`, class: "contact-link", text: value });
  }

  function addBookingCard() {
    return el("article", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("div", { class: "card-title", text: "Took one over the phone?" }),
      ]),
      el("div", { class: "card-meta", text: "Type it in and the date is claimed the same way a parent's booking claims it. No card is charged." }),
      el("div", { class: "card-actions" }, [
        el("button", { type: "button", class: "btn btn-primary btn-sm", text: "+ Add booking", onclick: openManual }),
      ]),
    ]);
  }

  // The balance is money the owner takes in person at the party, so the action is a confirm-then-act
  // button like the destructive ones, and the undo lives with the booking rather than behind a timeout:
  // a mis-tap is found later, at the till, not in the next four seconds.
  function balanceRow(b) {
    if (b.cancelled_at) return null;
    if (b.balance_cents <= 0) {
      return el("div", { class: "card-meta small muted", text: "Nothing due at the party." });
    }
    if (b.balance_paid_at) {
      return el("div", { class: "card-actions" }, [
        el("span", { class: "pill ok", text: `Balance paid ${when(b.balance_paid_at)}` }),
        el("button", {
          type: "button",
          class: "btn btn-ghost btn-sm",
          text: "Undo",
          onclick: () => setBalancePaid(b.id, false),
        }),
      ]);
    }
    return el("div", { class: "card-actions" }, [
      el("span", { class: "pill warn", text: `${money(b.balance_cents)} due at the party` }),
      confirmButton("Mark balance paid", "Tap again to confirm", () => setBalancePaid(b.id, true), "btn-primary"),
    ]);
  }

  async function setBalancePaid(id, paid) {
    const result = await guarded(`/api/admin/bookings/${id}/balance-paid`, { method: "POST", body: { paid } });
    if (result.status !== 200) return result.status === 401 ? null : problem(result.data);
    bookings();
  }

  async function bookings() {
    const { status, data } = await guarded("/api/admin/bookings");
    if (status !== 200) return status === 401 ? null : problem(data);
    if (!data.length) return panel.replaceChildren(addBookingCard(), empty("No bookings yet."));
    panel.replaceChildren(
      addBookingCard(),
      ...data.map((b) =>
        el("article", { class: "card" + (b.cancelled_at ? " cancelled" : "") }, [
          el("div", { class: "card-head" }, [
            el("div", { class: "card-title", text: `${b.day_label} · ${b.time_label}` }),
            b.cancelled_at ? el("span", { class: "pill bad", text: "Cancelled" }) : el("span", { class: "pill ok", text: `Deposit ${b.deposit_status}` }),
          ]),
          el("div", { class: "card-meta" }, [
            el("b", { text: `${b.package} · ${b.kids} kids` }),
            b.kid_name ? ` · for ${b.kid_name}${b.kid_age ? ` (${b.kid_age})` : ""}` : "",
            ` · ${b.addons}`,
            b.origin === "manual" ? el("span", { class: "pill", text: "By hand" }) : null,
          ]),
          el("div", { class: "card-meta" }, [
            "Total ", el("b", { text: money(b.total_cents) }), " · paid ", el("b", { text: money(b.paid_amount_cents) }),
            " · balance ", el("b", { text: money(b.balance_cents) }),
          ]),
          el("div", { class: "card-meta" }, [
            el("b", { text: b.name }), " · ", contact(b.phone, "tel"), " · ", contact(b.email, "mailto"),
          ]),
          b.birthday_line ? el("div", { class: "card-meta" }, [el("b", { text: "🎂 " + b.birthday_line })]) : null,
          notesCallout(b.notes),
          el("div", {
            class: "card-meta small muted",
            text: `Code ${b.confirmation_code} · booked ${when(b.booked_at)}`
              + (b.reschedule_count ? ` · moved ${b.reschedule_count}× (last ${when(b.rescheduled_at)})` : "")
              + (b.cancelled_at ? ` · cancelled ${when(b.cancelled_at)} by ${b.cancel_source || "owner"}${b.cancel_reason ? ` (${b.cancel_reason})` : ""}` : "")
              + (b.refund_status ? ` · refund ${b.refund_status}` : ""),
          }),
          balanceRow(b),
          el("div", { class: "card-actions" }, [
            el("span", { class: "pill" + (b.full_refund ? " ok" : " warn"), text: b.full_refund ? "Full refund if cancelled" : "Inside 7 days: no refund, one reschedule" }),
            b.cancelled_at
              ? null
              : confirmButton("Cancel booking", "Tap again to cancel", async () => {
                  const result = await guarded(`/api/admin/bookings/${b.id}/cancel`, { method: "POST", body: { reason: "Cancelled by owner" } });
                  if (result.status !== 200) return problem(result.data);
                  bookings();
                }),
          ]),
        ])
      )
    );
  }

  async function requests() {
    const { status, data } = await guarded("/api/admin/requests");
    if (status !== 200) return status === 401 ? null : problem(data);
    if (!data.length) return panel.replaceChildren(empty("No party requests yet."));
    panel.replaceChildren(
      ...data.map((r) =>
        el("article", { class: "card" + (r.status === "new" ? " flagged" : "") }, [
          el("div", { class: "card-head" }, [
            el("div", { class: "card-title", text: `${r.day_label} · ${r.time_label}` }),
            el("span", { class: "pill" + (r.status === "new" ? " warn" : ""), text: r.status }),
          ]),
          el("div", { class: "card-meta" }, [el("b", { text: `${r.package} · ${r.kids} kids` }), ` · ${r.addons}`]),
          el("div", { class: "card-meta" }, ["Quoted ", el("b", { text: money(r.total_cents) }), " · deposit would be ", el("b", { text: money(r.deposit_cents) })]),
          el("div", { class: "card-meta" }, [
            el("b", { text: r.name }), " · ", contact(r.phone, "tel"), " · ", contact(r.email, "mailto"),
          ]),
          r.birthday_line ? el("div", { class: "card-meta" }, [el("b", { text: "🎂 " + r.birthday_line })]) : null,
          notesCallout(r.notes),
          el("div", { class: "card-meta small muted", text: `Requested ${when(r.created_at)} · call or text the parent to confirm` }),
        ])
      )
    );
  }

  async function flags() {
    const { status, data } = await guarded("/api/admin/flags");
    if (status !== 200) return status === 401 ? null : problem(data);
    if (!data.length) return panel.replaceChildren(empty("No payment flags. Every payment became a booking."));
    panel.replaceChildren(
      ...data.map((f) =>
        el("article", { class: "card" + (f.resolved_at ? " cancelled" : " flagged") }, [
          el("div", { class: "card-head" }, [
            el("div", { class: "card-title", text: FLAG_TEXT[f.kind] || f.kind }),
            el("span", { class: "pill" + (f.resolved_at ? " ok" : " warn"), text: f.resolved_at ? "Resolved" : "Open" }),
          ]),
          f.name
            ? el("div", { class: "card-meta" }, [el("b", { text: f.name }), " · ", contact(f.phone, "tel"), " · ", contact(f.email, "mailto")])
            : null,
          f.day_label ? el("div", { class: "card-meta", text: `Wanted ${f.day_label} ${f.time_label} · ${f.package} · ${f.kids} kids` }) : null,
          el("div", { class: "card-meta small muted", text: `${f.amount_cents !== null ? money(f.amount_cents) : "?"} · Square payment ${f.square_payment_id} · ${when(f.created_at)}` }),
          f.resolved_at
            ? null
            : el("div", { class: "card-actions" }, [
                el("button", {
                  type: "button",
                  class: "btn btn-ghost btn-sm",
                  text: "Mark resolved",
                  onclick: async () => {
                    const result = await guarded(`/api/admin/flags/${f.id}/resolve`, { method: "POST" });
                    if (result.status !== 200) return problem(result.data);
                    flags();
                    counts();
                  },
                }),
              ]),
        ])
      )
    );
  }

  function resetCard() {
    return el("article", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("div", { class: "card-title", text: "Reset demo data" }),
        el("span", { class: "pill warn", text: "Deletes bookings" }),
      ]),
      el("div", {
        class: "card-meta",
        text: "Clears every booking, hold, refund and message, then lays the grid back out with sample parties, one date on hold and the rest open. Use it between demos.",
      }),
      el("div", { class: "card-actions" }, [
        confirmButton("Reset demo data", "Tap again to wipe and reseed", async () => {
          const result = await guarded("/api/admin/demo/reset", { method: "POST" });
          if (result.status !== 200) return problem(result.data);
          show("bookings");
          counts();
        }),
      ]),
    ]);
  }

  async function dates() {
    const { status, data } = await api("/api/availability");
    if (status !== 200) return problem(data);
    const slotDays = data.days.filter((s) => s.slot_id !== null);
    panel.replaceChildren(
      resetCard(),
      ...slotDays.map((s) => {
        const canBlock = s.status === "open" || s.status === "past" || s.status === "request";
        return el("article", { class: "card" }, [
          el("div", { class: "card-head" }, [
            el("div", { class: "card-title", text: `${s.day_label} · ${s.time_label}` }),
            el("span", { class: "pill" + (s.status === "open" ? " ok" : s.status === "blocked" ? " bad" : " warn"), text: s.status }),
          ]),
          el("div", { class: "card-actions" }, [
            s.status === "blocked"
              ? el("button", { type: "button", class: "btn btn-ghost btn-sm", text: "Unblock", onclick: () => setBlocked(s.slot_id, false) })
              : canBlock
                ? el("button", { type: "button", class: "btn btn-danger btn-sm", text: "Block date", onclick: () => setBlocked(s.slot_id, true) })
                : el("span", { class: "muted small", text: "Held or booked: cancel the booking before blocking." }),
          ]),
        ]);
      })
    );
  }

  async function setBlocked(slotId, blocked) {
    const result = await guarded(`/api/admin/slots/${slotId}/${blocked ? "block" : "unblock"}`, { method: "POST" });
    if (result.status !== 200 && result.status !== 401) {
      await dates();
      panel.prepend(el("p", { class: "notice error", text: detailText(result.data) }));
      return;
    }
    dates();
  }

  async function notifications() {
    const { status, data } = await guarded("/api/admin/notifications");
    if (status !== 200) return status === 401 ? null : problem(data);
    if (!data.length) return panel.replaceChildren(empty("No messages yet."));
    panel.replaceChildren(
      ...data.map((n) =>
        el("article", { class: "card" + (n.status === "failed" ? " failed" : "") }, [
          el("div", { class: "card-head" }, [
            el("div", { class: "card-meta" }, [el("b", { text: `${n.audience} · ${n.channel}` }), ` → ${n.recipient}`]),
            el("span", { class: "pill" + (n.status === "sent" ? " ok" : n.status === "failed" ? " bad" : " warn"), text: n.status === "logged" ? "logged (no Twilio)" : n.status }),
          ]),
          n.error ? el("div", { class: "notice error", text: n.error }) : null,
          el("div", { class: "body-text", text: n.body }),
          el("div", { class: "card-meta small muted", text: when(n.created_at) }),
        ])
      )
    );
  }

  async function leads() {
    const { status, data } = await guarded("/api/admin/leads");
    if (status !== 200) return status === 401 ? null : problem(data);
    if (!data.abandoned.length) return panel.replaceChildren(empty("No abandoned checkouts yet."));
    panel.replaceChildren(
      el("div", { class: "section-label", text: "Abandoned checkouts, last 14 days" }),
      ...(data.abandoned.length
        ? data.abandoned.map((a) =>
            el("article", { class: "card" }, [
              el("div", { class: "card-head" }, [
                el("div", { class: "card-title", text: a.day_label ? `${a.day_label} · ${a.time_label}` : "No date" }),
                el("span", { class: "pill warn", text: "Did not pay" }),
              ]),
              el("div", { class: "card-meta" }, [
                a.name ? el("b", { text: a.name }) : el("span", { class: "muted", text: "No name yet" }),
                " · ",
                contact(a.email, "mailto"),
                a.phone ? [" · ", contact(a.phone, "tel")] : null,
              ].flat()),
              el("div", { class: "card-meta small muted", text: `${a.package ? `${a.package}, ${a.kids} kids · ` : "Left before choosing a package · "}started ${when(a.created_at)}` }),
            ])
          )
        : [empty("None.")])
    );
  }

  // Grouped by date, because the question the owner asks here is per date: is this Saturday worth
  // opening a second slot for, or calling these people back about.
  async function waitlist() {
    const { status, data } = await guarded("/api/admin/waitlist");
    if (status !== 200) return status === 401 ? null : problem(data);
    if (!data.length) return panel.replaceChildren(empty("Nobody is waiting on a date yet."));

    const dates = [];
    data.forEach((row) => {
      const group = dates.find((d) => d.date === row.date);
      (group ? group.rows : (dates.push({ date: row.date, row: row, rows: [] }), dates[dates.length - 1].rows)).push(row);
    });

    panel.replaceChildren(
      ...dates.map((group) =>
        el("article", { class: "card" }, [
          el("div", { class: "card-head" }, [
            el("div", { class: "card-title", text: `${group.row.day_label} · ${group.row.time_label}` }),
            el("span", {
              class: group.rows.length > 1 ? "pill warn" : "pill",
              text: group.rows.length === 1 ? "1 waiting" : `${group.rows.length} waiting`,
            }),
          ]),
          ...group.rows.map((w) =>
            el("div", { class: "card-meta" }, [
              contact(w.email, "mailto"),
              w.phone ? [" · ", contact(w.phone, "tel")] : null,
              el("span", { class: "small muted", text: ` · signed up ${when(w.created_at)}` }),
              w.notified_at
                ? el("span", { class: "small muted", text: ` · notified ${when(w.notified_at)}` })
                : el("span", { class: "small muted", text: " · not notified" }),
            ].flat().filter(Boolean))
          ),
        ])
      )
    );
  }

  const REFUND_PILL = {
    pending_approval: ["warn", "Waiting for you"],
    pending: ["", "Sent to Square"],
    completed: ["ok", "Refunded"],
    failed: ["bad", "Failed"],
    rejected: ["bad", "Rejected"],
  };

  async function refunds() {
    const [refundRes, repeatRes] = await Promise.all([
      guarded("/api/admin/refunds"),
      guarded("/api/admin/repeat-cancellers"),
    ]);
    if (refundRes.status !== 200) return refundRes.status === 401 ? null : problem(refundRes.data);
    const repeats = repeatRes.status === 200 ? repeatRes.data : [];
    if (!refundRes.data.length && !repeats.length) {
      return panel.replaceChildren(empty("No refunds yet."));
    }
    panel.replaceChildren(
      ...repeats.map((r) =>
        el("article", { class: "card flagged" }, [
          el("div", { class: "card-head" }, [
            el("div", { class: "card-title", text: `${r.name} has cancelled ${r.cancellations} bookings` }),
            el("span", { class: "pill warn", text: "No auto-refund" }),
          ]),
          el("div", { class: "card-meta" }, [
            contact(r.phone, "tel"), " · ", contact(r.email, "mailto"),
            ` · matched by ${r.identity} · last cancelled ${when(r.last_cancelled_at)}`,
          ]),
          el("div", { class: "card-meta small muted", text: "Their refunds wait here for you instead of sending themselves." }),
        ])
      ),
      ...refundRes.data.map((r) => refundCard(r))
    );
  }

  function refundCard(r) {
    const [tone, label] = REFUND_PILL[r.status] || ["", r.status];
    return el("article", { class: "card" + (r.status === "pending_approval" ? " flagged" : "") }, [
      el("div", { class: "card-head" }, [
        el("div", { class: "card-title", text: `${money(r.amount_cents)} · ${r.day_label} · ${r.package}` }),
        el("span", { class: "pill " + tone, text: label }),
      ]),
      el("div", { class: "card-meta" }, [
        el("b", { text: r.name }), " · ", contact(r.phone, "tel"), " · ", contact(r.email, "mailto"),
      ]),
      r.queued_reason ? el("div", { class: "body-text", text: r.queued_reason }) : null,
      r.error ? el("div", { class: "body-text", text: `Square said: ${r.error}` }) : null,
      el("div", {
        class: "card-meta small muted",
        text: `Code ${r.confirmation_code} · ${r.cancellations} cancellation${r.cancellations === 1 ? "" : "s"} from this parent · raised ${when(r.created_at)}`
          + (r.sent_at ? ` · sent ${when(r.sent_at)}` : "")
          + (r.settled_at ? ` · settled ${when(r.settled_at)}` : ""),
      }),
      r.status === "pending_approval"
        ? el("div", { class: "card-actions" }, [
            confirmButton("Approve refund", "Tap again to send to Square", async () => {
              const result = await guarded(`/api/admin/refunds/${r.id}/approve`, { method: "POST" });
              if (result.status !== 200) return problem(result.data);
              refunds();
            }),
            confirmButton("Don't refund", "Tap again to reject", async () => {
              const result = await guarded(`/api/admin/refunds/${r.id}/reject`, { method: "POST" });
              if (result.status !== 200) return problem(result.data);
              refunds();
            }),
          ])
        : null,
    ]);
  }

  function countdown(seconds) {
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  }

  async function holds() {
    const { status, data } = await guarded("/api/admin/holds");
    if (status !== 200) return status === 401 ? null : problem(data);
    if (!data.length) return panel.replaceChildren(empty("No dates are being held right now."));
    panel.replaceChildren(
      ...data.map((h) =>
        el("article", { class: "card" }, [
          el("div", { class: "card-head" }, [
            el("div", { class: "card-title", text: `${h.day_label} · ${h.time_label}` }),
            el("span", { class: "pill warn", text: `${countdown(h.seconds_remaining)} left` }),
          ]),
          el("div", { class: "card-meta" }, [
            h.package ? el("b", { text: `${h.package}${h.kids ? ` · ${h.kids} kids` : ""}` }) : el("i", { text: "No package picked yet" }),
            h.total_cents ? [" · ", el("b", { text: money(h.total_cents) })] : "",
          ].flat()),
          h.name
            ? el("div", { class: "card-meta" }, [el("b", { text: h.name }), " · ", contact(h.phone, "tel"), " · ", contact(h.email, "mailto")])
            : el("div", { class: "card-meta muted", text: "Anonymous: still on the date or extras step" }),
          el("div", {
            class: "card-meta small muted",
            text: `Held since ${when(h.created_at)} · expires ${when(h.expires_at)}`
              + (h.reached_checkout ? " · reached the deposit step" : "")
              + (h.client_ip ? ` · ${h.client_ip}` : ""),
          }),
        ])
      )
    );
  }

  // ---------- manual booking entry ----------
  // The form is built from /api/catalog, so the packages, add-ons and limits are the server's own and cannot
  // drift from what the funnel prices against. The total is quoted by /api/quote on every change rather than
  // multiplied here: the client never computes a price. The date is typed, not chosen from a list, because
  // the owner books dates the parents' grid never shows; what state that date is in comes from the server.

  let catalog = null;
  const manual = { quoting: 0, checking: 0, today: "", date: "", state: null, weekdayOk: null };

  function field(label, control, hint) {
    return el("div", { class: "field" }, [el("label", { for: control.id, text: label }), control, hint ? el("p", { class: "field-hint", text: hint }) : null]);
  }

  function input(id, attrs) {
    return el("input", Object.assign({ id, name: id }, attrs));
  }

  async function openManual() {
    const dialog = $("manual-modal");
    const form = $("manual-form");
    form.replaceChildren(el("div", { class: "spinner", role: "status", "aria-label": "Loading" }));
    if (!dialog.open) dialog.showModal();
    // Availability is read for one thing only: today's date in the venue's timezone, which is what the date
    // field's minimum is. The list of dates in it is the parents' grid and is not what the owner books from.
    const [catalogRes, availabilityRes] = await Promise.all([
      catalog ? Promise.resolve({ status: 200, data: catalog }) : api("/api/catalog"),
      api("/api/availability"),
    ]);
    if (catalogRes.status !== 200 || availabilityRes.status !== 200) {
      form.replaceChildren(el("p", { class: "notice error", text: "Couldn't load the packages and dates. Close this and try again." }));
      return;
    }
    catalog = catalogRes.data;
    manual.today = availabilityRes.data.today;
    manual.date = "";
    manual.state = null;
    manual.weekdayOk = null;
    form.replaceChildren(...manualFields());
    quoteManual();
    syncSubmit();
    form.querySelector("#m-date-m").focus();
  }

  function manualFields() {
    const pkg = el("select", { id: "m-package", name: "m-package", onchange: quoteManual },
      catalog.packages.map((p) => el("option", { value: p.key, text: `${p.label} — ${money(p.price_cents)}, up to ${p.included_kids} kids` })));
    const kids = input("m-kids", { type: "number", inputmode: "numeric", min: catalog.min_kids, max: catalog.max_kids, value: 15, required: true, onchange: quoteManual });

    return [
      el("div", { class: "field" }, [
        el("label", { for: "m-date-m", text: "Date" }),
        dateControl(),
        el("div", { class: "date-state", id: "m-date-state", role: "status" }),
        el("div", { class: "date-warn", id: "m-date-warn", role: "status", hidden: true }),
        el("p", { class: "field-hint", text: "Any date from today on. The venue's party slots are weekends, at 10:00 AM." }),
      ]),
      field("Package", pkg),
      field("Kids", kids, `Over the included headcount is ${money(catalog.additional_kid_cents)} each.`),
      el("fieldset", { class: "manual-addons" }, [
        el("legend", { text: "Add-ons" }),
        ...catalog.addons.map(addonControl),
      ]),
      el("div", { class: "form-grid" }, [
        field("Kid's name", input("m-kid-name", { type: "text", maxlength: 80, autocomplete: "off" })),
        field("Kid's age", input("m-kid-age", { type: "number", inputmode: "numeric", min: 1, max: 18 })),
        field("Parent first name", input("m-first", { type: "text", maxlength: 80, required: true, autocomplete: "off" })),
        field("Parent last name", input("m-last", { type: "text", maxlength: 80, required: true, autocomplete: "off" })),
        field("Phone", input("m-phone", { type: "tel", required: true, autocomplete: "off" })),
        field("Email", input("m-email", { type: "email", required: true, autocomplete: "off" })),
      ]),
      field("Notes", el("textarea", { id: "m-notes", name: "m-notes", rows: 2, maxlength: 500, placeholder: "Theme, favorite game, anything the host should know" })),
      el("fieldset", { class: "manual-deposit" }, [
        el("legend", { text: `Deposit (${money(catalog.deposit_cents)})` }),
        depositChoice("collected", "Collected in person", true),
        depositChoice("waived", "Waived — nothing taken", false),
      ]),
      el("div", { class: "manual-total", id: "m-total" }),
      el("div", { id: "m-error", role: "alert" }),
      el("div", { class: "manual-actions" }, [
        el("button", { type: "button", class: "btn btn-ghost", text: "Cancel", onclick: closeManual }),
        el("button", { type: "submit", class: "btn btn-primary", id: "m-submit", text: "Add booking", disabled: true }),
      ]),
    ];
  }

  // ---------- the date field ----------
  // Three numeric boxes plus a calendar button, the same control the funnel's birthday entry uses: a date
  // the owner can type and one he can pick, with Backspace stepping back through the boxes. The old control
  // here was a dropdown of the next eight weekends, which could not express "the Saturday in March".

  const SEG_LENGTHS = [2, 2, 4];
  // What each state the server can report reads as, and whether it can be booked over.
  const DATE_WORDS = {
    open: { tone: "ok", word: "open" },
    none: { tone: "ok", word: "open" },
    booked: { tone: "bad", word: "booked" },
    held: { tone: "bad", word: "on hold" },
    blocked: { tone: "bad", word: "blocked" },
    past: { tone: "bad", word: "in the past" },
  };

  function realDate(value) {
    const when = new Date(value + "T12:00:00Z");
    return !Number.isNaN(when.getTime()) && when.toISOString().slice(0, 10) === value;
  }

  function dateBoxes() {
    return [...document.querySelectorAll("#manual-form .date-seg .seg")];
  }

  function writeDateBoxes(value) {
    const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || "");
    const [m, d, y] = dateBoxes();
    if (!m) return;
    m.value = parts ? parts[2] : "";
    d.value = parts ? parts[3] : "";
    y.value = parts ? parts[1] : "";
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
    const boxes = [box("seg-m", "mm", "Party month"), box("seg-d", "dd", "Party day"), box("seg-y", "yyyy", "Party year")];
    boxes[0].id = "m-date-m";

    boxes.forEach((input, i) => {
      input.addEventListener("input", () => {
        input.value = input.value.replace(/\D/g, "").slice(0, SEG_LENGTHS[i]);
        // A first digit that can only start a two-digit month or day pads itself and moves on.
        if (i === 0 && input.value.length === 1 && Number(input.value) > 1) input.value = "0" + input.value;
        if (i === 1 && input.value.length === 1 && Number(input.value) > 3) input.value = "0" + input.value;
        if (input.value.length === SEG_LENGTHS[i] && i < 2) boxes[i + 1].focus();
        commitDateBoxes();
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
      id: "m-date",
      type: "date",
      class: "seg-native",
      tabindex: "-1",
      "aria-hidden": "true",
      min: manual.today || null,
      onchange: (event) => {
        if (event.target.value) applyDate(event.target.value, true);
      },
    });
    // The one thing that opens the calendar. Everywhere else in the control is for typing.
    const calendar = el("button", {
      type: "button",
      class: "seg-cal",
      "aria-label": "Pick from calendar",
      onclick: () => {
        boxes.forEach((b) => b.blur());
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

    const wrap = el("div", { class: "date-seg manual-date", role: "group", "aria-label": "Party date" }, [
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

  // Called on every keystroke. Only a complete real date is sent to be checked, and a half-typed one puts
  // the field back to having no date: the submit books the date this has committed, so leaving the old one
  // committed under boxes that now read something else is how the wrong Saturday gets booked.
  function commitDateBoxes() {
    const [mm, dd, yy] = dateBoxes().map((b) => b.value);
    if (!mm && !dd && !yy) return applyDate("", false);
    if (mm.length !== 2 || dd.length !== 2 || yy.length !== 4) {
      applyDate("", false);
      return showDateState({ tone: "", text: "Finish the date." });
    }
    const value = `${yy}-${mm}-${dd}`;
    if (!realDate(value)) return showDateState({ tone: "bad", text: "That isn't a real date. Check the month and day." });
    applyDate(value, false);
  }

  function applyDate(value, fromPicker) {
    if (fromPicker) writeDateBoxes(value);
    const native = $("m-date");
    if (native) native.value = value || "";
    if (value === manual.date && (manual.state || !value)) return;
    manual.date = value;
    manual.state = null;
    // A new date is a new decision: a weekday confirmed a moment ago does not carry over to this one.
    manual.weekdayOk = null;
    showWeekday(null);
    if (!value) {
      showDateState(null);
      syncSubmit();
      return;
    }
    checkDate(value);
  }

  // The server is asked what every date is, and answers are sequenced so a slow reply cannot land after a
  // newer one and describe a date the owner has already changed.
  async function checkDate(value) {
    const ticket = ++manual.checking;
    showDateState({ tone: "", text: "Checking that date…" });
    syncSubmit();
    const { status, data } = await guarded(`/api/admin/slots/status?date=${encodeURIComponent(value)}`);
    if (ticket !== manual.checking || value !== manual.date) return;
    if (status !== 200) {
      showDateState({ tone: "bad", text: detailText(data, "Couldn't check that date.") });
      syncSubmit();
      return;
    }
    manual.state = data;
    const read = DATE_WORDS[data.status] || { tone: "bad", word: data.status };
    showDateState({
      tone: read.tone,
      check: read.tone === "ok",
      text: `${data.day_label} · ${data.time_label} · ${read.word}${data.confirmation_code ? ` (${data.confirmation_code})` : ""}`,
    });
    showWeekday(data.bookable && data.weekday ? data.weekday_label : null);
    syncSubmit();
  }

  function showDateState(view) {
    const node = $("m-date-state");
    if (!node) return;
    node.className = `date-state${view && view.tone ? " " + view.tone : ""}`;
    paint(node, view ? [view.check ? el("span", { class: "date-check", "aria-hidden": "true", text: "✓" }) : null, view.text] : []);
  }

  function showWeekday(label) {
    const node = $("m-date-warn");
    if (!node) return;
    node.hidden = !label;
    paint(node, label ? [`That's a ${label}, not a weekend. Check the date.`] : []);
  }

  // Nothing is submittable until the server has said the date can be booked. The button is the only guard
  // the owner sees; the real ones are the unique indexes the submit runs into.
  function syncSubmit() {
    const submit = $("m-submit");
    if (submit) submit.disabled = !(manual.date && manual.state && manual.state.bookable);
  }

  function addonControl(addon) {
    const id = `m-addon-${addon.key}`;
    const control = addon.has_quantity
      ? input(id, { type: "number", inputmode: "numeric", min: 0, max: catalog.max_pack_quantity, value: 0, class: "addon-qty", onchange: quoteManual })
      : input(id, { type: "checkbox", class: "addon-check", onchange: quoteManual });
    control.dataset.addon = addon.key;
    return el("label", { class: "manual-addon", for: id }, [
      control,
      el("span", { class: "manual-addon-name", text: addon.label }),
      el("span", { class: "manual-addon-price", text: `+${money(addon.price_cents)}` }),
    ]);
  }

  function depositChoice(value, label, checked) {
    const id = `m-deposit-${value}`;
    return el("label", { class: "manual-radio", for: id }, [
      el("input", { type: "radio", id, name: "m-deposit", value, checked }),
      el("span", { text: label }),
    ]);
  }

  function readAddons() {
    const addons = {};
    document.querySelectorAll("#manual-form [data-addon]").forEach((node) => {
      const quantity = node.type === "checkbox" ? (node.checked ? 1 : 0) : parseInt(node.value, 10) || 0;
      if (quantity > 0) addons[node.dataset.addon] = quantity;
    });
    return addons;
  }

  function manualPayload() {
    const age = $("m-kid-age").value.trim();
    return {
      date: manual.date,
      package: $("m-package").value,
      kids: parseInt($("m-kids").value, 10),
      addons: readAddons(),
      first_name: $("m-first").value.trim(),
      last_name: $("m-last").value.trim(),
      phone: $("m-phone").value.trim(),
      email: $("m-email").value.trim(),
      kid_name: $("m-kid-name").value.trim() || null,
      kid_age: age === "" ? null : parseInt(age, 10),
      notes: $("m-notes").value.trim() || null,
      deposit_collected: document.querySelector("input[name='m-deposit']:checked").value === "collected",
    };
  }

  // Every change re-quotes on the server. Answers are sequenced so a slow one cannot land after a newer
  // one and show a price for a party the owner has already changed.
  async function quoteManual() {
    const total = $("m-total");
    if (!total) return;
    const ticket = ++manual.quoting;
    const { status, data } = await api("/api/quote", {
      method: "POST",
      body: { package: $("m-package").value, kids: parseInt($("m-kids").value, 10) || 0, addons: readAddons() },
    });
    if (ticket !== manual.quoting) return;
    if (status !== 200) {
      total.replaceChildren(el("span", { class: "manual-total-bad", text: detailText(data) }));
      return;
    }
    const collected = document.querySelector("input[name='m-deposit']:checked").value === "collected";
    const paid = collected ? data.deposit_cents : 0;
    total.replaceChildren(
      el("span", { class: "manual-total-sum" }, ["Total ", el("b", { text: money(data.total_cents) })]),
      el("span", { class: "manual-total-due", text: `${money(data.total_cents - paid)} due at the party` })
    );
  }

  function closeManual() {
    const dialog = $("manual-modal");
    if (dialog.open) dialog.close();
  }

  $("manual-modal-close").addEventListener("click", closeManual);
  $("manual-form").addEventListener("change", (event) => {
    if (event.target.name === "m-deposit") quoteManual();
  });
  $("manual-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = $("m-submit");
    $("m-error").replaceChildren();
    // A weekday is allowed and is also far more likely a mistyped date than an intention, so it costs one
    // more press. The first one says what the date is; the second books it.
    if (manual.state && manual.state.weekday && manual.weekdayOk !== manual.date) {
      manual.weekdayOk = manual.date;
      $("m-error").replaceChildren(
        el("p", {
          class: "notice warn",
          text: `${manual.state.day_label} is a ${manual.state.weekday_label}, not a weekend. Press "Add booking" again to book it anyway.`,
        })
      );
      return;
    }
    submit.disabled = true;
    const { status, data } = await guarded("/api/admin/bookings", { method: "POST", body: manualPayload() });
    if (status === 201) {
      closeManual();
      show("bookings");
      counts();
      return;
    }
    if (status === 401) return closeManual();
    // The date may well be why this failed, so what it is now is re-read rather than left as it was.
    $("m-error").replaceChildren(el("p", { class: "notice error", text: detailText(data, "Couldn't add the booking.") }));
    if (manual.date) checkDate(manual.date);
    else syncSubmit();
  });
  const TABS = { bookings, holds, refunds, requests, flags, dates, notifications, leads, waitlist };

  async function counts() {
    const [flagRes, noteRes, reqRes, refundRes, holdRes] = await Promise.all([
      api("/api/admin/flags"),
      api("/api/admin/notifications"),
      api("/api/admin/requests"),
      api("/api/admin/refunds"),
      api("/api/admin/holds"),
    ]);
    const openFlags = flagRes.status === 200 ? flagRes.data.filter((f) => !f.resolved_at).length : 0;
    const failed = noteRes.status === 200 ? noteRes.data.filter((n) => n.status === "failed").length : 0;
    const fresh = reqRes.status === 200 ? reqRes.data.filter((r) => r.status === "new").length : 0;
    // Refunds waiting on the owner and refunds Square refused both need them to do something.
    const waiting = refundRes.status === 200
      ? refundRes.data.filter((r) => r.status === "pending_approval" || r.status === "failed").length
      : 0;
    $("flag-count").textContent = openFlags ? ` ${openFlags}` : "";
    $("failed-count").textContent = failed ? ` ${failed} failed` : "";
    $("request-count").textContent = fresh ? ` ${fresh}` : "";
    $("refund-count").textContent = waiting ? ` ${waiting}` : "";
    $("hold-count").textContent = holdRes.status === 200 && holdRes.data.length ? ` ${holdRes.data.length}` : "";
  }

  function show(tab) {
    current = tab;
    document.querySelectorAll(".tab").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
    panel.replaceChildren(el("div", { class: "spinner", role: "status", "aria-label": "Loading" }));
    TABS[tab]();
  }

  function showLogin() {
    $("dashboard").hidden = true;
    $("logout").hidden = true;
    $("login").hidden = false;
    $("password").focus();
  }

  function showDashboard() {
    $("login").hidden = true;
    $("dashboard").hidden = false;
    $("logout").hidden = false;
    show(current);
    counts();
  }

  $("login").addEventListener("submit", async (event) => {
    event.preventDefault();
    $("login-error").replaceChildren();
    const { status, data } = await api("/api/admin/login", { method: "POST", body: { password: $("password").value } });
    if (status === 200) {
      $("password").value = "";
      showDashboard();
    } else {
      $("login-error").replaceChildren(el("p", { class: "notice error", text: detailText(data, "Login failed.") }));
    }
  });
  $("logout").addEventListener("click", async () => {
    await api("/api/admin/logout", { method: "POST" });
    showLogin();
  });
  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => show(b.dataset.tab)));
  setInterval(() => {
    if (!$("dashboard").hidden && document.visibilityState === "visible" && current !== "dates") {
      TABS[current]();
      counts();
    }
  }, 15000);

  api("/api/admin/session").then(({ status }) => (status === 200 ? showDashboard() : showLogin()));
})();
