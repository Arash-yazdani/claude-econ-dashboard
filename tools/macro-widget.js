// Daily Macro Briefing — iOS home-screen widget for Scriptable
// =============================================================================
// Puts the headline on the home screen so there are ZERO taps to read it.
// Tapping the widget opens the PWA.
//
// INSTALL (once, ~3 minutes):
//   1. Install "Scriptable" from the App Store (free).
//   2. Open Scriptable → "+" (top right) → paste this whole file.
//   3. Tap the settings icon (top left) → name it "Macro" → Done.
//   4. Long-press the home screen → "+" → search "Scriptable" → pick the
//      MEDIUM size → Add Widget.
//   5. Long-press the new widget → "Edit Widget" → Script = "Macro".
//      Leave "When Interacting" as "Run Script".
//
// Supports small / medium / large home-screen widgets and the rectangular
// lock-screen widget. Medium is the one to use — it fits the headline plus
// both quotable numbers.
//
// Design notes:
//   * Colours are lifted verbatim from public/styles.css so the widget reads as
//     the same product, including the per-regime accent.
//   * Georgia + SF Mono are the iOS-resident members of the app's own font
//     stacks (--serif / --mono), so the "editorial serif over precise mono"
//     intent survives even though webfonts can't load here.
//   * Every network result is cached to disk. A widget that renders an error
//     is worse than useless on someone's home screen, so an offline refresh
//     shows the last good briefing (clearly dated) rather than a failure.
// =============================================================================

const BASE = "https://claude-econ.modeo.io";
const CACHE_FILE = "macro-widget-cache.json";
const REFRESH_MINUTES = 30;

// ── palette: public/styles.css ───────────────────────────────────────────────
const BG = "#0b0c0e";        // --bg
const TEXT = "#e9e6df";      // --text
const DIM = "#b3b2ab";       // --text-dim
const MUTE = "#7c7f86";      // --text-mute
const UP = "#30d158";        // --up
const DOWN = "#ff453a";      // --down
const AMBER = "#e0b15e";     // --amber

const ACCENT = {             // html[data-regime="…"] { --accent }
  soft_landing: "#6fc2a6",
  late_cycle_expansion: "#d8b25a",
  stagflation_risk: "#e0875e",
  recovery: "#6fa8d8",
  slowdown: "#cf9a6b",
  indeterminate: "#8b9096",   // W3 — neutral; "no call" is not a warning
};

// ── value formatting — ported from public/app.js fmtValue() ──────────────────
// Kept byte-for-byte equivalent so a number never reads differently on the home
// screen than it does inside the app.
const DEC2 = new Set(["treasury_10y", "fed_funds", "treasury_2y", "mortgage_30y",
  "hy_spread", "yield_curve_2s10s", "real_fed_funds", "real_10y", "tips_10y_real"]);
const SIGNED = new Set(["payrolls", "retail_sales_mom", "industrial_production",
  "yield_curve_2s10s", "real_fed_funds", "real_10y"]);

function fmtValue(ind) {
  const num = typeof ind.value === "number" ? ind.value : Number(ind.value);
  if (ind.value == null || !Number.isFinite(num)) return "—";
  const sign = SIGNED.has(ind.id) && num > 0 ? "+" : "";
  if (ind.unit === "K") return `${sign}${Math.round(num).toLocaleString()}K`;
  if (ind.unit === "count") return Math.round(num).toLocaleString();
  if (ind.unit === "usd_tn") return `$${(num / 1e6).toFixed(1)}T`;
  if (ind.unit === "USD") return `$${num.toFixed(2)}`;
  if (ind.unit === "index" && Math.abs(num) >= 1000)
    return num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const n = num.toFixed(DEC2.has(ind.id) ? 2 : 1);
  return ind.unit === "%" ? `${sign}${n}%` : `${sign}${n}`;
}

