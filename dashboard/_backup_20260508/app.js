/* botdash front */
(function () {
  const $ = (sel) => document.querySelector(sel);
  let currentLogName = "";
  let serviceControlsBound = false;
  let logControlsBound = false;

  const UNIT_LABELS = {
    "botdash.service": "Dashboard",
    "binance-aifout-bot.service": "Main Bot",
    "token-profile-selector.service": "Selector Run",
    "token-profile-selector.timer": "Selector Timer",
  };

  function fmt(n, digits = 2) {
    if (n === null || n === undefined || Number.isNaN(Number(n))) return "--";
    const v = Number(n);
    return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }
  function fmtInt(n) {
    if (n === null || n === undefined || Number.isNaN(Number(n))) return "--";
    return Number(n).toLocaleString();
  }
  function td(label, value, className = "") {
    const extra = className ? ` class="${className}"` : "";
    return `${value}`;
  }
  function escapeHtml(text) {
    return String(text || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }
  function unitLabel(name) {
    return UNIT_LABELS[name] || name || "--";
  }
  function findUnit(units, name) {
    return (units || []).find((u) => u.unit === name) || null;
  }
  function isUnitActive(unit) {
    return String(unit?.state || "").toLowerCase() === "active";
  }
  function badgeState(state) {
    const s = (state || "").toLowerCase();
    if (s.includes("active") || s.includes("running")) return `<span class="badge active">actif</span>`;
    if (s.includes("failed")) return `<span class="badge failed">echec</span>`;
    if (s.includes("inactive") || s.includes("dead")) return `<span class="badge inactive">arrete</span>`;
    return `<span class="badge">${state || "--"}</span>`;
  }
  function pnlClass(v) {
    const n = Number(v || 0);
    if (n > 0) return "pos-good";
    if (n < 0) return "pos-bad";
    return "";
  }

  function fmtPnlPair(usdc, eur) {
    if (usdc === null || usdc === undefined || Number.isNaN(Number(usdc))) return "--";
    return `${fmt(usdc, 2)} USDC | ${eur === null || eur === undefined ? "--" : fmt(eur, 2) + " EUR"}`;
  }

  function fmtBucket(bucket) {
    if (!bucket || bucket.usdc === null || bucket.usdc === undefined) return "--";
    return fmtPnlPair(bucket.usdc, bucket.eur);
  }

  function actionLabel(r) {
    const ev = String(r?.event || r?.side || "").toUpperCase();
    if (ev.includes("BUY")) return "Achat execute";
    if (ev.includes("SELL")) return "Vente executee";
    if (ev === "DECIDE_HOLD") return "Attente";
    return ev || "--";
  }

  function reasonLabel(r) {
    const reason = String(r?.reason || "").toUpperCase();
    if (!reason) return "--";
    if (reason === "TP") return "Objectif de gain atteint";
    if (reason === "PROTECT") return "Gain protege";
    if (reason === "STOP") return "Sortie de protection";
    if (reason === "TRAIL") return "Trailing stop";
    if (reason === "TIME") return "Sortie par duree max";
    if (reason === "TIME_HARD") return "Sortie par duree limite";
    if (reason === "STALE") return "Position stagnante";
    if (reason === "HOLD_MOM") return "Momentum insuffisant";
    if (reason === "HOLD_RANGE") return "Mouvement trop faible";
    if (reason === "HOLD_CHASE") return "Prix deja parti";
    if (reason === "HOLD_SPREAD") return "Spread trop large";
    if (reason === "HOLD_BAL") return "Solde non disponible";
    if (reason === "HOLD_MIN_NOTIONAL") return "Montant trop petit";
    if (reason === "HOLD_TICK") return "Micro-tendance insuffisante";
    if (reason === "HOLD_FLOW") return "Flux de ticks fragile";
    if (reason === "HOLD_REENTRY") return "Cooldown actif";
    if (reason === "HOLD_RECLAIM") return "Prix pas recupere";
    if (reason === "HOLD_SIGNAL") return "Signaux indisponibles";
    if (reason === "HOLD_EMA") return "EMA non confirmee";
    if (reason === "HOLD_RSI") return "RSI hors plage";
    if (reason === "HOLD_VOL") return "Volume insuffisant";
    if (reason.startsWith("PBUY")) return "Signal achat valide";
    if (reason.startsWith("PSELL")) return "Signal vente valide";
    return reason.replaceAll("_", " ").toLowerCase();
  }

  function fmtDateTime(ts) {
    const d = parseDateValue(ts);
    if (!d) return "--";
    return d.toLocaleString();
  }

  function parseDateValue(ts) {
    if (ts === null || ts === undefined || ts === "") return null;
    const raw = String(ts).trim();
    if (!raw) return null;
    if (/^\d{10}$/.test(raw)) {
      const d = new Date(Number(raw) * 1000);
      return Number.isNaN(d.getTime()) ? null : d;
    }
    if (/^\d{13}$/.test(raw)) {
      const d = new Date(Number(raw));
      return Number.isNaN(d.getTime()) ? null : d;
    }
    const d = new Date(raw);
    if (Number.isNaN(d.getTime())) return ts;
    return d;
  }

  function fmtAgo(sec) {
    if (sec === null || sec === undefined || Number.isNaN(Number(sec))) return "--";
    const s = Math.max(0, Math.floor(Number(sec)));
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s / 60)} min`;
    if (s < 86400) return `${Math.floor(s / 3600)} h`;
    return `${Math.floor(s / 86400)} j`;
  }

  function setPnlText(id, usdc, eur) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = fmtPnlPair(usdc, eur);
    el.className = `v ${pnlClass(usdc)}`.trim();
  }

  function setMetricPnl(id, bucket) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = fmtBucket(bucket);
    el.className = `kpi-v ${pnlClass(bucket?.usdc)}`.trim();
  }

function setHTML(id, html) {
  const el = document.getElementById(id);
  if (el) el.innerHTML = html;
}

function binanceSpotUrl(symbol) {
  if (!symbol || symbol === "--") return null;
  const s = String(symbol).toUpperCase().trim();
  const m = s.match(/^(\[A-Z0-9\]+)(USDC|USDT|EUR|BTC|BNB)$/);
  if (!m) return null;
  const base = m[1];
  const quote = m[2];
  return `https://www.binance.com/fr/trade/${base}_${quote}?type=spot`;
}

  async function jget(url) {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return await r.json();
  }

  function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
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
    document.addEventListener("click", (e) => {
      if (window.innerWidth > 1099) return;
      const t = e.target;
      if (!sb.contains(t) && t !== btn && !btn.contains(t)) syncMenu(false);
    });
  }

  async function loadHeader() {
    try {
      const st = await jget("/api/status");
      setText("hdr-host", st.host || "--");
      setText("hdr-utc", st.utc || "--");
      setText("kv-base", st.base || "--");
      setText("kv-logs", st.logs || "--");
      const fx = st.fx_usdc_eur;
      setText("kv-fx", fx ? `1 USDC ≈ ${fmt(fx, 4)} EUR` : "--");
      const bot = findUnit(st.units, "binance-aifout-bot.service");
      const dash = findUnit(st.units, "botdash.service");
      let status = "Incident";
      if (isUnitActive(bot)) status = "Trading actif";
      else if (isUnitActive(dash)) status = "Bot arrete";
      setText("footer-status", status);
    } catch (e) {
      setText("footer-status", "Incident");
    }
  }

  function renderWallet(data) {
    const tbl = $("#wallet-table tbody");
    const stack = $("#wallet-stack");

    const rows = (data.rows || []).filter((r) => {
      const values = [
        Number(r.free ?? 0),
        Number(r.locked ?? 0),
        Number(r.total ?? 0),
        Number(r.value_usdc ?? r.usdc_value ?? 0),
        Number(r.value_eur ?? r.eur_value ?? 0),
      ];
      return values.some((v) => Number.isFinite(v) && Math.abs(v) > 1e-12);
    });
    const fx = data.fx_usdc_eur;
    let totalUsdc = 0;
    let totalEur = 0;

    if (!rows.length) {
      if (tbl) tbl.innerHTML = `<tr><td colspan="6">wallet vide</td></tr>`;
      if (stack) stack.innerHTML = `<div class="card"><p>Aucune ligne portefeuille exploitable.</p></div>`;
      return;
    }

    const mappedRows = rows.map((r) => {
      const usdc = (r.value_usdc ?? r.usdc_value ?? null);
      const eur = (r.value_eur ?? r.eur_value ?? null);
      if (typeof usdc === "number") totalUsdc += usdc;
      if (typeof eur === "number") totalEur += eur;

      return {
        rowHtml: `
          ${td("Asset", `<strong>${r.asset}</strong>`)}
          ${td("Free", fmt(r.free, 6), "right")}
          ${td("Locked", fmt(r.locked, 6), "right")}
          ${td("Total", fmt(r.total, 6), "right")}
          ${td("≈ USDC", usdc === null ? "--" : fmt(usdc, 2), "right")}
          ${td("≈ EUR", eur === null ? "--" : fmt(eur, 2), "right")}
        `,
        asset: r.asset,
        free: r.free,
        locked: r.locked,
        total: r.total,
        usdc,
        eur,
      };
    });

    if (tbl) {
      tbl.innerHTML = mappedRows.map((item) => `<tr>${item.rowHtml}</tr>`).join("");
    }

    if (stack) {
      const baseTotal = totalUsdc > 0 ? totalUsdc : mappedRows.reduce((acc, item) => acc + Math.max(Number(item.usdc || 0), 0), 0);
      stack.innerHTML = mappedRows.slice(0, 10).map((item) => {
        const share = (baseTotal > 0 && item.usdc != null) ? ((Number(item.usdc) / baseTotal) * 100.0) : null;
        return `<div class="wallet-card">
          <div class="wallet-card-header">
            <span class="wallet-asset">${escapeHtml(item.asset || "--")}</span>
            <span class="wallet-value">${item.usdc == null ? "--" : fmt(item.usdc, 2) + " USDC"}</span>
          </div>
          <div class="wallet-card-body">
            <div>Total: ${fmt(item.total, 6)}</div>
            <div>Libre / bloque: ${fmt(item.free, 6)} / ${fmt(item.locked, 6)}</div>
            <div>Valeur EUR: ${item.eur == null ? "--" : fmt(item.eur, 2) + " EUR"}</div>
            <div>Poids: ${share == null ? "--" : fmt(share, 1) + "%"}</div>
          </div>
        </div>`;
      }).join("");
    }

    const totalText = `Total ≈ ${fmt(totalUsdc, 2)} USDC | ${fmt(totalEur, 2)} EUR`;
    const pill = $("#wallet-total");
    if (pill) pill.textContent = totalText;
  }

  function renderSignalStrip(st, trades) {
    if (!$("#sig-bot-value")) return;
    const units = st.units || [];
    const bot = findUnit(units, "binance-aifout-bot.service");
    const token = st.token || {};
    const pos = st.position || {};
    const last = (trades?.rows || [])[0] || null;

    setText("sig-bot-value", isUnitActive(bot) ? "Actif" : "Arrete");
    setText("sig-bot-meta", bot?.since ? `depuis ${fmtDateTime(bot.since)}` : (bot?.details || "etat inconnu"));

    setText("sig-token-value", token.symbol || "--");
    const runMode = token.dry_run == null ? "--" : (String(token.dry_run) === "1" ? "dry run" : "reel");
    setText("sig-token-meta", `${token.profile || "--"} | ${runMode}`);

    if (pos?.symbol) {
      setText("sig-position-value", pos.symbol);
      const pnlText = pos.unreal_pnl_usdc == null ? "PnL --" : `PnL ${fmt(pos.unreal_pnl_usdc, 2)} USDC`;
      setText("sig-position-meta", `qty ${fmt(pos.qty, 6)} | ${pnlText}`);
    } else {
      setText("sig-position-value", "Aucune");
      setText("sig-position-meta", "Pas d'exposition ouverte.");
    }

    if (last) {
      setText("sig-last-value", actionLabel(last));
      setText("sig-last-meta", `${last.symbol || "--"} | ${reasonLabel(last)}`);
    } else {
      setText("sig-last-value", "Aucune");
      setText("sig-last-meta", "Pas encore de decision.");
    }
  }

  function renderServiceHealth(units, selector = "#service-health", pillId = "") {
    const box = document.querySelector(selector);
    if (!box) return;
    const rows = units || [];
    if (!rows.length) {
      box.innerHTML = `<div class="card"><p>aucune unite</p></div>`;
      return;
    }
    box.innerHTML = rows.map((u) => {
      const active = isUnitActive(u);
      const metaBits = [];
      if (u.details) metaBits.push(u.details);
      if (u.since) metaBits.push(fmtDateTime(u.since));
      return `<div class="service-card ${active ? 'active' : 'inactive'}">
        <div class="service-name">${unitLabel(u.unit)}</div>
        <div class="service-unit">${u.unit}</div>
        <div class="service-state">${badgeState(u.state)}</div>
        <div class="service-meta">${metaBits.join(" | ") || "etat non remonte"}</div>
      </div>`;
    }).join("");

    const pill = pillId ? document.getElementById(pillId) : null;
    if (pill) {
      const activeCount = rows.filter((u) => isUnitActive(u)).length;
      pill.textContent = `${activeCount}/${rows.length} actifs`;
    }
  }

  function renderServices(units) {
    const tbl = $("#services-table tbody");
    const grid = $("#services-grid");
    const rows = units || [];
    if (!rows.length) {
      if (tbl) tbl.innerHTML = `<tr><td colspan="4">aucun service</td></tr>`;
      if (grid) grid.innerHTML = `<div class="card"><p>Aucune unite remontee.</p></div>`;
      return;
    }

    if (tbl) {
      tbl.innerHTML = rows.map(u => {
        const st = (u.state || "--");
        const since = u.since ? fmtDateTime(u.since) : "--";
        const details = u.details || "";
        return `<tr>
          <td><strong>${unitLabel(u.unit)}</strong></td>
          <td>${u.unit}</td>
          <td>${badgeState(st)}</td>
          <td>${since}<br><small>${details}</small></td>
        </tr>`;
      }).join("");
    }

    if (grid) {
      grid.innerHTML = rows.map((u) => {
        const st = (u.state || "--");
        const since = u.since ? fmtDateTime(u.since) : "--";
        const details = u.details || "etat non remonte";
        return `<div class="service-card">
          <div class="service-name">${unitLabel(u.unit)}</div>
          <div class="service-unit">${u.unit}</div>
          <div class="service-state">${badgeState(st)}</div>
          <div class="service-meta">Depuis: ${since}<br>Lecture: ${escapeHtml(details)}</div>
        </div>`;
      }).join("");
    }

    const pill = $("#svc-pill");
    if (pill) pill.textContent = `${rows.length} unités`;
  }

  function drawEquityChart(points) {
    const canvas = document.getElementById("equityChart");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const width = Math.max(canvas.clientWidth || canvas.parentElement?.clientWidth || 640, 320);
    const height = Math.max(canvas.clientHeight || 240, 180);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    ctx.fillStyle = "rgba(148, 163, 184, 0.75)";
    ctx.font = "12px sans-serif";

    if (!points || !points.length) {
      ctx.fillText("Pas assez de donnees pour tracer l'equity.", 16, 28);
      return;
    }

    const values = points.map((p) => Number(p.usdc ?? 0)).filter((v) => Number.isFinite(v));
    if (!values.length) {
      ctx.fillText("Pas assez de donnees pour tracer l'equity.", 16, 28);
      return;
    }

    const minV = Math.min(...values);
    const maxV = Math.max(...values);
    const range = Math.max(maxV - minV, 1e-9);
    const padL = 52;
    const padR = 16;
    const padT = 16;
    const padB = 26;
    const plotW = Math.max(width - padL - padR, 10);
    const plotH = Math.max(height - padT - padB, 10);

    ctx.strokeStyle = "rgba(148, 163, 184, 0.18)";
    ctx.lineWidth = 1;
    for (let i = 0; i < 3; i += 1) {
      const y = padT + (plotH / 2) * i;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(width - padR, y);
      ctx.stroke();
    }

    const yFor = (v) => padT + plotH - (((v - minV) / range) * plotH);
    const xFor = (idx) => padL + ((plotW * idx) / Math.max(points.length - 1, 1));

    ctx.fillStyle = "rgba(148, 163, 184, 0.75)";
    ctx.fillText(fmt(maxV, 2), 8, padT + 4);
    ctx.fillText(fmt((minV + maxV) / 2, 2), 8, padT + plotH / 2 + 4);
    ctx.fillText(fmt(minV, 2), 8, padT + plotH + 4);

    ctx.beginPath();
    points.forEach((p, idx) => {
      const x = xFor(idx);
      const y = yFor(Number(p.usdc ?? 0));
      if (idx === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = "rgba(59, 130, 246, 0.95)";
    ctx.lineWidth = 2;
    ctx.stroke();

    const last = points[points.length - 1];
    const lastX = xFor(points.length - 1);
    const lastY = yFor(Number(last.usdc ?? 0));
    ctx.fillStyle = "rgba(96, 165, 250, 1)";
    ctx.beginPath();
    ctx.arc(lastX, lastY, 3.5, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = "rgba(148, 163, 184, 0.75)";
    ctx.fillText(points[0].ts ? new Date(points[0].ts).toLocaleDateString() : "", padL, height - 8);
    const endLabel = last.ts ? new Date(last.ts).toLocaleDateString() : "";
    const labelWidth = ctx.measureText(endLabel).width;
    ctx.fillText(endLabel, width - padR - labelWidth, height - 8);
  }

  function renderStatsRankings(rankings) {
    const top = rankings?.top || [];
    const bottom = rankings?.bottom || [];
    const tbl = $("#stats-tokens-table tbody");
    const topBox = $("#stats-top-list");
    const bottomBox = $("#stats-bottom-list");
    const rows = [
      ...top.map((r) => ({ ...r, bucket: "Top" })),
      ...bottom.map((r) => ({ ...r, bucket: "Flop" })),
    ];
    if (!rows.length) {
      if (tbl) tbl.innerHTML = `<tr><td colspan="5">pas assez d'historique</td></tr>`;
      if (topBox) topBox.innerHTML = `<div class="card"><p>Pas assez d'historique.</p></div>`;
      if (bottomBox) bottomBox.innerHTML = `<div class="card"><p>Pas assez d'historique.</p></div>`;
      return;
    }

    if (tbl) {
      tbl.innerHTML = rows.map((r) => {
        const cls = r.bucket === "Top" ? "rank-good" : "rank-bad";
        const url = binanceSpotUrl(r.symbol);
        const symbolHtml = url ? `<a href="${url}" target="_blank">${r.symbol}</a>` : r.symbol;
        return `<tr class="${cls}">
          <td>${r.bucket}</td>
          <td><strong>${symbolHtml}</strong></td>
          <td class="right ${pnlClass(r.pnl_usdc)}">${fmt(r.pnl_usdc, 2)}</td>
          <td class="right">${fmtInt(r.trades)}</td>
          <td>${fmtDateTime(r.last_ts)}</td>
        </tr>`;
      }).join("");
    }

    const buildScoreCards = (list, tone) => {
      if (!list.length) return `<div class="card"><p>Aucune ligne.</p></div>`;
      return list.map((r) => {
        const url = binanceSpotUrl(r.symbol);
        const symbolHtml = url ? `<a href="${url}" target="_blank">${escapeHtml(r.symbol)}</a>` : escapeHtml(r.symbol);
        return `<div class="token-card ${tone}">
          <div class="token-header">
            <span class="token-badge">${tone === "good" ? "Top" : "Flop"}</span>
            <span class="token-symbol">${symbolHtml}</span>
          </div>
          <div class="token-pnl ${pnlClass(r.pnl_usdc)}">${fmt(r.pnl_usdc, 2)} USDC</div>
          <div class="token-meta">${fmtInt(r.trades)} trades | dernier ${fmtDateTime(r.last_ts)}</div>
        </div>`;
      }).join("");
    };

    if (topBox) topBox.innerHTML = buildScoreCards(top, "good");
    if (bottomBox) bottomBox.innerHTML = buildScoreCards(bottom, "bad");
    const pill = $("#stats-token-pill");
    if (pill) pill.textContent = `${top.length} top | ${bottom.length} flop`;
  }

  function renderRecentClosed(rows) {
    const tbl = $("#stats-recent-table tbody");
    const feed = $("#stats-recent-feed");
    const list = rows || [];
    if (!list.length) {
      if (tbl) tbl.innerHTML = `<tr><td colspan="6">aucun trade ferme</td></tr>`;
      if (feed) feed.innerHTML = `<div class="empty-state">Aucun trade ferme.</div>`;
      return;
    }

    if (tbl) {
      tbl.innerHTML = list.map((r) => {
        const url = binanceSpotUrl(r.symbol);
        const symbolHtml = url ? `<a href="${url}" target="_blank">${r.symbol}</a>` : r.symbol;
        const pnlUSDC = r.pnl_usdc != null ? parseFloat(r.pnl_usdc) : null;
        const pnlSign = pnlUSDC == null ? "" : (pnlUSDC >= 0 ? "+" : "");
        const reasonShort = (r.event || "--").replace(/^SELL_FILLED\s*/, "").split(" ")[0];
        return `<tr>
          <td>${fmtDateTime(r.ts)}</td>
          <td><strong>${symbolHtml}</strong></td>
          <td class="right" style="font-size:1.1rem;font-weight:800" class="${pnlClass(pnlUSDC)}"><span class="${pnlClass(pnlUSDC)}">${pnlUSDC == null ? "--" : pnlSign + fmt(pnlUSDC, 2)} USDC</span></td>
          <td>${reasonShort}</td>
          <td class="muted" style="font-size:.82rem">${(r.src || "--").split("/").pop()}</td>
        </tr>`;
      }).join("");
    }

    if (feed) {
      feed.innerHTML = `<table class="table"><thead><tr>
        <th>Heure</th><th>Token</th><th class="right">PnL USDC</th><th>Sortie</th>
      </tr></thead><tbody>` +
      list.map((r) => {
        const url = binanceSpotUrl(r.symbol);
        const symbolHtml = url ? `<a href="${url}" target="_blank">${escapeHtml(r.symbol)}</a>` : escapeHtml(r.symbol);
        const pnlUSDC = r.pnl_usdc != null ? parseFloat(r.pnl_usdc) : null;
        const pnlSign = pnlUSDC == null ? "" : (pnlUSDC >= 0 ? "+" : "");
        const reasonFull = (r.event || "--");
        const reasonShort = reasonFull.replace(/^SELL_FILLED\s*/, "").replace(/^BUY_FILLED\s*/, "BUY: ").split(" ").slice(0, 3).join(" ");
        return `<tr>
          <td style="white-space:nowrap;font-size:.88rem">${fmtDateTime(r.ts)}</td>
          <td><strong>${symbolHtml}</strong></td>
          <td class="right"><span class="${pnlClass(pnlUSDC)}" style="font-size:1.1rem;font-weight:800">${pnlUSDC == null ? "--" : pnlSign + fmt(pnlUSDC, 2)}</span></td>
          <td style="font-size:.88rem">${escapeHtml(reasonShort)}</td>
        </tr>`;
      }).join("") + `</tbody></table>`;
    }
    const pill = $("#stats-recent-pill");
    if (pill) pill.textContent = `${list.length} lignes`;
  }

  function renderTokenNow(st) {
    if (!$("#token-now")) return;
    const sym = (st.token?.symbol || st.token?.SYMBOL || "--");
    const url = binanceSpotUrl(sym);
    if (url) {
      setHTML("tn-symbol", `<a href="${url}" target="_blank">${sym}</a>`);
    } else {
      setText("tn-symbol", sym);
    }
    setText("tn-profile", st.token?.profile || st.token?.PROFILE || "--");
    setText("tn-dry", String(st.token?.dry_run ?? "--"));
    const pill = $("#token-now-pill");
    if (pill) {
      pill.innerHTML = url ? `<a href="${url}" target="_blank">${sym}</a>` : sym;
    }
  }

  function renderPosition(st) {
    if (!$("#pos-live")) return;
    const p = st.position || {};
    const psym = p.symbol || "--";
    const purl = binanceSpotUrl(psym);
    if (purl) setHTML("pos-symbol", `<a href="${purl}" target="_blank">${psym}</a>`);
    else setText("pos-symbol", psym);
    setText("pos-qty", p.qty === null || p.qty === undefined ? "--" : fmt(p.qty, 6));
    setText("pos-entry", p.entry === null || p.entry === undefined ? "--" : fmt(p.entry, 8));
    const ba = (p.bid !== null && p.ask !== null) ? `${fmt(p.bid, 8)} / ${fmt(p.ask, 8)}` : "--";
    setText("pos-ba", ba);

    const pnlUsdc = p.unreal_pnl_usdc;
    const pnlEur = p.unreal_pnl_eur;
    const pnlSign = (pnlUsdc == null) ? "" : (pnlUsdc >= 0 ? "+" : "");
    const pnlTxt = (pnlUsdc === null || pnlUsdc === undefined)
      ? "--"
      : `${pnlSign}${fmt(pnlUsdc, 2)} USDC${pnlEur != null ? " | " + fmt(pnlEur, 2) + " EUR" : ""}`;
    const pnlEl = $("#pos-pnl");
    if (pnlEl) {
      pnlEl.textContent = pnlTxt;
      pnlEl.style.fontSize = "1.3rem";
      pnlEl.style.fontWeight = "800";
      pnlEl.className = `v ${pnlClass(pnlUsdc)}`;
    }
    setText("pos-reason", p.last_reason || "--");
    const pill = $("#pos-pill");
    if (pill) pill.textContent = p.symbol ? "ouverte" : "aucune";
  }

  function renderTokensTable(summary) {
    const tbl = $("#tokens-table tbody");
    const topBox = $("#token-top-list");
    const riskBox = $("#token-risk-list");
    const allRows = (summary.rows || []).filter((r) => Number(r.pnl_usdc ?? 0) || Number(r.trades ?? 0));
    const rows = allRows.slice(0, 10);
    if (!rows.length) {
      if (tbl) tbl.innerHTML = `<tr><td colspan="9">aucun trade</td></tr>`;
      if (topBox) topBox.innerHTML = `<div class="card"><p>Aucune contribution positive.</p></div>`;
      if (riskBox) riskBox.innerHTML = `<div class="card"><p>Aucun point faible.</p></div>`;
      return;
    }

    if (tbl) {
      tbl.innerHTML = rows.map(r => {
        const usdc = r.pnl_usdc ?? 0;
        const eur = r.pnl_eur ?? null;
        const maxOpenUsdc = r.max_open_usdc ?? null;
        const buyUsdc = r.buy_usdc ?? null;
        const sellUsdc = r.sell_usdc ?? null;
        const pnlPct = r.pnl_pct ?? null;
        const cls = pnlClass(usdc);
        const period = r.last_ts ? `${r.last_ts}` : "--";
        const pctTxt = pnlPct == null ? "--" : `${fmt(pnlPct, 1)}%`;
        return `<tr>
          ${td("Token", `<strong>${r.symbol}</strong>`)}
          ${td("Periode", `${period}`)}
          ${td("Capital Max", maxOpenUsdc === null ? "--" : fmt(maxOpenUsdc, 2), "right")}
          ${td("Turnover Achat", buyUsdc === null ? "--" : fmt(buyUsdc, 2), "right")}
          ${td("Turnover Vente", sellUsdc === null ? "--" : fmt(sellUsdc, 2), "right")}
          ${td("Net USDC", fmt(usdc, 2), `right ${cls}`.trim())}
          ${td("Net EUR", eur === null ? "--" : fmt(eur, 2), `right ${cls}`.trim())}
          ${td("Net %", pctTxt, `right ${cls}`.trim())}
          ${td("Trades fermes", fmtInt(r.trades), "right")}
        </tr>`;
      }).join("");
    }

    const topRows = [...allRows]
      .sort((a, b) => Number(b.pnl_usdc || 0) - Number(a.pnl_usdc || 0))
      .filter((r) => Number(r.pnl_usdc || 0) > 0)
      .slice(0, 5);
    const riskRows = [...allRows]
      .sort((a, b) => Number(a.pnl_usdc || 0) - Number(b.pnl_usdc || 0))
      .filter((r) => Number(r.pnl_usdc || 0) < 0)
      .slice(0, 5);

    const buildTokenList = (list, tone) => {
      if (!list.length) return `<div class="card"><p>${tone === "good" ? "Aucun contributeur positif." : "Aucune perte par token."}</p></div>`;
      return list.map((r) => {
        const url = binanceSpotUrl(r.symbol);
        const symbolHtml = url ? `<a href="${url}" target="_blank">${escapeHtml(r.symbol)}</a>` : escapeHtml(r.symbol);
        const pctTxt = r.pnl_pct == null ? "--" : `${fmt(r.pnl_pct, 1)}%`;
        return `<div class="token-card ${tone}">
          <div class="token-header">
            <span class="token-badge">${tone === "good" ? "Contribution +" : "Contribution -"}</span>
            <span class="token-symbol">${symbolHtml}</span>
          </div>
          <div class="token-pnl ${pnlClass(r.pnl_usdc)}">${fmt(r.pnl_usdc, 2)} USDC</div>
          <div class="token-meta">${fmtInt(r.trades)} trades fermes | ${pctTxt} | dernier ${fmtDateTime(r.last_ts)}</div>
        </div>`;
      }).join("");
    };

    if (topBox) topBox.innerHTML = buildTokenList(topRows, "good");
    if (riskBox) riskBox.innerHTML = buildTokenList(riskRows, "bad");

    const pill = $("#tr-pill");
    if (pill) pill.textContent = `${allRows.length} tokens`;
  }

  function renderDecisions(trades) {
    const tbl = $("#decisions-table tbody");
    const feed = $("#decisions-feed");
    const rows = (trades.rows || []).slice(0, 12);
    if (!rows.length) {
      if (tbl) tbl.innerHTML = `<tr><td colspan="7">pas de decisions</td></tr>`;
      if (feed) feed.innerHTML = `<div class="card"><p>Pas encore de decision.</p></div>`;
      return;
    }

    if (tbl) {
      tbl.innerHTML = rows.map(r => {
        const ts = r.ts_utc || r.ts || "--";
        const symbol = r.symbol || "--";
        const price = r.price == null || r.price === "" ? "--" : fmt(r.price, 8);
        const qty = r.qty == null || r.qty === "" ? "--" : fmt(r.qty, 6);
        const pnl = r.pnl == null || r.pnl === "" ? "--" : fmt(r.pnl, 2);
        const cls = pnlClass(r.pnl);
        const url = binanceSpotUrl(symbol);
        const symbolHtml = url ? `<a href="${url}" target="_blank">${symbol}</a>` : symbol;
        return `<tr>
          ${td("Heure", `${fmtDateTime(ts)}`)}
          ${td("Token", `<strong>${symbolHtml}</strong>`)}
          ${td("Action", `<strong>${actionLabel(r)}</strong>`)}
          ${td("Lecture", `${reasonLabel(r)}`)}
          ${td("Prix", price, "right")}
          ${td("Qte", qty, "right")}
          ${td("Impact", pnl, `right ${cls}`.trim())}
        </tr>`;
      }).join("");
    }

    if (feed) {
      feed.innerHTML = `<table class="table"><thead><tr>
        <th>Heure</th><th>Token</th><th>Action</th><th>Raison</th><th class="right">Prix</th><th class="right">PnL USDC</th>
      </tr></thead><tbody>` +
      rows.map((r) => {
        const ts = r.ts_utc || r.ts || "--";
        const symbol = r.symbol || "--";
        const url = binanceSpotUrl(symbol);
        const symbolHtml = url ? `<a href="${url}" target="_blank">${escapeHtml(symbol)}</a>` : escapeHtml(symbol);
        const pnlVal = r.pnl == null || r.pnl === "" ? null : parseFloat(r.pnl);
        const pnlSign = pnlVal == null ? "" : (pnlVal >= 0 ? "+" : "");
        const pnlDisplay = pnlVal == null ? "--" : `${pnlSign}${fmt(pnlVal, 2)}`;
        const action = actionLabel(r);
        const isBuy = action.includes("Achat") || action.includes("BUY");
        const actionStyle = isBuy ? "color:var(--success);font-weight:800" : (pnlVal != null ? "" : "");
        return `<tr>
          <td style="white-space:nowrap;font-size:.88rem">${fmtDateTime(ts)}</td>
          <td><strong>${symbolHtml}</strong></td>
          <td style="${actionStyle}"><strong>${escapeHtml(action)}</strong></td>
          <td style="font-size:.88rem;max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(reasonLabel(r))}</td>
          <td class="right" style="font-size:.88rem">${r.price == null || r.price === "" ? "--" : fmt(r.price, 6)}</td>
          <td class="right"><span class="${pnlClass(r.pnl)}" style="font-size:1.1rem;font-weight:800">${pnlDisplay}</span></td>
        </tr>`;
      }).join("") + `</tbody></table>`;
    }
    const pill = $("#dec-pill");
    if (pill) pill.textContent = `${rows.length} lignes`;
  }

  async function loadDashboard() {
    const [st, wallet, summary, trades] = await Promise.all([
      jget("/api/status"),
      jget("/api/wallet"),
      jget("/api/summary"),
      jget("/api/trades"),
    ]);
    renderSignalStrip(st, trades);
    renderTokenNow(st);
    renderPosition(st);
    renderServiceHealth(st.units, "#service-health", "service-health-pill");
    renderWallet(wallet);
    renderTokensTable(summary);
    renderDecisions(trades);
  }

  function renderControlResult(result) {
    const out = $("#ctl-out");
    if (!out) return;
    if (!result) {
      out.innerHTML = `<div class="card"><p>Aucune commande envoyee.</p></div>`;
      return;
    }
    const ok = !!result.ok;
    const feedback = String(result.output || result.error || "").trim() || "Aucun retour.";
    out.innerHTML = `<div class="control-result ${ok ? 'success' : 'error'}">
      <div class="control-header">${ok ? "Commande appliquee" : "Commande refusee"}</div>
      <div class="control-meta">${unitLabel(result.unit)} | ${String(result.action || "").toUpperCase()}</div>
      <div class="control-badge ${ok ? 'ok' : 'fail'}">${ok ? 'OK' : 'ECHEC'}</div>
      <div class="control-feedback">${escapeHtml(feedback)}</div>
    </div>`;
  }

  function bindServiceControls() {
    if (serviceControlsBound) return;
    document.addEventListener("click", async (e) => {
      const btn = e.target.closest("button[data-unit][data-action]");
      if (!btn || !window.location.pathname.startsWith("/services")) return;
      btn.disabled = true;
      try {
        const unit = btn.getAttribute("data-unit");
        const action = btn.getAttribute("data-action");
        const r = await fetch("/api/control", {
          method: "POST",
          headers: {"Content-Type":"application/json"},
          body: JSON.stringify({ unit, action }),
        });
        const j = await r.json().catch(() => ({ ok: false, error: "bad json", unit, action }));
        renderControlResult(j);
        await loadServicesPage();
      } catch (err) {
        renderControlResult({ ok: false, unit: btn.getAttribute("data-unit"), action: btn.getAttribute("data-action"), error: err.message });
      } finally {
        btn.disabled = false;
      }
    });
    serviceControlsBound = true;
  }

  async function loadServicesPage() {
    const st = await jget("/api/status");
    renderServiceHealth(st.units, "#service-health-ops", "svc2-pill");
    renderServices(st.units);
    bindServiceControls();
  }

  async function openLog(name, n = 200) {
    const view = $("#log-view");
    if (!name) return;
    const t = await jget(`/api/log_tail?name=${encodeURIComponent(name)}&n=${encodeURIComponent(n)}`);
    currentLogName = name;
    if (view) view.textContent = (t.text || (t.lines ? t.lines.join("\n") : ""));
    setText("log-current", name);
  }

  function renderQuickLogs(rows) {
    const box = $("#log-quick");
    if (!box) return;
    const defs = [
      { label: "Dernier trades.log", match: (name) => /_trades\.log$/i.test(name) },
      { label: "Dernier errors.log", match: (name) => /_errors\.log$/i.test(name) },
      { label: "Dernier trades.csv", match: (name) => /_trades\.csv$/i.test(name) },
      { label: "Fichier le plus recent", match: () => true },
    ];
    box.innerHTML = defs.map((def) => {
      const hit = rows.find((row) => def.match(row.name));
      if (!hit) return ``;
      return `<button class="btn-quick" data-log-quick="${escapeHtml(hit.name)}">${def.label}: ${escapeHtml(hit.name)}</button>`;
    }).join("");
  }

  async function loadLogsPage() {
    const tbl = $("#logs-table tbody");
    const listBox = $("#logs-list");
    const j = await jget("/api/logs");
    const rows = j.files || [];
    if (currentLogName && !rows.some((row) => row.name === currentLogName)) currentLogName = "";
    const pill = $("#logs-pill");
    if (pill) pill.textContent = `${rows.length} fichiers`;
    renderQuickLogs(rows);
    if (!rows.length) {
      if (tbl) tbl.innerHTML = `<tr><td colspan="3">aucun fichier</td></tr>`;
      if (listBox) listBox.innerHTML = `<div class="card"><p>Aucun fichier detecte.</p></div>`;
      setText("log-current", "Aucun fichier");
      return;
    }

    const visibleRows = rows.slice(0, 50);
    if (tbl) {
      tbl.innerHTML = visibleRows.map(f => {
        const name = f.name;
        return `<tr>
          <td><strong>${name}</strong></td>
          <td class="right">${fmtInt(f.size || 0)}</td>
          <td>${fmtDateTime(f.mtime)}</td>
          <td><button class="btn-sm" data-log="${name}">Ouvrir</button></td>
        </tr>`;
      }).join("");
    }

    if (listBox) {
      listBox.innerHTML = visibleRows.map((f) => {
        const name = f.name;
        return `<div class="log-file-card">
          <div class="log-file-name">${escapeHtml(name)}</div>
          <div class="log-file-meta">${fmtDateTime(f.mtime)} | ${fmtInt(f.size || 0)} o</div>
          <button class="btn-sm" data-log="${name}">Ouvrir</button>
        </div>`;
      }).join("");
    }

    document.querySelectorAll("button[data-log]").forEach(b => {
      b.addEventListener("click", async () => {
        const name = b.getAttribute("data-log") || "";
        b.disabled = true;
        try {
          await openLog(name, 200);
          await loadLogsPage();
        } catch (e) {
          setText("log-current", "Erreur");
          const view = $("#log-view");
          if (view) view.textContent = `error: ${e.message}`;
        } finally {
          b.disabled = false;
        }
      });
    });

    document.querySelectorAll("button[data-log-quick]").forEach((b) => {
      b.addEventListener("click", async () => {
        const name = b.getAttribute("data-log-quick") || "";
        b.disabled = true;
        try {
          await openLog(name, 200);
          await loadLogsPage();
        } catch (e) {
          setText("log-current", "Erreur");
          const view = $("#log-view");
          if (view) view.textContent = `error: ${e.message}`;
        } finally {
          b.disabled = false;
        }
      });
    });

    if (!currentLogName && rows[0]?.name) {
      await openLog(rows[0].name, 200);
      await loadLogsPage();
    }

    bindLogControls();
  }

  function bindLogControls() {
    if (logControlsBound) return;
    const bindTail = (id, lines) => {
      const btn = document.getElementById(id);
      if (!btn) return;
      btn.addEventListener("click", async () => {
        if (!currentLogName) return;
        btn.disabled = true;
        try {
          await openLog(currentLogName, lines);
        } finally {
          btn.disabled = false;
        }
      });
    };
    bindTail("btnTail200", 200);
    bindTail("btnTail600", 600);
    logControlsBound = true;
  }

  async function loadStatisticsPage() {
    const pill = $("#pnl-pill");
    try {
      const pnl = await jget("/api/pnl");
      if (pill) pill.textContent = pnl.source ? `source: ${String(pnl.source).split("/").pop()}` : "--";

      setMetricPnl("kpi-today", pnl.today);
      setMetricPnl("kpi-session", pnl.session);
      setMetricPnl("kpi-week", pnl.week);
      setMetricPnl("kpi-month", pnl.month);
      setMetricPnl("kpi-year", pnl.year);
      setText("kpi-trades", pnl.trades != null ? fmtInt(pnl.trades) : "--");
      setText("kpi-winrate", pnl.winrate != null ? `${fmt(pnl.winrate,1)}%` : "--");
      setText("kpi-pf", pnl.profit_factor != null ? fmt(pnl.profit_factor,2) : "--");

      const quality = pnl.quality || {};
      setPnlText("q-avg-win", quality.avg_win_usdc, quality.avg_win_eur);
      setPnlText("q-avg-loss", quality.avg_loss_usdc, quality.avg_loss_eur);
      setPnlText("q-best", quality.best_trade_usdc, quality.best_trade_eur);
      setPnlText("q-worst", quality.worst_trade_usdc, quality.worst_trade_eur);
      setText("q-win-streak", quality.longest_win_streak != null ? `${fmtInt(quality.longest_win_streak)} trades` : "--");
      setText("q-loss-streak", quality.longest_loss_streak != null ? `${fmtInt(quality.longest_loss_streak)} trades` : "--");
      const ddEl = document.getElementById("q-drawdown");
      if (ddEl) {
        ddEl.textContent = fmtPnlPair(quality.max_drawdown_usdc, quality.max_drawdown_eur);
        ddEl.className = "v pos-bad";
      }
      setText("q-source", pnl.source ? String(pnl.source).split("/").pop() : "--");
      const qualityPill = $("#quality-pill");
      if (qualityPill) qualityPill.textContent = quality.max_drawdown_usdc != null ? `DD max ${fmt(quality.max_drawdown_usdc, 2)} USDC` : "--";

      const last = pnl.last_trade || {};
      const ltPill = $("#lasttrade-pill");
      const lastUrl = binanceSpotUrl(last.symbol);
      if (lastUrl) setHTML("lt-symbol", `<a href="${lastUrl}" target="_blank">${last.symbol}</a>`);
      else setText("lt-symbol", last.symbol || "--");
      setText("lt-time", fmtDateTime(last.ts));
      setText("lt-age", fmtAgo(last.age_sec));
      setPnlText("lt-pnl", last.pnl_usdc, last.pnl_eur);
      setText("lt-price", last.price != null ? fmt(last.price, 8) : "--");
      setText("lt-qty", last.qty != null ? fmt(last.qty, 6) : "--");
      setText("lt-event", last.event || "--");
      setText("lt-source", last.src || "--");
      if (ltPill) ltPill.textContent = last.age_sec != null ? `il y a ${fmtAgo(last.age_sec)}` : "aucun";

      renderStatsRankings(pnl.token_rankings);
      renderRecentClosed(pnl.recent_closed);
      drawEquityChart(pnl.equity_points || []);
    } catch (e) {
      if (pill) pill.textContent = `KO: ${e.message}`;
      renderStatsRankings({ top: [], bottom: [] });
      renderRecentClosed([]);
      drawEquityChart([]);
    }
  }

  async function refreshAll() {
    await loadHeader();
    const p = window.location.pathname;
    if (p === "/" || p === "") return await loadDashboard();
    if (p.startsWith("/services")) return await loadServicesPage();
    if (p.startsWith("/logs")) return await loadLogsPage();
    if (p.startsWith("/statistics")) return await loadStatisticsPage();
  }

  function init() {
    sidebarInit();
    const btnR = $("#btn-refresh");
    if (btnR) btnR.addEventListener("click", (e) => { e.preventDefault(); refreshAll().catch(() => location.reload()); });
    refreshAll();

    // auto refresh: dashboard + services states
    setInterval(() => {
      const p = window.location.pathname;
      if (p === "/" || p.startsWith("/services") || p.startsWith("/statistics")) refreshAll();
    }, 5000);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
