/* Daily Macro Briefing — read-only PWA.
   Cache-first paint (rows from cache instantly), then refresh. Renders the 5 D7 states:
   default · loading · stale · fallback · error. Invariant: never surface a failure —
   the card may collapse, but the indicator rows always render. */

const CACHE_KEY = "macro:v1";
const STALE_AFTER_H = 18; // briefing older than this (cron is twice-daily) => amber footer

const html = document.documentElement;
const els = {
  card: document.getElementById("card"),
  body: document.getElementById("card-body"),
  rows: document.getElementById("rows"),
  footer: document.getElementById("footer"),
  regime: document.getElementById("regime"),
  sheet: document.getElementById("sheet"),
  talkingPoints: document.getElementById("talking-points"),
  toast: document.getElementById("toast"),
  refresh: document.getElementById("refresh"),
};
const byId = (x) => document.getElementById(x);

const DEC2 = new Set(["treasury_10y", "fed_funds", "treasury_2y", "mortgage_30y", "hy_spread",
  "yield_curve_2s10s", "real_fed_funds", "real_10y", "tips_10y_real",
  // core_cpi_yoy stays 2dp to match the WHY-block basis line's rounding (basisHtml, 2dp), which
  // otherwise showed "2.48" there and "2.5%" on the Inflation tile for the identical value.
  // cpi_yoy (headline) is 1dp on purpose: it must read "3.4%" like the published BLS/news headline
  // Bruce quotes (it is NOT a regime-basis clause, so there is no WHY-block value to stay level with).
  "core_cpi_yoy"]); // yields/spreads + core CPI: 2 decimals
const SIGNED = new Set(["payrolls", "retail_sales_mom", "industrial_production",
  "yield_curve_2s10s", "real_fed_funds", "real_10y"]); // changes/spreads: show +/-
// Weekly-cadence series stored under the config's DAILY frequency (ICSA, Freddie PMMS): the
// detail sheet labels them "Weekly" — what the reader actually observes.
const WEEKLY_ROWS = new Set(["jobless_claims", "mortgage_30y"]);

const demo = new URLSearchParams(location.search).get("demo");

/* ── formatting ──────────────────────────────────────── */
function fmtValue(ind) {
  const num = typeof ind.value === "number" ? ind.value : Number(ind.value);
  if (ind.value == null || !Number.isFinite(num)) return "—"; // tolerate type drift / dropped value
  const sign = SIGNED.has(ind.id) && num > 0 ? "+" : "";
  if (ind.unit === "K") return `${sign}${Math.round(num).toLocaleString()}K`;      // payrolls, housing starts
  if (ind.unit === "count") return Math.round(num).toLocaleString();               // jobless claims
  if (ind.unit === "usd_tn") return `$${(num / 1e6).toFixed(1)}T`;                 // market cap ($millions → $T)
  if (ind.unit === "USD") return `$${num.toFixed(2)}`;                              // WTI oil ($/barrel)
  if (ind.unit === "index" && Math.abs(num) >= 1000)                               // S&P, Nasdaq
    return num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const n = num.toFixed(DEC2.has(ind.id) ? 2 : 1);
  return ind.unit === "%" ? `${sign}${n}%` : `${sign}${n}`; // suffix driven by the feed's unit
}
/* ── change chip: %/points/raw-level, toggled globally (Apple-Stocks style) ──── */
const MODE_KEY = "macro:mode";   // 0 = percent · 1 = points · 2 = raw level
// Default to POINTS: the honest native-unit change (GDP ▲0.58, not ▲29% — the percent-change-of-a-
// rate reads oddly for rate indicators). Tap any chip to cycle %/points/level; the choice persists.
let displayMode = (() => { const m = parseInt(localStorage.getItem(MODE_KEY), 10); return [0, 1, 2].includes(m) ? m : 1; })();
function writeMode(m) { try { localStorage.setItem(MODE_KEY, String(m)); } catch {} }

function pointsMag(ind, mag = ind.delta) { // |mag| in the indicator's own units (default: the latest delta)
  const a = Math.abs(mag);
  if (ind.unit === "K" || ind.unit === "count") return Math.round(a).toLocaleString();
  if (ind.unit === "usd_tn") return `${(a / 1e6).toFixed(2)}T`;
  return a.toFixed(2);
}
function fmtLevel(ind) { // the raw underlying FRED number (CPI index, total payrolls, …)
  const lv = ind.level != null ? Number(ind.level) : Number(ind.value);
  if (!Number.isFinite(lv)) return "";
  return Math.abs(lv) >= 1000 ? Math.round(lv).toLocaleString()
       : Number.isInteger(lv) ? String(lv) : lv.toFixed(2);
}
function chipHtml(ind) {
  const d = ind.delta;
  const up = d != null && d > 0.005, down = d != null && d < -0.005;
  const cls = up ? "up" : down ? "down" : "flat";   // unchanged / no-data → neutral grey
  const arrow = up ? "▲ " : down ? "▼ " : "";       // no arrow when flat (a plain 0.00)
  let inner;
  if (displayMode === 2) {                          // raw underlying level (absolute, no arrow)
    inner = fmtLevel(ind) || "—";
  } else if (d == null) {                           // no change data
    inner = "—";
  } else if (displayMode === 0 && ind.unit !== "%") { // percent change of the value
    const prior = (Number(ind.value) || 0) - d;
    inner = prior ? `${arrow}${Math.abs((d / prior) * 100).toFixed(2)}%` : "—";
  } else {                                          // points (native units) — ALWAYS for %-unit
    // rows: "percent change of a percent" (GDP ▲34.96%) is a meaningless read; ▲0.70 is the number.
    inner = `${arrow}${pointsMag(ind)}`;
  }
  return `<span class="chip ${cls}" data-id="${ind.id}" role="button" tabindex="0" aria-label="Toggle change display">${inner}</span>`;
}
// The detail-sheet header pill — reflects the change over the SELECTED timeframe (first→last of the
// sliced points), so it agrees with the chart's color (rising = last >= first) and updates when the
// timeframe changes. Display-only (no data-id) so rerenderChips skips it; cycleMode re-renders the
// open sheet to keep it in sync with the %/points/level mode.
function tfChipHtml(sliced, ind) {
  if (!sliced || sliced.length < 2) return chipHtml(ind);   // no range to compare → latest-delta chip
  const first = sliced[0][1], last = sliced[sliced.length - 1][1];
  const chg = last - first;
  const up = chg >= 0;                                        // match chartSvg: rising = last >= first
  const cls = up ? "up" : "down";
  const arrow = up ? "▲ " : "▼ ";
  let inner;
  if (displayMode === 2) {                                    // raw current level (no change)
    inner = fmtLevel(ind) || "—";
  } else if (displayMode === 0 && ind.unit !== "%") {         // % change over the timeframe
    inner = first ? `${arrow}${Math.abs((chg / first) * 100).toFixed(2)}%` : "—";
  } else {                                                    // points — always for %-unit rows
    inner = `${arrow}${pointsMag(ind, chg)}`;
  }
  return `<span class="chip ${cls}" role="button" tabindex="0" aria-label="Toggle change display">${inner}</span>`;
}
function rerenderChips() { // re-skin chips in place (no row re-animation) after a mode toggle
  if (!lastData || !lastData.indicators) return;
  const byId = {};
  lastData.indicators.forEach((i) => { byId[i.id] = i; });
  document.querySelectorAll(".chip[data-id]").forEach((el) => {
    const ind = byId[el.dataset.id];
    if (ind) el.outerHTML = chipHtml(ind);
  });
}
function cycleMode() {
  displayMode = (displayMode + 1) % 3; writeMode(displayMode); rerenderChips();
  if (sheetState && els.sheet && !els.sheet.hidden) renderSheet(); // keep the open sheet's timeframe pill in sync
}
function fmtETTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return ""; // never render a bogus 1970 epoch
  try {
    return new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York", hour: "numeric", minute: "2-digit", hour12: true,
    }).format(d);
  } catch { return ""; }
}

