# ADR-002 — Pre-Launch Compliance Sign-offs (SO6–SO9)

**Status:** ✅ **Accepted (2026-06-18)** — all four launch-blocking compliance sign-offs captured.
**Jira:** [SCRUM-39](https://modeo-team-1.atlassian.net/browse/SCRUM-39) · [SCRUM-40](https://modeo-team-1.atlassian.net/browse/SCRUM-40) · [SCRUM-41](https://modeo-team-1.atlassian.net/browse/SCRUM-41) · [SCRUM-42](https://modeo-team-1.atlassian.net/browse/SCRUM-42) · **Gate:** [G2 / SCRUM-43](https://modeo-team-1.atlassian.net/browse/SCRUM-43)
**Owners:** Bruce (SO6–SO8) · Arash (SO9) · **Recorded by:** Arash, 2026-06-18 (ADR-style capture per the PRD audit rule).
**Blocks:** Cohort 1 — *do not start without SO6–SO9*.

---

## Context

G2 (Launch-Ready) requires **four compliance sign-offs** (SO6–SO9) on top of the five design
sign-offs (D1–D5) captured at G0. They re-confirm, as formal launch gates, the trust-critical
decisions before any real cohort data touches the system. Per the PRD's audit rule, each sign-off
is captured as an ADR-style record; this ADR is that record. All four were signed off on
**2026-06-18** (Bruce for SO6–SO8, Arash for SO9).

## Sign-offs

### SO6 — Personal-tool consent (re-confirm) · Bruce · ✅
Bruce re-confirms informed consent to use this as a **private, single-user** tool, including the
use of his first name inside the LLM prompt. Re-confirmation of D1 ([SCRUM-13](https://modeo-team-1.atlassian.net/browse/SCRUM-13)).

### SO7 — Banned-phrase list (O8) + no-securities scope · Bruce · ✅
Bruce confirms the **O8 banned-phrase set** (the 10 D2-locked phrases) and the **no-securities /
no-investment-advice scope** — the core Investment Advisers Act defense. Re-confirmation of D2
([SCRUM-14](https://modeo-team-1.atlassian.net/browse/SCRUM-14)). Enforced deterministically in
`validator.py` (O8 word-boundary scan + the refusal envelope), a launch non-negotiable.

### SO8 — Thresholds + indicator list + tone · Bruce · ✅
Bruce confirms, as a bundle: the locked **regime-classifier thresholds** (D3 → classifier v1.1),
the **7-indicator list** (D4: CPI, Core CPI, Unemployment, Real GDP, 10-Year Treasury, Fed Funds,
U-Mich Sentiment), and the **production tone/prompt** (D5 → v1.3, security-patched to **v1.3.1** and
re-acked 2026-06-18). The v1.3.1 prompt measure-passed the V1 ship gate ([SCRUM-35](https://modeo-team-1.atlassian.net/browse/SCRUM-35)).

### SO9 — Private repo + encrypted secrets + no payment method · Arash · ✅
Arash confirms the infra / security posture — each item independently verifiable:
- **Private repo:** `Arash-yazdani/Economy-Dashboard` is **PRIVATE** (IAA-gated, per ADR-001).
- **Encrypted secrets at rest:** `FRED_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY` live **only** in
  GitHub Actions Secrets (libsodium sealed-box, AES-256 at rest). No secret is in the repo or its
  git history — verified: `.env` was never tracked / never committed, and no key-shaped strings
  exist in tracked files. The PWA holds no secrets (reads public JSON only).
- **No payment method on file** with **any** vendor (FRED, Google/Gemini, Groq, GitHub, Cloudflare)
  — every leg is free-tier. Quota exhaustion can only return HTTP 429; it can never bill.

This is exactly the posture ADR-001 stated its GitHub Actions + Cloudflare Pages architecture
"satisfies." It was put fully in place during the 2026-06-18 operator setup (repo pushed, secrets
set, Cloudflare Pages connected, live cron verified).

## Consequences

- **G2 sign-off criterion met (4/4).** G2 ([SCRUM-43](https://modeo-team-1.atlassian.net/browse/SCRUM-43))
  also requires the **V1 live regression green** (✅ passed 2026-06-18), the **V3 23-item readiness
  checklist**, and the **V4 runbooks** — those two remain before the gate formally closes.
- **Cohort 1 is unblocked** with respect to the sign-offs.
- Any change to a signed-off item (banned-phrase list, thresholds, indicator set, prompt, or the
  secrets/repo posture) re-opens the corresponding sign-off before the next cohort.
