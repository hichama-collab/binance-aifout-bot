// === FILE: dashboard/static/app.js ===
/**
 * BotDash — Alpine.js components + SSE manager + chart helpers
 */

'use strict';

// ============================================================
// Utilities
// ============================================================

const fmt = {
  usdc(v, sign = false) {
    if (v == null || isNaN(v)) return '—';
    const abs = Math.abs(v).toFixed(2);
    const prefix = v >= 0 ? (sign ? '+' : '') : '-';
    return `${prefix}${abs} USDC`;
  },
  eur(v, sign = false) {
    if (v == null || isNaN(v)) return '—';
    const abs = Math.abs(v).toFixed(2);
    const prefix = v >= 0 ? (sign ? '+' : '') : '-';
    return `${prefix}${abs} EUR`;
  },
  pct(v, sign = false) {
    if (v == null || isNaN(v)) return '—';
    const abs = Math.abs(v).toFixed(2);
    const prefix = v >= 0 ? (sign ? '+' : '') : '-';
    return `${prefix}${abs}%`;
  },
  price(v, decimals = 4) {
    if (v == null || isNaN(v)) return '—';
    return parseFloat(v).toFixed(decimals);
  },
  qty(v) {
    if (v == null || isNaN(v)) return '—';
    return parseFloat(v).toFixed(4);
  },
  ts(epoch) {
    if (!epoch) return '—';
    return new Date(epoch * 1000).toLocaleString('fr-FR', { timeZone: 'UTC', hour12: false });
  },
  ago(epoch) {
    if (!epoch) return '—';
    const diff = Math.floor(Date.now() / 1000 - epoch);
    if (diff < 60) return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  },
  duration(isoSince) {
    if (!isoSince) return '—';
    try {
      const since = new Date(isoSince).getTime();
      const diff = Math.floor((Date.now() - since) / 1000);
      if (diff < 0) return '—';
      if (diff < 60) return `${diff}s`;
      if (diff < 3600) return `${Math.floor(diff / 60)}m ${diff % 60}s`;
      const h = Math.floor(diff / 3600);
      const m = Math.floor((diff % 3600) / 60);
      return `${h}h ${m}m`;
    } catch { return '—'; }
  },
  symbol(s) {
    return (s || '').replace('USDC', '').replace('BUSD', '');
  },
};

function colorClass(value) {
  if (value == null || value === 0) return 'neutral';
  return value > 0 ? 'profit' : 'loss';
}

function signedColor(value) {
  if (value > 0) return '#0ECB81';
  if (value < 0) return '#F6465D';
  return '#848E9C';
}

// ============================================================
// Reason formatter
// ============================================================

let _reasonsMap = null;

async function _loadReasons() {
  if (_reasonsMap !== null) return;
  try {
    const r = await fetch('/static/i18n/reasons.json');
    _reasonsMap = await r.json();
  } catch {
    _reasonsMap = {};
  }
}

function formatReason(raw) {
  if (!raw) return '—';
  if (!_reasonsMap) return raw.length > 28 ? raw.slice(0, 28) + '…' : raw;
  const upper = (raw || '').toUpperCase();
  // Longest-match first so "PSELL FAIL" beats "PSELL"
  const codes = Object.keys(_reasonsMap).sort((a, b) => b.length - a.length);
  for (const code of codes) {
    if (upper.startsWith(code)) return _reasonsMap[code].fr;
  }
  return raw.length > 28 ? raw.slice(0, 28) + '…' : raw;
}

// ============================================================
// SSE Manager
// ============================================================

