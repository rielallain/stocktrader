/* --------------------------------------------------------------
   StockTracker Web - Frontend
   Single-file vanilla JS app. No build step, no dependencies.
-------------------------------------------------------------- */

const API = {
  async get(path)              { return this._req('GET', path); },
  async post(path, body)       { return this._req('POST', path, body); },
  async patch(path, body)      { return this._req('PATCH', path, body); },
  async del(path)              { return this._req('DELETE', path); },
  async _req(method, path, body) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const res = await fetch(path, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }
};

// Global state
const state = {
  stocks: [],           // full list from /api/stocks
  alerts: [],
  alertLog: [],
  activeTab: 'portfolio',
  filter: '',
  sortKey: 'ticker',
  sortDir: 'asc',
  selectedTicker: null,
  marketStatus: 'closed',
};

// Column definitions — mirror the desktop app's 16 columns
const COLUMNS = [
  { key: 'ticker',             label: 'Ticker',      cls: '',    mobile: true  },
  { key: 'company_name',       label: 'Company',     cls: '',    mobile: false },
  { key: 'last_price',         label: 'Price',       cls: 'num', mobile: true  },
  { key: 'day_percent_change', label: 'Day Chg',     cls: 'num', mobile: true  },
  { key: 'endorsement_price',  label: 'Endorsed',    cls: 'num', mobile: false },
  { key: 'endorsement_date',   label: 'End. Date',   cls: 'num', mobile: false },
  { key: 'allocation',         label: 'Alloc',       cls: 'num', mobile: false },
  { key: 'target_price',       label: 'Target',      cls: 'num', mobile: false },
  { key: 'dollar_change',      label: '$ P/L',       cls: 'num', mobile: false },
  { key: 'percent_change',     label: '% P/L',       cls: 'num', mobile: true  },
  { key: 'volume',             label: 'Volume',      cls: 'num', mobile: false },
  { key: 'market_cap',         label: 'Mkt Cap',     cls: 'num', mobile: false },
  { key: 'range_52w',          label: '52W Range',   cls: '',    mobile: false, noSort: false },
  { key: 'rsi',                label: 'RSI',         cls: 'num', mobile: false },
  { key: 'sma_200_pct',        label: '% vs SMA200', cls: 'num', mobile: false },
];

// ---------- formatting helpers ----------
function fmtNum(n, opts = {}) {
  if (n === null || n === undefined || isNaN(n)) return '—';
  const { prefix = '', suffix = '', decimals = 2 } = opts;
  return prefix + Number(n).toLocaleString('en-US', {
    minimumFractionDigits: decimals, maximumFractionDigits: decimals,
  }) + suffix;
}

function fmtCompact(n) {
  if (n === null || n === undefined) return '—';
  const abs = Math.abs(n);
  const sign = n < 0 ? '-' : '';
  if (abs >= 1e12) return `${sign}$${(abs/1e12).toFixed(2)}T`;
  if (abs >= 1e9)  return `${sign}$${(abs/1e9).toFixed(2)}B`;
  if (abs >= 1e6)  return `${sign}$${(abs/1e6).toFixed(2)}M`;
  if (abs >= 1e3)  return `${sign}${(abs/1e3).toFixed(1)}K`;
  return `${sign}${Math.round(abs).toLocaleString()}`;
}

function fmtVolume(n) {
  if (n === null || n === undefined) return '—';
  if (n >= 1e9) return `${(n/1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n/1e6).toFixed(2)}M`;
  if (n >= 1e3) return `${(n/1e3).toFixed(1)}K`;
  return Math.round(n).toLocaleString();
}

function signed(n, decimals = 2, suffix = '') {
  if (n === null || n === undefined || isNaN(n)) return '—';
  const sign = n >= 0 ? '+' : '';
  return `${sign}${Number(n).toFixed(decimals)}${suffix}`;
}

function gainLossClass(n) {
  if (n === null || n === undefined || isNaN(n)) return 'neutral';
  return n >= 0 ? 'gain' : 'loss';
}

// ---------- table rendering ----------
function sortStocks(stocks) {
  const { sortKey, sortDir } = state;
  const mult = sortDir === 'asc' ? 1 : -1;
  return [...stocks].sort((a, b) => {
    let av, bv;
    if (sortKey === 'range_52w') {
      // Sort by position within 52W range
      av = (a.last_price != null && a.low_52w != null && a.high_52w > a.low_52w)
        ? (a.last_price - a.low_52w) / (a.high_52w - a.low_52w) : 0;
      bv = (b.last_price != null && b.low_52w != null && b.high_52w > b.low_52w)
        ? (b.last_price - b.low_52w) / (b.high_52w - b.low_52w) : 0;
    } else {
      av = a[sortKey];
      bv = b[sortKey];
    }
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    if (typeof av === 'string') return av.localeCompare(bv) * mult;
    return (av - bv) * mult;
  });
}