/* ── sparkline ───────────────────────────────────────── */
// EVERY first-page spark shows the SAME 1-year window, whatever the row's cadence.
// The payload's `ind.spark` is the last 12 OBSERVATIONS, which is a different span per
// frequency: ~12 business days for a daily rate, 12 months for CPI, 12 QUARTERS (3 years)
// for GDP. Rows sat side by side implying a comparison they never made. Reuses the detail
// sheet's own slicer so the row and the chart you get on tap agree, and so "1 year" means
// one definition in one place. sliceHistory anchors on the last OBSERVATION, not today —
// correct for a lagging series (Q2 GDP measures back from Apr, not from August).
const SPARK_MONTHS = 12;
function sparkValues(ind) {
  const h = sliceHistory(ind && ind.history, SPARK_MONTHS).map((p) => p[1]);
  return h.length >= 2 ? h : (ind && ind.spark) || [];  // fall back rather than draw nothing
}

function sparkline(values, ind) {
  const w = 64, h = 26, pad = 3;
  const v = (values || []).filter((x) => x != null);
  if (v.length < 2) return `<svg class="row__spark" viewBox="0 0 64 26" aria-hidden="true"></svg>`; // sized placeholder keeps the grid column aligned
  const min = Math.min(...v), max = Math.max(...v), span = max - min || 1;
  const x = (i) => pad + (i * (w - pad * 2)) / (v.length - 1);
  const y = (val) => h - pad - ((val - min) / span) * (h - pad * 2);
  const pts = v.map((val, i) => `${x(i).toFixed(1)},${y(val).toFixed(1)}`);
  // Color by the SAME signal as the change chip (ind.delta) so the line agrees with the up/down
  // pill beside it (Apple-Stocks), not the spark window's own first-vs-last trend.
  const d = ind && ind.delta;
  const stroke = d != null && d > 0.005 ? "var(--up)"
    : d != null && d < -0.005 ? "var(--down)" : "var(--text-mute)";
  const [lx, ly] = pts[pts.length - 1].split(",");
  return `<svg class="row__spark" viewBox="0 0 ${w} ${h}" fill="none" aria-hidden="true">
    <polyline points="${pts.join(" ")}" stroke="${stroke}" stroke-width="1.6"
      stroke-linejoin="round" stroke-linecap="round" opacity="1"/>
    <circle cx="${lx}" cy="${ly}" r="1.9" fill="${stroke}"/></svg>`;
}

/* ── rendering ───────────────────────────────────────── */
function renderCard(b) {
  if (!b) return;
  html.dataset.regime = b.regime || "indeterminate";  // W3: default to "no call", never to the reassuring one

  if (b.state === "fallback") {
    els.body.innerHTML = `<p class="fallback">${escape(b.fallback_text || "")}</p>`;
    return;
  }
  const br = b.briefing || {};
  const quotes = (br.quotable_numbers || [])
    .map((q) => `<li class="quote" tabindex="0" role="button" title="Tap to copy" aria-label="Copy this line">${emphasizeNumbers(escape(q.sentence))}</li>`)
    .join("");
  const conds = [];
  if (br.whats_new) conds.push(`<p class="cond"><span class="tag">Today</span> ${escape(stripToday(br.whats_new))}</p>`);
  if (br.regime_change) conds.push(`<p class="cond cond--change">${escape(br.regime_change)}</p>`);

  // "Today"/regime-change lines render ABOVE the bullets: for a daily reader the change is the
  // story; the two quotable levels are the reference under it.
  els.body.innerHTML =
    `<h1 class="headline">${escape(br.headline || "")}</h1>` +
    (conds.length ? `<div class="conds">${conds.join("")}</div>` : "") +
    (quotes ? `<ul class="quotes">${quotes}</ul>` : "") +
    releaseCalendarHtml();
}

/* ── release calendar: "when to lean in" (OMB CY2026 schedule of principal federal
   economic indicators — Employment Situation, CPI, GDP advance/2nd/3rd; all 8:30am ET).
   ponytail: static 2026 table, refresh once a year from the next OMB/BLS schedule. */
const RELEASES_2026 = [
  ["2026-07-02", "Jobs report"], ["2026-07-14", "CPI"], ["2026-07-30", "GDP"],
  ["2026-08-07", "Jobs report"], ["2026-08-12", "CPI"], ["2026-08-26", "GDP"],
  ["2026-09-04", "Jobs report"], ["2026-09-11", "CPI"], ["2026-09-30", "GDP"],
  ["2026-10-02", "Jobs report"], ["2026-10-14", "CPI"], ["2026-10-29", "GDP"],
  ["2026-11-06", "Jobs report"], ["2026-11-10", "CPI"], ["2026-11-25", "GDP"],
  ["2026-12-04", "Jobs report"], ["2026-12-10", "CPI"], ["2026-12-23", "GDP"],
];

function releaseCalendarHtml() {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const week = new Date(today); week.setDate(week.getDate() + 7);
  const upcoming = RELEASES_2026
    .map(([iso, name]) => ({ d: localDate(iso), name }))
    .filter((r) => r.d >= today);
  if (!upcoming.length) return "";
  const thisWeek = upcoming.filter((r) => r.d < week);
  const fmt = (r) => `${escape(r.name)} ${r.d.toLocaleDateString("en-US", { weekday: "short" })}`;
  const line = thisWeek.length
    ? `This week: ${thisWeek.map(fmt).join(" · ")}`
    : `Next up: ${escape(upcoming[0].name)} ${upcoming[0].d.toLocaleDateString("en-US", { month: "short", day: "numeric" })}`;
  return `<p class="calendar">${line}</p>`;
}

