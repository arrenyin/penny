const $ = (id) => document.getElementById(id);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
if (window["chartjs-plugin-annotation"]) Chart.register(window["chartjs-plugin-annotation"]);

let data = null, chat = [], charts = {}, windowYears = 5;

// Big market events, marked on every time-based chart.
const EVENTS = [
  { label: "COVID crash", from: "2020-02-01", to: "2020-04-01", year: 2020 },
  { label: "Rate hikes", from: "2022-03-01", to: "2023-08-01", year: 2022 },
  { label: "AI boom begins", from: "2022-12-01", to: "2022-12-01", year: 2023 },
  { label: "Tariff shock", from: "2025-04-01", to: "2025-05-01", year: 2025 },
];

// ---------- formatting ----------
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
const sym = () => (0).toLocaleString("en-US", { style: "currency", currency: data?.currency || "USD" }).replace(/[\d.,\s]/g, "");
function money(v, short) {
  if (v == null) return "—";
  const a = Math.abs(v), sign = v < 0 ? "-" : "";
  const units = short ? [[1e12, "T"], [1e9, "B"], [1e6, "M"]] : [[1e12, " trillion"], [1e9, " billion"], [1e6, " million"]];
  for (const [n, s] of units) if (a >= n) return `${sign}${sym()}${(a / n).toFixed(a / n >= 100 ? 0 : 1)}${s}`;
  return `${sign}${sym()}${a.toLocaleString("en-US", { maximumFractionDigits: a < 10 ? 2 : 0 })}`;
}
const price = (v) => v == null ? "—" : v.toLocaleString("en-US", { style: "currency", currency: data?.currency || "USD" });
const pct = (v, signed, digits = 1) => {
  if (v == null) return "—";
  const s = (v * 100).toFixed(digits);
  if (+s === 0) return (0).toFixed(digits) + "%";
  return `${signed && v > 0 ? "+" : ""}${s}%`;
};
function fmt(v, f) {
  if (v == null) return "—";
  if (f === "x") return v.toFixed(1) + "×";
  if (f === "pct") return pct(v);
  if (f === "years") return v <= 0 ? "Net cash" : v.toFixed(1) + " yrs";
  return String(v);
}
const ICONS = { good: "✓", ok: "•", caution: "!" };
function badge(tone, text) { return el("span", `badge ${tone}`, `${ICONS[tone]} ${text}`); }

// "higher / about the same / lower", and whether that's good news given which direction is better.
function compare(today, ref, better) {
  if (today == null || ref == null) return null;
  let dir;
  if (today <= 0 && ref <= 0) dir = "same";  // e.g. both net cash
  else { const rel = (today - ref) / Math.abs(ref || 1e-9); dir = Math.abs(rel) < 0.1 ? "same" : today > ref ? "higher" : "lower"; }
  const tone = dir === "same" || !better ? "ok" : (dir === "higher") === (better === "higher") ? "good" : "caution";
  return { dir, tone };
}

// ---------- loading ----------
function showError(msg) { $("err").innerHTML = ""; if (msg) $("err").appendChild(el("div", "error", msg)); }

async function load(ticker) {
  ticker = ticker.trim().toUpperCase();
  if (!ticker) return;
  $("ticker").value = ticker; $("go").disabled = true; showError("");
  $("go").textContent = "Loading…"; $("loading").hidden = false;
  try {
    const res = await fetch(`/api/stock/${encodeURIComponent(ticker)}`);
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || "Request failed");
    data = body; chat = [];
    resetPenny();
    renderAll();
  } catch (e) { showError(e.message); }
  $("go").disabled = false; $("go").textContent = "Look it up"; $("loading").hidden = true;
}

function renderAll() {
  $("empty").hidden = true; $("report").hidden = false;
  renderHeader(); renderScorecard(); renderGrowth(); renderCharts(); renderPeers(); setupCalculator(); renderProjection();
  renderFooter();
}

function renderHeader() {
  const d = data;
  $("name").textContent = `${d.name} (${d.ticker})`;
  $("meta").textContent = [d.sector, d.industry, d.market_cap ? `Worth ${money(d.market_cap)}` : null].filter(Boolean).join(" · ");
  $("price").textContent = price(d.price);
  const p = d.prices, yearAgo = p[p.length - 13];
  const a = d.analyst;
  $("delta").textContent = [yearAgo ? `${pct(d.price / yearAgo.close - 1, true)} over the past year` : null,
    a.target_mean ? `Analysts' average target ${price(a.target_mean)}` : null].filter(Boolean).join(" · ");
}