function filterStocks(stocks) {
  if (!state.filter) return stocks;
  const q = state.filter.toLowerCase();
  return stocks.filter(s =>
    s.ticker.toLowerCase().includes(q) ||
    (s.company_name || '').toLowerCase().includes(q)
  );
}

function renderTable(which) {
  const table = document.getElementById(`${which}-table`);
  const thead = table.querySelector('thead');
  const tbody = table.querySelector('tbody');
  const emptyEl = table.parentElement.querySelector('.empty-state');

  // Filter stocks by which list
  const filterFn = which === 'portfolio'
    ? s => s.is_portfolio
    : s => s.is_watchlist;
  const base = state.stocks.filter(filterFn);
  const rows = sortStocks(filterStocks(base));

  // Header
  thead.innerHTML = '';
  const headerRow = document.createElement('tr');
  for (const col of COLUMNS) {
    const th = document.createElement('th');
    th.textContent = col.label;
    if (col.cls) th.classList.add(col.cls);
    if (!col.mobile) th.classList.add('col-hide-mobile');
    if (!col.noSort) {
      th.dataset.sortKey = col.key;
      if (state.sortKey === col.key) {
        th.classList.add(state.sortDir === 'asc' ? 'sort-asc' : 'sort-desc');
      }
      th.addEventListener('click', () => {
        if (state.sortKey === col.key) {
          state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
        } else {
          state.sortKey = col.key;
          state.sortDir = 'asc';
        }
        renderAll();
      });
    }
    headerRow.appendChild(th);
  }
  thead.appendChild(headerRow);

  // Body
  tbody.innerHTML = '';
  for (const s of rows) {
    tbody.appendChild(renderRow(s));
  }

  emptyEl.classList.toggle('hidden', rows.length > 0);
}

function renderRow(s) {
  const tr = document.createElement('tr');
  tr.dataset.ticker = s.ticker;
  tr.addEventListener('click', () => showDetail(s.ticker));

  for (const col of COLUMNS) {
    const td = document.createElement('td');
    if (col.cls) td.classList.add(col.cls);
    if (!col.mobile) td.classList.add('col-hide-mobile');
    td.innerHTML = cellContent(s, col.key);
    tr.appendChild(td);
  }
  return tr;
}