function sseConnection(onEvent) {
  return {
    source: null,
    status: 'disconnected', // connected | reconnecting | error | disconnected
    reconnectDelay: 2000,
    reconnectAttempts: 0,
    maxReconnectAttempts: 20,
    _pollInterval: null,

    connect() {
      this.disconnect();
      try {
        this.source = new EventSource('/api/stream');

        this.source.onopen = () => {
          this.status = 'connected';
          this.reconnectAttempts = 0;
          this.reconnectDelay = 2000;
          this._stopPolling();
          console.debug('[SSE] connected');
        };

        this.source.onerror = () => {
          this.status = 'reconnecting';
          this.source.close();
          this._scheduleReconnect();
        };

        for (const evt of ['position', 'price', 'services', 'heartbeat', 'error']) {
          this.source.addEventListener(evt, (e) => {
            try {
              const data = e.data === 'null' ? null : JSON.parse(e.data);
              onEvent(evt, data);
            } catch (err) {
              console.warn('[SSE] parse error', evt, err);
            }
          });
        }
      } catch (err) {
        console.warn('[SSE] connect error:', err);
        this.status = 'error';
        this._scheduleReconnect();
      }
    },

    disconnect() {
      if (this.source) {
        this.source.close();
        this.source = null;
      }
      this._stopPolling();
      this.status = 'disconnected';
    },

    _scheduleReconnect() {
      if (this.reconnectAttempts >= this.maxReconnectAttempts) {
        this.status = 'error';
        console.warn('[SSE] max reconnect attempts reached, falling back to polling');
        this._startPolling();
        return;
      }
      this.reconnectAttempts++;
      const delay = Math.min(this.reconnectDelay * Math.pow(1.5, this.reconnectAttempts - 1), 30000);
      console.debug(`[SSE] reconnecting in ${Math.round(delay)}ms (attempt ${this.reconnectAttempts})`);
      setTimeout(() => this.connect(), delay);
    },

    _startPolling() {
      if (this._pollInterval) return;
      this._pollInterval = setInterval(() => {
        onEvent('_poll', null);
      }, 5000);
    },

    _stopPolling() {
      if (this._pollInterval) {
        clearInterval(this._pollInterval);
        this._pollInterval = null;
      }
    },
  };
}

// ============================================================
// Chart helper (Lightweight Charts)
// ============================================================

function createEquityChart(containerId, data = []) {
  const LC = window.LightweightCharts;
  if (!LC) {
    console.warn('LightweightCharts not loaded');
    return null;
  }

  const el = document.getElementById(containerId);
  if (!el) return null;

  const chart = LC.createChart(el, {
    layout: {
      background: { color: 'transparent' },
      textColor: '#848E9C',
      fontSize: 11,
      fontFamily: "'Inter', system-ui, sans-serif",
    },
    grid: {
      vertLines: { color: 'rgba(43, 49, 57, 0.5)' },
      horzLines: { color: 'rgba(43, 49, 57, 0.5)' },
    },
    crosshair: {
      mode: LC.CrosshairMode.Normal,
    },
    rightPriceScale: {
      borderColor: 'rgba(43, 49, 57, 0.8)',
    },
    timeScale: {
      borderColor: 'rgba(43, 49, 57, 0.8)',
      timeVisible: true,
      secondsVisible: false,
    },
    handleScroll: { mouseWheel: false, pressedMouseMove: true },
    handleScale: { mouseWheel: false, pinch: true },
  });

  const series = chart.addAreaSeries({
    lineColor: '#F0B90B',
    topColor: 'rgba(240, 185, 11, 0.3)',
    bottomColor: 'rgba(240, 185, 11, 0.0)',
    lineWidth: 2,
    priceLineVisible: false,
    lastValueVisible: true,
    crosshairMarkerVisible: true,
    priceFormat: { type: 'custom', formatter: (p) => `${p >= 0 ? '+' : ''}${p.toFixed(2)}` },
  });

  if (data.length) {
    _setChartData(series, data);
  }

  // Responsive resize
  const ro = new ResizeObserver(() => {
    chart.applyOptions({ width: el.clientWidth });
  });
  ro.observe(el);

  return { chart, series, observer: ro };

  function _setChartData(s, rows) {
    const points = rows
      .filter(r => r.ts && r.ts > 0)
      .map(r => ({ time: Math.floor(r.ts), value: r.equity }))
      .sort((a, b) => a.time - b.time);
    // deduplicate times
    const seen = new Set();
    const deduped = points.filter(p => {
      if (seen.has(p.time)) return false;
      seen.add(p.time);
      return true;
    });
    s.setData(deduped);
  }
}

function updateChartData(chartObj, data) {
  if (!chartObj || !chartObj.series) return;
  const points = data
    .filter(r => r.ts && r.ts > 0)
    .map(r => ({ time: Math.floor(r.ts), value: r.equity }))
    .sort((a, b) => a.time - b.time);
  const seen = new Set();
  const deduped = points.filter(p => {
    if (seen.has(p.time)) return false;
    seen.add(p.time);
    return true;
  });
  chartObj.series.setData(deduped);
  chartObj.chart.timeScale().fitContent();
}