// ---------- scorecard ----------
function sparkline(values) {
  const pts = values.map((v, i) => [i, v]).filter(([, v]) => v != null);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 92 30"); svg.setAttribute("class", "spark"); svg.setAttribute("aria-hidden", "true");
  if (pts.length < 2) return svg;
  const ys = pts.map(([, v]) => v), lo = Math.min(...ys), hi = Math.max(...ys), span = hi - lo || 1;
  const n = values.length - 1 || 1;
  const xy = pts.map(([i, v]) => [4 + (i / n) * 84, 26 - ((v - lo) / span) * 22]);
  const line = document.createElementNS(svg.namespaceURI, "polyline");
  line.setAttribute("points", xy.map((p) => p.join(",")).join(" "));
  line.setAttribute("fill", "none"); line.setAttribute("stroke", css("--series-1")); line.setAttribute("stroke-width", "2");
  line.setAttribute("stroke-linejoin", "round"); line.setAttribute("stroke-linecap", "round");
  const dot = document.createElementNS(svg.namespaceURI, "circle");
  const [lx, ly] = xy[xy.length - 1];
  dot.setAttribute("cx", lx); dot.setAttribute("cy", ly); dot.setAttribute("r", "3"); dot.setAttribute("fill", css("--series-1"));
  svg.append(line, dot);
  return svg;
}

function renderScorecard() {
  const d = data, n = windowYears, usual = d.usual[n] || {}, peers = d.peers.median || {};
  const recent = d.yearly.slice(-n);
  $("nohistory").hidden = d.has_history;
  $("scoreHint").textContent = `"Today" compares today's price with the latest full year of results (for fast-growing companies this can look higher than "last 12 months" figures elsewhere). ` +
    `"Usual" is the middle value of the last ${n} years for this company. ` +
    `"Peers" is the middle value for ${d.peers.companies.length} competitors. The little line shows the last ${n} years.`;
  $("scorecard").innerHTML = "";
  const groups = {};
  for (const r of d.ratios) (groups[r.group] ||= []).push(r);
  for (const [group, rows] of Object.entries(groups)) {
    const card = el("section", "card score-group");
    card.style.padding = "0";
    card.appendChild(el("h3", null, group));
    const head = el("div", "score-head");
    head.append(el("span", null, ""), el("span", null, "Today"), el("span", null, `Usual (${n}y)`), el("span", null, "Peers"), el("span", null, "Trend"));
    card.appendChild(head);
    for (const r of rows) {
      const today = d.today[r.key];
      const row = el("div", "score-row");
      const label = el("div");
      const name = el("div", "name", r.plain + " "); name.appendChild(el("span", "term", r.term));
      label.append(name, el("div", "explain", r.explain));
      const spark = el("div", "spark-cell"); spark.appendChild(sparkline(recent.map((y) => y[r.key])));
      row.append(label, el("div", "num today", fmt(today, r.fmt)), el("div", "num", fmt(usual[r.key], r.fmt)),
                 el("div", "num", fmt(peers[r.key], r.fmt)), spark);
      const reads = el("div", "reads");
      const vsUsual = compare(today, usual[r.key], r.better), vsPeers = compare(today, peers[r.key], r.better);
      if (vsUsual) reads.appendChild(badge(vsUsual.tone, vsUsual.dir === "same" ? "About its usual" : `${cap(vsUsual.dir)} than its usual`));
      if (vsPeers) reads.appendChild(badge(vsPeers.tone, vsPeers.dir === "same" ? "In line with peers" : `${cap(vsPeers.dir)} than peers`));
      if (reads.children.length) row.appendChild(reads);
      card.appendChild(row);
    }
    $("scorecard").appendChild(card);
  }
}
const cap = (s) => s[0].toUpperCase() + s.slice(1);

