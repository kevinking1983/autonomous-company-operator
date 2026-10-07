// Page snapshot for the operator: the page's semantic structure as compact text.
//
// Built from the DOM plus accessibility information (labels, roles, ARIA
// names), not pixels. Only visible content is included, so a collapsed menu
// hides its contents just as it does for a person. Every interactive element
// gets a ref like [e12] that the click/fill/select tools use to find it again.
// Refs are reassigned on every snapshot.
({ maxRows, maxLines }) => {
  document.querySelectorAll("[data-aco-ref]").forEach((e) => e.removeAttribute("data-aco-ref"));
  let n = 0;
  const lines = [];
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  const text = (el) => clean(el.innerText !== undefined ? el.innerText : el.textContent);
  const quote = (s) => JSON.stringify(clean(s));
  const visible = (el) =>
    el.checkVisibility ? el.checkVisibility() : !!(el.offsetParent || el.getClientRects().length);
  const ref = (el) => {
    const r = "e" + ++n;
    el.setAttribute("data-aco-ref", r);
    return r;
  };
  const labelOf = (el) => {
    if (el.labels && el.labels.length) return text(el.labels[0]);
    return el.getAttribute("aria-label") || el.getAttribute("placeholder") || el.name || "";
  };
  const push = (depth, line) => {
    if (lines.length < maxLines) lines.push("  ".repeat(depth) + line);
  };
  const INTERACTIVE = "a,button,input,select,textarea,summary";
  const STRUCTURAL = INTERACTIVE + ",table,dl,img,h1,h2,h3,h4,h5,h6,form,section,article,aside,details,ul,ol,figure";

  // Text of an element, with refs appended for any links/buttons inside it.
  const inlineWithRefs = (el) => {
    let out = text(el);
    el.querySelectorAll("a,button").forEach((a) => {
      if (visible(a)) out += ` [${ref(a)}${a.tagName === "A" ? " -> " + a.getAttribute("href") : ""}]`;
    });
    return out;
  };

  const field = (el, depth) => {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (type === "hidden") return;
    const label = quote(labelOf(el));
    const req = el.required ? " required" : "";
    if (tag === "select") {
      const opts = Array.from(el.options).map((o) => clean(o.textContent) || "(empty)");
      const selected = el.selectedOptions.length ? clean(el.selectedOptions[0].textContent) : "";
      push(depth, `combobox ${label} [${ref(el)}]${req} selected=${quote(selected)} options=[${opts.join(", ")}]`);
    } else if (type === "checkbox" || type === "radio") {
      push(depth, `${type} ${label} [${ref(el)}] ${el.checked ? "checked" : "unchecked"}`);
    } else if (type === "submit" || type === "button") {
      push(depth, `button ${quote(el.value)} [${ref(el)}]`);
    } else {
      const kind = tag === "textarea" ? "textarea" : `textbox${type && type !== "text" ? "(" + type + ")" : ""}`;
      push(depth, `${kind} ${label} [${ref(el)}]${req} value=${quote(el.value)}`);
    }
  };

  const table = (el, depth) => {
    const label = el.getAttribute("aria-label");
    const rows = Array.from(el.rows).filter(visible);
    push(depth, `table${label ? " " + quote(label) : ""} (${rows.length} rows):`);
    rows.slice(0, maxRows).forEach((row) => {
      const cells = Array.from(row.cells).map((c) => inlineWithRefs(c).replace(/\|/g, "/"));
      push(depth + 1, "| " + cells.join(" | ") + " |");
    });
    if (rows.length > maxRows) push(depth + 1, `... ${rows.length - maxRows} more rows (narrow your search)`);
  };

  const walk = (el, depth) => {
    if (!(el instanceof HTMLElement) || !visible(el)) return;
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute("role");
    if (["script", "style", "noscript", "template", "label"].includes(tag)) return;
    if (/^h[1-6]$/.test(tag)) return push(depth, "#".repeat(+tag[1]) + " " + inlineWithRefs(el));
    if (role === "alert" || role === "status") return push(depth, `[${role}] ${text(el)}`);
    if (tag === "a") return push(depth, `link ${quote(text(el))} [${ref(el)}] -> ${el.getAttribute("href")}`);
    if (tag === "button") return push(depth, `button ${quote(text(el))} [${ref(el)}]`);
    if (["input", "select", "textarea"].includes(tag)) return field(el, depth);
    if (tag === "table") return table(el, depth);
    if (tag === "img") return push(depth, `image ${quote(el.alt)} src=${el.getAttribute("src")}`);
    if (tag === "summary") {
      const open = el.parentElement && el.parentElement.open;
      return push(depth, `${open ? "expanded" : "collapsed"} menu ${quote(text(el))} [${ref(el)}] (click to ${open ? "collapse" : "expand"})`);
    }
    if (tag === "dl") {
      const dts = el.querySelectorAll(":scope > dt");
      dts.forEach((dt) => {
        const dd = dt.nextElementSibling;
        push(depth, `${text(dt)}: ${dd ? inlineWithRefs(dd) : ""}`);
      });
      return;
    }
    let childDepth = depth;
    const name = el.getAttribute("aria-label");
    if (tag === "form" || ((tag === "section" || tag === "aside" || tag === "nav" || tag === "article") && name)) {
      push(depth, `${tag === "form" ? "form" : "region"}${name ? " " + quote(name) : ""}:`);
      childDepth = depth + 1;
    }
    // A block of plain or inline content: print it as one line.
    if (!el.querySelector(STRUCTURAL) && tag !== "form") {
      const t = text(el);
      if (t) push(depth, t);
      return;
    }
    for (const child of el.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) {
        const t = clean(child.textContent);
        if (t) push(childDepth, t);
      } else {
        walk(child, childDepth);
      }
    }
  };

  walk(document.body, 0);
  if (lines.length >= maxLines) lines.push(`... snapshot truncated at ${maxLines} lines`);
  return lines.join("\n");
};
