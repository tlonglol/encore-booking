(function () {
  const { api, detailText, money, clock, el } = window.Encore;
  const Funnel = window.EncoreFunnel;

  const state = Funnel.require((s) => s.package);
  if (!state) return;
  state.addons = state.addons || {};
  state.contact = state.contact || {};

  const $ = (id) => document.getElementById(id);
  const requesting = state.mode === "request";
  let catalog = null;
  let quote = null;
  let deadline = null; // performance.now() ms when the hold lapses, from the server's seconds_remaining
  let timerHandle = null;
  let paying = false;

  function pkg() {
    return catalog.packages.find((p) => p.key === state.package);
  }

  function kidsValue() {
    return state.kids || pkg().included_kids;
  }

  // ---------- Countdown (hold mode): driven by the hold row, never faked ----------
  function tick() {
    const remaining = (deadline - performance.now()) / 1000;
    const timer = $("hold-timer");
    if (timer) {
      timer.textContent = clock(Math.ceil(remaining));
      timer.classList.toggle("low", remaining < 120);
    }
    if (remaining <= 0) {
      clearInterval(timerHandle);
      if (!paying) showReleased("expired");
    }
  }

  function showReleased(status) {
    const copy = {
      expired: "Your 15-minute hold ran out, so the date was released. Nothing was charged.",
      cancelled: "This hold was cancelled, and nothing was charged.",
      booked: "This date is already booked. If that was you, check your confirmation text.",
    };
    $("pay-panel").hidden = true;
    $("hold-line").hidden = true;
    const released = $("released");
    released.hidden = false;
    released.replaceChildren(
      el("div", { class: "notice error", style: "margin-top:1.5rem" }, [
        el("div", { text: copy[status] || copy.expired }),
        el("div", { class: "alts" }, [el("a", { class: "chip", href: "index.html", text: "Pick a date" })]),
      ])
    );
    Funnel.barFrom(Math.min(...catalog.packages.map((p) => p.price_cents)));
  }

  // ---------- Summary ----------
  function renderSummary(fresh) {
    quote = fresh;
    $("summary-total").textContent = money(fresh.total_cents);
    $("summary-lines").replaceChildren(
      ...fresh.line_items.map((item) =>
        el("li", {}, [
          el("span", { text: item.quantity > 1 ? `${item.label} × ${item.quantity}` : item.label }),
          item.included ? el("span", { class: "inc", text: "Included" }) : el("span", { text: money(item.amount_cents) }),
        ])
      )
    );
    $("deposit-math").replaceChildren(
      `${money(fresh.total_cents)} total, `,
      el("b", { text: "$100 today" }),
      `, ${money(fresh.balance_cents)} day of party`
    );
  }

  function requote() {
    Funnel.save(state);
    Funnel.fetchQuote(state.package, kidsValue(), state.addons, renderSummary);
  }

  // ---------- Form ----------
  function payError(message) {
    $("pay-error").replaceChildren(message ? el("p", { class: "notice error", text: message }) : "");
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
    Funnel.save(state);
  }

  function restoreForm() {
    const saved = state.contact;
    for (const [id, key] of [["email", "email"], ["parent_name", "parentName"], ["phone", "phone"], ["kid_name", "kidName"], ["kid_age", "kidAge"]]) {
      if (saved[key]) $(id).value = saved[key];
    }
    $("kids").value = state.kids ? String(state.kids) : "";
    $("kids").placeholder = `up to ${pkg().included_kids} included`;
  }

  function splitName(full) {
    const parts = full.split(/\s+/).filter(Boolean);
    return { first: parts[0] || "", last: parts.slice(1).join(" ") };
  }

  function validateForm() {
    const form = readForm();
    const checks = [
      ["email", form.email.includes("@"), "Enter your email."],
      ["parent_name", form.parentName.length > 0, "Enter your name."],
      ["phone", form.phone.length >= 7, "Enter your phone number."],
      ["kid_name", form.kidName.length > 0, "Enter the birthday kid's name."],
      ["kid_age", /^\d{1,2}$/.test(form.kidAge) && Number(form.kidAge) > 0, "Enter the kid's age."],
      ["kids", /^\d{1,2}$/.test(form.kids) && Number(form.kids) >= catalog.min_kids && Number(form.kids) <= catalog.max_kids, `Estimated headcount: ${catalog.min_kids} to ${catalog.max_kids} kids.`],
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

  // ---------- Pay / Send ----------
  async function pay() {
    payError(null);
    const form = validateForm();
    if (!form) return;
    const button = $("pay-button");
    button.disabled = true;
    paying = true;
    button.textContent = requesting ? "Sending…" : "Starting secure checkout…";
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
    const response = requesting
      ? await api("/api/requests", { method: "POST", body: { date: state.date, preferred_time: $("preferred_time").value, ...body } })
      : await api(`/api/holds/${state.holdId}/checkout`, { method: "POST", body: body });
    const { status, data } = response;
    if (!requesting && status === 200) {
      window.location.href = data.checkout_url;
      return;
    }
    if (requesting && status === 201) {
      Funnel.clear();
      $("pay-panel").hidden = true;
      $("hold-line").hidden = true;
      $("request-done").hidden = false;
      $("request-summary").replaceChildren(
        el("b", { text: `${data.package_label} · ${data.day_label} at ${data.time_label}` }),
        ` · ${data.kids} kids · `,
        el("b", { text: money(data.total_cents) })
      );
      return;
    }
    paying = false;
    button.disabled = false;
    button.textContent = requesting ? "Send request" : "Pay $100 & lock it in";
    if (status === 409 && data && data.status) {
      showReleased(data.status);
      return;
    }
    if (status === 429 && data && data.expires_at) {
      const until = new Date(data.expires_at).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
      payError(`${detailText(data)} It frees up around ${until}.`);
      return;
    }
    payError(detailText(data));
  }

  async function saveEmailOnBlur() {
    rememberForm();
    const email = $("email").value.trim();
    if (!requesting && state.holdId && email.includes("@")) {
      api(`/api/holds/${state.holdId}/email`, { method: "PATCH", body: { email: email } });
    }
  }

  // ---------- Boot ----------
  async function boot() {
    Funnel.renderSteps(3);
    Funnel.renderHoldLine(state, { youAre: true, timer: !requesting });
    Funnel.barInit();
    catalog = await Funnel.catalog();
    if (!catalog || !pkg()) {
      payError("Couldn't load your party. Go back and try again.");
      return;
    }
    $("time-field").hidden = !requesting;
    $("risk-line").hidden = requesting;
    $("pay-fine").hidden = requesting;
    $("pay-button").textContent = requesting ? "Send request" : "Pay $100 & lock it in";
    $("pay-title").textContent = requesting ? "Send your request" : "Lock it in";
    restoreForm();

    $("summary-toggle").addEventListener("click", () => {
      const lines = $("summary-lines");
      lines.hidden = !lines.hidden;
      $("summary-toggle").setAttribute("aria-expanded", String(!lines.hidden));
      $("summary-toggle").textContent = lines.hidden ? "Details" : "Hide";
    });
    $("pay-button").addEventListener("click", pay);
    $("pay-form").addEventListener("submit", (event) => event.preventDefault());
    $("email").addEventListener("blur", saveEmailOnBlur);
    for (const id of ["parent_name", "phone", "kid_name", "kid_age"]) $(id).addEventListener("blur", rememberForm);
    $("kids").addEventListener("input", () => {
      const raw = $("kids").value.trim();
      if (/^\d{1,2}$/.test(raw)) {
        state.kids = Math.min(catalog.max_kids, Math.max(catalog.min_kids, Number(raw)));
        requote();
      }
    });

    if (!requesting) {
      // The real hold row drives the timer; a dead hold shows the released screen instead of a form.
      const { status, data } = await api(`/api/holds/${state.holdId}`);
      if (status !== 200) {
        showReleased("expired");
        return;
      }
      if (data.status !== "pending_payment") {
        showReleased(data.status);
        return;
      }
      deadline = performance.now() + data.seconds_remaining * 1000;
      tick();
      timerHandle = setInterval(tick, 250);
    }
    requote();
  }

  boot();
})();
