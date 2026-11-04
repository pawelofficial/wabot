// Weight chart: an SVG line of the trainee's current weight per day, drawn from the JSON in
// #weight-data (routes.weight). Hovering, or the arrow keys once the chart has focus, shows the
// day, the weight and the WhatsApp message it came from. The same values are in the table
// below the chart.
(() => {
  const box = document.querySelector("[data-weight-chart]");
  const source = document.getElementById("weight-data");
  if (!box || !source) return;

  const points = JSON.parse(source.textContent).map((p) => ({
    ...p,
    time: Date.parse(p.date + "T00:00:00Z"),
  }));
  if (!points.length) return;

  const SVG = "http://www.w3.org/2000/svg";
  const DAY = 86400000;
  const HEIGHT = 280;
  const MONTHS = ["sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paź", "lis", "gru"];
  const WEEKDAYS = ["niedz", "pon", "wt", "śr", "czw", "pt", "sob"]; // getUTCDay(): 0 is Sunday
  const number = (v, digits) => v.toFixed(digits).replace(".", ",");
  const kg = (v) => number(v, 1) + " kg";
  const shortDate = (t) => {
    const d = new Date(t);
    return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
  };
  const longDate = (t) => {
    const d = new Date(t);
    return `${WEEKDAYS[d.getUTCDay()]} ${shortDate(t)} ${d.getUTCFullYear()}`;
  };

  function el(name, attrs, parent) {
    const node = document.createElementNS(SVG, name);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    parent.appendChild(node);
    return node;
  }

  // The smallest round step that gives at most 5 gridlines.
  function niceStep(range) {
    for (const step of [0.5, 1, 2, 5, 10, 20]) if (range / step <= 5) return step;
    return 50;
  }

  // Dates for the x axis: Mondays for up to ~10 weeks, else the 1st of each month; thinned
  // so labels are at least 64px apart.
  function xTicks(t0, t1, width) {
    const ticks = [];
    if (t1 - t0 <= 70 * DAY) {
      const start = new Date(t0);
      const toMonday = (8 - start.getUTCDay()) % 7;
      for (let t = t0 + toMonday * DAY; t <= t1; t += 7 * DAY) ticks.push(t);
    } else {
      const d = new Date(t0);
      for (let m = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1)); m <= t1; m.setUTCMonth(m.getUTCMonth() + 1)) {
        ticks.push(m.getTime());
      }
    }
    const every = Math.max(1, Math.ceil((ticks.length * 64) / width));
    return ticks.filter((_, i) => i % every === 0);
  }

  let selected = null; // index of the point the tooltip shows

  function draw() {
    box.textContent = "";
    const width = Math.max(box.clientWidth, 280);
    const m = { top: 16, right: 72, bottom: 28, left: 44 };
    const w = width - m.left - m.right;
    const h = HEIGHT - m.top - m.bottom;

    // Dates: at least a week wide, so a single weigh-in sits in the middle.
    let t0 = points[0].time;
    let t1 = points[points.length - 1].time;
    if (t1 - t0 < 7 * DAY) {
      const pad = (7 * DAY - (t1 - t0)) / 2;
      t0 -= pad;
      t1 += pad;
    }
    // Weight: round gridlines around the data, not from zero (the change is what matters).
    const values = points.map((p) => p.kg);
    let lo = Math.min(...values);
    let hi = Math.max(...values);
    const step = niceStep(Math.max(hi - lo, 1));
    lo = Math.floor(lo / step) * step;
    hi = Math.ceil(hi / step) * step;
    if (hi - lo < 2 * step) {
      lo -= step;
      hi += step;
    }
    const x = (t) => m.left + ((t - t0) / (t1 - t0)) * w;
    const y = (v) => m.top + ((hi - v) / (hi - lo)) * h;

    const svg = el("svg", {
      width,
      height: HEIGHT,
      viewBox: `0 0 ${width} ${HEIGHT}`,
      role: "img",
      tabindex: 0,
      "aria-label": `Waga od ${longDate(points[0].time)} do ${longDate(points[points.length - 1].time)}, ostatnio ${kg(values[values.length - 1])}. Strzałki: kolejne pomiary.`,
    }, box);

    // Gridlines and weight labels.
    const digits = step < 1 ? 1 : 0;
    for (let v = lo; v <= hi + 1e-9; v += step) {
      el("line", { class: "grid", x1: m.left, x2: m.left + w, y1: y(v), y2: y(v) }, svg);
      el("text", { class: "axis", x: m.left - 8, y: y(v) + 4, "text-anchor": "end" }, svg).textContent = number(v, digits);
    }
    // Date labels.
    for (const t of xTicks(t0, t1, w)) {
      el("text", { class: "axis", x: x(t), y: HEIGHT - 8, "text-anchor": "middle" }, svg).textContent = shortDate(t);
    }

    // The line and the weigh-ins.
    const path = points.map((p, i) => `${i ? "L" : "M"}${x(p.time).toFixed(1)},${y(p.kg).toFixed(1)}`).join("");
    el("path", { class: "line", d: path }, svg);
    const crosshair = el("line", { class: "crosshair", y1: m.top, y2: m.top + h, visibility: "hidden" }, svg);
    const dots = points.map((p) => el("circle", { class: "dot", cx: x(p.time), cy: y(p.kg), r: 4 }, svg));

    // The latest value at the end of the line.
    const last = points[points.length - 1];
    el("text", { class: "end-label", x: x(last.time) + 10, y: y(last.kg) + 4 }, svg).textContent = kg(last.kg);

    const tooltip = document.createElement("div");
    tooltip.className = "chart-tooltip";
    tooltip.hidden = true;
    box.appendChild(tooltip);

    function show(index) {
      selected = index;
      dots.forEach((dot, i) => dot.classList.toggle("active", i === index));
      if (index === null) {
        crosshair.setAttribute("visibility", "hidden");
        tooltip.hidden = true;
        return;
      }
      const p = points[index];
      crosshair.setAttribute("x1", x(p.time));
      crosshair.setAttribute("x2", x(p.time));
      crosshair.setAttribute("visibility", "visible");

      tooltip.textContent = "";
      const value = document.createElement("strong");
      value.textContent = kg(p.kg);
      const day = document.createElement("div");
      day.className = "muted";
      day.textContent = longDate(p.time);
      tooltip.append(value, day);
      if (p.message) {
        const message = document.createElement("div");
        message.className = "chart-tooltip-message";
        message.textContent = `„${p.message}”`;
        tooltip.append(message);
      }
      tooltip.hidden = false;
      // Above the point, kept inside the chart.
      const left = Math.min(Math.max(x(p.time) - tooltip.offsetWidth / 2, 0), width - tooltip.offsetWidth);
      const top = Math.max(y(p.kg) - tooltip.offsetHeight - 14, 0);
      tooltip.style.left = `${left}px`;
      tooltip.style.top = `${top}px`;
    }

    // Hover anywhere over the plot: the nearest day wins, the pointer needn't hit the dot.
    const overlay = el("rect", { class: "overlay", x: m.left - 12, y: 0, width: w + 24, height: HEIGHT - m.bottom }, svg);
    overlay.addEventListener("pointermove", (event) => {
      const px = event.clientX - svg.getBoundingClientRect().left;
      let nearest = 0;
      points.forEach((p, i) => {
        if (Math.abs(x(p.time) - px) < Math.abs(x(points[nearest].time) - px)) nearest = i;
      });
      if (nearest !== selected) show(nearest);
    });
    overlay.addEventListener("pointerleave", () => show(null));
    svg.addEventListener("focus", () => show(selected ?? points.length - 1));
    svg.addEventListener("blur", () => show(null));
    svg.addEventListener("keydown", (event) => {
      const moves = { ArrowLeft: -1, ArrowRight: 1, Home: -Infinity, End: Infinity };
      if (!(event.key in moves)) return;
      event.preventDefault();
      const next = Math.min(Math.max((selected ?? points.length - 1) + moves[event.key], 0), points.length - 1);
      show(next);
    });
    if (selected !== null) show(selected);
  }

  draw();
  let lastWidth = box.clientWidth;
  new ResizeObserver(() => {
    if (box.clientWidth !== lastWidth) {
      lastWidth = box.clientWidth;
      draw();
    }
  }).observe(box);
})();