// Tiny per-row provenance subtitle: the indicator's own as_of date, plus its cadence when not
// daily — so a monthly/quarterly row's older date reads as expected, not stale (this is what stops
// the quarterly market_cap from looking broken next to daily rows). Kept small/muted in CSS so it's
// there for granular analysis without competing with the value during normal scanning.
// (localDate is defined below; rowSub only runs at render time, after the module has evaluated.)
function rowSub(ind) {
  const iso = ind.last_release_date || ind.period_label;
  const d = iso ? localDate(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return "";
  const date = d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  const freq = ind.frequency && ind.frequency !== "daily" ? ` · ${ind.frequency}` : "";
  return date + freq;
}
// Rows pinned to "raw level + growth-% bubble" (user decision 2026-07-03): the big number is the
// underlying LEVEL (GDP ~$24.2T), the bubble is the growth rate itself (▲2.7%), green when growing,
// red when shrinking. Fixed presentation — the global %/points/level chip cycling never touches it
// (the bubble carries no data-id, so rerenderChips skips it).
const LEVEL_ROWS = new Set(["real_gdp_yoy"]);

function levelRowVal(ind) {
  const lv = Number(ind.level);
  if (!Number.isFinite(lv) || ind.value == null) return null; // fallback feed → normal rendering
  const growth = Number(ind.value);
  const cls = growth > 0 ? "up" : growth < 0 ? "down" : "flat";
  const arrow = growth > 0 ? "▲ " : growth < 0 ? "▼ " : "";
  // GDPC1 level arrives in billions of chained dollars → $24.2T
  return `<span class="row__num">$${(lv / 1000).toFixed(1)}T</span>` +
    `<span class="chip ${cls}" aria-label="Year-over-year growth">${arrow}${Math.abs(growth).toFixed(1)}%</span>`;
}

// Rows pinned to "raw number (with its own trend arrow) + change-points bubble": the value IS
// already a net change (payrolls' +57K is jobs added/lost, not a level).
//
// The chip is POINTS, not percent — payrolls swings through zero most months (spark: …63, 20,
// -23), so "percent change of this month's net vs last month's net" divides by whatever the
// prior month happened to be, and a small prior base explodes into nonsense: 2026-07 read
// delta=-43 vs prior=+20, i.e. |−43/20|×100 = a fabricated "▼ 215.00%" on the live card. The
// swing itself (-43K, hiring decelerated) is the honest, useful number — this shows that.
// unit is hardcoded "K" (config.py's payrolls spec is fixed to it); reuse fmtValue's unit
// dispatch instead if TREND_ROWS ever grows to a second, differently-unitted row.
const TREND_ROWS = new Set(["payrolls"]);

function trendRowVal(ind) {
  const num = Number(ind.value);
  if (!Number.isFinite(num)) return null; // fallback feed → normal rendering
  const arrow = num > 0 ? "▲ " : num < 0 ? "▼ " : "";
  const d = ind.delta;
  const dCls = d == null ? "flat" : d > 0.005 ? "up" : d < -0.005 ? "down" : "flat";
  const dArrow = d == null ? "" : d > 0.005 ? "▲ " : d < -0.005 ? "▼ " : "";
  // No "K" here — same convention as every other points-mode chip on the card (e.g. Housing
  // starts' "▲ 228"): the row number already carries the unit, the chip states the magnitude.
  const inner = d == null ? "—" : `${dArrow}${Math.round(Math.abs(d)).toLocaleString()}`;
  // Magnitude only in the row number too — the arrow already carries the sign; showing both
  // ("▼ -23K") double-signals direction the way no other row on the card does.
  return `<span class="row__num">${arrow}${Math.round(Math.abs(num)).toLocaleString()}K</span>` +
    `<span class="chip ${dCls}" aria-label="Change vs prior period">${inner}</span>`;
}

function rowHtml(ind, i) {
  const sub = rowSub(ind);
  const val = (LEVEL_ROWS.has(ind.id) && levelRowVal(ind)) ||
    (TREND_ROWS.has(ind.id) && trendRowVal(ind)) ||
    `<span class="row__num">${fmtValue(ind)}</span>${chipHtml(ind)}`;
  return `<li class="row" data-id="${ind.id}" data-stale="${!!ind.stale}" tabindex="0"
      aria-label="${escape(ind.display_name)}, open detail" style="animation-delay:${i * 45}ms">
    <span class="row__label">
      <span class="row__name">${escape(ind.display_name)}</span>
      ${sub ? `<span class="row__sub">${escape(sub)}</span>` : ""}
    </span>
    ${sparkline(sparkValues(ind), ind)}
    <span class="row__val">${val}</span>
  </li>`;
}

let lastData = null;     // most recent indicators feed — chip toggling re-skins from this

// The Charter Rev 3 data-set categories — headers and row order mirror the charter's own
// sentences (display-only grouping; the backend `tier` field still governs what feeds the LLM).
// An id missing from this map falls into "Other" so a future config addition never vanishes.
const CATEGORIES = [
  ["Growth & debt", ["real_gdp_yoy", "federal_debt_gdp"]],
  ["Inflation", ["cpi_yoy", "core_cpi_yoy"]],
  ["Interest rates & policy", ["fed_funds", "treasury_2y", "treasury_10y", "yield_curve_2s10s",
    "real_fed_funds", "real_10y", "tips_10y_real"]],
  ["Labor", ["jobless_claims", "unemployment", "labor_participation", "payrolls"]],
  ["Activity & sentiment", ["umich_sentiment", "capacity_util", "mfg_employment", "construction_employment"]],
  ["Housing", ["housing_starts", "mortgage_30y"]],
  ["Markets — context only", ["market_cap", "hy_spread", "nasdaq", "sp500", "vix", "brent_oil", "usd_index", "gold_spot"]],
];
const MARKETS_NOTE = `<p class="markets__note">Market data for context — not advice.</p>`;

function renderRows(data) {
  const inds = (data && data.indicators) || [];
  if (!inds.length) return; // don't blank a populated list to nothing on an empty feed
  lastData = data;
  const byId = {};
  inds.forEach((i) => { byId[i.id] = i; });
  const placed = new Set();
  const sections = CATEGORIES.map(([title, ids]) => {
    const rows = ids.filter((id) => byId[id]);
    rows.forEach((id) => placed.add(id));
    if (!rows.length) return "";
    const isMarkets = title.startsWith("Markets");
    return `<p class="cat-head">${escape(title)}</p>` +
      `<ul class="rows" aria-label="${escape(title)}">${rows.map((id, i) => rowHtml(byId[id], i)).join("")}</ul>` +
      (isMarkets ? MARKETS_NOTE : "");
  });
  const leftovers = inds.filter((i) => !placed.has(i.id));
  if (leftovers.length) {
    sections.push(`<p class="cat-head">Other</p>` +
      `<ul class="rows" aria-label="Other">${leftovers.map(rowHtml).join("")}</ul>`);
  }
  els.rows.innerHTML = sections.join("");
}

function driverBadgesHtml(briefing) {
  // The deterministic classifier's ≤2 drivers (ids in briefing.drivers). briefing.json carries only
  // the ids, so cross-reference lastData (indicators.json) for the display name + change direction.
  const ids = (briefing && briefing.drivers) || [];
  if (!ids.length || !lastData || !lastData.indicators) return "";
  const byId = {};
  lastData.indicators.forEach((i) => { byId[i.id] = i; });
  const chips = ids.map((id) => {
    const ind = byId[id];
    if (!ind) return "";
    const d = ind.delta;
    const cls = d != null && d > 0.005 ? "up" : d != null && d < -0.005 ? "down" : "flat";
    const arrow = cls === "up" ? "▲" : cls === "down" ? "▼" : "";
    return `<span class="chip ${cls} driver-chip">${escape(ind.display_name)}${arrow ? " " + arrow : ""}</span>`;
  }).filter(Boolean).join("");
  if (!chips) return "";
  return `<div class="regime__drivers"><span class="regime__eyebrow regime__eyebrow--sm">Driven by</span>${chips}</div>`;
}

// One plain sentence per regime id — the label ("Soft landing") is insider shorthand; this says
// what it means. Static lookup, mirrors classifier.py's locked v1.1 regime set.
const REGIME_MEANING = {
  // Rewritten 2026-08-06 (Bruce D+1). The old line read "Prices are cooling without a big
  // rise in unemployment" — untrue of a core CPI that has been flat near 2.57%, and it
  // asserted a trend the rule never tested. The rule is a tolerance band; say that.
  soft_landing: "Growth, prices and jobs are all inside their normal bands. No single one is flashing.",
  late_cycle_expansion: "Growth is running hot and prices are above comfort, the stretch that usually comes before a cool-down.",
  slowdown: "Growth is losing steam; jobs and output are softening.",
  recovery: "The economy is climbing back from a weak stretch.",
  stagflation_risk: "The uncomfortable mix: prices rising while growth stalls.",
  // W3 — not a regime; the classifier declined. Saying so is more honest than the most
  // reassuring of five labels, which is what this used to publish.
  indeterminate: "Today's readings don't match any of the five patterns, so no call rather than a guess.",
};

// W1 (Bruce, D+1 2026-08-06): "Why are you calling it a soft landing when there is no reason to
// say that." There IS a reason — a three-clause tolerance band — but the card never showed it, so
// the label read as an opinion. This renders the test that actually fired.
//
// Deliberately a DETERMINISTIC layer, never LLM prose. The Fed's 2% target line below is the
// reason: 2.0 is not in the indicator set, so O5 number-grounding (validator.py, tolerance 0.05)
// would reject it in a briefing. Here it is static text, grounded by construction.
const BASIS_TARGET_NOTE = {
  core_cpi_yoy: "the Fed aims for 2%",
};

function basisHtml(briefing) {
  const basis = (briefing && briefing.basis) || [];
  if (!basis.length) return "";   // fallthrough tested nothing; claiming otherwise would be the lie
  // Same cross-reference driverBadgesHtml uses: briefing.json carries ids, indicators.json the
  // display names. Falls back to the raw id rather than hiding the clause if the feed is late.
  const named = {};
  if (lastData && lastData.indicators) lastData.indicators.forEach((i) => { named[i.id] = i; });
  const clauses = basis.map(([id, observed, op, threshold]) => {
    const name = (named[id] && named[id].display_name) || id;
    // Display rounding only — the stored basis keeps 4dp for the audit trail. "Real GDP 2.1018"
    // on the card reads as a machine leaking internals; every other number on the page shows 1-2dp.
    observed = Math.round(observed * 100) / 100;
    const note = BASIS_TARGET_NOTE[id] ? ` <em>(${escape(BASIS_TARGET_NOTE[id])})</em>` : "";
    return `<li>${escape(name)} <b>${observed}</b> <span class="basis__rule">(rule: ${escape(op)} ${threshold})</span>${note}</li>`;
  }).join("");
  return `<div class="regime__basis"><span class="regime__basis-label">Why:</span><ul>${clauses}</ul></div>`;
}

function renderRegime(briefing) {
  if (!els.regime) return;
  const label = briefing && briefing.regime_label; // the "CNBC snapshot" read (V2 Tier 0)
  if (!label) { els.regime.hidden = true; return; }
  const meaning = REGIME_MEANING[briefing.regime] || "";
  els.regime.innerHTML =
    `<span class="regime__eyebrow">Macro regime</span>` +
    `<span class="regime__value"><span class="regime__dot"></span>${escape(label)}</span>` +
    (meaning ? `<span class="regime__meaning">${escape(meaning)}</span>` : "") +
    basisHtml(briefing) +
    driverBadgesHtml(briefing);
  els.regime.hidden = false;
}

function renderTalkingPoints(data) {
  // A deterministic "talking points" strip — NO AI. Rule: the 2 biggest market-tier movers (by |%|)
  // + the first 2 present of a fixed core set, deduped, capped at 4. Pure template over published data.
  if (!els.talkingPoints) return;
  const inds = (data && data.indicators) || [];
  if (!inds.length) { els.talkingPoints.hidden = true; return; }
  const byIdx = {}; inds.forEach((i) => { byIdx[i.id] = i; });
  const movers = inds
    .filter((i) => i.tier === "market" && i.delta != null && i.value != null)
    .map((i) => { const prior = (Number(i.value) || 0) - i.delta; return { i, pct: prior ? Math.abs((i.delta / prior) * 100) : 0 }; })
    .sort((a, b) => b.pct - a.pct).slice(0, 2).map((x) => x.i);
  const levels = ["core_cpi_yoy", "unemployment", "treasury_10y"]
    .map((id) => byIdx[id]).filter((i) => i && i.value != null);
  const seen = new Set(); const picks = [];
  [...movers, ...levels].forEach((i) => { if (i && !seen.has(i.id)) { seen.add(i.id); picks.push(i); } });
  const items = picks.slice(0, 4);
  if (!items.length) { els.talkingPoints.hidden = true; return; }
  els.talkingPoints.innerHTML =
    `<span class="eyebrow">Talking points</span>` +
    `<div class="talking-points__row">` +
    items.map((ind) =>
      // data-id + tabindex makes the card open the detail sheet on tap/Enter (same as a row below);
      // the chip inside keeps its own change-mode toggle.
      `<span class="tp" data-id="${ind.id}" tabindex="0"><span class="tp__name">${escape(ind.display_name)}</span>` +
      `<span class="tp__val">${fmtValue(ind)}</span>${chipHtml(ind)}</span>`).join("") +
    `</div>`;
  els.talkingPoints.hidden = false;
}

function copyQuote(el) {
  const text = (el.textContent || "").trim();
  if (!text) return;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => showToast("Copied"), () => showToast("Couldn’t copy"));
  } else {
    showToast("Copy not supported");
  }
}

