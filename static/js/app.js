/* ── Valuation Chart Tool — frontend logic ─────────────────────────── */
"use strict";

const API = {
  chart:   (t, m, p) => `/api/valuation/chart?ticker=${encodeURIComponent(t)}&metric=${encodeURIComponent(m)}&period=${encodeURIComponent(p)}`,
  compare: (ts, m, p) => `/api/valuation/compare?tickers=${encodeURIComponent(ts)}&metric=${encodeURIComponent(m)}&period=${encodeURIComponent(p)}`,
  wlList:  () => `/api/watchlist`,
  wlAdd:   () => `/api/watchlist/add`,
  wlDel:   (t) => `/api/watchlist/remove/${t}`,
  wlRank:  (m, p) => `/api/watchlist/ranked?metric=${encodeURIComponent(m)}&period=${encodeURIComponent(p)}`,
};

const PLOTLY_CFG = { responsive: true, displayModeBar: false };

// Mirrors the server-side _TICKER_RE in data_fetcher.py
const TICKER_RE = /^[A-Z0-9.\-]{1,10}$/;

// Mirrors VALID_METRICS in data_fetcher.py
const VALID_METRICS = new Set(["trailing_pe", "price_sales"]);

// Mirrors VALID_PERIODS in data_fetcher.py
const VALID_PERIODS = new Set(["1y", "2y", "3y", "5y", "10y"]);

function validateTicker(t) {
  if (!t) return "Ticker symbol cannot be empty.";
  if (!TICKER_RE.test(t)) {
    return `'${t}' is not a valid ticker symbol. Use only letters, digits, dots, or hyphens (max 10 characters).`;
  }
  return null;
}

function validateMetric(m) {
  if (!VALID_METRICS.has(m)) {
    return `'${m}' is not a supported metric. Choose from: ${[...VALID_METRICS].join(", ")}.`;
  }
  return null;
}

function validatePeriod(p) {
  if (!VALID_PERIODS.has(p)) {
    return `'${p}' is not a valid period. Choose from: ${[...VALID_PERIODS].join(", ")}.`;
  }
  return null;
}

// ── helpers ──────────────────────────────────────────────────────────────
function zColor(z) {
  if (z < -1) return "#3fb950";
  if (z > 1)  return "#f78166";
  return "#58a6ff";
}
function zBadge(z) {
  if (z < -1) return ["Cheap",     "badge-cheap"];
  if (z > 1)  return ["Expensive", "badge-expensive"];
  return ["Fair Value", "badge-neutral"];
}
function pillClass(z) {
  if (z < -1) return "cheap";
  if (z > 1)  return "expensive";
  return "neutral";
}
function fmt(v, d = 2) { return v == null ? "—" : Number(v).toFixed(d); }

