/* botdash front */
(function () {
  const $ = (sel) => document.querySelector(sel);

  function fmt(n, digits = 2) {
    if (n === null || n === undefined || Number.isNaN(Number(n))) return "--";
    const v = Number(n);
    return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }
  function fmtInt(n) {
    if (n === null || n === undefined || Number.isNaN(Number(n))) return "--";
    return Number(n).toLocaleString();
  }
  function badgeState(state) {
    const s = (state || "").toLowerCase();
    if (s.includes("active") || s.includes("running")) return `<span class="pill pill-green">active</span>`;
    if (s.includes("failed")) return `<span class="pill pill-red">failed</span>`;
    if (s.includes("inactive") || s.includes("dead")) return `<span class="pill pill-amber">inactive</span>`;
    return `<span class="pill">${state || "--"}</span>`;
  }
  function pnlClass(v) {
    const n = Number(v || 0);
    if (n > 0) return "pos-good";
    if (n < 0) return "pos-bad";
    return "";
  }


function setHTML(id, html) {
  const el = document.getElementById(id);
  if (el) el.innerHTML = html;
}

function binanceSpotUrl(symbol) {
  if (!symbol || symbol === "--") return null;
  const s = String(symbol).toUpperCase().trim();
  // Typical: BASEQUOTE like NEWTUSDC -> NEWT_USDC
  const m = s.match(/^([A-Z0-9]+)(USDC|USDT|EUR|BTC|BNB)$/);
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
    if (!btn || !sb) return;
    btn.addEventListener("click", () => sb.classList.toggle("open"));
    document.addEventListener("click", (e) => {
      if (window.innerWidth > 960) return;
      const t = e.target;
      if (!sb.contains(t) && t !== btn && !btn.contains(t)) sb.classList.remove("open");
    });
  }

  async function loadHeader() {
    try {
      const st = await jget("/api/status");
      setText("hdr-host", `host: ${st.host || "--"}`);
      setText("hdr-utc", `utc: ${st.utc || "--"}`);
      setText("kv-base", st.base || "--");
      setText("kv-logs", st.logs || "--");
      const fx = st.fx_usdc_eur;
      setText("kv-fx", fx ? `1 USDC ≈ ${fmt(fx, 4)} EUR` : "--");
      setText("footer-status", st.ok ? "OK" : "KO");
    } catch (e) {
      setText("footer-status", `KO: ${e.message}`);
    }
  }

  function renderWallet(data) {
    const tbl = $("#wallet-table tbody");
    if (!tbl) return;

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
      tbl.innerHTML = `<tr><td colspan="6" class="muted">wallet vide</td></tr>`;
      return;
    }

    tbl.innerHTML = rows.map(r => {
      const usdc = (r.value_usdc ?? r.usdc_value ?? null);
      const eur = (r.value_eur ?? r.eur_value ?? null);
      if (typeof usdc === "number") totalUsdc += usdc;
      if (typeof eur === "number") totalEur += eur;

      return `<tr>
        <td><b>${r.asset}</b></td>
        <td class="right">${fmt(r.free, 6)}</td>
        <td class="right">${fmt(r.locked, 6)}</td>
        <td class="right">${fmt(r.total, 6)}</td>
        <td class="right">${usdc === null ? "--" : fmt(usdc, 2)}</td>
        <td class="right">${eur === null ? "--" : fmt(eur, 2)}</td>
      </tr>`;
    }).join("");

    const totalText = `Total ≈ ${fmt(totalUsdc, 2)} USDC | ${fmt(totalEur, 2)} EUR`;
    const pill = $("#wallet-total");
    if (pill) pill.textContent = totalText;
  }

  function renderServices(units) {
    const tbl = $("#services-table tbody");
    if (!tbl) return;
    const rows = units || [];
    if (!rows.length) {
      tbl.innerHTML = `<tr><td colspan="4" class="muted">aucun service</td></tr>`;
      return;
    }
    tbl.innerHTML = rows.map(u => {
      const st = (u.state || "--");
      const since = u.since || "--";
      const details = u.details || "";
      return `<tr>
        <td><b>${u.unit}</b></td>
        <td>${badgeState(st)}</td>
        <td>${since}</td>
        <td class="muted">${details}</td>
      </tr>`;
    }).join("");

    const pill = $("#svc-pill") || $("#svc2-pill");
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

  function renderTokenNow(st) {
    if (!$("#token-now")) return;
    const sym = (st.token?.symbol || st.token?.SYMBOL || "--");
    const url = binanceSpotUrl(sym);
    if (url) {
      setHTML("tn-symbol", `<a class="link" href="${url}" target="_blank" rel="noreferrer">${sym}</a>`);
    } else {
      setText("tn-symbol", sym);
    }
    setText("tn-profile", st.token?.profile || st.token?.PROFILE || "--");
    setText("tn-dry", String(st.token?.dry_run ?? "--"));
    const pill = $("#token-now-pill");
    if (pill) {
      pill.innerHTML = url ? `<a class="link" href="${url}" target="_blank" rel="noreferrer">${sym}</a>` : sym;
    }
  }

  function renderPosition(st) {
    if (!$("#pos-live")) return;
    const p = st.position || {};
    const psym = p.symbol || "--";
    const purl = binanceSpotUrl(psym);
    if (purl) setHTML("pos-symbol", `<a class="link" href="${purl}" target="_blank" rel="noreferrer">${psym}</a>`);
    else setText("pos-symbol", psym);
    setText("pos-qty", p.qty === null || p.qty === undefined ? "--" : fmt(p.qty, 6));
    setText("pos-entry", p.entry === null || p.entry === undefined ? "--" : fmt(p.entry, 8));
    const ba = (p.bid !== null && p.ask !== null) ? `${fmt(p.bid, 8)} / ${fmt(p.ask, 8)}` : "--";
    setText("pos-ba", ba);

    const pnlUsdc = p.unreal_pnl_usdc;
    const pnlEur = p.unreal_pnl_eur;
    const pnlTxt = (pnlUsdc === null || pnlUsdc === undefined) ? "--" : `${fmt(pnlUsdc, 2)} USDC | ${pnlEur == null ? "--" : fmt(pnlEur, 2) + " EUR"}`;
    const pnlEl = $("#pos-pnl");
    if (pnlEl) {
      pnlEl.textContent = pnlTxt;
      pnlEl.className = `v ${pnlClass(pnlUsdc)}`;
    }
    setText("pos-reason", p.last_reason || "--");
    const pill = $("#pos-pill");
    if (pill) pill.textContent = p.symbol ? "ouverte" : "aucune";
  }

  function renderTokensTable(summary) {
    const tbl = $("#tokens-table tbody");
    if (!tbl) return;
    const fx = summary.fx_usdc_eur;
    const rows = (summary.rows || []).slice(0, 10);
    if (!rows.length) {
      tbl.innerHTML = `<tr><td colspan="5" class="muted">aucun trade</td></tr>`;
      return;
    }
    tbl.innerHTML = rows.map(r => {
      const usdc = r.pnl_usdc ?? 0;
      const eur = r.pnl_eur ?? null;
      const cls = pnlClass(usdc);
      const period = r.last_ts ? `${r.last_ts}` : "--";
      return `<tr>
        <td><b><a class="link" href="${binanceSpotUrl(r.symbol) || "#"}" target="_blank" rel="noreferrer">${r.symbol}</a></b></td>
        <td class="muted">${period}</td>
        <td class="right ${cls}">${fmt(usdc, 2)}</td>
        <td class="right ${cls}">${eur === null ? "--" : fmt(eur, 2)}</td>
        <td class="right">${fmtInt(r.trades)}</td>
      </tr>`;
    }).join("");
    const pill = $("#tr-pill");
    if (pill) pill.textContent = `${rows.length} tokens`;
  }

  function renderDecisions(trades) {
    const tbl = $("#decisions-table tbody");
    if (!tbl) return;
    const rows = (trades.rows || []).slice(0, 10);
    if (!rows.length) {
      tbl.innerHTML = `<tr><td colspan="6" class="muted">pas de décisions</td></tr>`;
      return;
    }
    tbl.innerHTML = rows.map(r => {
      const ts = r.ts_utc || r.ts || "--";
      const ev = r.event || r.side || "--";
      const reason = r.reason || "--";
      const price = r.price == null ? "--" : fmt(r.price, 8);
      const qty = r.qty == null ? "--" : fmt(r.qty, 6);
      const pnl = r.pnl == null ? "--" : fmt(r.pnl, 2);
      const cls = pnlClass(r.pnl);
      return `<tr>
        <td class="muted">${ts}</td>
        <td><b>${ev}</b></td>
        <td class="muted">${reason}</td>
        <td class="right">${price}</td>
        <td class="right">${qty}</td>
        <td class="right ${cls}">${pnl}</td>
      </tr>`;
    }).join("");
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
    renderTokenNow(st);
    renderPosition(st);
    renderServices(st.units);
    renderWallet(wallet);
    renderTokensTable(summary);
    renderDecisions(trades);
  }

  async function loadServicesPage() {
    const st = await jget("/api/status");
    renderServices(st.units);
    const out = $("#ctl-out");
    const btns = document.querySelectorAll("button[data-unit][data-action]");
    btns.forEach(b => {
      b.addEventListener("click", async () => {
        b.disabled = true;
        try {
          const unit = b.getAttribute("data-unit");
          const action = b.getAttribute("data-action");
          const r = await fetch("/api/control", {
            method: "POST",
            headers: {"Content-Type":"application/json"},
            body: JSON.stringify({ unit, action }),
          });
          const j = await r.json().catch(()=>({ok:false, error:"bad json"}));
          if (out) out.textContent = JSON.stringify(j, null, 2);
          await loadServicesPage();
        } catch (e) {
          if (out) out.textContent = JSON.stringify({ ok:false, error:e.message }, null, 2);
        } finally {
          b.disabled = false;
        }
      });
    });
  }

  async function loadLogsPage() {
    const tbl = $("#logs-table tbody");
    const view = $("#log-view");
    if (!tbl) return;
    const j = await jget("/api/logs");
    const rows = j.files || [];
    const pill = $("#logs-pill");
    if (pill) pill.textContent = `${rows.length} fichiers`;
    if (!rows.length) {
      tbl.innerHTML = `<tr><td colspan="4" class="muted">no logs</td></tr>`;
      return;
    }
    tbl.innerHTML = rows.slice(0, 50).map(f => {
      const name = f.name;
      return `<tr>
        <td><b>${name}</b></td>
        <td class="right">${fmtInt(f.size || 0)}</td>
        <td class="muted">${f.mtime || "--"}</td>
        <td><button class="btn btn-blue" data-log="${name}">Open</button></td>
      </tr>`;
    }).join("");

    document.querySelectorAll("button[data-log]").forEach(b => {
      b.addEventListener("click", async () => {
        const name = b.getAttribute("data-log");
        b.disabled = true;
        try {
          const t = await jget(`/api/log_tail?name=${encodeURIComponent(name)}&n=200`);
          if (view) view.textContent = (t.text || (t.lines ? t.lines.join("\n") : ""));
        } catch (e) {
          if (view) view.textContent = `error: ${e.message}`;
        } finally {
          b.disabled = false;
        }
      });
    });
  }

  async function loadStatisticsPage() {
    // reuse existing endpoint /api/pnl if present
    const pill = $("#pnl-pill");
    try {
      const pnl = await jget("/api/pnl");
      if (pill) pill.textContent = pnl.fx_usdc_eur ? `FX: ${fmt(pnl.fx_usdc_eur,4)} EUR` : "--";

      setText("kpi-session", pnl.session?.usdc != null ? `${fmt(pnl.session.usdc,2)} USDC | ${fmt(pnl.session.eur,2)} EUR` : "--");
      setText("kpi-week", pnl.week?.usdc != null ? `${fmt(pnl.week.usdc,2)} USDC | ${fmt(pnl.week.eur,2)} EUR` : "--");
      setText("kpi-month", pnl.month?.usdc != null ? `${fmt(pnl.month.usdc,2)} USDC | ${fmt(pnl.month.eur,2)} EUR` : "--");
      setText("kpi-year", pnl.year?.usdc != null ? `${fmt(pnl.year.usdc,2)} USDC | ${fmt(pnl.year.eur,2)} EUR` : "--");
      setText("kpi-trades", pnl.trades != null ? fmtInt(pnl.trades) : "--");
      setText("kpi-winrate", pnl.winrate != null ? `${fmt(pnl.winrate,1)}%` : "--");
      setText("kpi-pf", pnl.profit_factor != null ? fmt(pnl.profit_factor,2) : "--");
      drawEquityChart(pnl.equity_points || []);
    } catch (e) {
      if (pill) pill.textContent = `KO: ${e.message}`;
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
      if (p === "/" || p.startsWith("/services")) refreshAll();
    }, 5000);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
