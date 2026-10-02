// The hold pill: one floating element that shows a live hold wherever the parent is on the site.
// The booking funnel owns the clock and drives this as a renderer; every other page gets the standalone
// watcher at the bottom of this file, which reads the same draft and asks the server for the time left.
(function () {
  const { api, clock, keep, kept } = window.Encore;
  const HOLD_KEY = "encore.hold";
  const LOW_SECONDS = 120;
  const EXPIRED_NOTICE_MS = 30000;
  const CLOCK_ICON =
    '<svg class="pill-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">' +
    '<circle cx="8" cy="8" r="6.25" fill="none" stroke="currentColor" stroke-width="1.4"/>' +
    '<path d="M8 4.4V8l2.4 1.6" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>' +
    "</svg>";

  let node = null;

  function build() {
    if (node) return node;
    node = document.createElement("div");
    node.className = "hold-pill";
    node.id = "hold-pill";
    node.hidden = true;
    node.innerHTML =
      CLOCK_ICON +
      '<span class="pill-clock" id="pill-clock" role="timer"></span>' +
      '<span class="pill-when" id="pill-when"></span>' +
      '<span class="pill-when-short" id="pill-when-short"></span>' +
      '<button class="pill-action" id="pill-action" type="button">' +
      '<span class="pill-action-long" id="pill-action-long"></span>' +
      '<span class="pill-action-short" id="pill-action-short"></span>' +
      "</button>";
    document.body.appendChild(node);
    placeBelowHeader();
    window.addEventListener("resize", placeBelowHeader);
    return node;
  }

  // Both surfaces have their own sticky header of their own height, and neither is touched here: the pill
  // measures whichever one this page has and floats under it.
  function placeBelowHeader() {
    if (!node) return;
    const header = document.querySelector(".banner, .nav");
    // The header's bottom edge, not its height: the test-mode ribbon sits above it and shifts it down.
    node.style.top = `${(header ? header.getBoundingClientRect().bottom : 0) + 10}px`;
  }

  // seconds null means "a hold, but the server has not said how long is left yet".
  function render({ seconds, dayLabel, timeLabel, shortLabel, actionText, actionShort, onAction, notice }) {
    build();
    const clockNode = document.getElementById("pill-clock");
    const text = notice || (seconds === null ? "" : clock(Math.ceil(seconds)));
    if (clockNode.textContent !== text) clockNode.textContent = text;
    clockNode.classList.toggle("low", !notice && seconds !== null && seconds < LOW_SECONDS);
    clockNode.setAttribute("aria-label", notice || (seconds === null ? "On hold" : `${text} left on your hold`));
    document.getElementById("pill-when").textContent = timeLabel ? `${dayLabel} · ${timeLabel}` : dayLabel;
    document.getElementById("pill-when-short").textContent = shortLabel || dayLabel;
    const action = document.getElementById("pill-action");
    action.hidden = !actionText;
    document.getElementById("pill-action-long").textContent = actionText || "";
    document.getElementById("pill-action-short").textContent = actionShort || actionText || "";
    action.onclick = onAction || null;
    node.hidden = false;
    document.body.classList.add("has-pill");
    placeBelowHeader();
  }

  function hide() {
    if (node) node.hidden = true;
    document.body.classList.remove("has-pill");
  }

  window.EncoreHoldPill = { render: render, hide: hide, LOW_SECONDS: LOW_SECONDS };

  // ---------- standalone watcher, for every page that is not the funnel ----------
  // The funnel drives the pill itself, from the clock it already owns.
  if (document.body && document.body.classList.contains("funnel-body")) return;

  // Browser-wide, so the pill shows in a tab that never saw the funnel.
  const draft = kept(HOLD_KEY);
  if (!draft || draft.mode !== "hold" || !draft.holdId || !draft.date) return;

  const step = draft.step || "info";
  const watch = { deadline: null, timer: null, expiredAt: null };

  function left() {
    return watch.deadline === null ? null : (watch.deadline - performance.now()) / 1000;
  }

  function resume() {
    window.location.href = `/book/${step}`;
  }

  function paint() {
    render({
      seconds: left(),
      dayLabel: draft.dayLabel,
      timeLabel: draft.timeLabel,
      shortLabel: shortLabel(draft.dayLabel),
      actionText: "Resume booking →",
      actionShort: "Resume",
      onAction: resume,
    });
  }

  // "Sat, Oct 25" is the phone's "Oct 25".
  function shortLabel(label) {
    return (label || "").replace(/^[A-Za-z]+,\s*/, "");
  }

  // A hold that is finished, paid for or gone stops being this browser's business.
  function stop() {
    clearInterval(watch.timer);
    watch.timer = null;
    keep(HOLD_KEY, null);
    hide();
  }

  // Nothing to interrupt on a page the parent is only browsing: offer the way back for half a minute,
  // but only while the date is actually still free, then take the pill away.
  async function expired() {
    clearInterval(watch.timer);
    watch.timer = null;
    // This clock can reach zero a moment before the server's does, and the server is the one that decides.
    // While it still calls the hold live, the slot reads as taken by this very hold, so wait it out.
    const hold = await api(`/api/holds/${draft.holdId}`);
    if (hold.status === 200 && hold.data.status === "pending_payment") {
      const wait = Math.max(hold.data.seconds_remaining, 1) * 1000;
      watch.deadline = performance.now() + wait;
      setTimeout(expired, wait);
      return paint();
    }
    const { status, data } = await api("/api/availability");
    const day = status === 200 ? data.days.find((entry) => entry.date === draft.date) : null;
    keep(HOLD_KEY, null);
    if (!day || day.status !== "open") return hide();
    render({
      seconds: null,
      dayLabel: draft.dayLabel,
      timeLabel: draft.timeLabel,
      shortLabel: shortLabel(draft.dayLabel),
      actionText: "Re-hold",
      onAction: resume,
      notice: "Hold expired",
    });
    setTimeout(hide, EXPIRED_NOTICE_MS);
  }

  function tick() {
    const remaining = left();
    if (remaining !== null && remaining <= 0) return expired();
    paint();
  }

  async function sync() {
    const { status, data } = await api(`/api/holds/${draft.holdId}`);
    if (status !== 200 || data.status !== "pending_payment") return stop();
    watch.deadline = performance.now() + data.seconds_remaining * 1000;
    if (!watch.timer) watch.timer = setInterval(tick, 250);
    paint();
  }

  sync();
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && watch.timer) sync();
  });
})();
