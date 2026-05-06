/* dashboard btc range */
(function () {
  const $ = (sel) => document.querySelector(sel);
  let currentLogName = "";

  function fmt(n, digits = 2) {
    if (n === null || n === undefined || Number.isNaN(Number(n))) return "--";
    const v = Number(n);
    return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }

  function fmtPrice(n) {
    return fmt(n, 2);
  }

  function fmtPct(n) {
    if (n === null || n === undefined || Number.isNaN(Number(n))) return "--";
    return `${fmt(Number(n) * 100, 3)}%`;
  }

  function fmtAgo(sec) {
    if (sec === null || sec === undefined || Number.isNaN(Number(sec))) return "--";
    const s = Math.max(0, Math.floor(Number(sec)));
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s / 60)} min`;
    return `${Math.floor(s / 3600)} h`;
  }

  function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  }

  function setHTML(id, html) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = html;
  }

  function parseDateValue(ts) {
    if (!ts) return null;
    const d = new Date(String(ts));
    return Number.isNaN(d.getTime()) ? null : d;
  }

  function fmtDateTime(ts) {
    const d = parseDateValue(ts);
    return d ? d.toLocaleString() : "--";
  }

  function pnlClass(v) {
    const n = Number(v || 0);
    if (n > 0) return "pos-good";
    if (n < 0) return "pos-bad";
    return "";
  }

  function badgeState(state) {
    const s = String(state || "").toLowerCase();
    if (s === "active") return `<span class="pill pill-green">active</span>`;
    if (s === "failed") return `<span class="pill pill-red">failed</span>`;
    if (s === "inactive") return `<span class="pill pill-amber">inactive</span>`;
    return `<span class="pill">${state || "--"}</span>`;
  }

  async function jget(url) {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return await r.json();
  }

  async function jpost(url, body) {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || `${r.status} ${r.statusText}`);
    return data;
  }

  function sidebarInit() {
    const btn = $("#btn-menu");
    const sb = $("#sidebar");
    const overlay = $("#sidebar-overlay");
    if (!btn || !sb) return;
    const syncMenu = (open) => {
      sb.classList.toggle("open", open);
      if (overlay) overlay.classList.toggle("active", open);
      btn.classList.toggle("active", open);
    };
    btn.addEventListener("click", () => syncMenu(!sb.classList.contains("open")));
    if (overlay) overlay.addEventListener("click", () => syncMenu(false));
  }

  function renderRecent(rows, targetId) {
    if (!rows || !rows.length) {
      setHTML(targetId, `<div class="empty-state">Aucune ligne exploitable pour l'instant.</div>`);
      return;
    }
    const header = `<table class="table"><thead><tr>
      <th>Heure</th><th>Action</th><th>Raison</th><th class="right">Prix</th><th class="right">PnL USDC</th>
    </tr></thead><tbody>`;
    const body = rows.map((row) => {
      const pnlVal = row.pnl === null || row.pnl === undefined ? null : parseFloat(row.pnl);
      const pnlCls = pnlClass(pnlVal);
      const pnlSign = pnlVal == null ? "" : (pnlVal >= 0 ? "+" : "");
      const pnlTxt = pnlVal == null ? "--" : `${pnlSign}${fmt(pnlVal, 4)}`;
      const reason = (row.reason || "--").split(" ").slice(0, 4).join(" ");
      const action = row.event || row.side || "--";
      const isBuy = action.includes("BUY");
      return `<tr>
        <td style="white-space:nowrap;font-size:.88rem">${fmtDateTime(row.ts_utc)}</td>
        <td style="${isBuy ? "color:var(--success);font-weight:800" : "font-weight:800"}">${action}</td>
        <td style="font-size:.88rem">${reason}</td>
        <td class="right" style="font-size:.88rem">${row.price == null ? "--" : fmt(row.price, 2)}</td>
        <td class="right"><span class="${pnlCls}" style="font-size:1.1rem;font-weight:800">${pnlTxt}</span></td>
      </tr>`;
    }).join("");
    setHTML(targetId, header + body + `</tbody></table>`);
  }

  function renderUnits(units, targetId) {
    if (!units || !units.length) {
      setHTML(targetId, `<div class="empty-state compact">Aucune unite disponible.</div>`);
      return;
    }
    const html = units.map((unit) => `
      <div class="service-card">
        <div class="service-card-top">
          <div>
            <div class="service-name">${unit.unit}</div>
            <div class="service-meta">${unit.details || "--"}</div>
          </div>
          ${badgeState(unit.state)}
        </div>
        <div class="service-since">${unit.since || "aucune date"}</div>
      </div>
    `).join("");
    setHTML(targetId, html);
  }

  async function loadDashboard() {
    const status = await jget("/api/status");
    const cfg = status.config || {};
    const st = status.status || {};
    const pos = st.position || null;
    const snapshot = st.snapshot || {};
    const units = status.units || [];
    const recent = status.recent || [];
    const botUnit = units.find((u) => u.unit === "btc-range-bot.service") || units[0] || {};

    setText("sig-bot-state", String(botUnit.state || "--").toUpperCase());
    setText("sig-bot-meta", botUnit.since || "Etat du service principal.");
    setText("sig-symbol", cfg.symbol || "--");
    setText("sig-profile", `profile ${cfg.profile || "--"} | dry ${cfg.dry_run || "--"}`);
    setText("sig-state", st.state || "--");
    setText("sig-state-meta", st.last_hold_reason || (pos ? "position ouverte" : "aucune exposition"));

    const last = recent[0] || null;
    setText("sig-last-action", last ? (last.event || last.side || "--") : "--");
    setText("sig-last-meta", last ? `${fmtDateTime(last.ts_utc)} | ${last.reason || "--"}` : "Aucune action recente.");

    setText("range-ba", `${fmtPrice(st.bid)} / ${fmtPrice(st.ask)}`);
    setText("range-spread", st.spread_pct === null || st.spread_pct === undefined ? "--" : `${fmt(st.spread_pct, 4)}%`);
    setText("range-low", fmtPrice(snapshot.low));
    setText("range-mid", fmtPrice(snapshot.mid));
    setText("range-high", fmtPrice(snapshot.high));
    setText("range-width", snapshot.rangePct === null || snapshot.rangePct === undefined ? "--" : `${fmt(snapshot.rangePct * 100, 3)}%`);
    setText("range-drift", snapshot.driftPct === null || snapshot.driftPct === undefined ? "--" : `${fmt(snapshot.driftPct * 100, 3)}%`);
    setText("range-reason", st.last_hold_reason || "--");
    setText("range-pill", snapshot.timeframe ? `${snapshot.timeframe} / ${snapshot.barCount || "--"} barres` : "--");

    if (pos) {
      setText("pos-pill", "IN_POS");
      setText("pos-qty", fmt(pos.qty, 6));
      setText("pos-entry", fmtPrice(pos.entry));
      setText("pos-stop", fmtPrice(pos.stop));
      setText("pos-target", fmtPrice(pos.target));
      setText("pos-high", fmtPrice(pos.high));
      setText("pos-protect", pos.protectArmed ? "arme" : "non");
      setText("pos-age", fmtAgo(((new Date()) - parseDateValue(status.status?.ts || status.ts || "")) / 1000));
      const latent = (Number(st.bid || 0) - Number(pos.entry || 0)) * Number(pos.qty || 0);
      const pnlEl = document.getElementById("pos-pnl");
      if (pnlEl) {
        const pnlSign = latent >= 0 ? "+" : "";
        pnlEl.textContent = `${pnlSign}${fmt(latent, 4)} USDC`;
        pnlEl.className = `v ${pnlClass(latent)}`.trim();
        pnlEl.style.fontSize = "1.3rem";
        pnlEl.style.fontWeight = "800";
      }
    } else {
      setText("pos-pill", "IDLE");
      ["pos-qty", "pos-entry", "pos-stop", "pos-target", "pos-high", "pos-pnl", "pos-age"].forEach((id) => setText(id, "--"));
      setText("pos-protect", "--");
    }

    setText("svc-pill", `${units.filter((u) => String(u.state).toLowerCase() === "active").length}/${units.length} actifs`);
    renderUnits(units, "service-health");
    setText("recent-pill", `${recent.length} lignes`);
    renderRecent(recent, "recent-feed");
  }

  async function loadServices() {
    const data = await jget("/api/services");
    const units = data.units || [];
    setText("svc2-pill", `${units.filter((u) => String(u.state).toLowerCase() === "active").length}/${units.length} actifs`);
    renderUnits(units, "service-health-ops");

    document.querySelectorAll("[data-unit][data-action]").forEach((btn) => {
      if (btn.dataset.bound === "1") return;
      btn.dataset.bound = "1";
      btn.addEventListener("click", async () => {
        const unit = btn.dataset.unit;
        const action = btn.dataset.action;
        const out = document.getElementById("ctl-out");
        if (out) out.innerHTML = `<div class="muted">Commande en cours...</div>`;
        try {
          const res = await jpost("/api/control", { unit, action });
          if (out) {
            out.innerHTML = `<div class="action-item ${res.ok ? "action-item-good" : "action-item-bad"}">
              <div class="action-title">${res.ok ? "Commande executee" : "Commande en echec"}</div>
              <div class="action-meta">${unit} | ${action}</div>
              <pre class="action-pre">${res.output || "--"}</pre>
            </div>`;
          }
          await loadServices();
        } catch (err) {
          if (out) out.innerHTML = `<div class="action-item action-item-bad"><div class="action-title">Erreur</div><div class="action-meta">${String(err.message || err)}</div></div>`;
        }
      });
    });
  }

  async function loadStats() {
    const data = await jget("/api/stats");
    const st = data.stats || {};
    setText("kpi-trades", st.closed_trades ?? "--");
    const totalEl = document.getElementById("kpi-total");
    if (totalEl) {
      totalEl.textContent = st.total_pnl_usdc === null || st.total_pnl_usdc === undefined ? "--" : `${fmt(st.total_pnl_usdc, 4)} USDC`;
      totalEl.className = `kpi-v ${pnlClass(st.total_pnl_usdc)}`.trim();
    }
    setText("kpi-winrate", st.winrate === null || st.winrate === undefined ? "--" : `${fmt(st.winrate, 2)}%`);
    setText("kpi-avg", st.avg_pnl_usdc === null || st.avg_pnl_usdc === undefined ? "--" : `${fmt(st.avg_pnl_usdc, 4)} USDC`);
    setText("kpi-best", st.best_trade_usdc === null || st.best_trade_usdc === undefined ? "--" : `${fmt(st.best_trade_usdc, 4)} USDC`);
    setText("kpi-worst", st.worst_trade_usdc === null || st.worst_trade_usdc === undefined ? "--" : `${fmt(st.worst_trade_usdc, 4)} USDC`);
    setText("pnl-pill", st.closed_trades ? `${st.closed_trades} trades fermes` : "aucun trade ferme");

    const last = st.last_sell || null;
    setText("lasttrade-pill", last ? "dernier trade dispo" : "aucun trade ferme");
    setText("lt-time", last ? fmtDateTime(last.ts_utc) : "--");
    setText("lt-event", last ? (last.event || last.side || "--") : "--");
    setText("lt-price", last ? fmtPrice(last.price) : "--");
    setText("lt-qty", last && last.qty !== null && last.qty !== undefined ? fmt(last.qty, 6) : "--");
    const pnlEl = document.getElementById("lt-pnl");
    if (pnlEl) {
      pnlEl.textContent = last && last.pnl !== null && last.pnl !== undefined ? `${fmt(last.pnl, 4)} USDC` : "--";
      pnlEl.className = `v ${pnlClass(last?.pnl)}`.trim();
    }
    setText("lt-reason", last ? (last.reason || "--") : "--");
    setText("lt-source", last ? (last.src || "--") : "--");

    const recent = st.recent_closed || [];
    setText("stats-recent-pill", `${recent.length} lignes`);
    renderRecent(recent, "stats-recent-feed");
  }

  async function loadLogs() {
    const data = await jget("/api/logs");
    const files = data.files || [];
    setText("logs-pill", `${files.length} fichiers`);
    if (!files.length) {
      setHTML("logs-list", `<div class="empty-state compact">Aucun fichier pour ce symbole.</div>`);
      setText("log-current", "Aucun fichier");
      setText("log-view", "Aucun fichier disponible.");
      return;
    }

    const html = files.map((file) => `
      <button class="log-row ${currentLogName === file.name ? "active" : ""}" data-log-name="${file.name}">
        <span class="log-row-name">${file.name}</span>
        <span class="log-row-meta">${fmt(file.size / 1024, 1)} KB | ${fmtDateTime(file.mtime)}</span>
      </button>
    `).join("");
    setHTML("logs-list", html);

    document.querySelectorAll("[data-log-name]").forEach((btn) => {
      if (btn.dataset.bound === "1") return;
      btn.dataset.bound = "1";
      btn.addEventListener("click", async () => {
        currentLogName = btn.dataset.logName;
        await loadTail(200);
        await loadLogs();
      });
    });

    if (!currentLogName) {
      currentLogName = files[0].name;
      await loadTail(200);
      await loadLogs();
    }
  }

  async function loadTail(n) {
    if (!currentLogName) return;
    const data = await jget(`/api/log_tail?name=${encodeURIComponent(currentLogName)}&n=${encodeURIComponent(n)}`);
    setText("log-current", currentLogName);
    setText("log-view", data.text || "");
  }

  function bindCommonActions() {
    const refresh = document.getElementById("btn-refresh");
    if (refresh) refresh.addEventListener("click", () => window.location.reload());
    const tail200 = document.getElementById("btnTail200");
    if (tail200) tail200.addEventListener("click", () => loadTail(200));
    const tail600 = document.getElementById("btnTail600");
    if (tail600) tail600.addEventListener("click", () => loadTail(600));
  }

  async function boot() {
    sidebarInit();
    bindCommonActions();
    try {
      if (document.getElementById("sig-bot-state")) {
        await loadDashboard();
      }
      if (document.getElementById("service-health-ops")) {
        await loadServices();
      }
      if (document.getElementById("kpi-trades")) {
        await loadStats();
      }
      if (document.getElementById("logs-list")) {
        await loadLogs();
      }
    } catch (err) {
      console.error(err);
    }
  }

  window.addEventListener("DOMContentLoaded", boot);
})();
