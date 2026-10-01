(function () {
  const { api, detailText, money, el } = window.Encore;
  const $ = (id) => document.getElementById(id);
  const panel = $("panel");
  let current = "bookings";

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

  function confirmButton(label, confirmLabel, action) {
    const button = el("button", { type: "button", class: "btn btn-danger btn-sm", text: label });
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

  async function bookings() {
    const { status, data } = await guarded("/api/admin/bookings");
    if (status !== 200) return status === 401 ? null : problem(data);
    if (!data.length) return panel.replaceChildren(empty("No bookings yet."));
    panel.replaceChildren(
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
          ]),
          el("div", { class: "card-meta" }, [
            "Total ", el("b", { text: money(b.total_cents) }), " · deposit ", el("b", { text: money(b.paid_amount_cents) }),
            " · balance due ", el("b", { text: money(b.balance_cents) }),
          ]),
          el("div", { class: "card-meta" }, [
            el("b", { text: b.name }), " · ", el("a", { href: `tel:${b.phone}`, text: b.phone }), " · ", el("a", { href: `mailto:${b.email}`, text: b.email }),
          ]),
          b.notes ? el("div", { class: "body-text", text: `Notes: ${b.notes}` }) : null,
          el("div", { class: "card-meta small muted", text: `Code ${b.confirmation_code} · booked ${when(b.booked_at)}` + (b.cancelled_at ? ` · cancelled ${when(b.cancelled_at)}${b.cancel_reason ? ` (${b.cancel_reason})` : ""}` : "") }),
          el("div", { class: "card-actions" }, [
            el("span", { class: "pill" + (b.full_refund ? " ok" : " warn"), text: b.full_refund ? "Full refund if cancelled" : "Inside 7 days: no automatic full refund" }),
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
            el("b", { text: r.name }), " · ", el("a", { href: `tel:${r.phone}`, text: r.phone }), " · ", el("a", { href: `mailto:${r.email}`, text: r.email }),
          ]),
          r.notes ? el("div", { class: "body-text", text: `Notes: ${r.notes}` }) : null,
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
            ? el("div", { class: "card-meta" }, [el("b", { text: f.name }), " · ", el("a", { href: `tel:${f.phone}`, text: f.phone }), ` · ${f.email}`])
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

  async function dates() {
    const { status, data } = await api("/api/availability");
    if (status !== 200) return problem(data);
    const slotDays = data.days.filter((s) => s.slot_id !== null);
    panel.replaceChildren(
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
    if (!data.waitlist.length && !data.abandoned.length) return panel.replaceChildren(empty("No waitlist signups or abandoned checkouts yet."));
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
                el("a", { href: `mailto:${a.email}`, text: a.email }),
                a.phone ? [" · ", el("a", { href: `tel:${a.phone}`, text: a.phone })] : null,
              ].flat()),
              el("div", { class: "card-meta small muted", text: `${a.package ? `${a.package}, ${a.kids} kids · ` : "Left before choosing a package · "}started ${when(a.created_at)}` }),
            ])
          )
        : [empty("None.")]),
      el("div", { class: "section-label", text: "Waitlist" }),
      ...(data.waitlist.length
        ? data.waitlist.map((w) =>
            el("article", { class: "card" }, [
              el("div", { class: "card-meta" }, [el("a", { href: `mailto:${w.email}`, text: w.email }), ` · signed up ${when(w.created_at)}`]),
            ])
          )
        : [empty("None.")])
    );
  }

  const TABS = { bookings, requests, flags, dates, notifications, leads };

  async function counts() {
    const [flagRes, noteRes, reqRes] = await Promise.all([
      api("/api/admin/flags"),
      api("/api/admin/notifications"),
      api("/api/admin/requests"),
    ]);
    const openFlags = flagRes.status === 200 ? flagRes.data.filter((f) => !f.resolved_at).length : 0;
    const failed = noteRes.status === 200 ? noteRes.data.filter((n) => n.status === "failed").length : 0;
    const fresh = reqRes.status === 200 ? reqRes.data.filter((r) => r.status === "new").length : 0;
    $("flag-count").textContent = openFlags ? ` ${openFlags}` : "";
    $("failed-count").textContent = failed ? ` ${failed} failed` : "";
    $("request-count").textContent = fresh ? ` ${fresh}` : "";
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