// ---------- growth ----------
function renderGrowth() {
  const d = data, t = $("growth");
  t.innerHTML = "";
  const head = t.insertRow();
  head.appendChild(el("th", null, ""));
  for (const n of [3, 5, 10]) { const th = el("th", n === windowYears ? "sel" : null, `Last ${n} years`); head.appendChild(th); }
  head.appendChild(el("th", null, "Latest year"));
  const latest = d.yearly[d.yearly.length - 1] || {};
  for (const g of d.growth_items) {
    const r = t.insertRow();
    const name = el("td", null, g.plain); name.appendChild(el("small", null, g.term));
    r.appendChild(name);
    for (const n of [3, 5, 10]) {
      const v = d.growth[n]?.[g.key];
      const good = g.key === "shares" ? v != null && v < -0.002 : v != null && v > 0.002;
      const td = el("td", n === windowYears ? "sel" : null, v == null ? "—" : `${pct(v, true)} / yr`);
      if (v != null && Math.abs(v) > 0.002) td.style.color = good ? css("--good-ink") : css("--caution-ink");
      r.appendChild(td);
    }
    const lv = latest[g.key];
    r.appendChild(el("td", null, lv == null ? "—" : g.key === "shares" ? (lv / 1e9).toFixed(2) + "B" : ["eps", "fcf_ps", "dividends_per_share"].includes(g.key) ? price(lv) : money(lv)));
  }
}

// ---------- charts ----------
function chartDefaults() {
  Chart.defaults.color = css("--text-2");
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.font.size = 14;
  return {
    tooltip: { backgroundColor: css("--text"), titleColor: css("--surface"), bodyColor: css("--surface"), padding: 12, cornerRadius: 8, boxPadding: 4,
               titleFont: { size: 15 }, bodyFont: { size: 15 } },
    axis: { grid: { color: css("--grid") }, border: { display: false } },
  };
}

function yearEvents(labels) {
  const ann = {};
  for (const ev of EVENTS) {
    const i = labels.indexOf(String(ev.year));
    if (i < 0) continue;
    ann[ev.label] = { type: "box", xMin: i - 0.5, xMax: i + 0.5, backgroundColor: css("--event"), borderWidth: 0,
      label: { display: true, content: ev.label, position: { x: "center", y: "start" }, color: css("--muted"), font: { size: 12, weight: "normal" } } };
  }
  return ann;
}

function dateEvents(labels) {
  const ann = {};
  const idx = (d) => labels.findIndex((l) => l >= d);
  for (const ev of EVENTS) {
    const a = idx(ev.from), b = idx(ev.to);
    if (a < 0) continue;
    ann[ev.label] = a === b
      ? { type: "line", xMin: a, xMax: a, borderColor: css("--event-line"), borderWidth: 1, borderDash: [4, 4],
          label: { display: true, content: ev.label, position: "start", backgroundColor: "transparent", color: css("--muted"), font: { size: 12, weight: "normal" } } }
      : { type: "box", xMin: a, xMax: b < 0 ? labels.length - 1 : b, backgroundColor: css("--event"), borderWidth: 0,
          label: { display: true, content: ev.label, position: { x: "center", y: "start" }, color: css("--muted"), font: { size: 12, weight: "normal" } } };
  }
  return ann;
}

function makeChart(id, config) {
  charts[id]?.destroy();
  charts[id] = new Chart($(id), config);
}

const fyLabel = (y) => String(y.year);

function chartCard(id, title, hint) {
  const card = el("div", "card");
  card.append(el("h3", null, title), el("p", "hint", hint));
  const box = el("div", "chart-box"); const c = el("canvas"); c.id = id; c.setAttribute("role", "img"); c.setAttribute("aria-label", title);
  box.appendChild(c); card.appendChild(box);
  $("charts").appendChild(card);
}