function cellContent(s, key) {
  switch (key) {
    case 'ticker':
      return `<span class="ticker-cell">${s.ticker}</span>`;

    case 'company_name':
      return (s.company_name || s.ticker).replace(/</g, '&lt;');

    case 'last_price': {
      const priceStr = fmtNum(s.last_price, { prefix: '$' });
      if (s.last_price == null) return priceStr;

      // Extended-hours price, colored blue (pre) / purple (post). Shown as a
      // small delta vs regular close so you can see the move at a glance.
      let extHtml = '';
      if (s.extended_price != null && s.extended_session) {
        const diff = s.extended_price - s.last_price;
        const cls = s.extended_session === 'pre' ? 'ext-pre' : 'ext-post';
        const label = s.extended_session === 'pre' ? 'pre-market' : 'after-hours';
        const sign = diff >= 0 ? '+' : '';
        extHtml = ` <span class="${cls}" title="${label}: $${s.extended_price.toFixed(2)}">${sign}${diff.toFixed(2)}</span>`;
      }

      // Stale indicator (yellow dot) if price wasn't refreshed today.
      let staleHtml = '';
      if (s.last_fetched) {
        const fetched = new Date(s.last_fetched);
        const today = new Date();
        const isStale = fetched.toDateString() !== today.toDateString();
        if (isStale) {
          const prettyDate = fetched.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
          staleHtml = `<span class="stale-dot" title="Price from ${prettyDate} — not refreshed today. Click ⟳ Refresh to retry.">●</span>`;
        }
      }
      return `${priceStr}${extHtml}${staleHtml}`;
    }

    case 'day_percent_change': {
      if (s.day_percent_change == null) return '<span class="neutral">—</span>';
      const cls = gainLossClass(s.day_percent_change);
      const dollar = signed(s.day_dollar_change);
      const pct = signed(s.day_percent_change, 2, '%');
      return `<span class="${cls}">${dollar} (${pct})</span>`;
    }

    case 'endorsement_price':
      return s.endorsement_price ? fmtNum(s.endorsement_price, { prefix: '$' }) : '<span class="dim">—</span>';

    case 'endorsement_date':
      return s.endorsement_date
        ? `<span class="dim">${s.endorsement_date}</span>`
        : '<span class="dim">—</span>';

    case 'allocation':
      return s.allocation != null && s.allocation > 0
        ? `${s.allocation.toFixed(2)}%`
        : '<span class="dim">—</span>';

    case 'target_price': {
      if (!s.target_price) return '<span class="dim">—</span>';
      const hit = s.last_price != null && s.last_price >= s.target_price;
      const cls = hit ? 'gain' : 'neutral';
      return `<span class="${cls}">${fmtNum(s.target_price, { prefix: '$' })}</span>`;
    }

    case 'dollar_change': {
      if (s.dollar_change == null) return '—';
      const cls = gainLossClass(s.dollar_change);
      return `<span class="${cls}">${signed(s.dollar_change)}</span>`;
    }

    case 'percent_change': {
      if (s.percent_change == null) return '<span class="neutral">—</span>';
      const cls = gainLossClass(s.percent_change);
      return `<span class="${cls}">${signed(s.percent_change, 2, '%')}</span>`;
    }

    case 'volume':
      return `<span class="dim">${fmtVolume(s.volume)}</span>`;

    case 'market_cap':
      return `<span class="dim">${fmtCompact(s.market_cap)}</span>`;

    case 'range_52w':
      return renderRangeBar(s);

    case 'rsi': {
      if (s.rsi == null) return '<span class="neutral">—</span>';
      let cls = 'dim';
      if (s.rsi >= 70) cls = 'rsi-over';
      else if (s.rsi <= 30) cls = 'rsi-under';
      return `<span class="${cls}">${s.rsi.toFixed(1)}</span>`;
    }

    case 'sma_200_pct': {
      if (s.sma_200_pct == null) return '<span class="neutral">—</span>';
      const cls = gainLossClass(s.sma_200_pct);
      return `<span class="${cls}">${signed(s.sma_200_pct, 1, '%')}</span>`;
    }

    default:
      return '';
  }
}

function renderRangeBar(s) {
  if (s.low_52w == null || s.high_52w == null || s.last_price == null || s.high_52w <= s.low_52w) {
    return '<span class="dim">—</span>';
  }
  const ratio = Math.max(0, Math.min(1, (s.last_price - s.low_52w) / (s.high_52w - s.low_52w)));
  let fillClass = '';
  if (ratio >= 0.6) fillClass = 'good';
  else if (ratio <= 0.4) fillClass = 'bad';
  const lo = s.low_52w >= 100 ? `$${Math.round(s.low_52w)}` : `$${s.low_52w.toFixed(2)}`;
  const hi = s.high_52w >= 100 ? `$${Math.round(s.high_52w)}` : `$${s.high_52w.toFixed(2)}`;
  return `
    <div class="range-bar-wrap">
      <span>${lo}</span>
      <div class="range-bar-track">
        <div class="range-bar-fill ${fillClass}" style="width: ${ratio * 100}%"></div>
        <div class="range-bar-marker" style="left: ${ratio * 100}%"></div>
      </div>
      <span>${hi}</span>
    </div>`;
}

// ---------- summary ----------
function renderSummary() {
  const pcount = state.stocks.filter(s => s.is_portfolio).length;
  const wcount = state.stocks.filter(s => s.is_watchlist).length;
  const acount = state.alerts.filter(a => a.active).length;

  document.getElementById('summary-portfolio-count').textContent = pcount;
  document.getElementById('summary-watchlist-count').textContent = wcount;
  document.getElementById('summary-alerts').textContent = acount;
}

// ---------- market status ----------
async function refreshMarketStatus() {
  try {
    const s = await API.get('/api/market-status');
    state.marketStatus = s.status;
    const el = document.getElementById('market-status');
    el.className = 'market-badge market-' + s.status;
    const labels = {
      open: '● MARKET OPEN',
      closed: '● MARKET CLOSED',
      pre: '● PRE-MARKET',
      post: '● AFTER HOURS',
    };
    el.textContent = labels[s.status] || s.status.toUpperCase();
  } catch (e) {
    console.warn('market status', e);
  }
}

// ---------- data loading ----------
async function loadStocks() {
  try {
    state.stocks = await API.get('/api/stocks');
    setConnection(true);
  } catch (e) {
    setConnection(false);
    toast('Failed to load stocks: ' + e.message, 'err');
  }
}