// ============================================================
// botDashboard() — Live page
// ============================================================

function botDashboard() {
  return {
    // State
    loading: true,
    error: null,

    // Bot
    botStatus: 'unknown',
    botSince: '',
    activeToken: '—',
    profile: '—',
    dryRun: false,

    // PnL
    pnlToday: null,
    pnlSession: null,
    pnlWeek: null,
    pnlFx: null,

    // Position
    position: null,
    positionLive: null,
    monitor: null,

    // Services
    services: [],

    // Trades
    trades: [],
    tradesLoading: false,

    // Equity
    equityRange: '7d',
    equityLoading: false,
    _chartObj: null,

    // SSE
    sse: null,
    sseStatus: 'disconnected',

    // Init
    async init() {
      await this.fetchSnapshot();
      this.fetchTrades();
      this._initSSE();
      this._initEquityChart();
      setInterval(() => this.fetchSnapshot(), 30000);
      setInterval(() => this.fetchTrades(), 30000);
    },

    // SSE
    _initSSE() {
      this.sse = sseConnection((evt, data) => this._handleSSEEvent(evt, data));
      this.sse.connect();
      // Expose SSE status reactively
      const updateStatus = () => {
        this.sseStatus = this.sse ? this.sse.status : 'disconnected';
        setTimeout(updateStatus, 500);
      };
      updateStatus();
    },

    _handleSSEEvent(evt, data) {
      if (evt === 'position') {
        this.positionLive = data;
      } else if (evt === 'services') {
        this.services = data || [];
      } else if (evt === '_poll') {
        this.fetchSnapshot();
      }
    },

    // API calls
    async fetchSnapshot() {
      try {
        const r = await fetch('/api/snapshot');
        if (!r.ok) throw new Error(await r.text());
        const d = await r.json();

        this.botStatus = d.bot?.status || 'unknown';
        this.botSince = d.bot?.since || '';
        this.activeToken = d.bot?.active_token || '—';
        this.profile = d.bot?.profile || '—';
        this.dryRun = !!d.bot?.dry_run;
        this.position = d.position;
        this.monitor = d.monitor || null;
        this.pnlToday = d.pnl?.today;
        this.pnlSession = d.pnl?.session;
        this.pnlWeek = d.pnl?.week;
        this.pnlFx = d.fx;
        this.services = d.services || [];
        this.loading = false;
        this.error = null;
      } catch (e) {
        this.error = e.message;
        this.loading = false;
      }
    },

    async fetchTrades() {
      this.tradesLoading = true;
      try {
        const r = await fetch('/api/trades?limit=10');
        if (!r.ok) throw new Error(await r.text());
        this.trades = await r.json();
      } catch (e) {
        console.warn('fetchTrades error:', e);
      } finally {
        this.tradesLoading = false;
      }
    },

    async loadEquity(range) {
      this.equityRange = range;
      this.equityLoading = true;
      try {
        const r = await fetch(`/api/equity?range=${range}`);
        if (!r.ok) throw new Error(await r.text());
        const d = await r.json();
        if (this._chartObj) {
          updateChartData(this._chartObj, d.points || []);
        }
      } catch (e) {
        console.warn('loadEquity error:', e);
      } finally {
        this.equityLoading = false;
      }
    },

    _initEquityChart() {
      this.$nextTick(async () => {
        this._chartObj = createEquityChart('equity-chart-main');
        await this.loadEquity(this.equityRange);
      });
    },

    // Derived
    get activePosition() {
      return this.positionLive || this.position;
    },

    get hasPosition() {
      const p = this.activePosition;
      return p && p.qty > 0;
    },

    get positionPnlClass() {
      const p = this.activePosition;
      if (!p) return 'neutral';
      return colorClass(p.pnl_usdc);
    },

    get botStatusClass() {
      if (this.botStatus === 'active') return 'badge-active';
      if (this.botStatus === 'failed') return 'badge-error';
      return 'badge-inactive';
    },

    get monitorMetrics() {
      return this.monitor?.metrics || {};
    },

    get monitorDecision() {
      return this.monitor?.decision || {};
    },

    get decisionLabel() {
      const reason = this.monitorDecision.reason || '';
      if (!reason) return '—';
      if (reason === 'NO_ENTRY_SIGNAL') return 'Pas de signal';
      return formatReason(reason);
    },

    get decisionBadgeClass() {
      const reason = this.monitorDecision.reason || '';
      if (!reason) return 'badge-inactive';
      if (reason.includes('SPREAD') || reason.includes('CIRCUIT') || reason.includes('MIN_NOTIONAL')) return 'badge-error';
      if (reason.includes('NO_ENTRY') || reason.includes('MOM') || reason.includes('RANGE') || reason.includes('TAPE')) return 'badge-warning';
      return 'badge-inactive';
    },

    pnlClass(v) { return colorClass(v); },
    fmtUsdc(v) { return fmt.usdc(v, true); },
    fmtPct(v) { return fmt.pct(v, true); },
    fmtPrice(v) { return fmt.price(v); },
    fmtQty(v) { return fmt.qty(v); },
    fmtTs(v) { return fmt.ts(v); },
    fmtAgo(v) { return fmt.ago(v); },
    fmtSymbol(v) { return fmt.symbol(v); },
    fmtDuration(v) { return fmt.duration(v); },
    fmtReason(v) { return formatReason(v); },
    fmtMetric(v, decimals = 2, suffix = '') {
      if (v == null || isNaN(v)) return '—';
      return `${Number(v).toFixed(decimals)}${suffix}`;
    },
    fmtAgeSec(v) {
      if (v == null || isNaN(v)) return '—';
      if (v < 60) return `${Math.round(v)}s`;
      if (v < 3600) return `${Math.floor(v / 60)}m ${Math.round(v % 60)}s`;
      return `${Math.floor(v / 3600)}h ${Math.floor((v % 3600) / 60)}m`;
    },
    binanceUrl(sym) {
      const base = fmt.symbol(sym || '');
      return `https://www.binance.com/en/trade/${base}_USDC?type=spot`;
    },
    signedColor,
  };
}