let toastTimer = null;
function showToast(msg) {
  if (!els.toast) return;
  els.toast.textContent = msg;
  els.toast.classList.add("toast--show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => els.toast.classList.remove("toast--show"), 1600);
}

async function refresh() {
  // "Regenerate" can only be a re-fetch in a static PWA (FRED/LLM keys are server-only).
  if (els.refresh) els.refresh.disabled = true;
  try {
    const { data, briefing } = await loadData();
    const cached = readCache();
    const finalData = data || (cached && cached.data) || null;
    const finalBriefing = briefing || (cached && cached.briefing) || null;
    if (finalData || finalBriefing) {
      paint(finalData, finalBriefing);
      if (data || briefing) writeCache({ data: finalData, briefing: finalBriefing });
    }
    showToast("Refreshed");
  } catch { showToast("Couldn’t refresh"); }
  finally { if (els.refresh) els.refresh.disabled = false; }
}

function renderFooter(briefing, data, isStale) {
  // The ET time itself signals which run produced this (10:15 AM vs 5:30 PM, ADR-001 v1.1).
  const through = fmtDateLong((briefing && briefing.data_through) || (data && data.as_of_date) || "");
  if (!briefing || !briefing.generated_at) {
    els.footer.innerHTML = through ? `Data through ${escape(through)}` : "";
    return;
  }
  const time = fmtETTime(briefing.generated_at);
  els.footer.innerHTML = isStale
    ? `Data may be stale — last updated <span class="run">${time} ET</span>`
    : `Updated <span class="run">${time} ET</span> · data through ${escape(through)}`;
}

function paint(data, briefing) {
  const isStale =
    demo === "stale" ||
    (data && data.any_stale) ||
    (briefing && briefing.generated_at && hoursSince(briefing.generated_at) > STALE_AFTER_H);

  if (data) renderRows(data); // rows render from a good feed regardless of the briefing
  if (data) renderTalkingPoints(data); // deterministic strip, also independent of the briefing

  if (!briefing) {
    html.dataset.state = "error"; // card collapses silently; rows stay (the invariant)
    if (els.regime) els.regime.hidden = true;
    if (data) renderFooter(null, data, false);
    return;
  }
  renderCard(briefing);
  renderRegime(briefing);
  renderFooter(briefing, data, isStale);
  html.dataset.state = isStale ? "stale" : briefing.state === "fallback" ? "fallback" : "default";
}

function paintTerminalError() {
  // Nothing to show and no cache — clear the skeletons so it doesn't shimmer forever.
  html.dataset.state = "error";
  els.rows.innerHTML = "";
  els.footer.textContent = "Couldn’t load the latest data — pull to refresh.";
}

/* ── data loading ────────────────────────────────────── */
async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${url} ${r.status}`);
  return r.json();
}

async function loadData() {
  const briefingUrl =
    demo === "hero" ? "demo/briefing-hero.json" :
    demo === "fallback" ? "demo/briefing-fallback.json" : "briefing.json";
  // allSettled, NOT Promise.all: a failed briefing.json must not discard a healthy
  // indicators.json — the rows have to render regardless (the invariant).
  const [d, b] = await Promise.allSettled([getJSON("indicators.json"), getJSON(briefingUrl)]);
  return {
    data: d.status === "fulfilled" ? d.value : null,
    briefing: b.status === "fulfilled" ? b.value : null,
  };
}

async function boot() {
  // 1) instant paint from cache (rows + last good card) — keeps first paint snappy.
  const cached = readCache();
  if (cached) paint(cached.data, cached.briefing);

  // 2) demo: error => collapse card but keep rows from the live indicators feed.
  if (demo === "error") {
    const { data } = await loadData();
    paint(data || (cached && cached.data) || null, null);
    return;
  }
  if (demo === "loading") return; // hold the skeleton for inspection

  // 3) refresh — each feed independently; fall back to cache per-feed.
  const { data, briefing } = await loadData();
  const finalData = data || (cached && cached.data) || null;
  const finalBriefing = briefing || (cached && cached.briefing) || null;
  if (finalData || finalBriefing) {
    paint(finalData, finalBriefing);
    if (data || briefing) writeCache({ data: finalData, briefing: finalBriefing });
  } else {
    paintTerminalError(); // nothing loaded and no cache
  }
}

/* ── cache + helpers ─────────────────────────────────── */
function readCache() { try { return JSON.parse(localStorage.getItem(CACHE_KEY)); } catch { return null; } }
function writeCache(o) { try { localStorage.setItem(CACHE_KEY, JSON.stringify(o)); } catch {} }
function hoursSince(iso) { const t = iso ? new Date(iso).getTime() : NaN; return Number.isNaN(t) ? Infinity : (Date.now() - t) / 3.6e6; }
function escape(s) { const d = document.createElement("div"); d.textContent = s == null ? "" : s; return d.innerHTML; }
function stripToday(s) { return s.replace(/^today:\s*/i, ""); }
function emphasizeNumbers(s) { return s.replace(/(\d[\d,.]*\s?(?:percent|%|basis points)?)/gi, "<b>$1</b>"); }

/* ── detail sheet (V2 Tier 4 — Apple-Stocks-style) ─────────────────────────── */
const TF_BASE = [["6M", 6], ["1Y", 12], ["2Y", 24], ["5Y", 60], ["10Y", 120], ["ALL", Infinity]];
const TF_DAILY = [["1M", 1], ["3M", 3], ...TF_BASE]; // FRED is daily-close → no "1D"
let sheetState = null;   // { ind, tf }
let lastFocus = null;

function timeframesFor(ind) { return ind.frequency === "daily" ? TF_DAILY : TF_BASE; }

function sliceHistory(history, months) {
  const h = (history || []).filter((p) => p && p[1] != null);
  if (h.length < 2 || !Number.isFinite(months)) return h;
  const last = new Date(h[h.length - 1][0]);
  const cutoff = new Date(last); cutoff.setMonth(cutoff.getMonth() - months);
  const sliced = h.filter((p) => new Date(p[0]) >= cutoff);
  return sliced.length >= 2 ? sliced : h.slice(-2); // never draw a degenerate 1-point line
}

function fmtUnitVal(n, ind) { // a history value in the row's own unit (no +/- sign)
  if (!Number.isFinite(n)) return "—";
  if (ind.unit === "usd_tn") return `$${(n / 1e6).toFixed(1)}T`;
  if (ind.unit === "USD") return `$${n.toFixed(2)}`;   // match fmtValue: Brent/gold ranges keep the $
  if (ind.unit === "K") return `${Math.round(n).toLocaleString()}K`;
  if (ind.unit === "count") return Math.round(n).toLocaleString();
  if (ind.unit === "index" && Math.abs(n) >= 1000)     // min 2dp too, else Nasdaq's range read 1dp
    return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const dec = DEC2.has(ind.id) ? 2 : 1;
  return ind.unit === "%" ? `${n.toFixed(dec)}%` : n.toFixed(dec);
}
function fmtAxis(n, ind) { // compact y-axis tick
  if (ind.unit === "usd_tn") return `$${(n / 1e6).toFixed(0)}T`;
  const a = Math.abs(n);
  if (a >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${(n / 1e3).toFixed(0)}k`;
  return ind.unit === "%" ? n.toFixed(1) : String(Math.round(n * 10) / 10);
}
// ponytail: a date-only ISO ("2026-06-23") parses as UTC midnight, which toLocaleDateString then
// renders one day EARLY in any negative-offset TZ (all of the Americas). Append a local time so it
// parses as local midnight and the displayed day matches the data's day. Date-only inputs only.
const localDate = (iso) => new Date(`${iso}T00:00:00`);
// Reader-facing dates are written out ("Aug 19, 2026"), never dashed ISO. Guarded: period labels
// arrive pre-written for monthly ("Jul 2026") and quarterly ("Q2 2026") rows — pass those through.
function fmtDateLong(s) {
  if (!s || !/^\d{4}-\d{2}-\d{2}$/.test(s)) return s || "";
  const d = localDate(s);
  if (Number.isNaN(d.getTime())) return s;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}
