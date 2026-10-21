# Bruce sign-off round, 2026-08-13

> **CORRECTION (2026-08-19): item 1 did NOT ship.** This document was committed by the very
> revert that pulled v1.6 back off main (`1d34247`): the strict gate FAILed (run 31758176028,
> A3 c3=1 below-3), so v1.6 was reverted the same evening and production stayed on v1.5.
> There is deliberately **no v1.6 row in `prompt_history.csv`** — a row is written only when a
> version certifies. Bruce's YES on the wording *intent* stands and re-applies to any future
> attempt; the "Shipped as prompt v1.6" claims below are left as written but are **false**.
> v1.6 is preserved as commit `90961b8` (in main's history, undone by revert `1d34247` — recover
> with `git show 90961b8` or `git revert 1d34247`); its gate evidence is in `eval_log.csv`.

**Outcome: YES on all four items.** Recorded per Runbook 5. Collected verbally / by reply;
transcribed here the same day.

| # | Item | Kind | Where it is recorded |
|---|---|---|---|
| 1 | The wording revision (labor market "resilient but cooling") | Prompt ack, re-opens SO8 | ~~`prompt_history.csv` row v1.6~~ **reverted — see correction above** |
| 2 | Project Charter revisions 1-3 | Governance | This file; see the erratum below |
| 3 | The July CPI figures on the card | Data confirmation | This file |
| 4 | The install guide and hand-off documents | Delivery acceptance | This file |

---

## 1 · The wording revision

His review of the 2026-08-12 card, verbatim in substance:

> Mostly correct, but I would revise it slightly. The data points are accurate, but the
> characterization of the labor market as simply "strong" is a little too bullish given the
> latest payroll numbers.

His proposed line:

> The soft landing continues, supported by easing inflation and a resilient, though cooling,
> labor market.

**He was right, and the writer could not have known.** `payrolls` was tier `drawer`, so the model
never saw it. Unemployment had FALLEN (4.2 to 4.1), so on the only labor data in front of it,
"strong" was the honest read. July payrolls were **-23K**, a 43K swing.

**Shipped as prompt v1.6**, style guidance rather than his literal sentence (his choice). Payrolls
now reaches the writer as **qualitative-only** context: it may judge direction, never state the
figure. That constraint is not stylistic. Payrolls is a net monthly change in thousands, stored as
`-23.0`, so "fell by 23,000" is ungrounded to the O5 check and would cost the whole briefing.

First live output under v1.6, on his own data:

> Unemployment at 4.1 percent for Jul 2026 shows a steady job market, despite slowing hiring.

**One deviation from his sample, disclosed:** the qualifier landed in the quotable number, not the
headline where he put it. Under style guidance the model chooses placement. If he wants it up
front, that is a further prompt edit and another SO8 event, not a bug.

**Three of his earlier requests remain declined** (unchanged from the 2026-08-06 round): the
AI-investment attribution, the global-conditions clause, and consumer spending. Reasons were
disclosed to him in writing before he approved the style.

## 2 · Charter revisions 1-3

Approved. **One erratum is owed, and it is our error rather than a change of plan.**

All three revisions state the app updates at **9:45 AM ET**. Since 2026-08-12 it updates at
**10:45 AM ET**. The move was necessary: BLS publishes CPI and the jobs report at 08:30 ET, but
FRED, our only source for them, did not post the July CPI until **10:10:19 ET**. A 09:45 fetch
returned June, so on release mornings the card showed last month's inflation. ADR-001 v1.2.

The charter is a signed document, so the correction goes to him as a short note rather than a
silent edit. Draft: `docs/feedback/2026-08-13-charter-time-erratum.md`.

**Also still open from the earlier audit, and NOT resolved by this round:** all three revisions
carry a scored competitive claim (9.3 of 10 against alternatives, best competitor 7.4, across 29
measures) for which no workpaper exists anywhere in the repo. It should either be evidenced or
withdrawn at the next revision. It was not raised with him in this round.

## 3 · The July CPI figures

Confirmed correct against source. Headline CPI 3.30% and core 2.47% for Jul 2026, both released
2026-08-12 and reconciled against FRED. His own timing note was right and matched our behaviour:

> As of August 11, 2026, July CPI has not yet been released; BLS says it is scheduled for
> August 12. So using June CPI alongside July unemployment is currently the appropriate
> comparison.

One small correction, not material to his conclusion: he put May core CPI at 2.9%. Our series has
**2.82%**. The direction and the argument are unaffected.

## 4 · Install guide and hand-off documents

Accepted: the illustrated home-screen install guide, the business-level hand-off summary, and the
data-quality report.

---

## What this round did NOT settle

- **Cohen's kappa is still unmeasured.** He has now given three rounds of qualitative feedback and
  zero numeric ratings. `human_c1` / `human_c3` are empty on every row. The judge remains a
  co-witness, not a referee. If the paired ratings are never going to arrive, the honest move is to
  retire the kappa target rather than carry it as permanently pending.
- **Payroll revisions.** His point that May and June were revised down is correct and is NOT
  represented in the product. FRED's standard endpoint serves only the current vintage; detecting
  revisions needs archival data or cross-run diffing. Declined for now, and his proposed wording
  does not require it.
