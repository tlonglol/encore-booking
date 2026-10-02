// Birthday finder: which weekends sit closest to a birthday. Pure functions, no DOM, so they can be tested alone.
// A "group" is one weekend: an array of day objects ({date, status, day_label, ...}), Saturday then Sunday.
(function () {
  const DAY_MS = 86400000;

  function ms(date) {
    return Date.UTC(Number(date.slice(0, 4)), Number(date.slice(5, 7)) - 1, Number(date.slice(8, 10)));
  }

  function diffDays(a, b) {
    return Math.round((ms(a) - ms(b)) / DAY_MS);
  }

  function isWeekend(date) {
    const dow = new Date(ms(date)).getUTCDay();
    return dow === 0 || dow === 6;
  }

  const firstDate = (group) => group[0].date;
  const lastDate = (group) => group[group.length - 1].date;
  const available = (group) => group.some((day) => day.status === "open");

  function plural(n, word) {
    return `${n} ${word}${n === 1 ? "" : "s"}`;
  }

  // "4 days before", "3 weeks after". Honest about distance: weeks only once it is two or more.
  function relative(days, direction) {
    if (days >= 14) return `${plural(Math.round(days / 7), "week")} ${direction}`;
    return `${plural(days, "day")} ${direction}`;
  }

  // The weekends to pin for a birthday, the rest, and whether anything suggested can actually be booked.
  //   weekday birthday -> the weekend before and the weekend after
  //   weekend birthday -> its own weekend first, then the weekends before and after as alternates
  // If none of the pinned weekends is available, the nearest open weekend is pinned too, labelled plainly.
  function suggest(groups, birthday) {
    const own = groups.find((g) => firstDate(g) <= birthday && birthday <= lastDate(g)) || null;
    const before = groups.slice().reverse().find((g) => g !== own && lastDate(g) < birthday) || null;
    const after = groups.find((g) => g !== own && firstDate(g) > birthday) || null;

    const pinned = [];
    if (own) pinned.push({ group: own, note: "birthday weekend", kind: "own" });
    if (before) pinned.push({ group: before, note: relative(diffDays(birthday, lastDate(before)), "before"), kind: "before" });
    if (after) pinned.push({ group: after, note: relative(diffDays(firstDate(after), birthday), "after"), kind: "after" });

    let exhausted = false;
    if (!pinned.some((entry) => available(entry.group))) {
      const taken = new Set(pinned.map((entry) => entry.group));
      let best = null;
      groups.forEach((group) => {
        if (taken.has(group)) return;
        group.forEach((day) => {
          if (day.status !== "open") return;
          const distance = Math.abs(diffDays(day.date, birthday));
          if (!best || distance < best.distance) best = { group: group, day: day, distance: distance, after: day.date > birthday };
        });
      });
      if (best) {
        pinned.push({
          group: best.group,
          note: `Closest open: ${best.day.day_label}, ${relative(best.distance, best.after ? "after" : "before")}`,
          kind: "fallback",
        });
      } else {
        exhausted = true;
      }
    }
    const pinnedGroups = new Set(pinned.map((entry) => entry.group));
    return {
      pinned: pinned,
      rest: groups.filter((g) => !pinnedGroups.has(g)),
      exhausted: exhausted,
      // True when the weekends nearest the birthday could not be booked, so the waitlist should be offered.
      noneNearby: exhausted || pinned.some((entry) => entry.kind === "fallback"),
    };
  }

  window.EncoreFinder = { suggest: suggest, diffDays: diffDays, isWeekend: isWeekend, relative: relative };
})();