// Quarterly series (FRED dates them to the quarter's FIRST day) label as "Q1 2026" — a raw
// "Jan 26" reads as three-months-stale data when the Q1 print is in fact the newest there is.
function fmtQuarter(d, longYear) {
  const q = Math.floor(d.getMonth() / 3) + 1;
  return `Q${q} ${longYear ? d.getFullYear() : String(d.getFullYear()).slice(-2)}`;
}
function fmtAxisDate(iso, ind) {
  const d = localDate(iso);
  if (Number.isNaN(d.getTime())) return "";
  if (ind && ind.frequency === "quarterly") return fmtQuarter(d);
  return d.toLocaleDateString("en-US", { month: "short", year: "2-digit" });
}

// Chart geometry — the single source of truth for the value→pixel mapping, shared by the SVG
// renderer (chartSvg) and the hover/touch scrubber (scrubAt) so the crosshair dot sits exactly
// on the rendered line. Returns the viewBox box + the X(time)/Y(value) closures.
function chartGeom(points) {
  const W = 320, H = 156, padL = 40, padR = 10, padT = 10, padB = 22;
  const xs = points.map((p) => new Date(p[0]).getTime());
  const ys = points.map((p) => p[1]);
  const minX = xs[0], maxX = xs[xs.length - 1];
  let minY = Math.min(...ys), maxY = Math.max(...ys);
  if (minY === maxY) { minY -= 1; maxY += 1; }
  const pad = (maxY - minY) * 0.08; minY -= pad; maxY += pad;
  const X = (t) => padL + (maxX === minX ? 0 : (t - minX) / (maxX - minX)) * (W - padL - padR);
  const Y = (v) => padT + (1 - (v - minY) / (maxY - minY)) * (H - padT - padB);
  return { W, H, padL, padR, padT, padB, xs, ys, minX, maxX, minY, maxY, X, Y };
}