async function loadAlerts() {
  try {
    state.alerts = await API.get('/api/alerts');
    state.alertLog = await API.get('/api/alerts/log?limit=20');
  } catch (e) {
    console.warn(e);
  }
}

function setConnection(ok) {
  const el = document.getElementById('connection-status');
  if (ok) { el.textContent = '● online'; el.className = 'connection-ok'; }
  else    { el.textContent = '● offline'; el.className = 'connection-err'; }
}

function renderAll() {
  renderTable('portfolio');
  renderTable('watchlist');
  renderSummary();
  renderAlerts();
  updateLastUpdated();
}

function updateLastUpdated() {
  // Find the most recent last_fetched
  let latest = null;
  for (const s of state.stocks) {
    if (s.last_fetched) {
      const t = new Date(s.last_fetched);
      if (!latest || t > latest) latest = t;
    }
  }
  const el = document.getElementById('last-updated');
  if (latest) {
    el.textContent = 'Updated ' + latest.toLocaleTimeString();
  } else {
    el.textContent = '';
  }
}

// ---------- detail panel ----------
function showDetail(ticker) {
  state.selectedTicker = ticker;
  const s = state.stocks.find(x => x.ticker === ticker);
  if (!s) return;
  _navPushModal('detail');

  document.getElementById('detail-ticker').textContent = s.ticker;
  document.getElementById('detail-company').textContent = s.company_name || '';

  const c = document.getElementById('detail-content');

  // Price header
  let priceHtml = `
    <div class="detail-price-frame">
      <div class="price">${fmtNum(s.last_price, { prefix: '$' })}</div>
      <div class="change ${gainLossClass(s.day_percent_change)}">
        ${signed(s.day_dollar_change)} (${signed(s.day_percent_change, 2, '%')})
      </div>`;
  if (s.extended_price != null && s.extended_session) {
    const diff = s.extended_price - s.last_price;
    const cls = s.extended_session === 'pre' ? 'ext-pre' : 'ext-post';
    const label = s.extended_session === 'pre' ? 'Pre-market' : 'After-hours';
    const sign = diff >= 0 ? '+' : '';
    priceHtml += `<div class="${cls}" style="font-size:12px;margin-top:4px;">${label}: $${s.extended_price.toFixed(2)} (${sign}${diff.toFixed(2)})</div>`;
  }
  if (s.previous_close) {
    priceHtml += `<div style="color:var(--muted);font-size:11px;margin-top:4px;">Previous close: $${s.previous_close.toFixed(2)}</div>`;
  }
  priceHtml += '</div>';

  const sections = [
    ['Position', [
      ['Endorsement price', s.endorsement_price ? `$${s.endorsement_price.toFixed(2)}` : '—'],
      ['Endorsement date',  s.endorsement_date || '—'],
      ['Target price',      s.target_price ? `$${s.target_price.toFixed(2)}` : '—'],
      ['Allocation',        s.allocation != null ? `${s.allocation.toFixed(2)}%` : '—'],
      ['$ P/L',             s.dollar_change != null ? signed(s.dollar_change) : '—'],
      ['% P/L',             s.percent_change != null ? signed(s.percent_change, 2, '%') : '—'],
    ]],
    ['Market Data', [
      ['Market cap',   fmtCompact(s.market_cap)],
      ['Volume',       fmtVolume(s.volume)],
      ['52W high',     s.high_52w ? `$${s.high_52w.toFixed(2)}` : '—'],
      ['52W low',      s.low_52w ? `$${s.low_52w.toFixed(2)}` : '—'],
    ]],
    ['Technical', [
      ['RSI (14)',     s.rsi != null ? s.rsi.toFixed(1) : '—'],
      ['% vs 200 SMA', s.sma_200_pct != null ? signed(s.sma_200_pct, 1, '%') : '—'],
    ]],
    ['Status', [
      ['In portfolio', s.is_portfolio ? '✓ Yes' : '—'],
      ['On watchlist', s.is_watchlist ? '✓ Yes' : '—'],
      ['Last fetched', s.last_fetched ? new Date(s.last_fetched).toLocaleString() : '—'],
    ]],
  ];

  let html = priceHtml;
  for (const [title, rows] of sections) {
    html += `<div class="detail-section"><h3>${title}</h3>`;
    for (const [k, v] of rows) {
      html += `<div class="detail-row"><span class="k">${k}</span><span class="v">${v}</span></div>`;
    }
    html += '</div>';
  }

  // Related alerts
  const related = state.alerts.filter(a => a.ticker === ticker);
  if (related.length) {
    html += '<div class="detail-section"><h3>Alerts</h3>';
    for (const a of related) {
      html += `<div class="detail-row"><span class="k">${formatRule(a)}</span>
        <span class="v">${a.active ? '● active' : '○ off'}</span></div>`;
    }
    html += '</div>';
  }

  c.innerHTML = html;

  // Action bar: edit fields, add alert, move between lists, remove
  const oldBar = document.querySelector('.detail-action-bar');
  if (oldBar) oldBar.remove();

  const bar = document.createElement('div');
  bar.className = 'detail-action-bar';
  bar.innerHTML = `
    <button class="btn-secondary" id="da-edit">Edit</button>
    <button class="btn-secondary" id="da-alert">+ Alert</button>
    <button class="btn-secondary" id="da-move">Move</button>
    <button class="btn-secondary" id="da-remove" style="color:var(--red);">Remove</button>
  `;
  document.getElementById('detail-panel').appendChild(bar);

  document.getElementById('da-edit').onclick   = () => editStock(ticker);
  document.getElementById('da-alert').onclick  = () => openAlertModal(ticker);
  document.getElementById('da-move').onclick   = () => moveStock(ticker);
  document.getElementById('da-remove').onclick = () => removeStock(ticker);

  document.getElementById('detail-panel').classList.remove('hidden');
  document.getElementById('detail-backdrop').classList.remove('hidden');
}

