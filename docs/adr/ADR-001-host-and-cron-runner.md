# ADR-001 — PWA Host & Cron Runner

**Status:** ✅ **Accepted (2026-06-11), rev v1.2 (2026-08-12)** — GitHub Actions + Cloudflare Pages, **cron 10:45 ET + 5:30 ET (twice daily)** · **Jira:** [SCRUM-18 / D6](https://modeo-team-1.atlassian.net/browse/SCRUM-18)
**Resolves:** PRD Open Question #3 (host/cron undecided) · **Unblocks:** B6 (cron + secrets), Cohort 0
**Constraints:** $0/yr · **no payment method on file with any vendor** · private repo for V1 (IAA-gated) · daily batch ~8 ET · PWA first paint ≤500ms · ≤30 min/wk maintenance

---

## Context

The system is a **daily batch job that writes static JSON + a static PWA that reads it**:

```
cron (daily ~8 ET) → FRED pull (5–7 indicators) → deltas → deterministic classifier
  → input validate → Gemini (+Groq failover) → 17-check output validate → fallback
  → write briefing.json + indicators.json (≤5MB) → PWA renders the card
```

So the decision is really two coupled choices: **where the cron runs** and **where the static PWA is served**. The pipeline is **Python** (FRED, validation, LLM SDKs), which favors a runner that executes Python directly. The PWA holds **no secrets** (it reads public data); only the cron needs the FRED/Gemini/Groq keys.

## Cron runner options

| Runner | $0 / no card | Python-native | Secrets at rest | Reliability / precision | New sub-processor? |
|---|---|---|---|---|---|
| **GitHub Actions** ⭐ | ✓ 2,000 min/mo (private); job is <2 min/day | ✓ runs Python directly | ✓ Actions Secrets (sealed-box, AES-256) | Best-effort timing (can drift a few min; auto-disables after 60d repo inactivity — daily commits keep it alive) | None (already using GitHub) |
| Cloudflare Workers Cron | ✓ | ✗ would rewrite pipeline in TS/JS | ✓ Worker secrets | Precise | None (if Pages is also CF) |
| launchd (local Mac) | ✓ *only if* an always-on Mac exists | ✓ | Depends on FileVault — **reintroduces** the PRD's "local-runner at-rest encryption" open item | Precise, but needs the machine awake at 8 ET (laptop sleep kills it) | None |
| VPS cron | ✗ costs money → fails the no-card gate | ✓ | ✓ | Precise | Adds a VPS provider |

## PWA host options

| Host | $0 / no card | Private repo | TLS | First paint | Ops | Notes |
|---|---|---|---|---|---|---|
| **Cloudflare Pages** ⭐ | ✓ no card | ✓ connects to private GitHub repo | ✓ free | CDN edge (excellent) | Low | No bandwidth cap; auto-deploys on each commit (fresh JSON ships when the cron commits); free **Web Analytics** (privacy-first, no cookies) can measure open-rate at $0 → bonus resolution of OQ5 |
| Vercel Hobby | ✓ no card | ✓ | ✓ | CDN edge | Low | Hobby ToS is non-commercial-only (fits this tool); 100 GB/mo |
| Netlify Free | ✓ no card | ✓ | ✓ | CDN edge | Low | 100 GB/mo; 300 build-min/mo (we deploy rarely) |
| GitHub Pages | ✗ private-repo Pages needs a **paid** plan | — | ✓ | CDN | Low | Would force a **public** repo → conflicts with the IAA-gated private-repo requirement. **Rejected.** |
| Self-host (existing box) | ✓ marginal | n/a | Needs Cloudflare Tunnel / Tailscale | Depends | **High** | Reintroduces the local at-rest encryption open item; uptime burden for a ≤30 min/wk tool. **Not recommended.** |

## Decision (recommended)

**GitHub Actions (cron + pipeline) → commits `briefing.json` + `indicators.json` to the private repo → Cloudflare Pages auto-deploys the static PWA.**

Why this pair:
1. **$0 and no card**, on both legs, with comfortable free-tier headroom.
2. **Python-native** cron — the pipeline runs as-is in a GitHub Actions YAML; no rewrite.
3. **Encryption handled out of the box** — Actions Secrets are encrypted at rest (satisfies compliance sign-off SO9); GitHub encrypts the repo at rest; Cloudflare serves over TLS 1.3; the PWA stores no secrets.
4. **Closes an open item for free** — using a cloud runner (not launchd) **eliminates** the PRD's "local-runner at-rest encryption — needs-decision" question entirely.
5. **Lowest ops** — one workflow file + one repo→Pages connection; manual re-runs via `workflow_dispatch`.
6. **Best first paint** — static assets on Cloudflare's CDN edge; the daily commit bakes fresh data into the deploy.

**Runner-up:** GitHub Actions + Netlify/Vercel (pick whichever Arash already uses). **All-Cloudflare** (Workers cron + Pages) only if single-vendor is preferred and rewriting the pipeline in TS is acceptable.

## Consequences

- **Sub-processor list (q50) — final for V1:** Google (Gemini) · Groq (failover) · FRED (public data) · GitHub (private repo + Actions Secrets + cron runner) · **Cloudflare (Pages host + CDN/TLS)** ← the one net-new processor. All free tier, no card, public-data-only (no PII), strong privacy posture.
- **Encryption posture (feeds SO9):** secrets at rest in GitHub Actions Secrets; repo at rest via GitHub; TLS 1.2+/1.3 in transit everywhere; no local runner.
- **Telemetry (OQ5):** Cloudflare Web Analytics (free, cookieless) can capture daily open-rate without standing up an endpoint or adding Bruce friction — recommend enabling it.
- **Unblocks:** B6, and removes OQ3 from the risk register.

## Cron timing — original analysis (⚠️ SUPERSEDED by the v1.1 decision above; kept for history)

**Cron trigger time vs the 8:30 ET release window.** Major releases (CPI, jobs) publish at **8:30 ET**. A ~8:06 ET trigger means an 8:30 release lands in *tomorrow's* briefing (the accepted Mode 3 gap, OQ8). Two choices:
- **Keep ~8:06 ET** (PRD default): briefing is ready before the workday; release-day freshness lags one day (V1-accepted).
- **Move to ~9:00 ET:** captures same-day 8:30 releases, at the cost of a slightly later briefing.

Note GitHub Actions `schedule` is best-effort and can drift several minutes; schedule a couple minutes early and rely on the healthchecks.io ping (by 8:30 ET) as the SLA.

## Recommendation in one line

> **DECIDED v1.1 (2026-06-11): GitHub Actions + Cloudflare Pages; TWO runs/day — 10:15 ET and 5:30 ET, both always publish; enable Cloudflare Web Analytics.** ✅ **Implemented in B6** ([SCRUM-30](https://modeo-team-1.atlassian.net/browse/SCRUM-30)): the DST-safe four-cron workflow + `run_daily.py` + the red-gate publish-pause — see [`docs/cron-b6.md`](../cron-b6.md).

> **AMENDED v1.2 (2026-08-12): the AM run is 10:45 ET, not 10:15 and not 09:45.**
> The v1.1 time was chosen to clear the 8:30 ET release window. Commit `969568f` (2026-06-29)
> moved it to 09:45 ET ("15m after the market open") without revisiting that reason, which
> silently reintroduced the problem v1.1 had solved: on 2026-08-12 the card served June core CPI
> all morning because FRED did not ingest the 08:30 July print until 10:10:19 ET, 25 minutes
> after the 09:45 fetch. The binding constraint is FRED's ingest lag (~100 min), not the market
> open. Enforced by the 10:30 ET floor in `resolve_run_label`, so a pre-ingest firing self-skips
> in both DST halves rather than claiming the session. See [`docs/cron-b6.md`](../cron-b6.md).

### Cron schedule rationale (rev v1.1)
- **10:15 ET (morning)** clears *both* release windows that touch this indicator set: the **8:30 ET** prints (CPI, Core CPI, Unemployment, GDP) and the **10:00 ET** U-Mich Sentiment release — so all 7 indicators are fresh on release days. (The earlier ~9:00 ET single-run would miss the 10:00 U-Mich update.)
- **5:30 ET (evening)** captures **FOMC decision days (2:00 ET, 8×/yr)** and settled end-of-day context.
- Together these supersede the PRD's single ~8:06 ET run and **largely close the Mode 3 intra-day gap (OQ8)** — every scheduled macro release window (8:30 / 10:00 / 2:00 ET) is now captured same-day.
- **Cost unchanged: $0.** Two runs ≈ 0.13% of the Gemini free RPD; GitHub Actions ≈ 6% of free minutes; Cloudflare ≈ 60 builds/mo of 500. You could run 5–6×/day and still be $0.

### Accepted trade-off
The evening run **always republishes**, so on a quiet day Bruce may see a different AM vs PM headline. **Build-time mitigation (required):** the freshness footer (D7) must show *which run* produced the current briefing (e.g., "Updated 5:32 PM ET"), so an evening change reads as an *update*, not a contradiction of the morning read. (Note: a "conditional/idempotent evening run" was offered and declined in favor of always-publish.)