function chartSvg(points, ind) {
  if (!points || points.length < 2) return `<p class="sheet__empty">Not enough data for this range.</p>`;
  const g = chartGeom(points);
  const { W, H, padL, padR, padT, padB, xs, ys, minX, maxX, minY, maxY, X, Y } = g;
  const line = points.map((p, i) => `${X(xs[i]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ");
  const rising = ys[ys.length - 1] >= ys[0];
  const stroke = rising ? "var(--up)" : "var(--down)";
  const grid = [0, 1, 2, 3].map((k) => {
    const v = minY + (maxY - minY) * (k / 3), y = Y(v);
    return `<line class="chart__grid" x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}"/>` +
           `<text class="chart__ylab" x="${padL - 5}" y="${(y + 3).toFixed(1)}" text-anchor="end">${fmtAxis(v, ind)}</text>`;
  }).join("");
  // Ticks by TIME (not point index): _downsample_history keeps recent days at full resolution
  // and thins older ones, so the array's middle index sits nowhere near the range's time midpoint
  // — that mismatch is what made label spacing look inconsistent (and collide) across timeframes.
  const xt = [minX, (minX + maxX) / 2, maxX];
  const xlabs = xt.map((t, k) => {
    const anchor = k === 0 ? "start" : k === 2 ? "end" : "middle";
    const iso = new Date(t).toISOString().slice(0, 10);
    return `<text class="chart__xlab" x="${X(t).toFixed(1)}" y="${H - 6}" text-anchor="${anchor}">${fmtAxisDate(iso, ind)}</text>`;
  }).join("");
  const lx = X(maxX).toFixed(1), ly = Y(ys[ys.length - 1]).toFixed(1);
  const area = `${X(minX).toFixed(1)},${Y(minY).toFixed(1)} ${line} ${X(maxX).toFixed(1)},${Y(minY).toFixed(1)}`;
  // Scrubber crosshair (hidden until a pointer hovers/touches the chart; positioned by scrubAt).
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${escape(ind.display_name)} history chart">
    <defs>
      <linearGradient id="area-grad" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="${stroke}" stop-opacity="0.38"/>
        <stop offset="1" stop-color="${stroke}" stop-opacity="0.02"/>
      </linearGradient>
    </defs>
    ${grid}
    <polygon class="chart__area" points="${area}" fill="url(#area-grad)"/>
    <polyline class="chart__line" points="${line}" fill="none" stroke="${stroke}"/>
    <circle class="chart__dot" cx="${lx}" cy="${ly}" r="2.6" fill="${stroke}"/>
    ${xlabs}
    <g class="chart__scrub" style="display:none">
      <line class="chart__scrub-line" x1="0" y1="${padT}" x2="0" y2="${H - padB}"/>
      <circle class="chart__scrub-dot" cx="0" cy="0" r="3.2"/>
    </g>
  </svg>`;
}

function pctChange(ind) {
  if (!ind.delta) return "";           // null AND exact 0 — a zero delta once rendered "· −0.00%"
  // A relative % of a net-change or spread is a meaningless read (payrolls "−215%", real fed
  // funds "+169%"): SIGNED marks exactly those rows, so their Latest change shows points only.
  if (SIGNED.has(ind.id)) return "";
  const prior = (Number(ind.value) || 0) - ind.delta;
  if (!prior) return "";
  return `${ind.delta > 0 ? "+" : "−"}${Math.abs((ind.delta / prior) * 100).toFixed(2)}%`;
}
function statsHtml(ind, sliced) {
  const vals = sliced.map((p) => p[1]);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const hist = ind.history || [];
  const last = hist.length ? new Date(hist[hist.length - 1][0]) : null;
  let y52 = "";
  if (last) {
    const yrAgo = new Date(last); yrAgo.setFullYear(yrAgo.getFullYear() - 1);
    const yv = hist.filter((p) => new Date(p[0]) >= yrAgo).map((p) => p[1]);
    if (yv.length) y52 = `${fmtUnitVal(Math.min(...yv), ind)} – ${fmtUnitVal(Math.max(...yv), ind)}`;
  }
  const chg = ind.delta == null ? null
    : `${ind.delta > 0 ? "▲" : ind.delta < 0 ? "▼" : ""} ${pointsMag(ind)}${pctChange(ind) ? ` · ${pctChange(ind)}` : ""}`;
  const rows = [
    ["Current", fmtValue(ind)],
    chg ? ["Latest change", chg] : null,
    ["Range shown", `${fmtUnitVal(lo, ind)} – ${fmtUnitVal(hi, ind)}`],
    y52 ? ["52-week range", y52] : null,
    (ind.level != null && ind.level !== ind.value) ? ["Underlying level", fmtLevel(ind)] : null,
    // WEEKLY series ride the config's DAILY cadence (staleness/labels want ISO dates), but the
    // sheet should say what the reader observes: one new print a week.
    ["Frequency", WEEKLY_ROWS.has(ind.id) ? "Weekly"
      : ind.frequency ? ind.frequency[0].toUpperCase() + ind.frequency.slice(1) : "—"],
    ["Last release", fmtDateLong(ind.last_release_date) || "—"],
  ].filter(Boolean);
  return rows.map(([k, v]) => `<div class="stat"><dt>${k}</dt><dd>${v}</dd></div>`).join("");
}
function linksHtml(ind) {
  const news = `https://news.google.com/search?q=${encodeURIComponent(`${ind.display_name} United States economy`)}`;
  // Only allow https:// — escape() HTML-encodes text but does not strip javascript: or data: URIs.
  const srcUrl = ind.source_url && ind.source_url.startsWith("https://") ? ind.source_url : "#";
  // "View source" (not "View on FRED") — most rows are FRED, but gold_spot links to gold-api.com.
  return `<a class="sheet__link" href="${escape(srcUrl)}" target="_blank" rel="noopener noreferrer">View source ↗</a>` +
         `<a class="sheet__link" href="${news}" target="_blank" rel="noopener noreferrer">Search news ↗</a>`;
}

function renderSheet() {
  if (!sheetState) return;
  const { ind, tf } = sheetState;
  const months = (timeframesFor(ind).find((t) => t[0] === tf) || [])[1];
  const sliced = sliceHistory(ind.history, months);
  // Cache the sliced points + geometry for the chart scrubber (scrubAt reads these).
  sheetState.points = sliced;
  sheetState.geom = sliced.length >= 2 ? chartGeom(sliced) : null;
  byId("sheet-name").textContent = ind.long_name || ind.display_name;
  byId("sheet-val").textContent = fmtValue(ind);
  byId("sheet-sub").innerHTML = `${tfChipHtml(sliced, ind)}<span class="sheet__period">${escape(fmtDateLong(ind.period_label))}</span>`;
  byId("sheet-tf").innerHTML = timeframesFor(ind)
    .map(([lab]) => `<button class="tf ${lab === tf ? "tf--on" : ""}" type="button" data-tf="${lab}" role="tab" aria-selected="${lab === tf}">${lab}</button>`)
    .join("");
  byId("sheet-chart").innerHTML = chartSvg(sliced, ind);
  byId("sheet-stats").innerHTML = statsHtml(ind, sliced);
  byId("sheet-links").innerHTML = linksHtml(ind);
  const ro = byId("chart-readout"); if (ro) ro.hidden = true; // clear any stale scrub readout on re-render
}
/* ── detail-sheet motion: Apple-Stocks open / close + drag-to-dismiss ─────
   Pointer Events (touch + mouse, one path) with setPointerCapture. All four
   close triggers (backdrop, X, Escape, dismiss-drag) funnel through
   animateSheetClose(); per-open listeners are torn down on close (no leaks). */

/* tunables */
const SHEET_CLOSE_MS    = 300;   /* keep in sync with .sheet__panel transition (.3s) */
const SHEET_DRAG_SLOP   = 6;     /* px of downward intent before we claim the gesture */
const SHEET_DISMISS_FRAC = 0.25; /* dragged past 25% of panel height => dismiss …     */
const SHEET_DISMISS_PX  = 110;   /* … or at least this many px (whichever is smaller) */
const SHEET_FLICK_VEL   = 0.55;  /* px/ms downward flick => dismiss regardless of dist */

const sheetReducedMotion = () =>
  !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

let sheetDrag    = null;   /* active gesture, or null */
let sheetClosing = false;  /* guard: a close animation is in flight */
let sheetCloseTimer = null;
let sheetSuppressClick = false; /* swallow the synthetic click iOS fires after a touch-drag */
let sheetScrollLockY = 0;       /* page scroll position captured while the sheet is open (iOS lock) */

function sheetPanel()    { return els.sheet && els.sheet.querySelector(".sheet__panel"); }
function sheetBackdrop() { return els.sheet && els.sheet.querySelector(".sheet__backdrop"); }

