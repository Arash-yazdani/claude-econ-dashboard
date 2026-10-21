# Incident 2026-08-04 — the measurement layer, not the product

- **Date / time (ET):** 2026-07-29 → 2026-08-04
- **Trigger:** healthchecks.io `Publish Quality` + `Data Sources` DOWN emails, then three
  consecutive ship-gate runs that read FAIL or INCONCLUSIVE
- **Impact:** one real user-visible data error (below); otherwise **no bad briefing ever reached
  Bruce**. Every other symptom was the monitoring and eval layer misreporting a healthy pipeline.
  Cost was trust and attention, which for an alert channel is the expensive kind.

## Signal

Nine defects surfaced in six days. Eight were in the code that *watches* the product; one was in
the product. Grouped by the belief that produced them:

### A. An absence was reported as a failure

1. **healthcheck slots armed instead of closing.** Cron mode re-arms on every ping: the next
   deadline is the first slot *strictly after* the ping received. Slots sat at `15 14,21` while
   the AM session publishes ~13:46 UTC — 29 minutes early — so each punctual run armed a 14:15
   slot the already-published session would never ping again. **The alert fired because the
   pipeline was on time.** Fixed by moving slots ahead of the earliest possible ping
   (`25 13,21`, grace 5h). Grace absorbs lateness and can never fix earliness.
2. **Treasury `degraded=true, failures=0`.** The feed is filed per calendar month; Sat 2026-08-01
   opened August with no business day, so the current-month file was an empty 200 and
   `degraded = not self._tip` called a calendar fact a degradation.

### B. "Could not measure" was reported as "failed"

3. **The ship gate.** Any errored case set `validator_pass=False`, so a free-tier 429 was
   reported as a quality regression. On 2026-07-29 four cases hit the ceiling, the eight that ran
   scored 4.88–5.0 with zero below-3, and the gate emailed *"FAILED on prompt v1.4.2"*. Under SO9
   (no card, ever) throttling is permanent, so this was guaranteed to recur.
   Fixed with a three-way verdict: PASS / FAIL / **INCONCLUSIVE**.

### C. A model's self-report was trusted as arithmetic

4. **`flag_below_3`.** The judge's own rubric says *"any non-null score < 3"*. On 2026-08-04 case
   T5 scored `c1=3, c3=5, c4=5, c5=5` and returned `flag_below_3: True`. Nothing recomputed it,
   so a flat 3 counted as a violation and **failed the strict gate on a clean run**.
5. **`c4_conditionals` vs validator O4.** O4 already enforces *exactly* what C4 scores —
   `whats_new` non-null iff a release dropped, `regime_change` non-null iff the regime changed.
   Two independent readings of one property, never compared. On 2026-08-03 case T4 passed O4 and
   the judge returned `c4=1` with the self-contradicting rationale *"whats_new missing despite no
   releases"*. That lone score was the run's only below-3.

### D. Evidence that existed but nothing read

6. **Fixture `assertions` blocks** — `must_include_released`, `must_not_cite`,
   `must_emit_two_numbers`, `whats_new_must_cite` — shipped with the D10 case set in June and
   were never read by any code.
7. **`eval_log.csv` stored verdicts but not the briefings they judged**, so the T4 investigation
   could not be settled from the log. It had to be reconstructed from `validator_status`.

### E. Arithmetic that silently favoured a pass

8. **Below-3 rate divided by `n`, not by measured.** Four errors plus one genuine below-3 scored
   `(12-1)/12 = 0.917` **PASS** when the honest rate over what actually ran was `(8-1)/8 = 0.875`
   **FAIL**. Unmeasured cases diluted a real violation into a pass.
9. **`eval_log.csv` header migration.** Appending three columns to `EVAL_LOG_FIELDS` would have
   written 16 values under the existing 13-column header. `DictWriter` never reads the file it
   appends to, so nothing raises — the new columns simply become unreadable by name, which would
   have defeated persisting `output` on the very run built to use it.

## The one real product defect

Losing the Treasury tip at a month boundary silently fell back to FRED, which is a day late by
design. On 2026-08-01 the dashboard served the 10-Year at **4.68 (Jul 30)** while Treasury had
published **4.75 for Jul 31** — a full day stale and 7bp off, on the row the briefing quotes most.
The class exists precisely to remove that staleness, and it inverted at every month boundary.

Found only because the false alert was investigated instead of dismissed. **The noisy alert was
sitting on top of a real one.**

## Root cause

Two, and they compound.

**The monitoring layer was never held to the pipeline's test standard.** The Treasury empty-month
path had *zero* coverage — every existing test passed an explicit month that always had rows. The
pipeline carries 300+ tests; its watchers carried almost none, and a watcher is exactly the code
whose failures are invisible by construction.

**Rubrics were enforced by asking the judged model to enforce them.** `flag_below_3` and
`clamped_by_b4` were both defined in prose and read back verbatim. A rubric whose arithmetic
depends on the judged model choosing to do it correctly is not a rubric.

## Fix

| # | Fix | Commit |
|---|---|---|
| 1 | Healthcheck slots `25 13,21`, grace 5h | `c453c48` |
| 2 | Treasury falls back a month; `degraded` regains meaning | `9081d97` |
| 3 | Three-way verdict PASS / FAIL / INCONCLUSIVE | `0cbae8c` |
| 8 | Below-3 denominator = measured | `0cbae8c` |
| 5,6 | `judge_conflicts_validator`; assertions wired; two-tier gate | `eb92f44` |
| 7,9 | `output` persisted; header migration | `5c36a06` |
| 4 | `below_3()` computes from scores | `5a1406d` |

Test count 304 → 331 over the week; every fix landed with a regression test naming the incident.

## Lessons

1. **Never trust a model's report of its own rubric.** Compute it. Both judges now do
   (`judge.below_3`, `persona_judge._apply_clamp`).
2. **"Cannot measure" is not "failed."** Conflating them produces confident false accusations, and
   an alert channel that cries wolf is one its reader learns to skip.
3. **An absence is not a fault.** Ask whether the missing thing *should* exist before flagging it.
4. **Where two independent measurements of one property exist, compare them.** Otherwise you have
   redundancy without verification — and the weaker instrument silently wins.
5. **Ground truth nothing reads is not ground truth.**
6. **Store the evidence, not just the verdict.** A score you cannot re-examine is a rumour.
7. **Alert-path code needs the pipeline's test rigor.** Its failures are invisible by design;
   that is the argument *for* coverage, not against it.
8. **Investigate the noisy alert.** A false positive is a claim about the system, and this one was
   sitting on a real 7bp error.
9. **Prefer the denominator that can only shrink the pass rate.** Where an arithmetic choice
   favours passing, assume it is wrong.

## Follow-up

- **v1.4.2 is still uncertified** — in production since 2026-07-20 with SO8 open. Three attempts,
  none a quality failure. Re-run `run_judge.py --strict` early in the UTC day (or the
  `weekly-regression` workflow with `strict: true`). Owner: Arash. Due: next fresh-quota morning.
- **Briefing sameness is unmeasured and untracked.** 94 of the first 104 published briefings
  carried the *identical* headline, and 87 the same quotable pair — explicable (the regime never
  left `soft_landing`), but no CTQ criterion asks *"would Bruce notice he has read this before?"*
  Filed separately; it is a product question, not a defect.
- **Judge C4 reliability.** T4's `c4=1` did not reproduce on 2026-08-04, so no prompt change yet.
  `judge_conflict` in `eval_log.csv` now tracks recurrence. Revisit if it fires twice more.
