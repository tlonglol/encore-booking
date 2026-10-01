// The site shell shared by every page: the live site's nav and footer.
(function () {
  const { el } = window.Encore;
  const C = window.EncoreContent;

  function mountNav(options) {
    const opts = options || {};
    const nav = document.getElementById("nav");
    if (!nav) return null;
    const cta = el(opts.ctaTag || "a", {
      class: "nav-cta",
      id: opts.ctaId || null,
      href: opts.ctaTag === "button" ? null : opts.ctaHref || "#book",
      type: opts.ctaTag === "button" ? "button" : null,
      text: opts.ctaLabel || "Book a Party",
      hidden: Boolean(opts.ctaHidden),
    });
    nav.replaceChildren(
      el("a", { class: "nav-logo", href: C.SITE }, ["ENCORE ", el("span", { text: opts.accent || "ESPORTS" })]),
      el(
        "div",
        { class: "nav-links" },
        C.NAV.map((link) => el("a", { class: "nav-link" + (link.active ? " active" : ""), href: link.href, text: link.label }))
      ),
      cta
    );
    return cta;
  }

  function mountFooter() {
    const footer = document.getElementById("footer");
    if (!footer) return;
    footer.replaceChildren(
      C.FOOTER.text,
      el("a", { href: C.FOOTER.href, text: C.FOOTER.site }),
      C.FOOTER.social
    );
  }

  window.EncoreShell = { mountNav: mountNav, mountFooter: mountFooter };
})();
