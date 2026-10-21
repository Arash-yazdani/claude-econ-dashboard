# 2026-09-03: Treasury graft dead for 22 consecutive runs, unpaged

**Window:** 2026-08-24 PM through 2026-09-03 PM (22 runs, ten days).
**Reader impact:** the 10Y, 2Y and 10Y TIPS rows (and the derived 2s10s) served FRED's
day-late value instead of Treasury.gov's same-day tip. Numbers were correct, one business day
stale. No fallback card, no validator failure.
**Found by:** the 7-day healthcheck audit on 2026-09-03, reading `sources.treasury` in
`logs/runs.jsonl`. Nothing paged.

## What happened

`TreasuryYieldSource` fetches one month of the daily par yield XML from home.treasury.gov with a
15s timeout. Treasury's edge (Akamai) started answering the default `python-requests/x.y` User-Agent
in about 18-19 seconds and a browser User-Agent in about 0.3 seconds. Measured 2026-09-03, same
host, same file: default UA 18.4s, `Mozilla/5.0` 0.3s. Against a 15s timeout that is a
deterministic read timeout on every run, and the source degraded to FRED-only every time.

## Why nothing paged

By design (d5243e4, 2026-08-14) the `data-sources` check pages on the **outcome**, not the fetch
attempt: a degraded source that still delivered a number goes into the Slack body and pings
`sources` success. That rule was right for the gold-api DNS blips it was written for, and wrong
here, because nothing distinguished run 22 from run 1.

## Fix

1. `treasury_client.BROWSER_UA = "Mozilla/5.0"`, set only on a session the class builds itself
   (injected sessions, including test fakes, are untouched). Pushed c83b71f. Live-verified the same
   day: DGS10 4.77, DGS2 4.34, DFII10 2.42 dated 2026-09-03, both feeds `ok`.
2. `observability.persistently_degraded`: a source degraded on this run **and** the previous 3
   logged runs (`PERSISTENT_DEGRADED_RUNS = 4`) now fails the `sources` check and names itself in
   the alert. Fails closed to no-page on any log read error. Replayed against the real log on
   2026-09-03: returns `['treasury', 'treasury_real']` for the last pre-fix row.

## What was deliberately not done

- The browser UA is Treasury-only. Cboe, Freddie Mac, NY Fed, FiscalData, BLS and gold-api serve
  the default UA fast today. Apply the same header the day a second origin shows the pattern.
- No retry or longer timeout. A 20s timeout would have "worked" and hidden the tarpit.

## Lesson

"Delivered, but degraded" is a fine reason not to page once. It is not a fine reason not to page
twenty-two times. Persistence is the signal a transient cannot fake.