function renderCharts() {
  const d = data, { tooltip, axis } = chartDefaults();
  Object.values(charts).forEach((c) => c.destroy()); charts = {};
  $("charts").innerHTML = "";
  const months = Math.min(d.prices.length, windowYears * 12 + 1);
  const prices = d.prices.slice(-months);

  chartCard("cPrice", "Share price", `Month by month over the last ${windowYears} years.`);
  const pl = prices.map((p) => p.date);
  makeChart("cPrice", { type: "line",
    data: { labels: pl, datasets: [{ label: "Price", data: prices.map((p) => p.close), borderColor: css("--series-1"), borderWidth: 2, pointRadius: 0, pointHoverRadius: 6, tension: 0.15 }] },
    options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: false }, annotation: { annotations: dateEvents(pl) },
        tooltip: { ...tooltip, callbacks: { title: (it) => new Date(it[0].label).toLocaleDateString("en-US", { month: "long", year: "numeric" }), label: (c) => " " + price(c.parsed.y) } } },
      scales: { x: { ...axis, grid: { display: false }, ticks: { maxTicksLimit: 7, callback(v) { return this.getLabelForValue(v).slice(0, 4); } } },
                y: { ...axis, ticks: { callback: (v) => price(v).replace(/\.00$/, "") } } } } });

  if (!d.has_history) return;
  const ys = d.yearly.slice(-windowYears - 0);
  const labels = ys.map(fyLabel);
  const ann = yearEvents(labels);
  const bars = (sets, fmtFn) => ({ type: "bar",
    data: { labels, datasets: sets.map(([key, label], i) => ({ label, data: ys.map((y) => y[key]), backgroundColor: css(`--series-${i + 1}`),
      borderRadius: { topLeft: 4, topRight: 4 }, borderSkipped: "bottom", categoryPercentage: 0.75, barPercentage: 0.9 })) },
    options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: sets.length > 1, position: "top", align: "start", labels: { boxWidth: 12, boxHeight: 12 } }, annotation: { annotations: ann },
        tooltip: { ...tooltip, callbacks: { label: (c) => ` ${c.dataset.label}: ${fmtFn(c.parsed.y)}` } } },
      scales: { x: { ...axis, grid: { display: false } }, y: { ...axis, ticks: { callback: (v) => fmtFn(v, true) } } } } });
  const lines = (sets, fmtFn, extra = {}) => ({ type: "line",
    data: { labels, datasets: sets.map(([key, label], i) => ({ label, data: ys.map((y) => y[key]), borderColor: css(`--series-${i + 1}`), backgroundColor: css(`--series-${i + 1}`),
      borderWidth: 2, pointRadius: 3, pointHoverRadius: 6, spanGaps: true })) },
    options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: sets.length > 1, position: "top", align: "start", labels: { boxWidth: 12, boxHeight: 12 } },
        annotation: { annotations: { ...ann, ...extra } },
        tooltip: { ...tooltip, callbacks: { label: (c) => ` ${c.dataset.label}: ${fmtFn(c.parsed.y)}` } } },
      scales: { x: { ...axis, grid: { display: false } }, y: { ...axis, ticks: { callback: (v) => fmtFn(v) } } } } });

  chartCard("cMoney", "Sales, profit and cash left over", "Fiscal years. Bars that grow over time are a healthy sign.");
  makeChart("cMoney", bars([["revenue", "Sales"], ["net_income", "Profit"], ["fcf", "Cash left over"]], (v, s) => money(v, true)));

  chartCard("cPerShare", "Per share: profit and cash left over", "What each single share earned. This is what matters to you as an owner.");
  makeChart("cPerShare", bars([["eps", "Profit per share"], ["fcf_ps", "Cash per share"]], (v) => v == null ? "—" : price(v)));

  chartCard("cMargins", "Profit margins", "Out of each $1 of sales, how much is kept at each stage. Steady or rising lines are a good sign.");
  makeChart("cMargins", lines([["gross_margin", "After making the product"], ["op_margin", "After running costs"], ["net_margin", "Final profit"]], (v) => pct(v, false, 0)));

  const usualPE = d.usual[windowYears]?.pe;
  chartCard("cPE", "Price vs. profit (P/E) over time", "Each year's P/E at the fiscal year end. The dashed line is its usual level for the period.");
  makeChart("cPE", lines([["pe", "P/E"]], (v) => v == null ? "—" : v.toFixed(0) + "×", usualPE ? {
    usual: { type: "line", yMin: usualPE, yMax: usualPE, borderColor: css("--series-2"), borderWidth: 2, borderDash: [6, 4],
      label: { display: true, content: `Usual ${usualPE.toFixed(0)}×`, position: "end", backgroundColor: css("--series-2"), color: "#fff", font: { size: 12 } } } } : {}));

  chartCard("cRoic", "Return on money invested (ROIC)", "Profit earned on all the money put into the business. Consistently above about 10% is usually a sign of a strong business.");
  makeChart("cRoic", lines([["roic", "ROIC"]], (v) => pct(v, false, 0)));

  chartCard("cShares", "Number of shares", "Falling means the company is buying back shares, so each remaining share owns more of the business.");
  makeChart("cShares", bars([["shares", "Shares"]], (v) => v == null ? "—" : (v / 1e9).toFixed(2) + "B"));

  chartCard("cDiv", "Dividend per share", "Cash paid to shareholders for each share, per year.");
  makeChart("cDiv", bars([["dividends_per_share", "Dividend per share"]], (v) => v == null ? "—" : price(v)));
}

