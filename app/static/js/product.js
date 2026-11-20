/* Product page: price-history chart, window toggles, summary table, offer sorting. */
(function () {
  "use strict";

  // ---- offers table sorting & "log a price" shortcuts -------------------------------
  const sortSel = document.getElementById("offer-sort");
  if (sortSel) {
    sortSel.addEventListener("change", () => {
      const tbody = document.querySelector("#offers tbody");
      const rows = Array.from(tbody.rows);
      const key = sortSel.value;
      rows.sort((a, b) => {
        const ga = +a.dataset.group, gb = +b.dataset.group;
        if (key !== "retailer" && ga !== gb) return ga - gb;
        if (key === "retailer") {
          if ((ga === 3) !== (gb === 3)) return ga === 3 ? 1 : -1;
          return a.dataset.retailer.localeCompare(b.dataset.retailer);
        }
        const va = parseFloat(a.dataset[key]), vb = parseFloat(b.dataset[key]);
        return (isNaN(va) ? Infinity : va) - (isNaN(vb) ? Infinity : vb);
      });
      rows.forEach((r) => tbody.appendChild(r));
    });
  }
  document.querySelectorAll("[data-log-retailer]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const details = document.getElementById("log-price");
      details.open = true;
      document.getElementById("log-retailer").value = btn.dataset.logRetailer;
      details.scrollIntoView({ behavior: "smooth", block: "center" });
      const price = details.querySelector('input[name="price"]');
      if (price) setTimeout(() => price.focus({ preventScroll: true }), 250);
    });
  });

  // ---- history -------------------------------------------------------------------------
  const dataEl = document.getElementById("history-data");
  if (!dataEl || typeof Chart === "undefined") return;
  const H = JSON.parse(dataEl.textContent);
  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
  const axisMoney = (n) => "$" + (n >= 100 ? Math.round(n).toLocaleString("en-US") : n.toFixed(0));
  const ICONS = { low: "↓", below: "↓", near: "→", above: "↑", insufficient: "…", none: "–" };

  function parseISO(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d);
  }
  function fmtDate(iso, withYear) {
    const opts = { month: "short", day: "numeric" };
    if (withYear) opts.year = "numeric";
    return parseISO(iso).toLocaleDateString("en-US", opts);
  }
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function keySwatch(slot) {
    const k = el("span", "key");
    k.setAttribute("aria-hidden", "true");
    k.style.setProperty("--key", slot ? `var(--series-${slot})` : "var(--text-primary)");
    return k;
  }

  let current = H.default;
  let chart = null;

  const startIndex = (key) => Math.max(0, H.labels.indexOf(H.windows[key].start));

  function renderSignal(key) {
    const sig = H.windows[key].signal;
    const box = document.getElementById("signal");
    box.textContent = "";
    const chip = el("span", "chip chip-" + sig.kind);
    chip.title = sig.detail || "";
    const icon = el("span", "chip-icon", ICONS[sig.kind] || "•");
    icon.setAttribute("aria-hidden", "true");
    chip.append(icon, document.createTextNode(sig.label));
    box.append(chip, el("span", "signal-detail", sig.detail || ""));
  }

  function statCells(tr, st) {
    const vals = st ? [st.current, st.low, st.high, st.avg] : [null, null, null, null];
    const days = st ? [null, st.low_day, st.high_day, null] : [];
    vals.forEach((v, i) => {
      const td = el("td", "num", v == null ? "—" : money.format(v));
      if (days[i]) td.title = "on " + fmtDate(days[i], true);
      tr.append(td);
    });
    let above = "—";
    if (st && st.pct_above_low != null) above = st.pct_above_low < 0.5 ? "At low" : "+" + st.pct_above_low.toFixed(0) + "%";
    tr.append(el("td", "num", above));
  }

  function renderStats(key) {
    const w = H.windows[key];
    const tbody = document.querySelector("#stats-table tbody");
    tbody.textContent = "";
    const best = el("tr", "best-row");
    const bth = el("th");
    bth.scope = "row";
    bth.append(keySwatch(null), document.createTextNode("Best available"));
    if (w.best && w.best.partial) bth.append(el("span", "sub", w.best.tracking_label));
    best.append(bth);
    statCells(best, w.best);
    tbody.append(best);

    const notes = [];
    w.rows.forEach((r) => {
      const st = r.stats;
      const tr = el("tr");
      const th = el("th");
      th.scope = "row";
      th.append(keySwatch(r.slot), document.createTextNode(r.label));
      const subs = [];
      if (!st) subs.push("No prices yet");
      else {
        if (st.partial) { subs.push(st.tracking_label); notes.push(r.label + ": " + st.tracking_label.replace("Tracking", "tracking")); }
        if (st.days === 0) subs.push("no prices in this window");
      }
      if (!r.in_stock) subs.push("out of stock now");
      if (subs.length) th.append(el("span", "sub", subs.join(" · ")));
      tr.append(th);
      statCells(tr, st);
      tbody.append(tr);
    });
    const cov = document.getElementById("coverage");
    cov.textContent = notes.length
      ? "Partial history in this window — " + notes.join(" · ") + ". Lines start when tracking began."
      : "";
  }

  function renderDaily(key) {
    const table = document.getElementById("daily-table");
    if (!table) return;
    const i0 = startIndex(key);
    const sets = H.datasets.filter((d) => !d.hidden);
    table.textContent = "";
    const thead = el("thead");
    const hr = el("tr");
    hr.append(el("th", null, "Date"));
    sets.forEach((d) => hr.append(el("th", "num", d.label)));
    thead.append(hr);
    const tbody = el("tbody");
    for (let i = H.labels.length - 1; i >= i0; i--) {
      const tr = el("tr");
      tr.append(el("td", null, fmtDate(H.labels[i], true)));
      sets.forEach((d) => {
        const v = d.data[i], s = d.stock[i];
        tr.append(el("td", "num", v == null ? (s === false ? "Out of stock" : "—") : money.format(v) + (s === false ? " (OOS)" : "")));
      });
      tbody.append(tr);
    }
    table.append(thead, tbody);
  }

  // Crosshair: a hairline that snaps to the hovered day.
  const crosshair = {
    id: "crosshair",
    afterDatasetsDraw(c) {
      const active = c.tooltip && c.tooltip.getActiveElements();
      if (!active || !active.length) return;
      const x = Math.round(active[0].element.x) + 0.5;
      const { top, bottom } = c.chartArea;
      const ctx = c.ctx;
      ctx.save();
      ctx.strokeStyle = cssVar("--text-muted");
      ctx.globalAlpha = 0.6;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, bottom);
      ctx.stroke();
      ctx.restore();
    },
  };

  // Direct end labels for <= 4 well-separated lines; otherwise legend + tooltip carry identity.
  const endLabels = {
    id: "endLabels",
    afterDatasetsDraw(c, _args, opts) {
      if (!opts || !opts.enabled) return;
      const ctx = c.ctx;
      ctx.save();
      ctx.font = "12px " + getComputedStyle(document.body).fontFamily;
      ctx.fillStyle = cssVar("--text-secondary");
      ctx.textBaseline = "middle";
      c.data.datasets.forEach((d, i) => {
        if (!c.isDatasetVisible(i)) return;
        const meta = c.getDatasetMeta(i);
        for (let k = d.data.length - 1; k >= 0; k--) {
          if (d.data[k] != null) {
            ctx.fillText(d._short, c.chartArea.right + 8, meta.data[k].y);
            break;
          }
        }
      });
      ctx.restore();
    },
  };

  function wantEndLabels(sets, i0, height) {
    if (window.innerWidth < 640) return false;
    const vis = sets.filter((d) => !d.hidden);
    if (vis.length < 2 || vis.length > 4) return false;
    const all = [], last = [];
    vis.forEach((d) => {
      const slice = d.data.slice(i0).filter((v) => v != null);
      all.push(...slice);
      if (slice.length) last.push(slice[slice.length - 1]);
      // Lines that end early would put their label in the wrong place.
      if (d.data[d.data.length - 1] == null) last.push(NaN);
    });
    if (last.some(isNaN) || !all.length) return false;
    const range = Math.max(...all) - Math.min(...all) || 1;
    last.sort((a, b) => a - b);
    const px = (height - 70) / range;
    for (let k = 1; k < last.length; k++) if ((last[k] - last[k - 1]) * px < 15) return false;
    return true;
  }

  function externalTooltip({ chart: c, tooltip }) {
    const tip = document.getElementById("chart-tip");
    if (!tooltip || tooltip.opacity === 0 || !tooltip.dataPoints || !tooltip.dataPoints.length) {
      tip.hidden = true;
      return;
    }
    const idx = tooltip.dataPoints[0].dataIndex;
    tip.textContent = "";
    tip.append(el("div", "tip-date", fmtDate(c.data.labels[idx], true)));
    const rows = c.data.datasets
      .map((d, i) => ({ d, i }))
      .filter((o) => c.isDatasetVisible(o.i))
      .map((o) => ({ name: o.d.label, val: o.d.data[idx], stock: o.d._stock[idx], color: o.d.borderColor, dashed: o.d.borderDash.length > 0 }))
      .filter((r) => r.val != null || r.stock === false)
      .sort((a, b) => (a.val == null ? Infinity : a.val) - (b.val == null ? Infinity : b.val));
    if (!rows.length) tip.append(el("div", "tip-name", "No prices recorded this day"));
    rows.forEach((r) => {
      const row = el("div", "tip-row" + (r.stock === false ? " oos" : ""));
      const key = el("span", "tip-key");
      key.style.background = r.dashed
        ? `repeating-linear-gradient(90deg, ${r.color} 0 4px, transparent 4px 7px)`
        : r.color;
      const val = el("span", "tip-val", r.val == null ? "Out of stock" : money.format(r.val));
      const name = el("span", "tip-name", r.name + (r.stock === false && r.val != null ? " · out of stock" : ""));
      row.append(key, val, name);
      tip.append(row);
    });
    tip.hidden = false;
    const wrap = c.canvas.parentNode;
    const w = tip.offsetWidth;
    let left = tooltip.caretX + 14;
    if (left + w > wrap.clientWidth) left = tooltip.caretX - w - 14;
    tip.style.left = Math.max(0, left) + "px";
    tip.style.top = c.chartArea.top + 4 + "px";
  }

  function shortName(label) {
    return label.replace(" (manufacturer)", "").replace("Manufacturer store", "Maker");
  }

  function build() {
    const canvas = document.getElementById("history-chart");
    const i0 = startIndex(current);
    const height = canvas.parentNode.clientHeight;
    const labelsOn = wantEndLabels(H.datasets, i0, height);
    const surface = cssVar("--surface-1");
    const datasets = H.datasets.map((d) => {
      const color = cssVar("--series-" + d.slot) || "#888";
      return {
        label: d.label,
        _short: shortName(d.label),
        data: d.data.slice(i0),
        _stock: d.stock.slice(i0),
        borderColor: color,
        backgroundColor: color,
        borderWidth: 2,
        borderDash: d.condition !== "new" ? [5, 4] : [],
        borderJoinStyle: "round",
        borderCapStyle: "round",
        pointRadius: d.manual ? 4 : 0,
        pointHoverRadius: 4,
        pointHitRadius: 8,
        pointBorderColor: surface,
        pointBorderWidth: 2,
        stepped: d.manual ? false : "after",
        spanGaps: d.manual,
        hidden: d.hidden,
      };
    });
    if (chart) chart.destroy();
    chart = new Chart(canvas, {
      type: "line",
      data: { labels: H.labels.slice(i0), datasets },
      plugins: [crosshair, endLabels],
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: "index", intersect: false },
        layout: { padding: { right: labelsOn ? 92 : 6, top: 2 } },
        scales: {
          x: {
            grid: { display: false },
            border: { color: cssVar("--border") },
            ticks: {
              color: cssVar("--text-muted"),
              maxRotation: 0,
              autoSkip: true,
              maxTicksLimit: window.innerWidth < 640 ? 4 : 7,
              callback(value) { return fmtDate(this.getLabelForValue(value)); },
            },
          },
          y: {
            grid: { color: cssVar("--grid") },
            border: { display: false },
            ticks: { color: cssVar("--text-muted"), maxTicksLimit: 6, callback: (v) => axisMoney(v) },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: { enabled: false, external: externalTooltip },
          endLabels: { enabled: labelsOn },
        },
      },
    });
  }

  // HTML legend: line keys (dashed for renewed/used), click to show/hide a retailer.
  function renderLegend() {
    const box = document.getElementById("chart-legend");
    if (!box) return;
    box.textContent = "";
    H.datasets.forEach((d, i) => {
      const b = el("button", "legend-item");
      b.type = "button";
      b.setAttribute("aria-pressed", String(!d.hidden));
      const k = el("span", "legend-key" + (d.condition !== "new" ? " dashed" : ""));
      k.setAttribute("aria-hidden", "true");
      k.style.setProperty("--key", `var(--series-${d.slot})`);
      b.append(k, document.createTextNode(d.label));
      b.addEventListener("click", () => {
        d.hidden = !d.hidden;
        build();
        renderLegend();
        renderDaily(current);
      });
      box.append(b);
    });
  }

  function select(key) {
    current = key;
    document.querySelectorAll("[data-window]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.window === key)));
    build();
    renderSignal(key);
    renderStats(key);
    renderDaily(key);
  }

  document.querySelectorAll("[data-window]").forEach((b) => b.addEventListener("click", () => select(b.dataset.window)));
  document.getElementById("history-chart").addEventListener("mouseleave", () => { document.getElementById("chart-tip").hidden = true; });
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  if (mq.addEventListener) mq.addEventListener("change", build);
  let resizeTimer, lastWidth = window.innerWidth;
  window.addEventListener("resize", () => {
    if (window.innerWidth === lastWidth) return;  // ignore mobile toolbar show/hide
    lastWidth = window.innerWidth;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(build, 200);
  });
  renderLegend();
  select(current);
})();
