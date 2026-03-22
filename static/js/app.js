/* ── Valuation Chart Tool — frontend logic ─────────────────────────── */
"use strict";

const API = {
  chart:   (t, m, p) => `/api/valuation/chart?ticker=${t}&metric=${m}&period=${p}`,
  compare: (ts, m, p) => `/api/valuation/compare?tickers=${encodeURIComponent(ts)}&metric=${m}&period=${p}`,
  wlList:  () => `/api/watchlist`,
  wlAdd:   () => `/api/watchlist/add`,
  wlDel:   (t) => `/api/watchlist/remove/${t}`,
  wlRank:  (m, p) => `/api/watchlist/ranked?metric=${m}&period=${p}`,
};

const PLOTLY_CFG = { responsive: true, displayModeBar: false };

// Mirrors the server-side _TICKER_RE in data_fetcher.py
const TICKER_RE = /^[A-Z0-9.\-]{1,10}$/;

function validateTicker(t) {
  if (!t) return "Ticker symbol cannot be empty.";
  if (!TICKER_RE.test(t)) {
    return `'${t}' is not a valid ticker symbol. Use only letters, digits, dots, or hyphens (max 10 characters).`;
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
const singleForm   = document.getElementById("single-form");
const singleTicker = document.getElementById("single-ticker");
const singleMetric = document.getElementById("single-metric");
const singlePeriod = document.getElementById("single-period");
const singleStatus = document.getElementById("single-status");
const chartDiv     = document.getElementById("chart-div");
const summaryBox   = document.getElementById("summary-box");
const summaryText  = document.getElementById("summary-text");
const statPills    = document.getElementById("stat-pills");

singleForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const ticker = singleTicker.value.trim().toUpperCase();
  const tickerErr = validateTicker(ticker);
  if (tickerErr) {
    showStatus(singleStatus, "error", tickerErr);
    return;
  }

  showStatus(singleStatus, "loading", `Fetching ${ticker}…`);
  chartDiv.innerHTML = "";
  summaryBox.style.display = "none";

  try {
    const data = await apiFetch(API.chart(ticker, singleMetric.value, singlePeriod.value));
    hideStatus(singleStatus);
    renderSingleChart(data);
  } catch (err) {
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

  // z-score bar
  renderZscoreBar(z);
}

function renderZscoreBar(z) {
  const wrap = document.getElementById("zscore-bar-wrap");
  // clamp to [-3, 3]
  const pct = ((Math.max(-3, Math.min(3, z)) + 3) / 6) * 100;
  const color = zColor(z);
  document.getElementById("zscore-marker").style.left = `${pct}%`;
  document.getElementById("zscore-marker").style.background = color;
  wrap.style.display = "";
}

// ── COMPARE CHART ─────────────────────────────────────────────────────────
const compareForm    = document.getElementById("compare-form");
const compareTickers = document.getElementById("compare-tickers");
const compareMetric  = document.getElementById("compare-metric");
const comparePeriod  = document.getElementById("compare-period");
const compareStatus  = document.getElementById("compare-status");
const compareDiv     = document.getElementById("compare-div");
const compareTable   = document.getElementById("compare-table");

compareForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const rawTickers = compareTickers.value
    .split(",").map(t => t.trim().toUpperCase()).filter(Boolean);
  if (!rawTickers.length) {
    showStatus(compareStatus, "error", "Please enter at least one ticker.");
    return;
  }
  const tickerErrors = rawTickers.map(t => validateTicker(t)).filter(Boolean);
  if (tickerErrors.length) {
    showStatus(compareStatus, "error", tickerErrors[0]);
    return;
  }
  showStatus(compareStatus, "loading", "Loading comparison…");
  compareDiv.innerHTML = "";
  compareTable.innerHTML = "";

  try {
    const data = await apiFetch(API.compare(tickers, compareMetric.value, comparePeriod.value));
    hideStatus(compareStatus);
    if (data.errors?.length) {
      showStatus(compareStatus, "error", "Skipped: " + data.errors.join(" | "));
      compareStatus.style.display = "";
    }
    renderCompareChart(data);
  } catch (err) {
    showStatus(compareStatus, "error", err.message);
  }
});

function renderCompareChart(data) {
  const fig = JSON.parse(data.chart);
  Plotly.newPlot(compareDiv, fig.data, fig.layout, PLOTLY_CFG);

  // summary table
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
  if (tickerErr) {
    showStatus(wlStatus, "error", tickerErr);
    return;
  }
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
  showStatus(wlStatus, "loading", "Ranking watchlist…");
  wlTableBody.innerHTML = "";
  wlEmpty.style.display = "none";

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
    showStatus(wlStatus, "error", err.message);
  }
}

// Auto-load watchlist if it's the active panel on page load
document.addEventListener("DOMContentLoaded", () => {
  const activePanel = document.querySelector(".panel.active");
  if (activePanel?.id === "panel-watchlist") loadWatchlistRanked();
});