async function apiFetch(url) {
  const r = await fetch(url);
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${r.status}`);
  }
  return r.json();
}

// Hard contract checks — reject degenerate payloads before any Plotly call.
function assertSinglePayload(data, symbol) {
  if (!data || typeof data !== "object")
    throw new Error(`No data returned for ${symbol}.`);
  if (typeof data.current !== "number" || !isFinite(data.current))
    throw new Error(`Invalid response for ${symbol}: missing current value.`);
  if (!data.chart)
    throw new Error(`No chart payload returned for ${symbol}.`);
  if (typeof data.metric !== "string" || !data.metric)
    throw new Error(`Response missing metric field for ${symbol}.`);
  if (typeof data.period !== "string" || !data.period)
    throw new Error(`Response missing period field for ${symbol}.`);
  if (typeof data.points !== "number" || data.points < 1)
    throw new Error(`Response has no data points for ${symbol}.`);
}

function assertComparePayload(data) {
  if (!data || typeof data !== "object")
    throw new Error("No data returned from compare endpoint.");
  if (!data.chart)
    throw new Error("No chart payload returned from compare endpoint.");
  if (!Array.isArray(data.results) || data.results.length === 0)
    throw new Error("Compare returned no results. All tickers may have failed.");
}

function showStatus(el, type, msg) {
  el.className = `status ${type}`;
  if (type === "loading") {
    el.innerHTML = `<div class="spinner"></div><span>${msg}</span>`;
  } else {
    el.textContent = msg;
  }
  el.style.display = "";
}
function hideStatus(el) { el.style.display = "none"; }

// ── NAV ───────────────────────────────────────────────────────────────────
document.querySelectorAll("nav button[data-panel]").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("nav button[data-panel]").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(btn.dataset.panel).classList.add("active");
    if (btn.dataset.panel === "panel-watchlist") loadWatchlistRanked();
  });
});

// ── SINGLE CHART ──────────────────────────────────────────────────────────
const singleForm    = document.getElementById("single-form");
const singleTicker  = document.getElementById("single-ticker");
const singleMetric  = document.getElementById("single-metric");
const singlePeriod  = document.getElementById("single-period");
const singleStatus  = document.getElementById("single-status");
const singleDebug   = document.getElementById("single-debug");
const chartDiv      = document.getElementById("chart-div");
const summaryBox    = document.getElementById("summary-box");
const summaryText   = document.getElementById("summary-text");
const statPills     = document.getElementById("stat-pills");
const zscoreBarWrap = document.getElementById("zscore-bar-wrap");

// Unconditionally destroy every piece of single-chart output.
function clearSingleChart() {
  Plotly.purge(chartDiv);
  chartDiv.innerHTML = "";
  zscoreBarWrap.style.display = "none";
  summaryBox.style.display = "none";
  summaryText.innerHTML = "";
  statPills.innerHTML = "";
  singleDebug.style.display = "none";
  singleDebug.textContent = "";
}

singleForm.addEventListener("submit", async (e) => {
  e.preventDefault();

  // Read values directly from the DOM elements at submit time.
  const ticker = singleTicker.value.trim().toUpperCase();
  const metric = singleMetric.value;
  const period = singlePeriod.value;

  // Wipe stale output unconditionally before any early-return path.
  clearSingleChart();
  hideStatus(singleStatus);

  // Client-side validation — mirrors server-side checks.
  const tickerErr = validateTicker(ticker);
  if (tickerErr) { showStatus(singleStatus, "error", tickerErr); return; }

  const metricErr = validateMetric(metric);
  if (metricErr) { showStatus(singleStatus, "error", metricErr); return; }

  const periodErr = validatePeriod(period);
  if (periodErr) { showStatus(singleStatus, "error", periodErr); return; }

  showStatus(singleStatus, "loading", `Fetching ${ticker} — ${metric} — ${period}…`);

  try {
    const url = API.chart(ticker, metric, period);
    const data = await apiFetch(url);

    // Hard contract check — never render a degenerate payload.
    assertSinglePayload(data, ticker);

    // Verify the server actually computed what was requested.
    if (data.metric !== metric) {
      throw new Error(
        `Metric mismatch: requested '${metric}' but server returned '${data.metric}'.`
      );
    }
    if (data.period !== period) {
      throw new Error(
        `Period mismatch: requested '${period}' but server returned '${data.period}'.`
      );
    }

    hideStatus(singleStatus);

    // Show debug line with exactly what was computed.
    singleDebug.textContent =
      `${data.symbol} | ${data.metric} | ${data.period} | ${data.points} pts`;
    singleDebug.style.display = "block";

    renderSingleChart(data);
  } catch (err) {
    clearSingleChart();
    showStatus(singleStatus, "error", err.message);
  }
});

function renderSingleChart(data) {
  const fig = JSON.parse(data.chart);
  Plotly.newPlot(chartDiv, fig.data, fig.layout, PLOTLY_CFG);

  const z = data.zscore;
  const [label, cls] = zBadge(z);
  const pc = pillClass(z);

  summaryText.innerHTML = `<strong>${data.symbol}</strong> — ${data.summary}`;
  statPills.innerHTML = `
    <div class="pill"><span class="pill-label">Current</span><span class="pill-val">${fmt(data.current)}x</span></div>
    <div class="pill"><span class="pill-label">Mean</span><span class="pill-val">${fmt(data.stats.mean)}x</span></div>
    <div class="pill"><span class="pill-label">+1σ</span><span class="pill-val">${fmt(data.stats.plus1)}x</span></div>
    <div class="pill"><span class="pill-label">−1σ</span><span class="pill-val">${fmt(data.stats.minus1)}x</span></div>
    <div class="pill"><span class="pill-label">Z-Score</span><span class="pill-val ${pc}">${z > 0 ? "+" : ""}${fmt(z, 2)}</span></div>
    <div class="pill"><span class="pill-label">Signal</span><span class="pill-val ${pc}">${label}</span></div>
  `;
  summaryBox.style.display = "flex";
  renderZscoreBar(z);
}

function renderZscoreBar(z) {
  const pct = ((Math.max(-3, Math.min(3, z)) + 3) / 6) * 100;
  const color = zColor(z);
  document.getElementById("zscore-marker").style.left = `${pct}%`;
  document.getElementById("zscore-marker").style.background = color;
  zscoreBarWrap.style.display = "";
}

// ── COMPARE CHART ─────────────────────────────────────────────────────────
const compareForm    = document.getElementById("compare-form");
const compareTickers = document.getElementById("compare-tickers");
const compareMetric  = document.getElementById("compare-metric");
const comparePeriod  = document.getElementById("compare-period");
const compareStatus  = document.getElementById("compare-status");
const compareDebug   = document.getElementById("compare-debug");
const compareDiv     = document.getElementById("compare-div");
const compareTable   = document.getElementById("compare-table");

function clearCompareChart() {
  Plotly.purge(compareDiv);
  compareDiv.innerHTML = "";
  compareTable.innerHTML = "";
  compareDebug.style.display = "none";
  compareDebug.textContent = "";
}

compareForm.addEventListener("submit", async (e) => {
  e.preventDefault();

  // Read values directly from the DOM elements at submit time.
  const metric = compareMetric.value;
  const period = comparePeriod.value;
  const rawTickers = compareTickers.value
    .split(",").map(t => t.trim().toUpperCase()).filter(Boolean);

  // Wipe stale output unconditionally before any early-return path.
  clearCompareChart();
  hideStatus(compareStatus);

  if (!rawTickers.length) {
    showStatus(compareStatus, "error", "Please enter at least one ticker.");
    return;
  }

  const metricErr = validateMetric(metric);
  if (metricErr) { showStatus(compareStatus, "error", metricErr); return; }

  const periodErr = validatePeriod(period);
  if (periodErr) { showStatus(compareStatus, "error", periodErr); return; }

  const tickerErrors = rawTickers.map(t => validateTicker(t)).filter(Boolean);
  if (tickerErrors.length) {
    showStatus(compareStatus, "error", tickerErrors[0]);
    return;
  }

  showStatus(compareStatus, "loading",
    `Comparing ${rawTickers.join(", ")} — ${metric} — ${period}…`);

  try {
    const url = API.compare(rawTickers.join(","), metric, period);
    const data = await apiFetch(url);

    assertComparePayload(data);

    hideStatus(compareStatus);

    if (data.errors?.length) {
      showStatus(compareStatus, "error", "Skipped: " + data.errors.join(" | "));
      compareStatus.style.display = "";
    }

    // Debug line: list all tickers, the metric, period, and per-ticker point counts.
    const ptsSummary = data.results
      .map(r => `${r.symbol}:${r.points}pts`)
      .join(", ");
    compareDebug.textContent =
      `${rawTickers.join(", ")} | ${data.metric} | ${data.period} | ${ptsSummary}`;
    compareDebug.style.display = "block";

    renderCompareChart(data);
  } catch (err) {
    clearCompareChart();
    showStatus(compareStatus, "error", err.message);
  }
});

function renderCompareChart(data) {
  const fig = JSON.parse(data.chart);
  Plotly.newPlot(compareDiv, fig.data, fig.layout, PLOTLY_CFG);

  const rows = data.results
    .slice()
    .sort((a, b) => a.zscore - b.zscore)
    .map((r, i) => {
      const [lbl, cls] = zBadge(r.zscore);
      return `
        <tr>
          <td class="rank">#${i + 1}</td>
          <td class="symbol">${r.symbol}</td>
          <td>${fmt(r.current)}x</td>
          <td>${fmt(r.stats.mean)}x</td>
          <td style="color:${zColor(r.zscore)}">${r.zscore > 0 ? "+" : ""}${fmt(r.zscore, 2)}</td>
          <td><span class="badge ${cls}">${lbl}</span></td>
        </tr>`;
    }).join("");

  compareTable.innerHTML = `
    <table class="compare-summary-table">
      <thead>
        <tr>
          <th>#</th><th>Ticker</th><th>Current</th><th>Mean</th><th>Z-Score</th><th>Signal</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// ── WATCHLIST ─────────────────────────────────────────────────────────────
const wlAddInput  = document.getElementById("wl-add-input");
const wlAddBtn    = document.getElementById("wl-add-btn");
const wlMetric    = document.getElementById("wl-metric");
const wlPeriod    = document.getElementById("wl-period");
const wlRefresh   = document.getElementById("wl-refresh");
const wlStatus    = document.getElementById("wl-status");
const wlTableBody = document.getElementById("wl-table-body");
const wlEmpty     = document.getElementById("wl-empty");

wlAddBtn.addEventListener("click", async () => {
  const t = wlAddInput.value.trim().toUpperCase();
  const tickerErr = validateTicker(t);
  if (tickerErr) { showStatus(wlStatus, "error", tickerErr); return; }
  try {
    await fetch(API.wlAdd(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticker: t }),
    });
    wlAddInput.value = "";
    await loadWatchlistRanked();
  } catch (err) {
    showStatus(wlStatus, "error", err.message);
  }
});

wlAddInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") wlAddBtn.click();
});

wlRefresh.addEventListener("click", loadWatchlistRanked);

async function removeTicker(t) {
  await fetch(API.wlDel(t), { method: "DELETE" });
  await loadWatchlistRanked();
}

async function loadWatchlistRanked() {
  wlTableBody.innerHTML = "";
  wlEmpty.style.display = "none";
  showStatus(wlStatus, "loading", "Ranking watchlist…");

  try {
    const data = await apiFetch(API.wlRank(wlMetric.value, wlPeriod.value));
    hideStatus(wlStatus);

    if (data.errors?.length) {
      showStatus(wlStatus, "error", "Skipped: " + data.errors.join(" | "));
      wlStatus.style.display = "";
    }

    if (!data.ranked.length) {
      wlEmpty.style.display = "";
      return;
    }

    wlTableBody.innerHTML = data.ranked.map((r, i) => {
      const [lbl, cls] = zBadge(r.zscore);
      const pc = pillClass(r.zscore);
      return `
        <tr>
          <td class="rank">#${i + 1}</td>
          <td class="symbol">${r.symbol}</td>
          <td>${fmt(r.current)}x</td>
          <td>${fmt(r.mean)}x</td>
          <td>${fmt(r.std)}x</td>
          <td style="color:${zColor(r.zscore)}">${r.zscore > 0 ? "+" : ""}${fmt(r.zscore, 2)}</td>
          <td><span class="badge ${cls}">${lbl}</span></td>
          <td style="font-size:12px;color:#8b949e;max-width:260px">${r.summary}</td>
          <td><button class="del-btn" onclick="removeTicker('${r.symbol}')">×</button></td>
        </tr>`;
    }).join("");
  } catch (err) {
    wlTableBody.innerHTML = "";
    showStatus(wlStatus, "error", err.message);
  }
}

// Auto-load watchlist if it's the active panel on page load
document.addEventListener("DOMContentLoaded", () => {
  const activePanel = document.querySelector(".panel.active");
  if (activePanel?.id === "panel-watchlist") loadWatchlistRanked();
});
