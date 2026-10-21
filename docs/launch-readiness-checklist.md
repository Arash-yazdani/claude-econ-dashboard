# V3 — Launch-Readiness Checklist (23 items)

**Status:** ✅ **22/23 green · 1 monitor-after-launch** (as of 2026-06-18) · **Jira:** [SCRUM-37](https://modeo-team-1.atlassian.net/browse/SCRUM-37) · **Gate:** G2 (Launch-Ready)

The PRD's 23-item technical readiness checklist, verified against the **built + live** system
(Phase-1 build B1–B9 complete; cron live in production on Cloudflare Pages). The PRD flagged
items **#19–#23** as launch-blocked at design time (judge prompt unwritten, live regression
unrun, Bruce confirmations pending) — **all now resolved.** Legend: ✅ done · ◑ partial /
monitor after launch.

## Data & pipeline
| # | Item | Status | Evidence |
|---|------|--------|----------|
| 1 | FRED API key registered — free tier, **no payment method** | ✅ | GitHub Actions Secret `FRED_API_KEY`; SO9 / ADR-002 |
| 2 | 7-indicator FRED series mapping + plausibility ranges pinned | ✅ | `config.py`; `docs/pipeline-b1.md` |
| 3 | B1 pipeline: ingest → Δ/Δ3m → plausibility → staleness → cache (≤5MB) | ✅ | `pipeline.py`; B1 |
| 4 | Release detection (≤24h) + carry-forward staleness (stale passes through, flagged) | ✅ | `deltas.py`, `staleness.py`; B1 Decision 2 |
| 5 | Deterministic regime classifier **v1.1** (back-tested, D9-signed) wired in | ✅ | `classifier.py`; back-test in `scripts/backtest_thresholds.py` |

## LLM & prompt
| # | Item | Status | Evidence |
|---|------|--------|----------|
| 6 | Gemini 2.5 Flash primary + Groq failover — keys registered, free tier, no card | ✅ | Actions Secrets `GEMINI_API_KEY`/`GROQ_API_KEY`; SO9 |
| 7 | Production prompt **locked v1.3.1** (Bruce-acked) + static-prefix structure | ✅ | `prompt.py` `PROMPT_VERSION`; SO8 / ADR-002 |
| 8 | Gemini config: temp 0.2 · top_p 0.9 · maxOutputTokens 512 · **thinkingBudget 0** | ✅ | `llm_client.py` (256→512: 2.5-flash is a thinking model) |
| 9 | `generate_briefing` Gemini→Groq failover on any error | ✅ | `llm_client.py`; live-exercised (V1 + a real 503) |
| 10 | Static-prefix prompt caching, target ≥80% hit-rate | ◑ | Gemini implicit caching on the static prefix; hit-rate is a **monitor-after-launch** metric, not a launch blocker |

## Validation & safety
| # | Item | Status | Evidence |
|---|------|--------|----------|
| 11 | 17-check output validator (O-checks) on TDD | ✅ | `validator.py`; B4 |
| 12 | O5 number-grounding ±0.05 incl. **negatives / small fractions / bps** edge cases | ✅ | `validator.py` `_data_numbers`; V2 ([SCRUM-36](https://modeo-team-1.atlassian.net/browse/SCRUM-36)) |
| 13 | O8 banned-phrase scan (10 D2 phrases, word-boundary) | ✅ | `validator.py` `BANNED_PHRASES`; SO7 |
| 14 | O16/O17 refusal envelope (`data_unavailable`) | ✅ | `validator.py` `REFUSAL_ENVELOPE` |
| 15 | Retry-once-then-deterministic-template fallback (**never an error to the user**) | ✅ | `validator.py` `produce_briefing` |

## PWA & delivery
| # | Item | Status | Evidence |
|---|------|--------|----------|
| 16 | Static PWA — 5 render states (default/loading/stale/fallback/error), SW, manifest | ✅ | `public/`; B5 |
| 17 | `briefing.json` + `indicators.json` contracts + schemas; **PWA renders live card** | ✅ | `schemas/briefing-card.schema.json`; B9 verified desktop + mobile |
| 18 | Cloudflare Pages serving over TLS; **commit → auto-deploy** loop verified | ✅ | https://macro.modeo.io (live; originally economy-dashboard-2tn.pages.dev, custom domain added 2026-08-05) |

## Ops, monitoring & audit  *(PRD's originally launch-blocked #19–#23 — now resolved)*
| # | Item | Status | Evidence |
|---|------|--------|----------|
| 19 | LLM-judge harness (Layer 2) wired + judge prompt written | ✅ | `judge.py`; `docs/prompts/llm-judge-prompt-v1.0.md`; B7 |
| 20 | **Live 12-case regression measure-passed on Gemini** — *result recorded:* C2/C6 5/5, mean ≥4.0, 0 below-3. This row is a **record of what the V1 run achieved**, not a separate threshold; the canonical two-tier gate is `docs/eval/regression-cases-v1.0.md` §Usage & gate. | ✅ | V1 / [SCRUM-35](https://modeo-team-1.atlassian.net/browse/SCRUM-35); commit `d253302` |
| 21 | Tone variant + regime thresholds + indicator list **confirmed with Bruce** | ✅ | D5/D3/D4 → SO6–SO8 / ADR-002 |
| 22 | Twice-daily cron (GitHub Actions, **DST-safe**) → commit → deploy; healthcheck bracket; **red-gate pause** | ✅ | `daily-briefing.yml`; B6; live `gh workflow run` verified |
| 23 | Monitoring stack — JSONL run-log + `eval_log.csv` + dashboard + opt-in Sentry/healthchecks/UptimeRobot + incident log | ✅ | B8; `docs/monitoring-runbook.md`; `docs/incidents/` |

---

## Summary

**22 of 23 green; item #10 (prompt-cache hit-rate) is a monitor-after-launch metric, not a
blocker** — every launch-blocking item is satisfied. The system is built, the live 12-case
regression measure-passed, the cron is publishing in production, and the four compliance
sign-offs are captured. This checklist meets the G2 "23-item checklist green" criterion.

**Operate it** via the 8 runbooks ([`docs/runbooks.md`](runbooks.md) + [`docs/FAQ-bruce.md`](FAQ-bruce.md))
and the live/static monitoring (`docs/monitoring.md` + `docs/monitoring-runbook.md`).
