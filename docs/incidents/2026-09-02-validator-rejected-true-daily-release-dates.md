# 2026-09-02 — the validator rejected a TRUE sentence about a daily release

**Reader impact: two sessions served the plain fallback card** (2026-08-25 PM, 2026-09-02 PM)
instead of a written briefing. No wrong number ever reached the reader. The 08-25 event also
tripped the old RED gate, which is what froze publishing for two days
(`2026-08-26-red-gate-latched-on-a-caught-check.md`) — so this defect is the upstream cause of
that outage too.

## The defect

A **daily** indicator's `period_label` is an ISO date (`"2026-09-02"`). Two gaps followed:

1. `_source_period_tokens` extracted no month **word** from an ISO label, so `"Sep"` was not a
   legal period token even though the data's period *is* September 2.
2. `_data_numbers` read the day-of-month (`2` in `"Sep 2"`) as a **data value**, which O5 then
   could not ground.

When a daily series released, O4 *requires* a non-null `whats_new`, so the writer had to
describe it — and the natural phrasing names the day. The result:

> `"Brent rose to 96.02 USD on Sep 2."` → `['O5', 'period']`

Both checks rejected a statement that was entirely true and grounded. Retry produced the same
shape, so the run published the deterministic fallback.

## Evidence

- Both production failures logged the identical pair `validator_failed: ['O5', 'period']`.
- The signature reproduces exactly offline from the reconstructed request.
- **All 3** release-run fallbacks in 167 runs involved a **daily** series; 37 of the 38
  release-runs included one. Base rate ≈ 8% per daily-release run — stochastic, because it
  depends on whether the writer happens to phrase the date in prose.
- The rejected drafts themselves are **not preserved** (`output` on a fallback row holds the
  *fallback* text), so this was diagnosed from the failure signature, not from the text. See
  "Open" below.

## Fix

- `_source_period_tokens`: an ISO date now also licenses its own month word.
- `_data_numbers(text, day_tokens)`: a 1–31 integer following a month word is skipped **only**
  when `"mon d"` matches a real period in the request (`_source_day_tokens`). A wrong day or
  month still fails, as does a fabricated value or a computed percentage.

Verified: the two production sentences now pass; `Sep 27`, `Oct 2`, `111.11`, and a computed
`8.8 percent` all still fail. Locked fixtures unchanged; suite 412 → 415.

## Lessons

1. **A validator that cannot express a true statement will reject one.** O5 and the period check
   each owned half the vocabulary for a date; neither could see that `"Sep 2"` was the period.
   When two checks fail *together and only together*, suspect a shared blind spot, not the model.
2. **A conditional that forces output creates exposure.** O4 mandates `whats_new` on a release —
   so the one path the writer was compelled down was the one the checks could not verify.
3. **Cadence changes what a field means.** `period_label` is prose for monthly rows and an ISO
   date for daily ones; logic reading it must handle both.

## Follow-up — closed same day

The **rejected draft was not stored**, which is why this took signature-matching to diagnose.
`produce_briefing` now keeps every draft the validator rejects (`FinalBriefing.rejected`, attempt
order), and `record_from` writes them to a new **last** RunRecord field, `rejected_outputs`, so
the audit row holds both what the reader saw (`output`) and what the model said and lost. The
field is in the schema's `properties` but **not** `required`, so every row written before it
stays valid; llm and refusal rows carry `[]`. The next occurrence is diagnosable from the log.