// ============================================================
// botStats() — Statistics page
// ============================================================

function botStats() {
  return {
    loading: true,
    error: null,

    // PnL buckets
    pnl: {},
    stats: {},
    fx: null,

    // Equity
    equityRange: '30d',
    equityLoading: false,
    _chartObj: null,

    // Contributions
    contributions: { top: [], bottom: [] },
    contributionsLoading: false,

    // Trades table
    trades: [],
    tradesPage: 1,
    tradesPerPage: 20,
    tradesFilter: '',
    tradesLoading: false,
    tradesSortKey: 'ts_epoch',
    tradesSortDir: -1,

    async init() {
      await Promise.all([
        this.fetchSnapshot(),
        this.fetchContributions(),
        this.fetchTrades(),
      ]);
      this._initEquityChart();
    },

    async fetchSnapshot() {
      try {
        const r = await fetch('/api/snapshot');
        if (!r.ok) throw new Error(await r.text());
        const d = await r.json();
        this.pnl = d.pnl || {};
        this.stats = d.stats || {};
        this.fx = d.fx;
        this.loading = false;
      } catch (e) {
        this.error = e.message;
        this.loading = false;
      }
    },

    async fetchContributions() {
      this.contributionsLoading = true;
      try {
        const r = await fetch('/api/contributions');
        if (!r.ok) throw new Error(await r.text());
        this.contributions = await r.json();
      } catch (e) {
        console.warn('fetchContributions error:', e);
      } finally {
        this.contributionsLoading = false;
      }
    },

    async fetchTrades() {
      this.tradesLoading = true;
      try {
        const r = await fetch('/api/trades?limit=500');
        if (!r.ok) throw new Error(await r.text());
        this.trades = await r.json();
      } catch (e) {
        console.warn('fetchTrades error:', e);
      } finally {
        this.tradesLoading = false;
      }
    },

    async loadEquity(range) {
      this.equityRange = range;
      this.equityLoading = true;
      try {
        const r = await fetch(`/api/equity?range=${range}`);
        if (!r.ok) throw new Error(await r.text());
        const d = await r.json();
        if (this._chartObj) updateChartData(this._chartObj, d.points || []);
      } catch (e) {
        console.warn('loadEquity error:', e);
      } finally {
        this.equityLoading = false;
      }
    },

    _initEquityChart() {
      this.$nextTick(async () => {
        this._chartObj = createEquityChart('equity-chart-stats');
        await this.loadEquity(this.equityRange);
      });
    },

    get filteredTrades() {
      const q = this.tradesFilter.toLowerCase();
      let rows = this.trades;
      if (q) {
        rows = rows.filter(t =>
          (t.symbol || '').toLowerCase().includes(q) ||
          (t.reason || '').toLowerCase().includes(q)
        );
      }
      rows = [...rows].sort((a, b) => {
        const va = a[this.tradesSortKey] ?? 0;
        const vb = b[this.tradesSortKey] ?? 0;
        return (va > vb ? 1 : va < vb ? -1 : 0) * this.tradesSortDir;
      });
      return rows;
    },

    get pagedTrades() {
      const start = (this.tradesPage - 1) * this.tradesPerPage;
      return this.filteredTrades.slice(start, start + this.tradesPerPage);
    },

    get totalPages() {
      return Math.max(1, Math.ceil(this.filteredTrades.length / this.tradesPerPage));
    },

    sortBy(key) {
      if (this.tradesSortKey === key) {
        this.tradesSortDir *= -1;
      } else {
        this.tradesSortKey = key;
        this.tradesSortDir = -1;
      }
      this.tradesPage = 1;
    },

    pnlClass(v) { return colorClass(v); },
    fmtUsdc(v) { return fmt.usdc(v, true); },
    fmtPct(v) { return fmt.pct(v, true); },
    fmtTs(v) { return fmt.ts(v); },
    fmtSymbol(v) { return fmt.symbol(v); },
    fmtPrice(v) { return fmt.price(v); },
    fmtReason(v) { return formatReason(v); },
    binanceUrl(sym) {
      const base = fmt.symbol(sym || '');
      return `https://www.binance.com/en/trade/${base}_USDC?type=spot`;
    },
  };
}