// ---------- peers ----------
function renderPeers() {
  const d = data, t = $("peers"), cols = [["pe", "P/E", "x"], ["ps", "Price / Sales", "x"], ["pfcf", "Price / Cash left over", "x"],
    ["op_margin", "Operating margin", "pct"], ["net_margin", "Net margin", "pct"], ["div_yield", "Dividend", "pct"]];
  $("peerHint").textContent = d.peers.companies.length
    ? `The largest US-listed companies by sales in the same industry (${d.industry || "same SEC industry code"}), using each company's latest full year of results.`
    : "Couldn't find competitors for this company.";
  t.innerHTML = "";
  const head = t.insertRow();
  head.appendChild(el("th", null, "Company")); head.appendChild(el("th", null, "Worth"));
  cols.forEach(([, label]) => head.appendChild(el("th", null, label)));
  const addRow = (cls, name, sub, worth, values) => {
    const r = t.insertRow(); if (cls) r.className = cls;
    const td = el("td", null, name); if (sub) td.appendChild(el("small", null, sub)); r.appendChild(td);
    r.appendChild(el("td", null, worth == null ? "" : money(worth, true)));
    cols.forEach(([k, , f]) => r.appendChild(el("td", null, fmt(values[k], f))));
  };
  addRow("self", d.ticker, d.name, d.market_cap, d.today);
  for (const p of d.peers.companies) addRow(null, p.ticker, p.name, p.market_cap, p.values);
  if (d.peers.companies.length > 1) addRow("median", "Peer middle value", null, null, d.peers.median);
}

// ---------- return calculator ----------
function setupCalculator() {
  const first = data.prices[0].date.slice(0, 4), last = new Date().getFullYear();
  const opts = [1, 3, 5, 10, 20, 30].filter((n) => last - n >= +first);
  $("since").innerHTML = "";
  for (const n of opts) { const o = el("option", null, `${n} year${n > 1 ? "s" : ""} ago (${last - n})`); o.value = n; if (n === Math.min(10, opts[opts.length - 1])) o.selected = true; $("since").appendChild(o); }
  renderCalculator();
}

// Money-weighted yearly return (IRR) for monthly cash flows, by bisection.
function irr(flows) {
  const npv = (r) => flows.reduce((s, f, i) => s + f / Math.pow(1 + r, i), 0);
  let lo = -0.99, hi = 1;
  if (npv(lo) * npv(hi) > 0) return null;
  for (let k = 0; k < 100; k++) { const mid = (lo + hi) / 2; (npv(lo) * npv(mid) <= 0) ? (hi = mid) : (lo = mid); }
  return Math.pow(1 + (lo + hi) / 2, 12) - 1;
}

function simulate(series, amount, monthly) {
  let units = 0, invested = 0; const values = [], flows = [];
  series.forEach((p, i) => {
    const add = i === 0 ? amount : monthly;
    units += add / p.total; invested += add;
    values.push(units * p.total); flows.push(-add);
  });
  flows[flows.length - 1] += values[values.length - 1];
  return { values, invested, final: values[values.length - 1], yearly: irr(flows) };
}

