# Deterministic Regime Classifier — Rules v1.0

> ⚠️ **SUPERSEDED by v1.1** (D9 re-sign-off, 2026-06-17) after the carry-forward #1 back-test. Two thresholds changed — recovery now uses hysteresis (enter Δ3m ≤ −0.3, stay until > +0.1) and hot inflation (core ≥ 3.0) no longer defaults to soft_landing → late_cycle_expansion. The implemented rules live in `classify()`; see **`regime-classifier-v1.1-proposal.md`** for the accepted changes + evidence. This doc is kept for the original D9 rationale.

**Status:** ✅ Accepted (2026-06-11), **superseded by v1.1 (2026-06-17)** · **Jira:** [SCRUM-21 / D9](https://modeo-team-1.atlassian.net/browse/SCRUM-21) · **Depends on:** D3 (thresholds sign-off) · **Feeds:** B2 (classifier impl), D10 (regime labels)

This is the **deterministic** logic that labels the macro regime. The LLM *narrates* this label — it never re-classifies (enforced by the prompt's `<role>`). Rules-only, fully explainable, no model.

## Regime label set

| id | Plain-English (headline must use this, never the id) |
|---|---|
| `soft_landing` | "Soft landing" |
| `late_cycle_expansion` | "Late-cycle expansion" |
| `stagflation_risk` | "Stagflation territory" |
| `recovery` | "Recovery" |
| `slowdown` | "Slowing growth" |

## Derived signals (computed from the 7 indicators + 3-month deltas)

```
inflation       = core_cpi_yoy            # primary gauge; cpi_yoy secondary
growth          = real_gdp_yoy
labor_level     = unemployment
labor_trend     = sign(Δ3m unemployment)  # rising if Δ ≥ +0.3
yield_curve     = treasury_10y − fed_funds
sentiment       = umich_sentiment, sentiment_trend = sign(Δ umich)
```

## Thresholds (v1.0 — operationalizes the D3 sign-off; tunable via O3 quarterly recal)

| Band | Rule |
|---|---|
| Inflation hot | `core_cpi_yoy ≥ 3.0` |
| Inflation near-target | `core_cpi_yoy < 2.5` |
| Growth strong | `real_gdp_yoy ≥ 2.5` |
| Growth moderate | `1.0 ≤ real_gdp_yoy < 2.5` |
| Growth weak | `real_gdp_yoy < 1.0` |
| Labor tight | `unemployment ≤ 4.0` |
| Labor loosening | `Δ3m unemployment ≥ +0.3` or `unemployment > 5.0` |
| Curve inverted | `yield_curve < 0` |

## Classification (first match wins)

```
1. stagflation_risk     IF inflation hot (core_cpi_yoy ≥ 3.0) AND growth weak (real_gdp_yoy < 1.0)
2. slowdown             IF growth weak (real_gdp_yoy < 1.0) AND labor loosening AND core_cpi_yoy < 3.0
3. recovery             IF growth moderate (1.0 ≤ gdp < 2.5) AND unemployment falling (Δ3m ≤ −0.2)
                           AND core_cpi_yoy < 3.0
4. late_cycle_expansion IF growth strong (gdp ≥ 2.5) AND labor tight (unemployment ≤ 4.0)
                           AND (core_cpi_yoy ≥ 2.5 OR yield_curve < 0)
5. soft_landing         IF growth ≥ 1.0 AND core_cpi_yoy < 3.0 AND unemployment ≤ 5.0
6. else → soft_landing  (default; data already passed plausibility, so a label is always produced)
```

## Driver selection

`drivers` = the indicators named in the **matched rule's binding conditions**, ranked by how far each sits past its threshold, capped at **2**. Examples:

| Matched regime | drivers |
|---|---|
| stagflation_risk | `[core_cpi_yoy, real_gdp_yoy]` |
| slowdown | `[real_gdp_yoy, unemployment]` |
| recovery | `[unemployment, real_gdp_yoy]` (or `umich_sentiment` if it moved most) |
| late_cycle_expansion | `[real_gdp_yoy, treasury_10y]` (or `core_cpi_yoy`) |
| soft_landing | `[core_cpi_yoy, unemployment]` |

The prompt requires the headline's driver clause and (when present) `regime_change` to cite indicators from this `drivers` list.

## Regime-change detection

Not the classifier's job — the **pipeline** stores yesterday's label and sets `regime_today != regime_yesterday`. The prompt fires `regime_change` iff they differ (validator check O4).

## Worked examples (match the D10 fixtures)

| Fixture | Inputs (key) | → label |
|---|---|---|
| T1 | gdp 2.1, core 3.1?, unemp 4.2 | `soft_landing` (core 3.1 ≥ 3.0 but gdp ≥1, unemp ≤5 → rule 5; not stagflation since growth not weak) |
| T2 | core 3.4 hot, gdp 0.8 weak | `stagflation_risk` (rule 1) — flips from soft_landing |
| T5 | gdp 2.4, unemp falling, umich rising | `recovery` (rule 3) |
| A1 | unemployment 42 | **rejected pre-LLM** (outside 0–30) → fallback, classifier not reached |

## Open items (before live run)
1. **Back-test the thresholds against real FRED history** (last ~10y) before V1 — confirm they don't oscillate or mislabel obvious periods. This is the #1 validation task for D9.
2. Δ3m windows assume a 3-month lookback in the cache; confirm against the B1 cache design.
3. Thresholds are v1.0 — expected to move at the first quarterly recalibration (O3) with Bruce-flagged disagreements.