// ============================================================
// botServices() — Services page
// ============================================================

const ACTION_LOG_KEY = 'botdash_action_log';

function botServices() {
  return {
    services: [],
    loading: true,
    error: null,
    actionLog: [],
    actionInProgress: {},

    // Confirm modal state
    modal: {
      open: false,
      title: '',
      description: '',
      confirmWord: '',
      inputValue: '',
      action: null,
      dangerous: false,
    },

    async init() {
      this._loadLog();
      await this.fetchServices();
      setInterval(() => this.fetchServices(), 5000);
    },

    _loadLog() {
      try {
        const saved = localStorage.getItem(ACTION_LOG_KEY);
        if (saved) this.actionLog = JSON.parse(saved);
      } catch {}
    },

    _saveLog() {
      try {
        localStorage.setItem(ACTION_LOG_KEY, JSON.stringify(this.actionLog.slice(0, 50)));
      } catch {}
    },

    async fetchServices() {
      try {
        const r = await fetch('/api/snapshot');
        if (!r.ok) throw new Error(await r.text());
        const d = await r.json();
        this.services = d.services || [];
        this.loading = false;
      } catch (e) {
        this.error = e.message;
        this.loading = false;
      }
    },

    async doAction(unit, action) {
      const key = `${unit}-${action}`;
      this.actionInProgress[key] = true;
      try {
        const r = await fetch(`/api/services/${encodeURIComponent(unit)}/${action}`, {
          method: 'POST',
          credentials: 'same-origin',
        });
        const d = await r.json();
        this._log(unit, action, d.ok, d.output);
        await this.fetchServices();
        return d;
      } catch (e) {
        this._log(unit, action, false, e.message);
      } finally {
        this.actionInProgress[key] = false;
      }
    },

    requestAction(unit, action) {
      const isDangerous = action === 'stop';
      if (isDangerous) {
        this.modal = {
          open: true,
          title: `${action.toUpperCase()} ${unit}`,
          description: `Tapez "${action}" pour confirmer l'arrêt de ce service.`,
          confirmWord: action,
          inputValue: '',
          dangerous: true,
          action: () => this.doAction(unit, action),
        };
      } else {
        this.doAction(unit, action);
      }
    },

    async confirmModal() {
      if (this.modal.inputValue !== this.modal.confirmWord) return;
      this.modal.open = false;
      if (this.modal.action) await this.modal.action();
    },

    async doPanic() {
      this.modal = {
        open: true,
        title: 'PANIC — Stop ALL Services',
        description: 'Tapez "PANIC" (en majuscules) pour confirmer l\'arrêt immédiat de tous les services. Les positions ouvertes ne seront PAS fermées automatiquement.',
        confirmWord: 'PANIC',
        inputValue: '',
        dangerous: true,
        action: async () => {
          const r = await fetch('/api/bot/panic', { method: 'POST', credentials: 'same-origin' });
          const d = await r.json();
          this._log('ALL', 'panic', true, JSON.stringify(d.results));
          await this.fetchServices();
        },
      };
    },

    _log(unit, action, ok, output) {
      this.actionLog = [{
        ts: new Date().toISOString(),
        unit,
        action,
        ok,
        output: (output || '').trim().slice(0, 200),
      }, ...this.actionLog].slice(0, 50);
      this._saveLog();
    },

    isRunning(svc) {
      return svc.sub === 'running' || svc.sub === 'waiting' || svc.state === 'activating';
    },

    isStopped(svc) {
      return svc.sub === 'dead' || svc.state === 'inactive' || svc.state === 'failed';
    },

    stateClass(state) {
      if (state === 'active') return 'badge-active';
      if (state === 'failed') return 'badge-error';
      if (state === 'activating') return 'badge-warning';
      return 'badge-inactive';
    },

    dotClass(state) {
      if (state === 'active') return 'active';
      if (state === 'failed') return 'inactive';
      if (state === 'activating') return 'activating';
      return 'unknown';
    },

    serviceCardClass(state) {
      if (state === 'active') return 'service-card state-active';
      if (state === 'failed' || state === 'inactive') return 'service-card state-inactive';
      if (state === 'activating') return 'service-card state-activating';
      return 'service-card';
    },

    fmtTs(iso) {
      if (!iso) return '—';
      try { return new Date(iso).toLocaleString('fr-FR', { hour12: false }); }
      catch { return '—'; }
    },
    fmtDuration(iso) { return fmt.duration(iso); },
  };
}

