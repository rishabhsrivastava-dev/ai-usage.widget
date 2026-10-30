// ============================================================
//  ai-usage.widget / index.jsx   (Übersicht)
//  Data from fetch-usage.py: official limits via the Claude/Codex CLIs,
//  plus estimated token counts from local logs (ccusage).
// ============================================================

// Run through a login shell so node/npx/python3 are on PATH (ccusage runs via npx)
export const command = "/bin/zsh -lc 'python3 ai-usage.widget/fetch-usage.py'";
export const refreshFrequency = 15 * 60 * 1000; // 15 minutes

// ---------- Palette ----------
const C = {
  paper: "#F2EDE2", card: "#FFFFFF", black: "#111111", gray: "#9A9A9A",
  claude: "#D97757", gpt: "#10A37F",
  red: "#E63946", orange: "#F08A1F", yellow: "#F4C430", green: "#3A9D5D", blue: "#1D4E89",
};

const accent = (r) =>
  r >= 0.85 ? C.red : r >= 0.65 ? C.orange : r >= 0.4 ? C.yellow : C.green;

// Progress fill gradient: the green→yellow→orange→red stops are pinned to the
// full track and the fill only clips it, so at 50% the right edge sits at yellow/orange.
function fillStyle(r) {
  const grad = `linear-gradient(to right, ${C.green} 0%, ${C.yellow} 40%, ${C.orange} 65%, ${C.red} 85%)`;
  return {
    width: r * 100 + "%",
    background: grad,
    backgroundSize: r > 0 ? (100 / r) + "% 100%" : "100% 100%",
    backgroundRepeat: "no-repeat",
  };
}

// Compact token count: 1.2M / 850K
function fmtTok(n) {
  if (!n) return "0";
  if (n >= 1e9) return (n / 1e9).toFixed(1) + "B";
  if (n >= 1e7) return Math.round(n / 1e6) + "M";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return Math.round(n / 1e3) + "K";
  return String(n);
}

// Reset time: epoch seconds from the official source, shown in local time
function resetText(epoch, short) {
  if (!epoch) return "";
  const r = new Date(epoch * 1000);
  if (isNaN(r.getTime())) return "";
  const p = (n) => String(n).padStart(2, "0");
  let clk = p(r.getHours()) + ":" + p(r.getMinutes());
  if (r.toDateString() !== new Date().toDateString()) {
    clk = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" }).format(r) + " " + clk;
  }
  const diff = r.getTime() - Date.now();
  if (short || diff <= 0) return (short ? "" : "Resets ") + clk;
  const d = Math.floor(diff / 86400000);
  const h = Math.floor((diff % 86400000) / 3600000), m = Math.floor((diff % 3600000) / 60000);
  const rel = d > 0 ? d + "d " + h + "h" : (h > 0 ? h + "h " : "") + m + "m";
  return "Resets " + clk + " · in " + rel;
}

function clockText(ts) {
  const d = new Date(ts * 1000);
  return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
}

// ---------- Last refresh time / "x min ago" ----------
function agoText(ts) {
  if (!ts) return "Just now";
  const m = Math.floor((Date.now() / 1000 - Number(ts)) / 60);
  if (m <= 0) return "Just now";
  if (m < 60) return m + "m ago";
  return Math.floor(m / 60) + "h ago";
}
function setAgoSpan(el, ts) {
  if (!el) return;
  if (ts) el.setAttribute("data-ts", String(ts));
  else el.removeAttribute("data-ts");
  el.textContent = "(" + agoText(ts) + ")";
}
function setSub(el, date, asOf, ts) {
  if (!el) return;
  el.textContent = (date ? date + " · " : "") + asOf + " ";
  const span = document.createElement("span");
  span.className = "ai-ago";
  setAgoSpan(span, ts);
  el.appendChild(span);
  el.appendChild(document.createTextNode(" · CLI usage"));
}
function startAgoTicker() {
  if (typeof document === "undefined" || typeof window === "undefined") return;
  if (window.__aiAgoTimer) return;
  window.__aiAgoTimer = setInterval(() => {
    document.querySelectorAll(".ai-ago").forEach((el) => {
      setAgoSpan(el, el.getAttribute("data-ts"));
    });
  }, 30000);
}