function renderCalculator() {
  const years = +$("since").value, amount = Math.max(0, +$("amount").value || 0), monthly = Math.max(0, +$("monthly").value || 0);
  const bench = new Map(data.benchmark.map((p) => [p.date, p.total]));
  const series = data.prices.filter((p) => bench.has(p.date)).slice(-(years * 12 + 1));
  if (series.length < 2 || amount + monthly === 0) return;
  const stock = simulate(series, amount, monthly);
  const spy = simulate(series.map((p) => ({ date: p.date, total: bench.get(p.date) })), amount, monthly);
  const tiles = [
    [`In ${data.ticker}`, money(stock.final), `${pct(stock.final / stock.invested - 1, true, 0)} total · ${pct(stock.yearly, true)} a year`],
    ["In the S&P 500", money(spy.final), `${pct(spy.final / spy.invested - 1, true, 0)} total · ${pct(spy.yearly, true)} a year`],
    ["You'd have put in", money(stock.invested), `Since ${new Date(series[0].date).toLocaleDateString("en-US", { month: "short", year: "numeric" })}`],
    [stock.final >= spy.final ? `${data.ticker} beat the market by` : `${data.ticker} trailed the market by`, money(Math.abs(stock.final - spy.final)), ""],
  ];
  $("calcTiles").innerHTML = "";
  for (const [k, v, s] of tiles) { const t = el("div", "tile"); t.append(el("div", "k", k), el("div", "v", v), el("div", "s", s)); $("calcTiles").appendChild(t); }

  const { tooltip, axis } = chartDefaults(), labels = series.map((p) => p.date);
  const contributed = series.map((_, i) => amount + monthly * i);
  const datasets = [
    { label: data.ticker, data: stock.values, borderColor: css("--series-1"), backgroundColor: css("--series-1"), borderWidth: 2, pointRadius: 0, pointHoverRadius: 6 },
    { label: "S&P 500", data: spy.values, borderColor: css("--series-2"), backgroundColor: css("--series-2"), borderWidth: 2, pointRadius: 0, pointHoverRadius: 6 },
    { label: "Money you put in", data: contributed, borderColor: css("--muted"), backgroundColor: css("--muted"), borderWidth: 1.5, borderDash: [5, 4], pointRadius: 0 },
  ];
  makeChart("calcChart", { type: "line", data: { labels, datasets },
    options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { position: "top", align: "start", labels: { boxWidth: 12, boxHeight: 12 } }, annotation: { annotations: dateEvents(labels) },
        tooltip: { ...tooltip, callbacks: { title: (it) => new Date(it[0].label).toLocaleDateString("en-US", { month: "long", year: "numeric" }), label: (c) => ` ${c.dataset.label}: ${money(c.parsed.y)}` } } },
      scales: { x: { ...axis, grid: { display: false }, ticks: { maxTicksLimit: 8, callback(v) { return this.getLabelForValue(v).slice(0, 4); } } },
                y: { ...axis, ticks: { callback: (v) => money(v, true) } } } } });
}

// ---------- rough look ahead ----------
function monthlyStats(series) {
  const r = [];
  for (let i = 1; i < series.length; i++) r.push(Math.log(series[i] / series[i - 1]));
  const mean = r.reduce((a, b) => a + b, 0) / r.length;
  const sd = Math.sqrt(r.reduce((a, b) => a + (b - mean) ** 2, 0) / (r.length - 1));
  return { mean, sd };
}

function randn(rng) { let u = 0, v = 0; while (u === 0) u = rng(); v = rng(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); }
function seeded(seed) { return () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; }; }

// Many random futures with the given monthly drift and bumpiness; returns the 10th/50th/90th percentile path.
// stepMonths < 1 gives a finer path (e.g. weekly) for short horizons; drift, bumpiness and additions scale to the step.
function fan(meanLog, sd, steps, stepMonths, amount, monthly, paths = 2000) {
  const rng = seeded(42), finals = Array.from({ length: steps + 1 }, () => new Float64Array(paths));
  const mu = meanLog * stepMonths, s = sd * Math.sqrt(stepMonths), add = monthly * stepMonths;
  for (let p = 0; p < paths; p++) {
    let v = amount; finals[0][p] = v;
    for (let m = 1; m <= steps; m++) { v = v * Math.exp(mu + s * randn(rng)) + add; finals[m][p] = v; }
  }
  const q = (arr, f) => { const s = Array.from(arr).sort((a, b) => a - b); return s[Math.floor(f * (s.length - 1))]; };
  return { p10: finals.map((a) => q(a, 0.1)), p50: finals.map((a) => q(a, 0.5)), p90: finals.map((a) => q(a, 0.9)) };
}

