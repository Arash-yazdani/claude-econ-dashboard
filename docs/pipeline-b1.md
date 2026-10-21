# B1 — Data Pipeline (FRED ingest → cache → deltas → plausibility → staleness)

**Status:** ✅ Implemented (Phase 1) · **Jira:** [SCRUM-25 / B1](https://modeo-team-1.atlassian.net/browse/SCRUM-25) · **Feeds:** B3 (LLM call) · **Code:** `src/macro_pipeline/`

The data layer. Produces the **data half** of a `BriefingRequest`
(`docs/schemas/briefing-io-v1.0.md`): `as_of_date`, `indicators[]`, `releases_last_24h[]`.
The regime half (`regime_today`/`regime_yesterday`/`drivers`) is filled by the **B2
classifier**, which consumes `PipelineResult.readings` (it needs `value` + `delta_3m`).

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # add your free FRED_API_KEY
python -m macro_pipeline --as-of 2026-06-19 --briefing-out public/briefing-request.json
pytest -q                     # 46 tests, no network
```

Exit codes: `0` ok · `2` plausibility fallback (implausible value → skip LLM) · `1` error.

## Decision 1 — indicator → FRED series mapping (pinned here; not pinned in Phase 0)

The indicator *ids* were locked at D8; the FRED series + transforms were not. Defined in
`config.py` (`units` uses FRED's built-in transform so the API returns the briefed value):

| id | FRED series | units | freq |
|---|---|---|---|
| `cpi_yoy` | CPIAUCSL | pc1 (YoY) | monthly |
| `core_cpi_yoy` | CPILFESL | pc1 (YoY) | monthly |
| `unemployment` | UNRATE | lin | monthly |
| `real_gdp_yoy` | GDPC1 | pc1 (YoY) | quarterly |
| `treasury_10y` | DGS10 | lin | daily |
| `fed_funds` | DFF | lin | daily |
| `umich_sentiment` | UMCSENT | lin | monthly |

`last_release_date` is taken from FRED's series `last_updated` (a v1 approximation; the
precise publication calendar would need the FRED *releases* API — revisit if release-day
freshness needs exact timestamps).

## Decision 2 — stale-data handling (resolves Phase-0 build carry-forward #2)

**Question:** does the pipeline pass stale data to the LLM, or short-circuit to the
cached prior briefing? **Answer (grounded in regression case E4):** a value that is
*plausible but old* is **passed through** to the LLM with its real, stale `period_label`
(the prompt narrates the actual period — no fresh-date fabrication) and is **flagged**
(`stale` per indicator + `any_stale` rollup) for the D7 freshness banner, shown
separately from the briefing text. Only **implausible** values (case A1) trigger the
refusal/fallback. Staleness is advisory metadata, never a refusal. See `staleness.py`.

## Module map

| File | Responsibility |
|---|---|
| `config.py` | 7-indicator registry: FRED series, transforms, plausibility ranges, staleness cadence |
| `models.py` | `IndicatorReading` (+ schema-clean `to_request_indicator`), `Release`, `PipelineResult` |
| `fred_client.py` | `FredSource` protocol + HTTP `FredClient` (≤14 calls/day) |
| `cache.py` | JSON cache (previous snapshot for release detection; 5MB cap enforced) |
| `deltas.py` | 1-period delta, Δ3m (classifier input), release detection |
| `plausibility.py` | pre-LLM range gate (null = allowed; out-of-range = reject→fallback) |
| `staleness.py` | stale-but-valid flagging (Decision 2) |
| `classifier.py` | **B2** deterministic regime classifier + driver selection (integrated into the pipeline) |
| `pipeline.py` | orchestration → `PipelineResult` (full `briefing_request()`) + `indicators.json` |
| `scripts/backtest_thresholds.py` | **B2** threshold back-test over ~10y FRED history (carry-forward #1; needs key) |
| `schemas/briefing-request.schema.json` | machine-readable copy of the locked contract (reused by B4) |

## Open / handoffs

- **B2 classifier is integrated** — `pipeline.run_pipeline` now emits a complete
  `BriefingRequest` (regime_today from the classifier, regime_yesterday from the cache,
  drivers). Next: **B3** (LLM call) consumes `PipelineResult.briefing_request()`.
- **Two live runs still pending `FRED_API_KEY`:** (1) the B1 end-to-end pipeline run, and
  (2) the B2 threshold back-test (carry-forward #1). All 55 tests run offline against an
  in-memory FRED source.