function hideDetail() {
  const wasOpen = !document.getElementById('detail-panel').classList.contains('hidden');
  document.getElementById('detail-panel').classList.add('hidden');
  document.getElementById('detail-backdrop').classList.add('hidden');
  state.selectedTicker = null;
  if (wasOpen) _navBack();
}

async function editStock(ticker) {
  const s = state.stocks.find(x => x.ticker === ticker);
  const newEnd = prompt(`Endorsement price for ${ticker}:`, s.endorsement_price || '');
  if (newEnd === null) return;
  const newTarget = prompt(`Target price for ${ticker} (optional):`, s.target_price || '');
  if (newTarget === null) return;
  const newDate = prompt(`Endorsement date (YYYY-MM-DD, optional):`, s.endorsement_date || '');
  if (newDate === null) return;
  const newAlloc = prompt(`Allocation %  (optional):`, s.allocation || '');
  if (newAlloc === null) return;

  try {
    const body = {
      endorsement_price: parseFloat(newEnd) || 0,
      target_price: parseFloat(newTarget) || 0,
    };
    if (newDate.trim())  body.endorsement_date = newDate.trim();
    if (newAlloc.trim()) body.allocation = parseFloat(newAlloc);
    await API.patch(`/api/stocks/${ticker}`, body);
    await loadStocks();
    renderAll();
    showDetail(ticker);
    toast('Updated');
  } catch (e) { toast(e.message, 'err'); }
}

async function moveStock(ticker) {
  const s = state.stocks.find(x => x.ticker === ticker);
  const current = s.is_portfolio && s.is_watchlist ? 'both'
    : s.is_portfolio ? 'portfolio'
    : s.is_watchlist ? 'watchlist' : 'neither';
  const next = prompt(`Current: ${current}\nMove ${ticker} to: portfolio / watchlist / both`, current);
  if (!next) return;
  const val = next.trim().toLowerCase();
  if (!['portfolio', 'watchlist', 'both'].includes(val)) {
    toast('Invalid target', 'err'); return;
  }
  try {
    await API.post(`/api/stocks/${ticker}/move`, { target: val });
    await loadStocks();
    renderAll();
    showDetail(ticker);
    toast('Moved');
  } catch (e) { toast(e.message, 'err'); }
}

async function removeStock(ticker) {
  if (!confirm(`Remove ${ticker}? This also deletes any alerts for it.`)) return;
  try {
    await API.del(`/api/stocks/${ticker}`);
    await Promise.all([loadStocks(), loadAlerts()]);
    renderAll();
    hideDetail();
    toast(`Removed ${ticker}`);
  } catch (e) { toast(e.message, 'err'); }
}

// ---------- alerts UI ----------
function formatRule(a) {
  const t = a.threshold;
  switch (a.rule_type) {
    case 'price_above':           return `${a.ticker}: price ≥ $${t.toFixed(2)}`;
    case 'price_below':           return `${a.ticker}: price ≤ $${t.toFixed(2)}`;
    case 'pct_from_endorsement':  return `${a.ticker}: ${t >= 0 ? 'up' : 'down'} ${Math.abs(t).toFixed(1)}% from endorsement`;
    case 'rsi_above':             return `${a.ticker}: RSI ≥ ${t.toFixed(0)} (overbought)`;
    case 'rsi_below':             return `${a.ticker}: RSI ≤ ${t.toFixed(0)} (oversold)`;
    default:                      return `${a.ticker}: ${a.rule_type} @ ${t}`;
  }
}