function deltaText(ind) {
  const d = typeof ind.delta === "number" ? ind.delta : Number(ind.delta);
  if (ind.delta == null || !Number.isFinite(d) || d === 0) return { text: "—", color: MUTE };
  const arrow = d > 0 ? "▲" : "▼";
  const mag = Math.abs(d) >= 100 ? Math.round(Math.abs(d)).toLocaleString() : Math.abs(d).toFixed(2);
  return { text: `${arrow}${mag}`, color: d > 0 ? UP : DOWN };
}

// ── data: network first, disk cache as the offline fallback ──────────────────
function cachePath() {
  return FileManager.local().joinPath(FileManager.local().documentsDirectory(), CACHE_FILE);
}

async function loadData() {
  try {
    const briefing = await new Request(`${BASE}/briefing.json`).loadJSON();
    const indicators = await new Request(`${BASE}/indicators.json`).loadJSON();
    const payload = { briefing, indicators, cachedAt: new Date().toISOString() };
    try {
      FileManager.local().writeString(cachePath(), JSON.stringify(payload));
    } catch (e) { /* a failed write must never cost us a good render */ }
    return { ...payload, stale: false };
  } catch (e) {
    try {
      const raw = FileManager.local().readString(cachePath());
      return { ...JSON.parse(raw), stale: true };
    } catch (e2) {
      return null;  // never fetched successfully — first run with no signal
    }
  }
}

// ── the indicators worth showing ─────────────────────────────────────────────
// The quotable numbers ARE the product (CTQ: "one read he can lead with, backed
// by two numbers he can repeat"), so those come first, in the briefing's own
// order. Core-tier rows pad out the larger sizes.
function pickIndicators(data, n) {
  const byId = new Map((data.indicators.indicators || []).map((i) => [i.id, i]));
  const quoted = (data.briefing.briefing?.quotable_numbers || [])
    .map((q) => byId.get(q.indicator_id)).filter(Boolean);
  const seen = new Set(quoted.map((i) => i.id));
  const core = (data.indicators.indicators || [])
    .filter((i) => i.tier === "core" && !seen.has(i.id));
  return [...quoted, ...core].slice(0, n);
}

// ── rendering helpers ────────────────────────────────────────────────────────
function addRegimeLine(w, regimeLabel, accent, compact) {
  const row = w.addStack();
  row.centerAlignContent();
  const dot = row.addText("●");
  dot.font = Font.systemFont(compact ? 8 : 9);
  dot.textColor = new Color(accent);
  row.addSpacer(5);
  const label = row.addText((regimeLabel || "—").toUpperCase());
  label.font = Font.semiboldSystemFont(compact ? 9 : 10);
  label.textColor = new Color(accent);
  label.lineLimit = 1;
  row.addSpacer();
  return row;
}

function addNumbers(w, inds, compact) {
  const row = w.addStack();
  row.layoutHorizontally();
  inds.forEach((ind, idx) => {
    if (idx) row.addSpacer(compact ? 10 : 14);
    const col = row.addStack();
    col.layoutVertically();

    const name = col.addText(ind.display_name || ind.id);
    name.font = Font.systemFont(compact ? 8 : 9);
    name.textColor = new Color(MUTE);
    name.lineLimit = 1;
    col.addSpacer(2);

    const val = col.addText(fmtValue(ind));
    val.font = Font.regularMonospacedSystemFont(compact ? 13 : 15);
    val.textColor = new Color(TEXT);
    val.lineLimit = 1;
    col.addSpacer(1);

    const d = deltaText(ind);
    const del = col.addText(d.text);
    del.font = Font.regularMonospacedSystemFont(compact ? 8 : 9);
    del.textColor = new Color(d.color);
    del.lineLimit = 1;
  });
  row.addSpacer();
}

