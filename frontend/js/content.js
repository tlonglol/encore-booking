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

  // Each feature is {text, hl, off}. hl is the substring shown in blue; off marks a tier that does not include it.
  const PACKAGE_FEATURES = {
    standard: [
      { text: "4 games pre-loaded from full catalog", hl: "4 games" },
      { text: "Private full-venue buyout — just your group" },
      { text: "Birthday banner on every screen" },
      { text: "Switch, PS5 & PC stations" },
      { text: "Dedicated staff host" },
      { text: "Digital group photo after party" },
      { text: "Gamer-themed decorations", off: true },
      { text: "Premium favor bags", off: true },
      { text: "DDR pad full session", off: true },
      { text: "VR gaming full session", off: true },
    ],
    plus: [
      { text: "6 games pre-loaded from full catalog", hl: "6 games" },
      { text: "Private full-venue buyout — just your group" },
      { text: "Birthday banner on every screen" },
      { text: "Switch, PS5 & PC stations" },
      { text: "Dedicated staff host" },
      { text: "Digital group photo after party" },
      { text: "Gamer-themed decorations + balloon tower" },
      { text: "Party favor bags — toys & candy" },
      { text: "DDR pad full session", off: true },
      { text: "VR gaming full session", off: true },
    ],
    premium: [
      { text: "Full catalog — all stations pre-loaded", hl: "Full catalog" },
      { text: "Private full-venue buyout — just your group" },
      { text: "Birthday banner on every screen" },
      { text: "Switch, PS5 & PC stations" },
      { text: "Dedicated staff host" },
      { text: "Digital group photo after party" },
      { text: "Full decoration setup throughout space" },
      { text: "Premium favor bags + Encore branded item", hl: "Premium favor bags" },
      { text: "DDR pad — full party duration", hl: "full party duration" },
      { text: "VR gaming — Beat Saber & more, full duration", hl: "Beat Saber & more" },
    ],
  };

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
    PACKAGE_FEATURES: PACKAGE_FEATURES,
    BUYOUT: BUYOUT,
    ADDON_ORDER: ADDON_ORDER,
    ADDONS: ADDONS,
    FAQ: FAQ,
    QUOTES: QUOTES,
    FOOTER: FOOTER,
  };
})();