function renderAlerts() {
  const list = document.getElementById('alerts-list');
  if (!state.alerts.length) {
    list.innerHTML = '<div class="empty-state">No alert rules yet. Click "＋ New Alert".</div>';
  } else {
    list.innerHTML = '';
    for (const a of state.alerts) {
      const row = document.createElement('div');
      row.className = 'alert-row' + (a.active ? '' : ' inactive');
      const cur = state.stocks.find(s => s.ticker === a.ticker);
      const curPrice = cur && cur.last_price != null ? `now $${cur.last_price.toFixed(2)}` : '';
      row.innerHTML = `
        <span class="ticker">${a.ticker}</span>
        <span class="rule">${formatRule(a)} ${a.one_shot ? '(one-shot)' : '(repeating)'}
          <span style="color:var(--muted);">${curPrice}</span>
          ${a.note ? `<div style="color:var(--muted);font-size:10px;">${a.note}</div>` : ''}
        </span>
        <div class="actions">
          <button data-act="toggle">${a.active ? 'Pause' : 'Resume'}</button>
          <button data-act="delete" class="danger">Delete</button>
        </div>`;
      row.querySelector('[data-act="toggle"]').onclick = async () => {
        await API.patch(`/api/alerts/${a.id}`, { active: !a.active });
        await loadAlerts(); renderAlerts(); renderSummary();
      };
      row.querySelector('[data-act="delete"]').onclick = async () => {
        if (!confirm('Delete this alert?')) return;
        await API.del(`/api/alerts/${a.id}`);
        await loadAlerts(); renderAlerts(); renderSummary();
      };
      list.appendChild(row);
    }
  }

  // Log
  const log = document.getElementById('alerts-log');
  if (!state.alertLog.length) {
    log.innerHTML = '<div class="empty-state">No alerts have fired yet.</div>';
  } else {
    log.innerHTML = '';
    for (const l of state.alertLog) {
      const row = document.createElement('div');
      row.className = 'log-row';
      const ts = new Date(l.fired_at + (l.fired_at.endsWith('Z') ? '' : 'Z')).toLocaleString();
      row.innerHTML = `<div class="ts">${ts} ${l.sent_ok ? '✓ Email sent' : '✗ ' + (l.error || 'failed')}</div>
        <div>${l.message}</div>`;
      log.appendChild(row);
    }
  }
}

function openAlertModal(prefillTicker = null) {
  const modal = document.getElementById('alert-modal');
  const tickerSel = document.getElementById('alert-ticker');
  tickerSel.innerHTML = '';
  for (const s of [...state.stocks].sort((a,b) => a.ticker.localeCompare(b.ticker))) {
    const opt = document.createElement('option');
    opt.value = s.ticker;
    opt.textContent = `${s.ticker} — ${s.company_name || ''}`;
    tickerSel.appendChild(opt);
  }
  if (prefillTicker) tickerSel.value = prefillTicker;

  updateThresholdHint();
  document.getElementById('alert-threshold').value = '';
  document.getElementById('alert-note').value = '';
  document.getElementById('alert-one-shot').checked = true;
  modal.classList.remove('hidden');
  _navPushModal('alert');
}

function updateThresholdHint() {
  const rt = document.getElementById('alert-rule-type').value;
  const hint = document.getElementById('alert-threshold-hint');
  const label = document.getElementById('alert-threshold-label');
  const hints = {
    price_above:          'Threshold is a dollar price (e.g. 150.00). Alert fires when price ≥ this value.',
    price_below:          'Threshold is a dollar price (e.g. 45.00). Alert fires when price ≤ this value.',
    pct_from_endorsement: 'Signed percent. Use +20 for a 20% gain, -15 for a 15% drop. Requires endorsement price.',
    rsi_above:            'RSI value 0-100 (typically 70 for overbought).',
    rsi_below:            'RSI value 0-100 (typically 30 for oversold).',
  };
  hint.textContent = hints[rt] || '';
  const labels = {
    price_above: 'Price threshold ($)',
    price_below: 'Price threshold ($)',
    pct_from_endorsement: 'Percent move (signed)',
    rsi_above: 'RSI threshold',
    rsi_below: 'RSI threshold',
  };
  label.childNodes[0].textContent = labels[rt];
}