function addFooter(w, data) {
  const row = w.addStack();
  row.centerAlignContent();

  const anyStale = data.indicators?.any_stale;
  if (data.stale || anyStale) {
    const warn = row.addText(data.stale ? "⚠ offline · " : "⚠ stale data · ");
    warn.font = Font.systemFont(9);
    warn.textColor = new Color(AMBER);
  }

  const when = data.briefing.generated_at ? new Date(data.briefing.generated_at) : null;
  let stamp = "—";
  if (when && !isNaN(when)) {
    const df = new DateFormatter();
    df.dateFormat = "MMM d, h:mm a";
    stamp = df.string(when);
  }
  const label = row.addText(`${data.briefing.run_label || ""} ${stamp}`.trim());
  label.font = Font.systemFont(9);
  label.textColor = new Color(MUTE);
  label.lineLimit = 1;
  row.addSpacer();
}

function emptyWidget(message) {
  const w = new ListWidget();
  w.backgroundColor = new Color(BG);
  w.url = BASE;
  w.addSpacer();
  const t = w.addText(message);
  t.font = new Font("Georgia", 14);
  t.textColor = new Color(DIM);
  t.centerAlignText();
  w.addSpacer();
  return w;
}

// ── widget ───────────────────────────────────────────────────────────────────
function buildWidget(data, family) {
  const w = new ListWidget();
  w.backgroundColor = new Color(BG);
  w.url = BASE;                      // tap → open the PWA
  w.setPadding(14, 14, 12, 14);
  w.refreshAfterDate = new Date(Date.now() + REFRESH_MINUTES * 60 * 1000);

  const b = data.briefing;
  const accent = ACCENT[b.regime] || ACCENT.indeterminate;  // W3: never default to the reassuring colour

  // A published fallback means the LLM was unavailable and the app deliberately
  // said so. Mirror that honestly — never dress a fallback up as a briefing.
  const isFallback = b.state === "fallback";
  const headline = isFallback
    ? (b.fallback_text || "Briefing unavailable — numbers are still current.")
    : (b.briefing?.headline || "—");

  // Lock screen: two lines of text, no colour, nothing else fits.
  if (family === "accessoryRectangular" || family === "accessoryInline") {
    const t = w.addText(headline);
    t.font = Font.systemFont(12);
    t.lineLimit = family === "accessoryInline" ? 1 : 3;
    w.setPadding(0, 0, 0, 0);
    return w;
  }

  const compact = family === "small";
  addRegimeLine(w, isFallback ? "Data only" : b.regime_label, isFallback ? AMBER : accent, compact);
  w.addSpacer(compact ? 6 : 8);

  const h = w.addText(headline);
  h.font = new Font("Georgia", compact ? 14 : 17);
  h.textColor = new Color(TEXT);
  h.lineLimit = compact ? 4 : 3;
  h.minimumScaleFactor = 0.8;

  // What's new / regime change — the app surfaces these above the numbers, and
  // they are the only lines that ever say "something changed today".
  const extra = b.briefing?.regime_change || b.briefing?.whats_new;
  if (!compact && !isFallback && extra) {
    w.addSpacer(5);
    const e = w.addText(extra);
    e.font = Font.systemFont(11);
    e.textColor = new Color(DIM);
    e.lineLimit = 2;
    e.minimumScaleFactor = 0.85;
  }

  w.addSpacer();

  const count = family === "large" ? 6 : family === "medium" ? 3 : 2;
  const picks = pickIndicators(data, count);
  if (picks.length) {
    addNumbers(w, picks, compact);
    w.addSpacer(compact ? 6 : 8);
  }

  addFooter(w, data);
  return w;
}

// ── main ─────────────────────────────────────────────────────────────────────
const data = await loadData();
const family = config.widgetFamily || "medium";

const widget = data
  ? buildWidget(data, family)
  : emptyWidget("No briefing yet.\nOpen the app once while online.");

if (config.runsInWidget) {
  Script.setWidget(widget);
} else {
  // Tapping "Run" inside Scriptable previews it at the chosen size.
  if (family === "small") await widget.presentSmall();
  else if (family === "large") await widget.presentLarge();
  else await widget.presentMedium();
}
Script.complete();
