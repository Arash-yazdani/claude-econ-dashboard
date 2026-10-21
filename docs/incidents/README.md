# Incident Log (PRD §339)

The audit trail for things going wrong. PRD §339 requires an incident log in
`/docs/incidents/` alongside the per-briefing audit (`logs/runs.jsonl`) — indefinite
retention, so a future reviewer can reconstruct *what happened, why, and what we changed*.

**File one whenever** a monitoring signal fires or a briefing misbehaves, in particular:

- the dashboard goes 🔴 **RED** (a critical check `O5`/`O8` < 100%, or fallback rate > 50% —
  see [`../monitoring-runbook.md`](../monitoring-runbook.md)) and the cron is paused;
- a **Sentry** exception (pipeline or PWA), a **healthchecks.io** missed-ping, or an
  **UptimeRobot** down alert;
- a **Bruce-reported** issue (PRD §395 — every Bruce item gets a shipped fix *or* an explicit
  "won't fix and why" within 14 days);
- any fabricated/ungrounded number, banned-phrase leak, or fresh-date fabrication that reached
  (or nearly reached) the rendered card.

**Naming:** `YYYY-MM-DD-short-slug.md` (e.g. `2026-07-03-o5-number-grounding.md`). One file
per incident. Link the resolving commit / prompt_history.csv row in the **Fix** section as
ADR-style evidence.

## Template — copy into a new file

```markdown
# Incident YYYY-MM-DD — <short title>

- **Date / time (ET):** 2026-__-__ __:__
- **Trigger:** <dashboard red | Sentry | healthchecks.io | UptimeRobot | Bruce SMS | manual>
- **Signal:** <the exact metric/log line — e.g. "O5 pass rate 0.83 (5/6) over 30d";
  paste the offending `logs/runs.jsonl` line(s) / `input_hash` / Sentry issue link>
- **Impact:** <was a bad briefing rendered? cron paused? user-visible? N=1 Bruce affected?>
- **Root cause:** <what actually broke — model regression, prompt edge case, data staleness,
  service outage, config>
- **Fix:** <what changed + evidence: commit hash, prompt_history.csv row, prompt version bump,
  config change. Note the 12-case regression re-greened C2=5/5 / C6=5/5 before re-enabling.>
- **Follow-up:** <preventive action, new regression case, monitoring gap to close; owner + due
  date. Mark "none" if truly closed.>
```

---

*Keep it honest and keep it current — the PRD's cardinal rule is that silent staleness is the
trust failure. An unlogged incident is the same failure in a different place.*