async function confirmAlert() {
  const body = {
    ticker: document.getElementById('alert-ticker').value,
    rule_type: document.getElementById('alert-rule-type').value,
    threshold: parseFloat(document.getElementById('alert-threshold').value),
    one_shot: document.getElementById('alert-one-shot').checked,
    note: document.getElementById('alert-note').value || null,
  };
  if (!body.ticker || isNaN(body.threshold)) {
    toast('Ticker and threshold required', 'err'); return;
  }
  try {
    await API.post('/api/alerts', body);
    await loadAlerts();
    renderAlerts(); renderSummary();
    document.getElementById('alert-modal').classList.add('hidden');
    _navBack();
    toast('Alert created');
  } catch (e) { toast(e.message, 'err'); }
}

// ---------- add ticker modal ----------
function openAddModal() {
  document.getElementById('add-ticker-input').value = '';
  document.getElementById('add-endorsement').value = '';
  document.getElementById('add-target').value = '';
  document.getElementById('add-validation-result').textContent = '';
  document.getElementById('add-validation-result').className = '';
  document.getElementById('add-modal').classList.remove('hidden');
  _navPushModal('add');
}

async function validateAdd() {
  const symbol = document.getElementById('add-ticker-input').value.trim().toUpperCase();
  const suffix = document.getElementById('add-exchange').value;
  let ticker = symbol;
  if (suffix !== 'US') {
    if (suffix === '-USD') {
      if (!ticker.endsWith('-USD')) ticker += '-USD';
    } else {
      if (!ticker.endsWith(suffix)) ticker += suffix;
    }
  }
  document.getElementById('add-ticker-input').value = ticker;

  const result = document.getElementById('add-validation-result');
  result.textContent = 'Checking...';
  result.className = '';
  try {
    const v = await API.get(`/api/validate/${ticker}`);
    if (v.valid) {
      result.textContent = `✓ ${v.company_name} — $${v.current_price?.toFixed(2) ?? '?'}`;
      result.className = 'ok';
    } else {
      result.textContent = `⚠ Could not find ${ticker}`;
      result.className = 'err';
    }
  } catch (e) {
    result.textContent = '⚠ ' + e.message;
    result.className = 'err';
  }
}

async function confirmAdd() {
  const body = {
    ticker: document.getElementById('add-ticker-input').value.trim().toUpperCase(),
    endorsement_price: parseFloat(document.getElementById('add-endorsement').value) || 0,
    target_price: parseFloat(document.getElementById('add-target').value) || 0,
    target_list: document.getElementById('add-target-list').value,
  };
  if (!body.ticker) { toast('Ticker required', 'err'); return; }
  try {
    await API.post('/api/stocks', body);
    await loadStocks();
    renderAll();
    document.getElementById('add-modal').classList.add('hidden');
    _navBack();
    toast(`Added ${body.ticker}`);
  } catch (e) { toast(e.message, 'err'); }
}

// ---------- refresh ----------
async function refreshNow() {
  const btn = document.getElementById('refresh-btn');
  btn.disabled = true;
  btn.textContent = '⟳ Refreshing...';
  try {
    const resp = await API.post('/api/refresh', {});
    if (resp.status === 'already_running') {
      toast('Refresh already in progress', 'ok');
    } else {
      toast('Refresh started (~2 min for full batch)', 'ok');
    }
    // The backend refreshes in a daemon thread. Poll /api/stocks every 20s
    // so the UI picks up new prices as they land instead of waiting for the
    // 60s auto-refresh cycle.
    for (let i = 0; i < 8; i++) {
      await new Promise(r => setTimeout(r, 20_000));
      await loadStocks();
      renderAll();
    }
  } catch (e) { toast(e.message, 'err'); }
  finally {
    btn.disabled = false;
    btn.textContent = '⟳ Refresh';
  }
}

// ---------- tabs ----------
let _navPopping = false;
function switchTab(name) {
  if (!_navPopping && state.activeTab !== name) {
    history.pushState({ nav: 'tab', name }, '');
  }
  state.activeTab = name;
  document.querySelectorAll('.tab-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.tab-panel').forEach(p =>
    p.classList.toggle('active', p.id === 'tab-' + name));
}

