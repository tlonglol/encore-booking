// Page copy, taken from the live Birthday Parties page at encoregaming.us.
// Prices are never in here: they come from GET /api/catalog.
(function () {
  const SITE = "https://encoregaming.us";

  const NAV = [
    ["Home", "/"],
    ["Events", "/encore_events"],
    ["Games", "/encore_games"],
    ["Food Menu", "/encore_food"],
    ["Monroe Esports", "/monroe"],
    ["Birthday Parties", "/encore_birthday"],
    ["Contact", "/encore_contact"],
  ].map(function (pair) {
    return { label: pair[0], href: SITE + pair[1], active: pair[0] === "Birthday Parties" };
  });

  const HERO = {
    eyebrow: "538 Main St · New Rochelle, NY",
    line1: "Level Up",
    line2: "Their Birthday",
    sub: "Your kid deserves a party they'll talk about on Monday. We handle the setup, the games, and the energy — you just show up and watch them have the time of their life.",
    chips: ["Switch · PS5 · PC Gaming", "VR · DDR", "Dedicated Host Included", "Ages 6+"],
  };

  const STATS = [
    ["🎮", "Platforms", "Switch · PS5 · PC"],
    ["👤", "Staff Host", "Every Package"],
    ["🎂", "Ages", "6 and Up"],
    ["📅", "Available", "Sat & Sun AM"],
  ];

  const REVIEWS = {
    href: "https://www.google.com/search?q=encore+esports+new+rochelle+reviews",
    rating: "4.9 · 161 Google reviews",
    tail: "Read what parents say →",
  };

  const TIERS = ["standard", "plus", "premium"];
  const TIER_ACCENT = { standard: "var(--tier-standard)", plus: "var(--tier-plus)", premium: "var(--tier-premium)" };

  // One feature matrix for every surface that explains a tier: the landing cards, the diff strip, the tab
  // popovers and the compare drawer all read this, so they cannot drift apart.
  //   all   - every tier includes it, same wording
  //   text  - per-tier wording; a tier listed here includes the feature
  //   off   - the greyed wording shown for a tier that does not include it
  //   hl    - the substring shown in the tier accent
  //   short - the compact wording used in diff strips and summaries
  const FEATURES = [
    {
      id: "games",
      row: "Games pre-loaded",
      text: {
        standard: "4 games pre-loaded from full catalog",
        plus: "6 games pre-loaded from full catalog",
        premium: "Full catalog — all stations pre-loaded",
      },
      hl: { standard: "4 games", plus: "6 games", premium: "Full catalog" },
      short: { standard: "4 games", plus: "6 games", premium: "full catalog" },
    },
    { id: "buyout", all: "Private full-venue buyout — just your group", short: { all: "private buyout" } },
    { id: "banner", all: "Birthday banner on every screen", short: { all: "birthday banner" } },
    { id: "stations", all: "Switch, PS5 & PC stations", short: { all: "Switch, PS5 & PC" } },
    { id: "host", all: "Dedicated staff host", short: { all: "dedicated host" } },
    { id: "photo", all: "Digital group photo after party", short: { all: "group photo" } },
    {
      id: "decorations",
      row: "Decorations",
      off: "Gamer-themed decorations",
      text: { plus: "Gamer-themed decorations + balloon tower", premium: "Full decoration setup throughout space" },
      short: { plus: "decorations + balloon tower", premium: "full decoration setup" },
    },
    {
      id: "favors",
      row: "Favor bags",
      off: "Premium favor bags",
      text: { plus: "Party favor bags — toys & candy", premium: "Premium favor bags + Encore branded item" },
      hl: { premium: "Premium favor bags" },
      short: { plus: "favor bags", premium: "premium favor bags" },
    },
    {
      id: "ddr",
      row: "DDR pad",
      off: "DDR pad full session",
      text: { premium: "DDR pad — full party duration" },
      hl: { premium: "full party duration" },
      short: { premium: "DDR pad" },
    },
    {
      id: "vr",
      row: "VR gaming",
      off: "VR gaming full session",
      text: { premium: "VR gaming — Beat Saber & more, full duration" },
      hl: { premium: "Beat Saber & more" },
      short: { premium: "VR gaming" },
    },
  ];

  function includes(feature, tier) {
    return Boolean(feature.all || (feature.text && feature.text[tier]));
  }

  // {text, on, hl} for one feature as one tier states it.
  function featureFor(feature, tier) {
    if (feature.all) return { text: feature.all, on: true, hl: null };
    const text = feature.text && feature.text[tier];
    if (text) return { text: text, on: true, hl: (feature.hl || {})[tier] || null };
    return { text: feature.off, on: false, hl: null };
  }

  function rowLabel(feature) {
    return feature.row || feature.all || featureFor(feature, "premium").text;
  }

  // True when the tiers word this feature differently, so a tick alone would hide the difference.
  function varies(feature) {
    if (feature.all) return false;
    const texts = TIERS.map(function (tier) { return featureFor(feature, tier).text; });
    return texts[0] !== texts[1] || texts[1] !== texts[2];
  }

  function shortFor(feature, tier) {
    const short = feature.short || {};
    return short[tier] || short.all || featureFor(feature, tier).text;
  }

  // What this tier unlocks over the one below it; for the base tier, its headline inclusions.
  function highlights(tier) {
    const index = TIERS.indexOf(tier);
    const below = index > 0 ? TIERS[index - 1] : null;
    const gained = FEATURES.filter(function (feature) {
      if (!includes(feature, tier)) return false;
      if (!below) return false;
      if (!includes(feature, below)) return true;
      return featureFor(feature, below).text !== featureFor(feature, tier).text;
    });
    if (gained.length) return gained;
    return FEATURES.filter(function (feature) { return includes(feature, tier); }).slice(0, 3);
  }

  // What changes moving between two tiers: added or upgraded going up, lost or downgraded going down.
  function diff(fromTier, toTier) {
    const up = TIERS.indexOf(toTier) > TIERS.indexOf(fromTier);
    const items = [];
    FEATURES.forEach(function (feature) {
      const inFrom = includes(feature, fromTier);
      const inTo = includes(feature, toTier);
      if (inTo && !inFrom) {
        if (up) items.push(shortFor(feature, toTier));
      } else if (inFrom && !inTo) {
        if (!up) items.push(shortFor(feature, fromTier));
      } else if (inFrom && inTo && featureFor(feature, fromTier).text !== featureFor(feature, toTier).text) {
        items.push(shortFor(feature, up ? toTier : fromTier));
      }
    });
    return { up: up, items: items };
  }

  const BUYOUT = {
    eyebrow: "Outside Normal Birthday Hours?",
    title: "Rent the whole place.",
    body: "Private full-venue buyouts for adult birthdays, corporate events, larger family gatherings — anything outside our standard Sat/Sun morning kids' window. Evenings, late nights, and weekday daytime available.",
    facts: "3,000+ sq ft · 40+ gaming stations · BYOB welcome · Custom catering coordination",
    fromLabel: "Starting at",
    fromPrice: "$1,500",
    fromNote: "Custom quote based on hours, guests, add-ons",
    cta: "Request a Quote",
    href: SITE + "/encore_contact",
  };

  // Display order of the add-on grid, matching the live page. {reel} is filled from the catalog's Highlight Reel price.
  const ADDON_ORDER = ["tournament", "pizza_pack", "extra_hour", "drinks_pack", "vr", "ddr", "decorations"];
  const ADDONS = {
    tournament: {
      icon: "🏆",
      featured: true,
      desc: "Bracket setup, host MC, livestream to YouTube, printed bracket, trophy for the birthday champion. +{reel} for an edited highlight Reel.",
      short: "Bracket, host MC, YouTube livestream, printed bracket, trophy for the birthday champion.",
    },
    highlight_reel: { icon: "🎬", desc: "Edited highlight reel of the tournament.", short: "Edited highlight reel of the tournament. Needs Tournament + Livestream." },
    pizza_pack: { icon: "🍕", desc: "2 large pizzas + drinks — feeds ~10 kids. Order multiple packs as needed.", short: "2 large pizzas + drinks. Feeds about 10 kids." },
    extra_hour: { icon: "⏱️", desc: "Extend the party — full venue, host, and games stay yours.", short: "Extend the party. Venue, host, and games stay yours." },
    drinks_pack: { icon: "🥤", desc: "Soda + juice boxes for the whole group.", short: "Soda + juice boxes for the whole group." },
    vr: { icon: "🥽", desc: "Beat Saber, Superhot & more — full party duration. (Included in Premium.)", short: "Beat Saber, Superhot & more, full party duration." },
    ddr: { icon: "🕹️", desc: "Full party duration access for the group. (Included in Premium.)", short: "DDR / Stepmania pad for the whole group, full party." },
    decorations: { icon: "🎈", desc: "Balloon tower, tabletop décor, themed setup. (Included in Plus & Premium.)", short: "Balloon tower, tabletop décor, themed setup." },
  };

  const FAQ = [
    ["Can I bring my own cake?", "Absolutely. Bring your cake, ice cream, whatever. Pizza is now optional — add a $40 pizza pack if you want us to handle food, or bring your own."],
    ["What if more kids show up?", "$35 per additional kid — easy to add the day of. Your tier just sets the included headcount."],
    ["Do parents stay or drop off?", "Your call. Parents are welcome to hang out — we have seating. Or drop and go and pick up after."],
    ["Can we run a real tournament?", "Yes. Add Tournament + Livestream ($199) and we'll crown a birthday champion with a printed bracket, host MC, and a YouTube stream you can share with relatives who can't make it."],
    ["What ages work best?", "Ages 6 and up. The sweet spot is 7-13 — all games and content are age-appropriate for the room."],
    ["Allergies or dietary needs?", "Let us know when you book. We can adjust the pizza order or swap to allergen-friendly options."],
  ];

  const QUOTES = [
    ["“He asked me for a Spotify playlist for the party and had all the screens displaying my kids' names with Happy Birthday. A lot of nice touches.”", "Jose F."],
    ["“An awesome place to have a low key, intimate but spacious party. It was comfy and relaxing. The set up is great too — so easy to play all the games.”", "Samantha O."],
    ["“They have all the set up you need — computers, Xbox, PlayStation and Nintendo in abundance. It's amazing how much they got. A complete place for everyone to enjoy.”", "Nelson R. · Local Guide"],
  ];

  // The one place the refund and reschedule promise is written.
  const REFUND_PROMISE =
    "Cancel or reschedule free up to 7 days before your party. Inside 7 days, your deposit isn't refundable, but you can reschedule once.";

  const FOOTER = {
    text: "538 Main St, 2nd Floor · New Rochelle, NY · ",
    site: "encoregaming.us",
    href: SITE,
    social: " · @encorewestchester",
  };

  window.EncoreContent = {
    SITE: SITE,
    NAV: NAV,
    HERO: HERO,
    STATS: STATS,
    REVIEWS: REVIEWS,
    TIERS: TIERS,
    TIER_ACCENT: TIER_ACCENT,
    FEATURES: FEATURES,
    includes: includes,
    featureFor: featureFor,
    shortFor: shortFor,
    rowLabel: rowLabel,
    varies: varies,
    highlights: highlights,
    diff: diff,
    BUYOUT: BUYOUT,
    ADDON_ORDER: ADDON_ORDER,
    ADDONS: ADDONS,
    FAQ: FAQ,
    QUOTES: QUOTES,
    REFUND_PROMISE: REFUND_PROMISE,
    FOOTER: FOOTER,
  };
})();