function renderProjection() {
  const months = +$("years").value, assume = $("assume").value;
  // Weekly steps for horizons up to 6 months so short charts still have a shape; monthly beyond that.
  const stepMonths = months <= 6 ? 12 / 52 : 1, steps = Math.round(months / stepMonths);
  const horizon = months < 12 ? `${months} month${months > 1 ? "s" : ""}` : `${months / 12} year${months > 12 ? "s" : ""}`;
  const amount = Math.max(0, +$("amount").value || 0), monthly = Math.max(0, +$("monthly").value || 0);
  if (amount + monthly === 0) return;
  const bench = new Map(data.benchmark.map((p) => [p.date, p.total]));
  const tenYears = data.prices.slice(-121);
  const s = monthlyStats(tenYears.map((p) => p.total));
  const m = monthlyStats(tenYears.filter((p) => bench.has(p.date)).map((p) => bench.get(p.date)));
  const target = { market: 0.10, cautious: 0.05 }[assume];
  // Drift chosen so the typical (middle) path grows at the target rate; "own" uses the stock's actual past drift.
  const drift = (stats) => target == null ? stats.mean : Math.log(1 + target) / 12;
  const stock = fan(drift(s), s.sd, steps, stepMonths, amount, monthly), spy = fan(drift(m), m.sd, steps, stepMonths, amount, monthly);
  const invested = amount + monthly * months;

  $("projNote").textContent = `${data.ticker} has been ${(s.sd / m.sd).toFixed(1)}× as bumpy as the S&P 500 over the past 10 years` +
    (assume === "own" ? `, and grew about ${pct(Math.exp(s.mean * 12) - 1)} a year. Repeating that is far from certain.` : ".") +
    " A bumpier stock gives a wider range of outcomes, both up and down.";
  const last = steps;
  const tiles = [
    ["Middle outcome", money(stock.p50[last]), `S&P 500 middle outcome: ${money(spy.p50[last])}`],
    ["Bad case (1 in 10 worse)", money(stock.p10[last]), `S&P 500: ${money(spy.p10[last])}`],
    ["Good case (1 in 10 better)", money(stock.p90[last]), `S&P 500: ${money(spy.p90[last])}`],
    ["You'd put in", money(invested), `Over ${horizon}`],
  ];
  $("projTiles").innerHTML = "";
  for (const [k, v, sub] of tiles) { const t = el("div", "tile"); t.append(el("div", "k", k), el("div", "v", v), el("div", "s", sub)); $("projTiles").appendChild(t); }

  const { tooltip, axis } = chartDefaults();
  const start = new Date(), labels = Array.from({ length: steps + 1 }, (_, i) => {
    const d = new Date(start);
    if (stepMonths < 1) d.setDate(d.getDate() + 7 * i); else d.setMonth(d.getMonth() + i);
    return d.toISOString().slice(0, 10);
  });
  const short = months <= 12;
  const tickLabel = (iso) => new Date(iso + "T12:00").toLocaleDateString("en-US", short ? { month: "short", day: stepMonths < 1 ? "numeric" : undefined } : months > 60 ? { year: "numeric" } : { month: "short", year: "numeric" });
  const band = css("--series-1");
  makeChart("projChart", { type: "line",
    data: { labels, datasets: [
      { label: "Likely range (8 in 10)", data: stock.p90, borderColor: "transparent", pointRadius: 0, fill: "+1", backgroundColor: band + "26" },
      { label: "Bad case", data: stock.p10, borderColor: "transparent", pointRadius: 0, fill: false },
      { label: `${data.ticker} middle outcome`, data: stock.p50, borderColor: band, backgroundColor: band, borderWidth: 2, pointRadius: 0, pointHoverRadius: 6 },
      { label: "S&P 500 middle outcome", data: spy.p50, borderColor: css("--series-2"), backgroundColor: css("--series-2"), borderWidth: 2, borderDash: [6, 4], pointRadius: 0, pointHoverRadius: 6 },
    ] },
    options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { position: "top", align: "start", labels: { boxWidth: 12, boxHeight: 12, filter: (it) => !["Bad case"].includes(it.text) } },
        tooltip: { ...tooltip, callbacks: { title: (it) => new Date(it[0].label + "T12:00").toLocaleDateString("en-US", { month: "long", day: stepMonths < 1 ? "numeric" : undefined, year: "numeric" }), label: (c) => ` ${c.dataset.label}: ${money(c.parsed.y)}` } } },
      scales: { x: { ...axis, grid: { display: false }, ticks: { maxTicksLimit: 8, callback(v) { return tickLabel(this.getLabelForValue(v)); } } },
                y: { ...axis, ticks: { callback: (v) => money(v, true) } } } } });
}