// ============================================================
// botLogs() — Logs page
// ============================================================

function botLogs() {
  return {
    files: [],
    activeFile: '',
    lines: [],
    loading: false,
    filesLoading: true,
    error: null,

    tailSize: 200,
    autoScroll: true,
    searchQuery: '',
    refreshInterval: null,

    async init() {
      await this.fetchFiles();
      if (this.files.length) {
        await this.loadFile(this.files[0]);
      }
    },

    async fetchFiles() {
      this.filesLoading = true;
      try {
        const r = await fetch('/api/logs');
        if (!r.ok) throw new Error(await r.text());
        this.files = await r.json();
      } catch (e) {
        this.error = e.message;
      } finally {
        this.filesLoading = false;
      }
    },

    async loadFile(fileEntry) {
      const filePath = fileEntry?.path || fileEntry?.name || fileEntry;
      const fileName = fileEntry?.name || fileEntry;
      this.activeFile = fileName;
      this.loading = true;
      this.error = null;
      try {
        const r = await fetch(`/api/log_tail?name=${encodeURIComponent(filePath)}&n=${this.tailSize}`);
        if (!r.ok) throw new Error(await r.text());
        this.lines = await r.json();
        this._scrollToBottom();
      } catch (e) {
        this.error = e.message;
        this.lines = [];
      } finally {
        this.loading = false;
      }
    },

    async refresh() {
      if (!this.activeFile) return;
      const entry = this.files.find(f => f.name === this.activeFile) || this.activeFile;
      await this.loadFile(entry);
    },

    toggleAutoScroll() {
      this.autoScroll = !this.autoScroll;
      if (this.autoScroll) {
        this._startAutoRefresh();
      } else {
        this._stopAutoRefresh();
      }
    },

    _startAutoRefresh() {
      this._stopAutoRefresh();
      this.refreshInterval = setInterval(() => {
        if (this.autoScroll && this.activeFile) this.refresh();
      }, 2000);
    },

    _stopAutoRefresh() {
      if (this.refreshInterval) {
        clearInterval(this.refreshInterval);
        this.refreshInterval = null;
      }
    },

    _scrollToBottom() {
      if (!this.autoScroll) return;
      this.$nextTick(() => {
        const el = document.getElementById('log-content-area');
        if (el) el.scrollTop = el.scrollHeight;
      });
    },

    changeTailSize(n) {
      this.tailSize = parseInt(n);
      if (this.activeFile) this.loadFile(this.activeFile);
    },

    download() {
      if (!this.activeFile) return;
      const entry = this.files.find(f => f.name === this.activeFile);
      const filePath = entry?.path || this.activeFile;
      window.open(`/api/log_tail?name=${encodeURIComponent(filePath)}&n=5000`);
    },

    get filteredLines() {
      if (!this.searchQuery) return this.lines;
      const q = this.searchQuery.toLowerCase();
      return this.lines.filter(l => l.toLowerCase().includes(q));
    },

    lineClass(line) {
      const l = line.toUpperCase();
      if (l.includes('ERROR') || l.includes('CRITICAL') || l.includes('EXCEPTION')) return 'log-line log-error';
      if (l.includes('WARN')) return 'log-line log-warn';
      if (l.includes('DEBUG')) return 'log-line log-debug';
      return 'log-line log-info';
    },

    highlightLine(line) {
      if (!this.searchQuery) return this._escapeHtml(line);
      const q = this.searchQuery;
      const escaped = this._escapeHtml(line);
      const re = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
      return escaped.replace(re, m => `<mark class="log-search-match">${m}</mark>`);
    },

    _escapeHtml(s) {
      return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    },

    fmtSize(bytes) {
      if (bytes < 1024) return `${bytes}B`;
      if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)}KB`;
      return `${(bytes / 1048576).toFixed(1)}MB`;
    },
  };
}

// ============================================================
// Toast manager (global)
// ============================================================

function toastManager() {
  return {
    toasts: [],
    _id: 0,

    show(msg, type = 'info', duration = 4000) {
      const id = ++this._id;
      this.toasts.push({ id, msg, type });
      setTimeout(() => this.dismiss(id), duration);
    },

    dismiss(id) {
      const idx = this.toasts.findIndex(t => t.id === id);
      if (idx >= 0) this.toasts.splice(idx, 1);
    },

    success(msg) { this.show(msg, 'success'); },
    error(msg)   { this.show(msg, 'error', 6000); },
    warn(msg)    { this.show(msg, 'warn'); },
    info(msg)    { this.show(msg, 'info'); },
  };
}

// ============================================================
// UTC Clock
// ============================================================

function utcClock() {
  return {
    time: '',
    init() {
      this._update();
      setInterval(() => this._update(), 1000);
    },
    _update() {
      const now = new Date();
      const pad = n => String(n).padStart(2, '0');
      this.time = `${now.getUTCFullYear()}-${pad(now.getUTCMonth()+1)}-${pad(now.getUTCDate())} `
        + `${pad(now.getUTCHours())}:${pad(now.getUTCMinutes())}:${pad(now.getUTCSeconds())} UTC`;
    },
  };
}

// ============================================================
// Theme toggle
// ============================================================

function themeToggle() {
  return {
    dark: true,
    init() {
      const saved = localStorage.getItem('theme');
      this.dark = saved !== 'light';
      this._apply();
    },
    toggle() {
      this.dark = !this.dark;
      localStorage.setItem('theme', this.dark ? 'dark' : 'light');
      this._apply();
    },
    _apply() {
      document.documentElement.classList.toggle('light', !this.dark);
    },
  };
}

// ============================================================
// Register Alpine components
// ============================================================

document.addEventListener('alpine:init', () => {
  _loadReasons();
  Alpine.data('botDashboard', botDashboard);
  Alpine.data('botStats', botStats);
  Alpine.data('botServices', botServices);
  Alpine.data('botLogs', botLogs);
  Alpine.data('toastManager', toastManager);
  Alpine.data('utcClock', utcClock);
  Alpine.data('themeToggle', themeToggle);
});
