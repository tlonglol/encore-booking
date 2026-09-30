(function () {
  const origin = (window.ENCORE_CONFIG && window.ENCORE_CONFIG.apiOrigin) || "";

  async function api(path, options) {
    const opts = options || {};
    const init = { method: opts.method || "GET", credentials: "include", headers: {} };
    if (opts.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    let response;
    try {
      response = await fetch(origin + path, init);
    } catch (error) {
      return { status: 0, data: { detail: "Can't reach the booking server. Check your connection and try again." } };
    }
    let data = null;
    try {
      data = await response.json();
    } catch (error) {
      data = null;
    }
    return { status: response.status, data: data };
  }

  const FIELD_NAMES = {
    first_name: "First name",
    last_name: "Last name",
    phone: "Phone",
    email: "Email",
    notes: "Notes",
    kids: "Kids",
  };

  function detailText(data, fallback) {
    if (!data || data.detail === undefined) return fallback || "Something went wrong. Please try again.";
    if (typeof data.detail === "string") return data.detail;
    if (Array.isArray(data.detail)) {
      return data.detail
        .map(function (item) {
          const field = item.loc && item.loc[item.loc.length - 1];
          const name = FIELD_NAMES[field] || field || "Input";
          if (field === "email") return "Enter a valid email address.";
          return name + ": " + String(item.msg || "invalid").replace(/^Value error, /, "");
        })
        .join(" ");
    }
    return fallback || "Something went wrong. Please try again.";
  }

  function money(cents) {
    const dollars = cents / 100;
    return "$" + (cents % 100 === 0 ? dollars.toLocaleString("en-US") : dollars.toLocaleString("en-US", { minimumFractionDigits: 2 }));
  }

  function clock(seconds) {
    const s = Math.max(0, Math.floor(seconds));
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  }

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(function (entry) {
      const key = entry[0];
      const value = entry[1];
      if (value === null || value === undefined || value === false) return;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    });
    (children || []).forEach(function (child) {
      if (child === null || child === undefined || child === false) return;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    });
    return node;
  }

  function store(key, value) {
    try {
      if (value === null) sessionStorage.removeItem(key);
      else sessionStorage.setItem(key, JSON.stringify(value));
    } catch (error) {
      /* storage unavailable: the page still works, it just forgets on reload */
    }
  }

  function recall(key) {
    try {
      return JSON.parse(sessionStorage.getItem(key) || "null");
    } catch (error) {
      return null;
    }
  }

  // Parses what's typed into a count field. null means "empty, still typing": the caller keeps the old value.
  // Anything that isn't all digits falls back to the previous value; digits are clamped to [min, max].
  function readCount(raw, previous, min, max) {
    const text = String(raw).trim();
    if (text === "") return null;
    if (!/^[0-9]+$/.test(text)) return previous;
    return Math.min(max, Math.max(min, parseInt(text, 10)));
  }

  // Wires a typed count input: updates on every keystroke (like a +/- tap) and settles on blur.
  function bindCount(input, min, max, get, set) {
    input.addEventListener("input", function () {
      const value = readCount(input.value, get(), min, max);
      if (value === null) return;
      if (input.value !== String(value)) input.value = String(value);
      if (value !== get()) set(value);
    });
    input.addEventListener("blur", function () {
      const value = readCount(input.value, get(), min, max);
      const settled = value === null ? get() : value;
      input.value = String(settled);
      if (settled !== get()) set(settled);
    });
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter") input.blur();
    });
  }

  window.Encore = {
    api: api,
    detailText: detailText,
    money: money,
    clock: clock,
    el: el,
    store: store,
    recall: recall,
    readCount: readCount,
    bindCount: bindCount,
  };
})();
