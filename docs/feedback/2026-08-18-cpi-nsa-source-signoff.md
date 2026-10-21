# Bruce sign-off — CPI to the NSA source, and BLS-direct release grafting (2026-08-18)

**Decision:** switch the published CPI / core CPI from FRED's seasonally-adjusted series to the
**not-seasonally-adjusted** series that BLS and the news headline, and add a BLS same-day source
graft for the BLS-origin indicators. **Bruce signed off 2026-08-18.**

## What Bruce reported
The app showed CPI **3.3%** while the news said **3.4%**. His instruction: *"use the source
whenever possible."*

## Why they differed (not an error)
Seasonal adjustment. Proven against the BLS API on 2026-08-17, July 2026:

| Series | 12-month change | Where it shows |
|--------|-----------------|----------------|
| NSA `CUUR0000SA0` / FRED `CPIAUCNS` | 3.36% → **3.4%** | BLS headline over-the-year, the news, Bruce |
| SA `CUSR0000SA0` / FRED `CPIAUCSL` | 3.30% | what the app had been publishing |

BLS's headline over-the-year CPI change is conventionally the **NSA** figure; FRED's default CPI
series (`CPIAUCSL`) is seasonally adjusted. Core CPI has the same split but ~0.01pp today
(NSA 2.48% vs SA 2.47%). Unemployment and payrolls headlines are already SA, so they are unchanged.

## The classifier gate (D9)
`core_cpi_yoy` is a live regime input (compared to `INFLATION_HOT = 3.0` / `INFLATION_NEAR_TARGET
= 2.5`), whose thresholds were back-tested on the SA series. `scripts/backtest_thresholds.py` was
re-run before/after over **144 months (2014-09 … 2026-08)**:

| | SA (baseline) | NSA (proposed) |
|--------|---------------|----------------|
| Regime distribution | — | **identical** |
| Transitions | 15 (~1.2/yr) | **15 (~1.2/yr), identical** |
| Month labels changed | — | **0** |

Only the `core=` input value shifts (e.g. 2015-08: 1.9 → 1.8); no month's regime changes. The
3.0 / 2.5 thresholds behave identically on NSA, so **no recalibration is required** and the
classifier's D9 signature is preserved. Full SA and NSA back-test outputs are on the branch.

## What shipped (branch `feat/bls-source-nsa-cpi`)
- `config.py`: `cpi_yoy` → `CPIAUCNS`, `core_cpi_yoy` → `CPILFENS` (NSA). Value now 3.36 / 2.48.
- `bls_client.BlsSource`: grafts BLS's 08:30 ET release tip onto CPI/core (NSA) and unemployment /
  payrolls / participation / mfg + construction employment (SA), so they read same-day from the
  primary publisher. Never-raises; `BLS_API_KEY` optional (raises the 25→500/day cap only).
- `sources.build_source()`: one source constructor for every entrypoint, retiring the bare-
  `FredClient` trap (which had also left `python -m macro_pipeline` 400ing since 2026-07-20).
- `public/app.js`: headline `cpi_yoy` now displays 1dp ("3.4%") to match the headline.
- Tests: +9 for `BlsSource`; suite green (the one langsmith-extra failure is pre-existing).

## Not changed
The classifier thresholds (v1.2), the prompt (v1.5), and the AM schedule floor (10:30 ET). The BLS
graft is release-morning insurance + provenance; it does not by itself publish earlier.
