(function () {
  const { api, el, money, store, recall } = window.Encore;
  const KEY = "encore.funnel";

  const STEPS = [
    ["Date", "index.html"],
    ["Package", "package.html"],
    ["Extras", "addons.html"],
    ["Confirm", "checkout.html"],
  ];

  const COPY = {
    packages: {
      standard: ["4 games pre-loaded", "Private full-venue buyout", "Switch, PS5 & PC stations", "Dedicated staff host", "Birthday banner on every screen"],
      plus: ["6 games pre-loaded", "Everything in Standard", "Gamer decorations + balloon tower", "Party favor bags"],
      premium: ["Full catalog, every station", "Everything in Plus", "VR gaming & DDR pad, full party", "Premium favor bags + Encore item"],
    },
    addons: {
      tournament: ["🏆", "Bracket, host MC, YouTube livestream, printed bracket, trophy for the birthday champion."],
      highlight_reel: ["🎬", "Edited highlight reel of the tournament. Needs Tournament + Livestream."],
      extra_hour: ["⏱️", "Extend the party. Venue, host, and games stay yours."],
      vr: ["🥽", "Beat Saber, Superhot & more, full party duration."],
      ddr: ["🕹️", "DDR / Stepmania pad for the whole group, full party."],
      decorations: ["🎈", "Balloon tower, tabletop décor, themed setup."],
      pizza_pack: ["🍕", "2 large pizzas + drinks. Feeds about 10 kids."],
      drinks_pack: ["🥤", "Soda + juice boxes for the whole group."],
    },
  };

  function load() {
    return recall(KEY);
  }

  function save(state) {
    store(KEY, state);
  }

  function clear() {
    store(KEY, null);
  }

  // Guard for pages 2-4: no funnel state means nothing is held; go pick a date.
  function require(check) {
    const state = load();
    if (!state || !state.date || (check && !check(state))) {
      window.location.replace("index.html");
      return null;
    }
    return state;
  }

  function shortDate(dateStr) {
    return new Date(dateStr + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" });
  }

  function renderSteps(currentIndex) {
    const root = document.getElementById("steps");
    if (!root) return;
    root.replaceChildren(
      ...STEPS.map(([label, href], index) => {
        if (index < currentIndex) return el("li", { class: "done" }, [el("a", { href: href, text: label })]);
        return el("li", { class: index === currentIndex ? "current" : "" , text: label });
      })
    );
  }

  // The persistent funnel header: "Holding Sat, Oct 17 at 10:00 AM". Returns the timer node (checkout only).
  function renderHoldLine(state, options) {
    const root = document.getElementById("hold-line");
    if (!root) return null;
    const opts = options || {};
    const requesting = state.mode === "request";
    const what = requesting ? "Requesting" : opts.youAre ? "You're holding" : "Holding";
    const when = state.timeLabel ? `${state.dayLabel} at ${state.timeLabel}` : state.dayLabel;
    const timer = opts.timer && !requesting ? el("span", { class: "timer", id: "hold-timer", text: "–:––" }) : null;
    const children = [el("span", { class: "what", text: what }), el("span", { class: "when", text: when })];
    if (timer) children.push(timer);
    root.replaceChildren(...children);
    return timer;
  }

  async function catalog() {
    const { status, data } = await api("/api/catalog");
    return status === 200 ? data : null;
  }

  // ---------- Sticky bar ----------
  function barInit() {
    const toggle = document.getElementById("quote-toggle");
    const lines = document.getElementById("quote-lines");
    if (!toggle) return;
    toggle.addEventListener("click", () => {
      lines.hidden = !lines.hidden;
      toggle.setAttribute("aria-expanded", String(!lines.hidden));
      toggle.textContent = lines.hidden ? "Details" : "Hide";
    });
  }

  function barFrom(minCents, caption) {
    document.getElementById("quote-total").textContent = `from ${money(minCents)}`;
    document.getElementById("quote-split").textContent = caption || "Private venue party";
    document.getElementById("quote-toggle").hidden = true;
  }

  function barStale() {
    document.getElementById("quote-total").classList.add("stale");
  }

  function barQuote(quote) {
    const total = document.getElementById("quote-total");
    const split = document.getElementById("quote-split");
    const lines = document.getElementById("quote-lines");
    total.classList.remove("stale");
    total.textContent = money(quote.total_cents);
    split.replaceChildren(
      el("b", { text: `${money(quote.deposit_cents)} today` }),
      el("br"),
      `${money(quote.balance_cents)} day of party`
    );
    document.getElementById("quote-toggle").hidden = false;
    lines.replaceChildren(
      ...quote.line_items.map((item) =>
        el("li", {}, [
          el("span", { text: item.quantity > 1 ? `${item.label} × ${item.quantity}` : item.label }),
          item.included ? el("span", { class: "inc", text: "Included" }) : el("span", { text: money(item.amount_cents) }),
        ])
      ),
      el("li", {}, [el("span", { text: "Deposit due now" }), el("span", { text: money(quote.deposit_cents) })]),
      el("li", {}, [el("span", { text: "Balance due day of party" }), el("span", { text: money(quote.balance_cents) })])
    );
  }

  let quoteSeq = 0;
  async function fetchQuote(packageKey, kids, addons, onQuote, onError) {
    const seq = ++quoteSeq;
    barStale();
    const { status, data } = await api("/api/quote", { method: "POST", body: { package: packageKey, kids: kids, addons: addons } });
    if (seq !== quoteSeq) return;
    if (status !== 200) {
      if (onError) onError(data);
      return;
    }
    barQuote(data);
    if (onQuote) onQuote(data);
  }

  window.EncoreFunnel = {
    COPY: COPY,
    load: load,
    save: save,
    clear: clear,
    require: require,
    shortDate: shortDate,
    renderSteps: renderSteps,
    renderHoldLine: renderHoldLine,
    catalog: catalog,
    barInit: barInit,
    barFrom: barFrom,
    barStale: barStale,
    barQuote: barQuote,
    fetchQuote: fetchQuote,
  };
})();
