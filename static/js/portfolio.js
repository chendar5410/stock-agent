// Portfolio dashboard — poll-based live refresh.
// Equity curve: every 30s. State+macro+picks+summaries: every 15s.

const REFRESH_FAST_MS = 15000;
const REFRESH_EQUITY_MS = 30000;

const $ = (sel) => document.querySelector(sel);
const fmtUSD = (v) => v == null ? '—' :
  v.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const fmtNum = (v, d = 2) => v == null ? '—' : Number(v).toFixed(d);
const fmtPct = (v, d = 2) => v == null ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(d) + '%';

function cls(v) { return v == null ? 'neutral' : (v > 0 ? 'positive' : v < 0 ? 'negative' : 'neutral'); }

async function getJSON(url, opts = {}) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return r.json();
}

// ── Status pills ─────────────────────────────────────────────────────
async function refreshStatus() {
  try {
    const s = await getJSON('/api/portfolio/status');
    const bp = $('#broker-pill');
    bp.textContent = `broker: ${s.broker}`;
    bp.className = 'pill ' + (s.use_alpaca ? 'green' : 'yellow');

    const mp = $('#market-pill');
    mp.textContent = s.market_open ? 'market: OPEN' : 'market: closed';
    mp.className = 'pill ' + (s.market_open ? 'green' : 'red');
  } catch (e) { console.error(e); }
}

// ── Portfolio compare (KPIs + positions) ─────────────────────────────
async function refreshCompare() {
  try {
    const { agent, user } = await getJSON('/api/portfolio/compare');
    renderKPI('agent', agent);
    renderKPI('user',  user);
    renderPositions('agent-positions', agent.positions, true);
    renderPositions('user-positions',  user.positions,  false);
  } catch (e) { console.error(e); }
}

function renderKPI(name, st) {
  $(`#${name}-equity`).textContent = fmtUSD(st.equity);
  $(`#${name}-cash`).textContent   = fmtUSD(st.cash);
  const ret = $(`#${name}-return`);
  ret.textContent = fmtPct(st.total_return_pct);
  ret.className = cls(st.total_return_pct);
}

function renderPositions(tableId, positions, showStops) {
  const tbody = document.querySelector(`#${tableId} tbody`);
  if (!positions || positions.length === 0) {
    const cols = showStops ? 8 : 6;
    tbody.innerHTML = `<tr><td colspan="${cols}" class="muted">No open positions.</td></tr>`;
    return;
  }
  tbody.innerHTML = positions.map(p => {
    const pnlCls = cls(p.unrealized_pnl);
    const cells = [
      `<b>${p.symbol}</b>`,
      fmtNum(p.quantity, 4),
      fmtUSD(p.avg_entry_price),
      fmtUSD(p.last_price),
      fmtUSD(p.market_value),
      `<span class="${pnlCls}">${fmtUSD(p.unrealized_pnl)} (${fmtPct(p.unrealized_pnl_pct)})</span>`,
    ];
    if (showStops) {
      cells.push(p.stop_loss ? fmtUSD(p.stop_loss) : '—');
      cells.push(p.take_profit ? fmtUSD(p.take_profit) : '—');
    }
    return `<tr>${cells.map(c => `<td>${c}</td>`).join('')}</tr>`;
  }).join('');
}

// ── Equity curve ─────────────────────────────────────────────────────
async function refreshEquityChart() {
  try {
    const [a, u] = await Promise.all([
      getJSON('/api/portfolio/equity/agent?days=365'),
      getJSON('/api/portfolio/equity/user?days=365'),
    ]);
    const traceA = {
      x: a.curve.map(p => p.t), y: a.curve.map(p => p.equity),
      mode: 'lines', name: 'Agent', line: { color: '#58a6ff', width: 2 },
    };
    const traceU = {
      x: u.curve.map(p => p.t), y: u.curve.map(p => p.equity),
      mode: 'lines', name: 'You', line: { color: '#d2a8ff', width: 2 },
    };
    const layout = {
      margin: { l: 60, r: 30, t: 10, b: 40 },
      paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
      font: { color: '#c9d1d9' },
      xaxis: { gridcolor: '#30363d' },
      yaxis: { gridcolor: '#30363d', tickprefix: '$' },
      legend: { orientation: 'h', y: -0.15 },
    };
    Plotly.react('equity-chart', [traceA, traceU], layout, { displayModeBar: false, responsive: true });
  } catch (e) { console.error(e); }
}

// ── Macro ────────────────────────────────────────────────────────────
async function refreshMacro() {
  try {
    const m = await getJSON('/api/portfolio/macro');
    $('#m-vix').textContent = fmtNum(m.vix, 1);
    $('#m-dxy').textContent = fmtNum(m.dxy, 2);
    $('#m-tlt').textContent = fmtNum(m.tlt, 2);
    $('#m-oil').textContent = fmtNum(m.oil, 2);
    $('#m-pc').textContent  = fmtNum(m.put_call_ratio, 2);

    const rp = $('#regime-pill');
    rp.textContent = `regime: ${m.regime}`;
    rp.className = 'pill ' + (m.regime === 'risk_on' ? 'green'
                            : m.regime === 'risk_off' ? 'red' : 'yellow');
  } catch (e) { console.error(e); }
}