function renderFooter() {
  $("foot").textContent = "Yearly financials, industries and competitors from SEC filings (10-K annual reports), adjusted for stock splits. Prices from Tiingo (end of day). " +
    "Fiscal years are labeled by the year they end. Past results don't guarantee future ones. This is research about the stock, not personal financial advice.";
}

// ---------- Penny ----------
const VERDICTS = { undervalued: ["--good", "▲", "Looks cheap"], fairly: ["--warning", "●", "Looks fairly priced"], overvalued: ["--critical", "▼", "Looks expensive"] };
function resetPenny() {
  $("verdict").innerHTML = ""; $("analysis").innerHTML = ""; $("qa").innerHTML = ""; $("qa").hidden = true;
  $("ask").hidden = true; $("summarize").hidden = false; $("summarize").disabled = false;
}
function renderAnalysis(md) {
  const m = md.match(/^\s*\**VERDICT:\**\s*([^\n(]+)(\([^)]*\))?[^\n]*\n?/i);
  if (m) {
    const label = m[1].replace(/\*/g, "").replace(/[,;.\s]+$/, "").trim();
    const key = Object.keys(VERDICTS).find((k) => label.toLowerCase().includes(k));
    const [color, icon, plain] = VERDICTS[key] || ["--muted", "●", label];
    const v = el("div", "verdict"); v.style.setProperty("--v", `var(${color})`);
    const conf = m[2] ? m[2].replace(/[()]/g, "").replace(/confidence:\s*/i, "") : "";
    v.append(el("span", "icon", icon), el("span", null, plain), el("small", null, `(${label.toLowerCase()}${conf ? ", " + conf + " confidence" : ""})`));
    $("verdict").innerHTML = ""; $("verdict").appendChild(v);
    md = md.slice(m[0].length);
  }
  $("analysis").innerHTML = DOMPurify.sanitize(marked.parse(md));
}

async function askPenny(question, first) {
  $("askBtn").disabled = true; $("summarize").disabled = true;
  chat.push({ role: "user", content: question });
  let target;
  if (first) { target = $("analysis"); target.innerHTML = '<p class="hint">Penny is reading the numbers…</p>'; }
  else { $("qa").hidden = false; target = el("div"); target.innerHTML = '<p class="hint">Thinking…</p>'; $("qa").append(el("div", "q", question), target); }
  let reply = "", buf = "", failed = false;
  const fail = (msg) => { failed = true; target.innerHTML = ""; target.appendChild(el("div", "error", msg)); };
  try {
    const res = await fetch("/api/analyze", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ticker: data.ticker, messages: chat }) });
    const reader = res.body.getReader(), dec = new TextDecoder();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const events = buf.split("\n\n"); buf = events.pop();
      for (const ev of events) {
        const raw = ev.replace(/^data: /, "");
        if (raw === "[DONE]") continue;
        const msg = JSON.parse(raw);
        if (msg.error) fail(msg.error);
        else { reply += msg.text; first ? renderAnalysis(reply) : (target.innerHTML = DOMPurify.sanitize(marked.parse(reply))); }
      }
    }
  } catch (e) { fail("Connection error: " + e.message); }
  if (failed || !reply) chat.pop(); else chat.push({ role: "assistant", content: reply });
  $("askBtn").disabled = false;
  if (first) { $("summarize").hidden = !failed; $("summarize").disabled = false; $("ask").hidden = failed; }
}

// ---------- events ----------
$("search").addEventListener("submit", (e) => { e.preventDefault(); load($("ticker").value); });
document.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => load(c.dataset.t)));
$("window").addEventListener("click", (e) => {
  const n = +e.target.dataset.n; if (!n) return;
  windowYears = n;
  document.querySelectorAll("#window button").forEach((b) => b.classList.toggle("on", +b.dataset.n === n));
  if (data) { renderScorecard(); renderGrowth(); renderCharts(); }
});
$("summarize").addEventListener("click", () => data && askPenny(`Summarize what ${data.ticker}'s numbers show, and whether the price of ${data.price} looks high or low for the business.`, true));
$("ask").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("question").value.trim();
  if (!q || !data || $("askBtn").disabled) return;
  $("question").value = ""; askPenny(q, false);
});
["amount", "monthly", "since"].forEach((id) => $(id).addEventListener("input", () => { if (data) { renderCalculator(); renderProjection(); } }));
["years", "assume"].forEach((id) => $(id).addEventListener("input", () => data && renderProjection()));
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => data && renderAll());