/* ── the ONE animated-close funnel ───────────────────────────────────────── */
function animateSheetClose() {
  if (!els.sheet || els.sheet.hidden || sheetClosing) return;   /* re-entrancy guard */
  sheetClosing = true;
  teardownSheetDrag();                                          /* drop in-flight gesture + listeners */

  const panel = sheetPanel(), backdrop = sheetBackdrop();

  /* Reduced motion (or no panel): the global CSS rule disables transitions and
     transitionend will never fire — finish instantly. */
  if (sheetReducedMotion() || !panel) { finishSheetClose(); return; }

  els.sheet.classList.remove("sheet--dragging");  /* ensure the transition is live */
  panel.style.animation = "none";                 /* stop any still-running entrance animation */
  /* Animate the panel from its CURRENT position (rest translateY(0) OR a mid-drag inline
     offset) down to fully off-screen; the backdrop fades out in step. Inline-transform
     close = the slide-down continues smoothly from wherever the finger left it. */
  panel.style.transform = "translateY(100%)";
  if (backdrop) backdrop.style.opacity = "0";

  let done = false;
  const finish = () => {
    if (done) return; done = true;
    clearTimeout(sheetCloseTimer); sheetCloseTimer = null;
    panel.removeEventListener("transitionend", onEnd);
    finishSheetClose();
  };
  const onEnd = (e) => { if (e.target === panel && e.propertyName === "transform") finish(); };
  panel.addEventListener("transitionend", onEnd);
  sheetCloseTimer = setTimeout(finish, SHEET_CLOSE_MS + 80);    /* fallback if transitionend is missed */
}

/* Final teardown — the single resolution point for every close path. */
function finishSheetClose() {
  if (!els.sheet) return;
  els.sheet.hidden = true;
  els.sheet.classList.remove("sheet--dragging");
  const panel = sheetPanel(), backdrop = sheetBackdrop();
  if (panel)    { panel.style.transform = ""; panel.style.animation = ""; }
  if (backdrop) backdrop.style.opacity = "";
  document.body.classList.remove("sheet-open");  /* unlock the iOS scroll lock + restore position */
  document.body.style.top = "";
  window.scrollTo(0, sheetScrollLockY);
  sheetState = null;
  sheetClosing = false;
  if (lastFocus && lastFocus.focus) lastFocus.focus();          /* restore focus to the opener row */
}

/* Spring back to fully open after an aborted (sub-threshold) drag. */
function springSheetOpen() {
  const panel = sheetPanel(), backdrop = sheetBackdrop();
  els.sheet.classList.remove("sheet--dragging");                /* re-enable the .3s transition … */
  if (panel)    panel.style.transform = "";                     /* … animate back to rest translateY(0) */
  if (backdrop) backdrop.style.opacity = "";
}

/* ── drag-to-dismiss (Pointer Events) ────────────────────────────────────── */
function onSheetPointerDown(e) {
  if (sheetClosing) return;
  if (e.pointerType === "mouse" && e.button !== 0) return;      /* primary button only */
  const panel = sheetPanel();
  if (!panel) return;

  /* Never start a drag from an interactive control — taps on the X, links,
     timeframe pills, and chips must keep working. (Chips live in the sheet
     header via renderSheet's #sheet-sub.) */
  if (e.target.closest && e.target.closest("button, a, .tf, .chip")) return;

  /* The chart owns its own pointer gesture (hover/touch scrubber). Never arm a
     dismiss-drag when the gesture starts on the chart, or a finger-drag across
     it would slide the sheet away instead of scrubbing. Dismiss stays available
     via the handle/header/backdrop/✕. */
  if (e.target.closest && e.target.closest("#sheet-chart")) return;

  const onGrab = !!(e.target.closest && e.target.closest(".sheet__handle, .sheet__head"));
  /* Scroll-vs-drag arbitration: a body-of-panel drag only arms when the panel
     is scrolled to the very top; a drag on the handle/header always arms.
     Otherwise the native inner scroll keeps the gesture and we never fight it. */
  if (!onGrab && panel.scrollTop > 0) return;

  const now = (e.timeStamp != null ? e.timeStamp : performance.now());
  sheetDrag = {
    id: e.pointerId,
    startY: e.clientY, startX: e.clientX,
    height: panel.getBoundingClientRect().height || window.innerHeight,
    claimed: false,            /* becomes true once we own the gesture */
    onGrab,
    samples: [{ t: now, y: e.clientY }],   /* ring buffer for flick velocity */
  };
}

function onSheetPointerMove(e) {
  const d = sheetDrag;
  if (!d || e.pointerId !== d.id) return;

  const dy = e.clientY - d.startY;
  const dx = e.clientX - d.startX;

  /* velocity ring buffer (most recent ~80ms) */
  const now = (e.timeStamp != null ? e.timeStamp : performance.now());
  d.samples.push({ t: now, y: e.clientY });
  if (d.samples.length > 6) d.samples.shift();

  if (!d.claimed) {
    if (dy > SHEET_DRAG_SLOP && dy > Math.abs(dx)) {
      /* downward-dominant past slop → claim it */
      d.claimed = true;
      els.sheet.classList.add("sheet--dragging");
      try { sheetPanel().setPointerCapture(d.id); } catch (_) {}
    } else if (Math.abs(dx) > SHEET_DRAG_SLOP || dy < -SHEET_DRAG_SLOP) {
      sheetDrag = null;        /* horizontal / upward → not ours; yield to native scroll */
      return;
    } else {
      return;                  /* still within slop — wait */
    }
  }

  if (e.cancelable) e.preventDefault();   /* we own it: block scroll / pull-to-refresh */
  const offset = Math.max(0, dy);         /* clamp: never lift above the open position */
  const panel = sheetPanel(), backdrop = sheetBackdrop();
  if (panel)    panel.style.transform = `translateY(${offset}px)`;
  if (backdrop) backdrop.style.opacity = String(Math.max(0, 1 - offset / d.height));
}

function onSheetPointerUp(e) {
  const d = sheetDrag;
  if (!d || e.pointerId !== d.id) return;
  try { sheetPanel().releasePointerCapture(d.id); } catch (_) {}

  if (!d.claimed) { sheetDrag = null; return; }   /* never engaged — a tap/scroll */

  const offset = Math.max(0, e.clientY - d.startY);
  /* flick velocity from the ring buffer (px/ms, +ve = downward) */
  const s = d.samples, last = s[s.length - 1];
  let first = s[0];
  for (let i = s.length - 1; i >= 0; i--) { if (last.t - s[i].t <= 80) first = s[i]; else break; }
  const dt = last.t - first.t;
  const vy = dt > 0 ? (last.y - first.y) / dt : 0;

  const threshold = Math.min(SHEET_DISMISS_PX, d.height * SHEET_DISMISS_FRAC);
  const dismiss = offset > threshold || vy > SHEET_FLICK_VEL;

  sheetDrag = null;
  if (dismiss) {
    /* swallow the synthetic click iOS fires ~300ms after touchend so it can't reopen a row
       sitting under the lift point once the sheet is gone (only on dismiss — a spring-back
       keeps the sheet open, so a follow-up X/chip/pill tap must still register) */
    sheetSuppressClick = true;
    setTimeout(() => { sheetSuppressClick = false; }, 500);
    animateSheetClose();              /* continue the slide-down from the current offset */
  } else {
    springSheetOpen();                /* snap back to fully open */
  }
}

function onSheetPointerCancel(e) {
  const d = sheetDrag;
  if (!d || e.pointerId !== d.id) return;
  try { sheetPanel().releasePointerCapture(d.id); } catch (_) {}
  sheetDrag = null;
  springSheetOpen();                  /* OS/gesture takeover → restore to open */
}

/* per-open listener wiring */
function bindSheetDrag() {
  const panel = sheetPanel();
  if (!panel) return;
  panel.addEventListener("pointerdown",   onSheetPointerDown);
  panel.addEventListener("pointermove",   onSheetPointerMove, { passive: false }); /* must preventDefault */
  panel.addEventListener("pointerup",     onSheetPointerUp);
  panel.addEventListener("pointercancel", onSheetPointerCancel);
}
function teardownSheetDrag() {
  const panel = sheetPanel();
  if (sheetDrag && panel) { try { panel.releasePointerCapture(sheetDrag.id); } catch (_) {} }
  sheetDrag = null;
  if (!panel) return;
  panel.removeEventListener("pointerdown",   onSheetPointerDown);
  panel.removeEventListener("pointermove",   onSheetPointerMove);
  panel.removeEventListener("pointerup",     onSheetPointerUp);
  panel.removeEventListener("pointercancel", onSheetPointerCancel);
}