// ── Picks ────────────────────────────────────────────────────────────
async function refreshPicks() {
  try {
    const { picks } = await getJSON('/api/portfolio/picks?weeks=1');
    const tbody = document.querySelector('#picks-table tbody');
    if (!picks || picks.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" class="muted">No picks yet — click "Run picker".</td></tr>';
      return;
    }
    tbody.innerHTML = picks.map(p => `
      <tr>
        <td>${p.rank}</td>
        <td><b>${p.symbol}</b></td>
        <td>${p.metric || '—'}</td>
        <td>${fmtNum(p.score, 2)}</td>
        <td class="thesis-cell">${p.thesis || ''}</td>
      </tr>
    `).join('');
  } catch (e) { console.error(e); }
}

// ── Recent trades (agent) ────────────────────────────────────────────
async function refreshTrades() {
  try {
    const { trades } = await getJSON('/api/portfolio/trades/agent?limit=30');
    const tbody = document.querySelector('#agent-trades tbody');
    if (!trades || trades.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" class="muted">No trades yet.</td></tr>';
      return;
    }
    tbody.innerHTML = trades.map(t => {
      const sideCls = t.side === 'buy' ? 'positive' : 'negative';
      const time = new Date(t.executed_at).toLocaleString();
      return `<tr>
        <td>${time}</td>
        <td><span class="${sideCls}">${t.side.toUpperCase()}</span></td>
        <td><b>${t.symbol}</b></td>
        <td>${fmtNum(t.quantity, 4)}</td>
        <td>${fmtUSD(t.price)}</td>
        <td class="thesis-cell">${t.reason || ''}</td>
      </tr>`;
    }).join('');
  } catch (e) { console.error(e); }
}

// ── Daily summaries ──────────────────────────────────────────────────
async function refreshSummaries() {
  try {
    const { summaries } = await getJSON('/api/portfolio/summaries?limit=7');
    const container = $('#summaries-list');
    if (!summaries || summaries.length === 0) {
      container.innerHTML = '<p class="muted">No summaries yet — runs daily at 16:30 ET, or click "Generate summary".</p>';
      return;
    }
    container.innerHTML = summaries.map(s => {
      const pnlCls = cls(s.pnl_day);
      return `<div class="summary-card">
        <h3>${s.date}</h3>
        <div class="meta">
          regime: ${s.regime || '—'} · trades: ${s.trades} ·
          <span class="${pnlCls}">P&L ${fmtUSD(s.pnl_day)}</span>
        </div>
        <div class="md">${escapeHtml(s.markdown || '')}</div>
      </div>`;
    }).join('');
  } catch (e) { console.error(e); }
}

function escapeHtml(s) {
  return s.replace(/[&<>]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;'})[c]);
}

// ── Buttons: manual jobs & manual trade ──────────────────────────────
async function runJob(job, btn) {
  btn.disabled = true;
  const prev = btn.textContent;
  btn.textContent = 'Running…';
  try {
    await getJSON(`/api/portfolio/run/${job}`, { method: 'POST' });
    await Promise.all([refreshCompare(), refreshPicks(), refreshTrades(), refreshSummaries(), refreshEquityChart()]);
  } catch (e) {
    alert(`Job ${job} failed: ${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = prev;
  }
}

$('#btn-tick').addEventListener('click', (e) => runJob('tick', e.target));
$('#btn-picker').addEventListener('click', (e) => runJob('picker', e.target));
$('#btn-summary').addEventListener('click', (e) => runJob('summary', e.target));

$('#manual-trade-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const form = ev.target;
  const data = new FormData(form);
  const payload = {
    symbol:  data.get('symbol'),
    side:    data.get('side'),
    reason:  data.get('reason') || 'manual user trade',
  };
  const d = parseFloat(data.get('dollars'));
  const q = parseFloat(data.get('quantity'));
  if (!Number.isNaN(d)) payload.dollars = d;
  if (!Number.isNaN(q)) payload.quantity = q;

  const fb = $('#trade-feedback');
  try {
    await getJSON('/api/portfolio/user/trade', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    fb.textContent = 'ok';
    fb.className = 'positive';
    form.reset();
    await Promise.all([refreshCompare(), refreshEquityChart()]);
  } catch (e) {
    fb.textContent = e.message;
    fb.className = 'negative';
  }
});

// ── Scheduler ────────────────────────────────────────────────────────
async function refreshAll() {
  await Promise.all([
    refreshStatus(), refreshCompare(), refreshMacro(),
    refreshPicks(), refreshTrades(), refreshSummaries(),
  ]);
  $('#last-refresh').textContent = 'updated ' + new Date().toLocaleTimeString();
}

refreshAll();
refreshEquityChart();

setInterval(refreshAll, REFRESH_FAST_MS);
setInterval(refreshEquityChart, REFRESH_EQUITY_MS);