// ---------- Drag + remembered position (localStorage) ----------
// Übersicht has no native dragging; add mouse dragging to .wrap and persist its coordinates.
const POS_KEY = "ai-usage-widget-pos";
function initWrap(node) {
  if (!node || node.__dragInit) return;
  node.__dragInit = true;
  startAgoTicker();

  try {
    const s = JSON.parse(localStorage.getItem(POS_KEY) || "null");
    if (s && typeof s.left === "number" && typeof s.top === "number") {
      node.style.left = s.left + "px";
      node.style.top = s.top + "px";
    }
  } catch (e) {}

  let drag = null;
  const onMove = (e) => {
    if (!drag) return;
    node.style.left = drag.ox + (e.clientX - drag.sx) + "px";
    node.style.top = drag.oy + (e.clientY - drag.sy) + "px";
  };
  const onUp = () => {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
    if (drag) {
      try {
        localStorage.setItem(POS_KEY, JSON.stringify({
          left: parseInt(node.style.left, 10) || 0,
          top: parseInt(node.style.top, 10) || 0,
        }));
      } catch (e) {}
    }
    drag = null;
  };
  node.addEventListener("mousedown", (e) => {
    const r = node.getBoundingClientRect();
    drag = { sx: e.clientX, sy: e.clientY, ox: r.left, oy: r.top };
    e.preventDefault();
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

// Open links in the default browser (Übersicht's global run executes macOS `open`)
function openExternal(url) {
  if (!url || typeof run !== "function") return;
  run("open " + JSON.stringify(url)).catch(() => {});
}

// ---------- Bar chart: hover detail panel ----------
function fmtDate(iso) {
  const p = String(iso).split("-");
  return p.length >= 3 ? +p[1] + "/" + +p[2] : iso;
}

// Fill the detail panel via the DOM (Übersicht's render has no hooks, so hover state lives in the DOM)
function showDetail(panel, day, brand) {
  if (!panel || !day) return;
  const head = panel.querySelector(".cdhd");
  if (head) {
    head.textContent = fmtDate(day.date) + ": ~" + fmtTok(day.tokens || 0) + " tokens (est.)";
  }
  const box = panel.querySelector(".cdmodels");
  if (!box) return;
  box.textContent = "";
  const models = day.models || [];
  if (!models.length) {
    const e = document.createElement("div");
    e.className = "cdempty";
    e.textContent = "No model details";
    box.appendChild(e);
    return;
  }
  models.forEach((m) => {
    const row = document.createElement("div");
    row.className = "cdrow";
    const tick = document.createElement("i");
    tick.className = "cdtick";
    tick.style.background = brand;
    const txt = document.createElement("div");
    txt.className = "cdtxt";
    const nm = document.createElement("div");
    nm.className = "cdname";
    nm.textContent = m.name;
    const vv = document.createElement("div");
    vv.className = "cdval";
    vv.textContent = "~" + fmtTok(m.tokens || 0) + " tokens";
    txt.appendChild(nm);
    txt.appendChild(vv);
    row.appendChild(tick);
    row.appendChild(txt);
    box.appendChild(row);
  });
}

// Default to the most recent day with usage
function initDetail(node, series, brand) {
  if (!node || !series || !series.length) return;
  let day = null;
  for (let i = series.length - 1; i >= 0; i--) {
    if ((series[i].tokens || 0) > 0) { day = series[i]; break; }
  }
  showDetail(node, day || series[series.length - 1], brand);
}

// ---------- Bar chart: remembered open state + horizontal drag scrolling ----------
function toggleChart(e, pid) {
  e.stopPropagation();
  const head = e.currentTarget;
  const card = head.closest(".card");
  const chart = card && card.querySelector(".chartwrap");
  if (!chart) return;
  const open = chart.classList.toggle("open");
  head.classList.toggle("open", open);
  try { localStorage.setItem("ai-usage-chart-" + pid, open ? "1" : "0"); } catch (e2) {}
  if (open) {
    const sc = chart.querySelector(".chartscroll");
    if (sc) requestAnimationFrame(() => { sc.scrollLeft = sc.scrollWidth; });
  }
}

function initChart(node, pid) {
  if (!node) return;
  let open = false;
  try { open = localStorage.getItem("ai-usage-chart-" + pid) === "1"; } catch (e) {}
  const card = node.closest(".card");
  const head = card && card.querySelector(".sthd");
  if (open) {
    node.classList.add("open");
    if (head) head.classList.add("open");
  }

  const sc = node.querySelector(".chartscroll");
  if (sc) {
    requestAnimationFrame(() => { if (open) sc.scrollLeft = sc.scrollWidth; });
    if (!sc.__dragInit) {
      sc.__dragInit = true;
      sc.addEventListener("mousedown", (e) => {
        e.stopPropagation();          // don't start a widget drag
        const start = { x: e.clientX, sl: sc.scrollLeft };
        const mv = (ev) => { sc.scrollLeft = start.sl - (ev.clientX - start.x); };
        const up = () => {
          document.removeEventListener("mousemove", mv);
          document.removeEventListener("mouseup", up);
          sc.classList.remove("grabbing");
        };
        document.addEventListener("mousemove", mv);
        document.addEventListener("mouseup", up);
        sc.classList.add("grabbing");
        e.preventDefault();
      });
    }
  }
}

// ---------- Root styles ----------
export const className = `
  top: 0; left: 0;
  font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Helvetica Neue', sans-serif;
  -webkit-font-smoothing: antialiased;

  /* Glass palette shared with the System Monitor widget. */
  --paper: rgba(22, 27, 38, 0.90); --card: rgba(255,255,255,0.075);
  --ink: rgba(255,255,255,0.92); --muted: rgba(255,255,255,0.48);
  --track: rgba(255,255,255,0.08); --barframe: rgba(255,255,255,0.12);
  --tick: rgba(255,255,255,0.72); --tickmini: rgba(255,255,255,0.30);
  --line: rgba(255,255,255,0.12); --hover: rgba(255,255,255,0.10);
  --scroll: rgba(255,255,255,0.22); --shadow: rgba(0,0,0,0.42);
  color: var(--ink);

  /* Dragging: position comes from .wrap's fixed positioning and is saved to localStorage on release */
  .wrap {
    position: fixed; top: 40px; left: 40px; width: 280px; box-sizing: border-box;
    cursor: move; user-select: none;
    background: var(--paper);
    backdrop-filter: blur(34px) saturate(190%);
    -webkit-backdrop-filter: blur(34px) saturate(190%);
    border: 1px solid rgba(255,255,255,0.18);
    border-radius: 18px; padding: 16px 18px 14px;
    box-shadow: 0 16px 52px rgba(0,0,0,0.42), inset 0 1px 0 rgba(255,255,255,0.18), inset 0 -1px 0 rgba(255,255,255,0.04);
  }

  /* Refresh button: round red button in the header */
  .rfbtn {
    width: 16px; height: 16px; padding: 0;
    border: 0; border-radius: 50%;
    background: ${C.red}; color: #fff;
    font-size: 10px; line-height: 14px; font-weight: 800;
    cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    transition: transform .2s ease, filter .15s ease;
  }
  .rfbtn:hover { filter: brightness(1.15); }
  .rfbtn.spin { animation: rfspin .6s linear; }
  @keyframes rfspin { from { transform: rotate(0); } to { transform: rotate(360deg); } }

  .head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
  .head > div:first-child { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .tag { font-size: 11px; font-weight: 700; letter-spacing: 1.2px; color: rgba(255,255,255,0.88); }
  .sub { font-size: 9px; color: var(--muted); margin-top: 0; letter-spacing: .5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .decor { display: flex; align-items: center; }

  .card { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--line); }
  .head + .card { margin-top: 0; padding-top: 0; border-top: 0; }

  .ctop { display: flex; align-items: center; }
  .ctop .cnames { margin-left: 8px; display: flex; flex-direction: column; min-width: 0; }
  .ctop .name { font-size: 11px; font-weight: 700; letter-spacing: .3px; line-height: 1.1; }
  .ctop .acct { font-size: 9px; color: var(--muted); margin-top: 2px; letter-spacing: .2px;
                white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ctop .acct.link { cursor: pointer; }
  .ctop .acct.link:hover { color: var(--ink); text-decoration: underline; }

  .mid { display: flex; align-items: baseline; margin: 9px 0 3px; }
  .mid .num { font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; line-height: 1; }
  .mid .unit { font-size: 11px; font-weight: 600; color: var(--muted); margin-left: 6px; }
  .mid .pct { margin-left: auto; font-size: 20px; font-weight: 800; }

  .bar { height: 8px; background: var(--barframe); border-radius: 6px; padding: 1px; box-sizing: border-box; }
  .track { position: relative; height: 100%; background: var(--track); border-radius: 5px; overflow: hidden; }
  .fill { position: absolute; left: 0; top: 0; height: 100%; border-radius: 5px; transition: width .4s ease; }
  .tick { position: absolute; top: 1px; bottom: 1px; width: 2px; background: var(--tick); opacity: .9; }

  .foot { margin-top: 6px; font-size: 9px; color: var(--muted); letter-spacing: .3px; }
  .foot.err { color: ${C.red}; font-weight: 700; }
  .foot.na { color: var(--muted); font-style: italic; }

  /* No official data: neutral grey striped track, never a green 0% bar */
  .mid .num.na { font-size: 11px; color: var(--muted); }
  .mid .lbl { font-size: 10px; font-weight: 400; letter-spacing: .8px; color: var(--muted); margin-left: 6px; }
  .mid .left { margin-left: auto; font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; }
  .bar.na .track { background: repeating-linear-gradient(135deg, rgba(255,255,255,0.07) 0 4px, transparent 4px 8px); }
  .over { font-size: 8px; font-weight: 800; color: #fff; background: ${C.red};
          border-radius: 4px; padding: 1px 4px; margin-left: 6px; letter-spacing: .5px; align-self: center; }
  .plan { color: var(--ink); opacity: .8; }

  .shd { font-size: 9px; color: var(--muted); letter-spacing: .8px; text-transform: uppercase; margin-bottom: 5px; }
  .est { display: flex; gap: 12px; font-size: 9px; color: var(--muted); }
  .est b { color: var(--ink); font-weight: 800; margin-left: 4px; }

  .stats { margin-top: 9px; padding-top: 8px; border-top: 1px dashed var(--line); }
  .st { display: flex; align-items: center; font-size: 9px; margin-top: 4px; }
  .st:first-child { margin-top: 0; }
  .st i { font-style: normal; color: var(--muted); letter-spacing: 1px; width: 16px; flex: none; }
  .st > i:first-child { width: 40px; white-space: nowrap; }
  .st .sval { color: var(--ink); font-weight: 800; min-width: 80px; }
  .st .sval em { font-style: normal; color: var(--muted); font-weight: 500; margin-left: 3px; }
  .st .spct { font-weight: 800; min-width: 32px; text-align: right; margin-right: 6px; }
  .st .smini { position: relative; flex: 1; min-width: 0; height: 4px; background: var(--track); border-radius: 2px; margin-right: 8px; overflow: hidden; }
  .st .smini .sfill { position: absolute; left: 0; top: 0; height: 100%; border-radius: 2px; }
  .st .smini .stick { position: absolute; top: 0; bottom: 0; width: 1px; background: var(--tickmini); }
  .st .srem { color: var(--muted); min-width: 64px; text-align: right; }

  /* Expand arrow (click to toggle the bar chart) */
  .sthd { display: flex; align-items: center; cursor: pointer; user-select: none;
          font-size: 9px; color: var(--muted); margin-top: 8px; }
  .sthd:hover { color: var(--ink); }
  .sthd .arw { margin-left: auto; font-size: 14px; font-weight: 900; line-height: 1;
               transition: transform .25s ease; }
  .sthd.open .arw { transform: rotate(90deg); }

  /* Daily bar chart: outer wrapper collapses, inner area scrolls horizontally by dragging */
  .chartwrap { max-height: 0; overflow: hidden; transition: max-height .35s ease; }
  .chartwrap.open { max-height: 420px; margin-top: 6px; }
  .chartscroll { overflow-x: auto; overflow-y: hidden; cursor: default; padding-bottom: 4px; }
  .chartscroll.grabbing { cursor: default; }
  .chartscroll::-webkit-scrollbar { height: 5px; }
  .chartscroll::-webkit-scrollbar-thumb { background: var(--scroll); border-radius: 3px; }
  .chartscroll::-webkit-scrollbar-track { background: transparent; }
  .chartbars { display: flex; align-items: flex-end; gap: 3px; height: 84px; width: max-content; }
  .cbar { width: 9px; flex: none; height: 100%; display: flex; align-items: flex-end;
          border-radius: 3px; transition: background .12s ease, box-shadow .12s ease; }
  /* box-shadow extends 2px each side (bar gap is 3px): wider than the bar without covering neighbours */
  .cbar:hover { background: var(--hover); box-shadow: 0 0 0 2px var(--hover); }
  .cbar > i { display: block; width: 100%; border-radius: 2px 2px 0 0; min-height: 2px;
              transition: height .2s ease; }
  .chartcap { display: flex; justify-content: space-between; font-size: 9px;
              color: var(--muted); margin-top: 3px; letter-spacing: .3px; }

  /* Hover detail panel */
  .cdetail { margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--line); }
  .cdhd { font-size: 11px; font-weight: 800; color: var(--ink); margin-bottom: 7px; }
  .cdrow { display: flex; align-items: stretch; margin-top: 7px; }
  .cdrow:first-child { margin-top: 0; }
  .cdtick { width: 3px; border-radius: 2px; flex: none; margin-right: 9px; align-self: stretch; }
  .cdtxt { display: flex; flex-direction: column; }
  .cdname { font-size: 11px; font-weight: 700; color: var(--ink); }
  .cdval { font-size: 10px; color: var(--muted); margin-top: 1px; }
  .cdempty { font-size: 9px; color: var(--muted); }
  .cdtotal { margin-top: 9px; padding-top: 8px; border-top: 1px dashed var(--line);
             font-size: 10px; color: var(--muted); }
  .cdtotal b { color: var(--ink); font-weight: 800; }

  .loading { position: fixed; top: 40px; left: 40px; width: 280px; box-sizing: border-box; background: var(--paper); backdrop-filter: blur(34px) saturate(190%); -webkit-backdrop-filter: blur(34px) saturate(190%); border: 1px solid rgba(255,255,255,0.18); border-radius: 18px; padding: 24px; font-size: 12px; color: var(--muted); box-shadow: 0 16px 52px rgba(0,0,0,0.42), inset 0 1px 0 rgba(255,255,255,0.18); }
`;

// ---------- Inline SVG logos ----------
const ClaudeLogo = () => {
  const cx = 14, cy = 14, n = 11, spokes = [];
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2 - Math.PI / 2;
    spokes.push(
      <line key={i}
        x1={cx + Math.cos(a) * 2.4} y1={cy + Math.sin(a) * 2.4}
        x2={cx + Math.cos(a) * 9} y2={cy + Math.sin(a) * 9}
        stroke={C.paper} strokeWidth="2" strokeLinecap="round" />
    );
  }
  return (
    <svg width="20" height="20" viewBox="0 0 28 28">
      <circle cx="14" cy="14" r="14" fill={C.claude} />
      {spokes}
      <circle cx="14" cy="14" r="2" fill={C.paper} />
    </svg>
  );
};

const GptLogo = () => {
  const cx = 14, cy = 14, n = 6, ring = 7, parts = [];
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2 - Math.PI / 2;
    const x = cx + Math.cos(a) * ring, y = cy + Math.sin(a) * ring;
    parts.push(
      <g key={i}>
        <line x1={cx} y1={cy} x2={x} y2={y} stroke="#fff" strokeWidth="1.2" />
        <circle cx={x} cy={y} r="2" fill="#fff" />
      </g>
    );
  }
  return (
    <svg width="20" height="20" viewBox="0 0 28 28">
      <rect width="28" height="28" rx="8" fill={C.gpt} />
      {parts}
      <circle cx="14" cy="14" r="2.6" fill="#fff" />
    </svg>
  );
};

// ---------- Provider card ----------
// Official limits and local estimates are kept strictly apart:
//   limits   → percent + reset time, from claude /usage and codex app-server
//   estimate → token counts from local logs via ccusage; shown only, never compared to a limit
const LimitRow = ({ l }) => (
  <div className="st">
    <i>{l.label}</i>
    <span className="spct" style={{ color: l.available ? accent(l.bar) : "var(--muted)" }}>
      {l.available ? l.pctText : "—"}
    </span>
    <span className="smini">
      {l.available && <i className="sfill" style={fillStyle(l.bar)} />}
    </span>
    <span className="srem">{l.available ? resetText(l.resetsAt, true) || l.remainingText : "Unavailable"}</span>
  </div>
);

const Card = ({ prov, brand, Logo, pid }) => {
  const limits = prov.limits || [];
  const main = limits.find((l) => l.available) || limits[0] || null;
  const rest = limits.filter((l) => l !== main);
  const ok = !!(main && main.available);
  const ratio = ok ? main.bar : 0;
  const est = prov.estimate || {};
  const tot = est.totals;
  const series = est.series || [];
  const maxV = Math.max(1, ...series.map((d) => d.tokens || 0));
  const total30 = series.reduce((s, d) => s + (d.tokens || 0), 0);
  const plan = prov.plan || (prov.authMode === "subscription" ? "Subscription" : "Plan unknown");
  const planText = prov.authMode === "subscription" ? plan + " subscription" : plan;
  const naReason = (main && main.reason) || prov.limitsError || "Usage unavailable";
  let foot = "";
  if (ok) {
    foot = resetText(main.resetsAt) || main.resetText || main.remainingText;
    if (prov.stale && main.asOf) foot += " · as of " + clockText(main.asOf);
  }
  return (
    <div className="card">
      <div className="ctop">
        <Logo />
        <div className="cnames">
          <span className="name">{prov.label}</span>
          <span
            className={"acct" + (prov.accountUrl ? " link" : "")}
            title={prov.accountUrl ? "Open usage page" : ""}
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => { e.stopPropagation(); openExternal(prov.accountUrl); }}
          >
            <span className="plan">{planText}</span>{prov.account ? " · " + prov.account : ""}
          </span>
        </div>
      </div>
      {ok ? (
        <div className="mid">
          <span className="num">{main.pctText}</span>
          <span className="lbl">{main.label} used</span>
          {main.overLimit && <span className="over">OVER LIMIT</span>}
          <span className="left" style={{ color: accent(ratio) }}>{main.remainingText}</span>
        </div>
      ) : (
        <div className="mid">
          <span className="num na">Usage unavailable</span>
        </div>
      )}
      <div className={"bar" + (ok ? "" : " na")}>
        <div className="track">
          {ok && <div className="fill" style={fillStyle(ratio)} />}
          <i className="tick" style={{ left: "25%" }} />
          <i className="tick" style={{ left: "50%" }} />
          <i className="tick" style={{ left: "75%" }} />
        </div>
      </div>
      {ok
        ? <div className="foot">{foot}</div>
        : <div className="foot na">{naReason}</div>}
      {rest.length > 0 && (
        <div className="stats">
          {rest.map((l) => <LimitRow l={l} key={l.label} />)}
        </div>
      )}
      <div className="stats">
        <div className="shd">Estimated tokens used · local logs</div>
        {tot ? (
          <div className="est">
            <span>Today<b>{fmtTok(tot.today)}</b></span>
            <span>7d<b>{fmtTok(tot["7d"])}</b></span>
            <span>30d<b>{fmtTok(tot["30d"])}</b></span>
          </div>
        ) : (
          <div className="est">{est.error || "Estimate unavailable"}</div>
        )}
      </div>
      {series.length > 0 && (
        <div className="chartblock">
          <div
            className="sthd"
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => toggleChart(e, pid)}
          >
            <span>Estimated daily tokens · {series.length} days</span>
            <span className="arw">▸</span>
          </div>
          <div className="chartwrap" ref={(n) => initChart(n, pid)}>
            <div className="chartscroll">
              <div className="chartbars">
                {series.map((d) => {
                  const val = d.tokens || 0;
                  const h = (val / maxV) * 100;
                  return (
                    <div
                      className="cbar"
                      key={d.date}
                      title={d.date.slice(5) + "  ~" + fmtTok(val)}
                      onMouseEnter={(e) =>
                        showDetail(e.currentTarget.closest(".chartblock").querySelector(".cdetail"), d, brand)
                      }
                    >
                      <i style={{ height: Math.max(h, 0) + "%", background: brand, opacity: 0.85 }} />
                    </div>
                  );
                })}
              </div>
            </div>
            <div className="chartcap">
              <span>{fmtDate(series[0].date)}</span>
              <span>{fmtDate(series[series.length - 1].date)}</span>
            </div>
            <div className="cdetail" ref={(n) => initDetail(n, series, brand)}>
              <div className="cdhd" />
              <div className="cdmodels" />
            </div>
            <div className="cdtotal">
              Estimated total (last {series.length} days): <b>~{fmtTok(total30)} tokens</b>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

// ---------- Main render ----------
export const render = ({ output }) => {
  if (!output || !output.trim()) {
    return <div className="loading">AI USAGE · Loading…</div>;
  }
  let data;
  try {
    data = JSON.parse(output);
  } catch (e) {
    return <div className="loading">Parse error: {String(output).slice(0, 160)}</div>;
  }
  if (data.fatal) {
    return <div className="loading">Script error: {data.fatal}</div>;
  }

  const onRefresh = (e) => {
    e.stopPropagation();
    const btn = e.currentTarget;
    btn.classList.remove("spin");
    void btn.offsetWidth; // force the animation to restart
    btn.classList.add("spin");
    // Update the time immediately (the full refresh waits for the script to finish)
    const wrap = btn.closest(".wrap");
    if (wrap) {
      const d = new Date();
      const hhmm = ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
      const md = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" }).format(d);
      setSub(wrap.querySelector(".sub"), md, hhmm, Math.floor(Date.now() / 1000));
    }
    if (typeof run === "function") {
      run("osascript -e 'tell application \"Übersicht\" to refresh'").catch(() => {});
    }
  };

  return (
    <div className="wrap" ref={initWrap}>
      <div className="head">
        <div>
          <div className="tag">AI USAGE</div>
          <div className="sub" ref={(el) => setSub(el, data.date, data.asOf, data.ts)}></div>
        </div>
        <div className="decor">
          <button
            className="rfbtn"
            title="Refresh"
            onMouseDown={(e) => e.stopPropagation()}
            onClick={onRefresh}
          >↻</button>
        </div>
      </div>
      <Card prov={data.claude} brand={C.claude} Logo={ClaudeLogo} pid="claude" />
      <Card prov={data.chatgpt} brand={C.gpt} Logo={GptLogo} pid="chatgpt" />
    </div>
  );
};