// ---------- back-button / history integration ----------
// Android's system back button should close the top-most modal, then fall
// back to switching tabs toward 'portfolio'. Each UI layer pushes a history
// entry when opened; popstate closes the layer.
function _navPushModal(kind) {
  if (!_navPopping) history.pushState({ nav: 'modal', kind }, '');
}
function _navBack() {
  if (!_navPopping) history.back();
}
window.addEventListener('popstate', (e) => {
  _navPopping = true;
  try {
    const detail = document.getElementById('detail-panel');
    const alertM = document.getElementById('alert-modal');
    const addM   = document.getElementById('add-modal');
    if (detail && !detail.classList.contains('hidden')) {
      hideDetail();
    } else if (alertM && !alertM.classList.contains('hidden')) {
      alertM.classList.add('hidden');
    } else if (addM && !addM.classList.contains('hidden')) {
      addM.classList.add('hidden');
    } else {
      const targetTab = (e.state && e.state.nav === 'tab' && e.state.name) || 'portfolio';
      if (state.activeTab !== targetTab) switchTab(targetTab);
    }
  } finally {
    _navPopping = false;
  }
});

// ---------- toast ----------
let toastTimer = null;
function toast(msg, kind = 'ok') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'toast ' + kind;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), 3000);
}

// ---------- init ----------
async function init() {
  // Tab buttons
  document.querySelectorAll('.tab-btn').forEach(b =>
    b.addEventListener('click', () => switchTab(b.dataset.tab)));

  document.getElementById('refresh-btn').addEventListener('click', refreshNow);

  // Push notifications toggle
  const pushBtn = document.getElementById('push-btn');
  if (pushBtn) {
    if (!('serviceWorker' in navigator) || !('PushManager' in window) ||
        (typeof Notification === 'undefined')) {
      pushBtn.style.display = 'none';
    } else {
      pushBtn.addEventListener('click', togglePush);
      updatePushButton();
    }
  }

  // Filter
  document.getElementById('filter-input').addEventListener('input', (e) => {
    state.filter = e.target.value;
    renderTable('portfolio');
    renderTable('watchlist');
  });

  // Add ticker
  document.getElementById('add-ticker-btn').addEventListener('click', openAddModal);
  document.getElementById('add-cancel').onclick = () => {
    document.getElementById('add-modal').classList.add('hidden');
    _navBack();
  };
  document.getElementById('add-confirm').onclick = confirmAdd;
  document.getElementById('add-validate-btn').onclick = validateAdd;

  // Alert modal
  document.getElementById('add-alert-btn').addEventListener('click', () => openAlertModal());
  document.getElementById('alert-cancel').onclick = () => {
    document.getElementById('alert-modal').classList.add('hidden');
    _navBack();
  };
  document.getElementById('alert-confirm').onclick = confirmAlert;
  document.getElementById('alert-rule-type').addEventListener('change', updateThresholdHint);

  // Detail panel close
  document.getElementById('detail-close').onclick = hideDetail;
  document.getElementById('detail-backdrop').onclick = hideDetail;

  // Initial load
  await Promise.all([refreshMarketStatus(), loadStocks(), loadAlerts()]);
  renderAll();

  // Periodic background refresh of market status + data
  setInterval(refreshMarketStatus, 60_000);
  setInterval(async () => {
    // Only refetch from the DB (light); the alert worker is the one actually
    // pulling new prices from yfinance on its schedule.
    await loadStocks();
    await loadAlerts();
    renderAll();
  }, 60_000);
}

// ---------- push notifications ----------
function urlB64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - base64String.length % 4) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  const arr = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
  return arr;
}

async function updatePushButton() {
  const btn = document.getElementById('push-btn');
  if (!btn) return;
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub && Notification.permission === 'granted') {
      btn.textContent = '🔕';
      btn.title = 'Disable push notifications';
    } else {
      btn.textContent = '🔔';
      btn.title = 'Enable push notifications for alerts';
    }
  } catch (e) {
    console.warn('push button state', e);
  }
}

async function togglePush() {
  try {
    const reg = await navigator.serviceWorker.ready;
    const existing = await reg.pushManager.getSubscription();

    if (existing && Notification.permission === 'granted') {
      // Unsubscribe
      await API.post('/api/push/unsubscribe', { endpoint: existing.endpoint });
      await existing.unsubscribe();
      toast('Push notifications disabled', 'ok');
      await updatePushButton();
      return;
    }

    // Subscribe
    const perm = await Notification.requestPermission();
    if (perm !== 'granted') {
      toast('Notification permission denied', 'err');
      return;
    }

    const { key } = await API.get('/api/push/public-key');
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlB64ToUint8Array(key),
    });
    await API.post('/api/push/subscribe', sub.toJSON());
    toast('Push notifications enabled', 'ok');
    await updatePushButton();
  } catch (e) {
    console.error('togglePush', e);
    toast('Failed: ' + (e.message || e), 'err');
  }
}

document.addEventListener('DOMContentLoaded', init);