function openSheet(id) {
  if (!lastData || !els.sheet) return;
  const ind = (lastData.indicators || []).find((i) => i.id === id);
  if (!ind || !ind.history || ind.history.length < 2) return; // no chart → no sheet

  /* If a close animation is mid-flight, cancel it and reopen cleanly. */
  clearTimeout(sheetCloseTimer); sheetCloseTimer = null; sheetClosing = false;
  teardownSheetDrag();

  const tfs = timeframesFor(ind).map((t) => t[0]);
  sheetState = { ind, tf: tfs.includes("1Y") ? "1Y" : tfs[0] };
  renderSheet();

  /* clear residual inline state from a prior open/close */
  els.sheet.classList.remove("sheet--dragging");
  const panel = sheetPanel(), backdrop = sheetBackdrop();
  if (panel)    panel.style.transform = "";
  if (backdrop) backdrop.style.opacity = "";

  /* iOS scroll lock: position:fixed the body so the page can't scroll/rubber-band behind the
     sheet — body{overflow:hidden} alone is ignored by iOS Safari. */
  sheetScrollLockY = window.scrollY || window.pageYOffset || 0;
  document.body.classList.add("sheet-open");
  document.body.style.top = `-${sheetScrollLockY}px`;
  lastFocus = document.activeElement;

  els.sheet.hidden = false;            // display:none → flex

  /* Force the entrance animations to (re)play on EVERY open: WebKit/Blink do NOT restart a
     descendant's CSS animation when only an ancestor toggles display, so do the standard
     none → reflow → "" restart. (No rAF — robust even when the frame clock is throttled.) */
  if (panel)    { panel.style.animation = "none";    void panel.offsetWidth;    panel.style.animation = ""; }
  if (backdrop) { backdrop.style.animation = "none"; void backdrop.offsetWidth; backdrop.style.animation = ""; }

  bindSheetDrag();                                  // wire per-open pointer listeners
  const close = els.sheet.querySelector(".sheet__close");
  if (close) close.focus({ preventScroll: true });
}

function closeSheet() {                              // all 4 triggers land here
  animateSheetClose();
}


/* Delegated interaction:
   • a chip cycles the global %/points/level mode (and never opens the sheet)
   • a timeframe pill re-slices the chart   • [data-close] closes the sheet
   • any other row tap opens that indicator's detail sheet */
document.addEventListener("click", (e) => {
  if (sheetSuppressClick) { sheetSuppressClick = false; e.stopPropagation(); e.preventDefault(); return; }
  if (e.target.closest(".chip")) { e.stopPropagation(); cycleMode(); return; }
  if (e.target.closest("#refresh")) { refresh(); return; }
  const quote = e.target.closest(".quote");
  if (quote) { copyQuote(quote); return; }   // tap a briefing line to copy it
  if (e.target.closest("[data-close]")) { closeSheet(); return; }
  const pill = e.target.closest(".tf");
  if (pill) { if (sheetState) { sheetState.tf = pill.dataset.tf; renderSheet(); } return; }
  const tp = e.target.closest(".tp[data-id]");   // a talking-points card opens its indicator's detail sheet
  if (tp) { openSheet(tp.dataset.id); return; }
  const row = e.target.closest(".row[data-id]");
  if (row) openSheet(row.dataset.id);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && els.sheet && !els.sheet.hidden) { closeSheet(); return; }
  if (e.key !== "Enter" && e.key !== " ") return;
  const t = e.target;
  if (t.classList && t.classList.contains("chip")) { e.preventDefault(); cycleMode(); return; }
  if (t.classList && t.classList.contains("tf")) { e.preventDefault(); if (sheetState) { sheetState.tf = t.dataset.tf; renderSheet(); } return; }
  if (t.classList && t.classList.contains("quote")) { e.preventDefault(); copyQuote(t); return; }
  const tp = t.closest && t.closest(".tp[data-id]");
  if (tp) { e.preventDefault(); openSheet(tp.dataset.id); return; }
  const row = t.closest && t.closest(".row[data-id]");
  if (row) { e.preventDefault(); openSheet(row.dataset.id); }
});

/* ── chart scrubber (Apple-Stocks crosshair) ──────────────────────────────────
   Hover (mouse) or press-drag (touch) over the detail-sheet chart shows a vertical
   crosshair through the nearest data point + a date/value readout. Listeners live on
   the persistent #sheet-chart element (its innerHTML is swapped each renderSheet, but
   the element — and these listeners — survive). Geometry comes from sheetState.geom. */
function scrubAt(clientX) {
  if (!sheetState || !sheetState.geom) return;
  const host = byId("sheet-chart");
  const svg = host && host.querySelector("svg");
  if (!svg) return;
  const g = sheetState.geom, pts = sheetState.points;
  const rect = svg.getBoundingClientRect();        // re-read each event — the sheet body can scroll
  if (!rect.width) return;
  let vbX = ((clientX - rect.left) / rect.width) * g.W;
  vbX = Math.max(g.padL, Math.min(g.W - g.padR, vbX));
  let best = 0, bestD = Infinity;                  // nearest point by screen-x distance
  for (let i = 0; i < g.xs.length; i++) {
    const d = Math.abs(g.X(g.xs[i]) - vbX);
    if (d < bestD) { bestD = d; best = i; }
  }
  const px = g.X(g.xs[best]).toFixed(1), py = g.Y(pts[best][1]).toFixed(1);
  const ln = svg.querySelector(".chart__scrub-line");
  const dot = svg.querySelector(".chart__scrub-dot");
  const grp = svg.querySelector(".chart__scrub");
  if (ln)  { ln.setAttribute("x1", px); ln.setAttribute("x2", px); }
  if (dot) { dot.setAttribute("cx", px); dot.setAttribute("cy", py); }
  if (grp) grp.style.display = "";
  const ro = byId("chart-readout");
  if (ro) {
    const d = localDate(pts[best][0]);
    const date = Number.isNaN(d.getTime()) ? ""
      : sheetState.ind.frequency === "quarterly" ? fmtQuarter(d, true)
      : d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
    ro.innerHTML = `<span class="ro-date">${escape(date)}</span><span class="ro-val">${fmtUnitVal(pts[best][1], sheetState.ind)}</span>`;
    ro.hidden = false;
  }
}
function hideScrub() {
  const host = byId("sheet-chart");
  const grp = host && host.querySelector(".chart__scrub");
  if (grp) grp.style.display = "none";
  const ro = byId("chart-readout"); if (ro) ro.hidden = true;
}
function bindChartScrub() {
  const host = byId("sheet-chart");
  if (!host) return;
  let touchScrub = false;
  host.addEventListener("pointermove", (e) => {
    if (e.pointerType === "mouse" || touchScrub) scrubAt(e.clientX);
  });
  host.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") hideScrub(); });
  host.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse") return;           // touch/pen: own the gesture so the panel can't dismiss
    e.stopPropagation();
    touchScrub = true;
    try { host.setPointerCapture(e.pointerId); } catch (_) {}
    scrubAt(e.clientX);
  });
  const end = (e) => {
    if (e.pointerType === "mouse") return;
    touchScrub = false;
    try { host.releasePointerCapture(e.pointerId); } catch (_) {}
    hideScrub();
  };
  host.addEventListener("pointerup", end);
  host.addEventListener("pointercancel", end);
}
bindChartScrub();

boot();

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
}
