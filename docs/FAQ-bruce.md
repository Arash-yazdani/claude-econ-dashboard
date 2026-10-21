# Your Daily Macro Briefing — FAQ

A quick guide to the dashboard. It's built for you — one card a day, plain English, no noise.
Questions or anything looks off? Text Arash.

---

**What is this?**
A personal dashboard that reads the day's key U.S. economic numbers (inflation, jobs, growth,
interest rates, sentiment) and writes you **one short briefing** — a headline and two numbers worth
repeating — on top of an at-a-glance list of the indicators. No login, no feed, no notifications.

**When does it update?**
**Twice a day, automatically: ~10:45 AM and ~5:30 PM Eastern.** The morning run catches the big
8:30 AM data releases plus the 10:00 AM sentiment print; the evening run catches Fed decision days
and end-of-day context. The footer always tells you which run you're looking at — "Updated 10:47 AM ET."

**Why didn't a number change since yesterday?**
Most of these are released monthly (inflation, jobs) or quarterly (GDP). On a quiet day the numbers
genuinely haven't moved — that's normal and worth knowing. Interest rates update daily.

**Why does it say "data through May 2026" when it's June?**
That's the period the freshest official number covers, not a bug. Government data is released with a
lag (e.g. May's inflation comes out in mid-June). The briefing always tells you the real period.

**The footer says "Data may be stale." What does that mean?**
It just means the dashboard hasn't refreshed in a while (more than ~18 hours) — usually a brief hiccup
on the data side. **It never shows you a made-up number to fill the gap** — it shows you the last
good one and tells you it's old. It'll catch up on the next run.

**The card looks plain today — just "Regime: … CPI: …% Unemployment: …%." Why?**
That's the **safe fallback**. If the AI's draft didn't pass the quality checks (or a data source was
briefly down), the dashboard shows you the bare facts instead of a polished sentence. You're never
shown something wrong — at worst, something plain.

**Is this investment advice?**
**No.** This is a personal information tool. It describes what the economy's numbers *are* — it never
tells you what to buy, sell, or do with money, and it's screened to avoid that kind of language. For
decisions about your money, talk to a licensed professional.

**How do I put it on my phone's home screen?**
Open the link in Safari (iPhone) or Chrome (Android) → tap **Share** → **Add to Home Screen.** It then
opens like an app, full-screen, and works even with a flaky connection (it remembers the last briefing).

**Is it private?**
Yes — it's just for you. It reads only public government data; it stores none of your personal
information. The keys that run it are encrypted, and there's no payment method attached to anything.

**Something looks wrong / I'd phrase it differently / the "regime" call seems off.**
Tell Arash — that feedback is the whole point during these first weeks. Mention the date and what
struck you. Your read is the benchmark the system is tuned against.

**Who keeps it running?**
Arash. It runs itself on a schedule, but he watches the monitoring and handles anything that breaks.

**Where do the numbers come from?**
Each figure is read from the agency that publishes it, not from a service that republishes it
later. That is why the CPI here matches the number in the news. The Federal Reserve's FRED
database keeps the long history behind every chart, and if a publisher is ever unreachable the
app quietly falls back to FRED, so a bad day at one agency can never make the dashboard worse.

| Source | What you see from it |
|---|---|
| U.S. Bureau of Labor Statistics | CPI and Core CPI (the same headline figures BLS publishes), Unemployment, Payrolls, Labor participation, Manufacturing and Construction employment |
| U.S. Treasury (daily yield curves) | 10-Year Treasury, 2-Year Treasury, 10Y TIPS yield |
| U.S. Treasury, Fiscal Data | Federal Debt (the daily "Debt to the Penny" figure, updated every business day) |
| Federal Reserve Bank of New York | Fed Funds rate (the effective rate the New York Fed itself administers) |
| U.S. Bureau of Economic Analysis | Real GDP |
| U.S. Census Bureau | Housing starts |
| U.S. Energy Information Administration | Brent spot oil (the physical cargo price; news sites usually quote futures, which can differ) |
| Freddie Mac (weekly mortgage survey) | 30-Yr mortgage rate, fresh on its Thursday release |
| Cboe | VIX volatility, same-day close |
| gold-api.com | Gold spot price |
| Federal Reserve (FRED) | U-Mich sentiment, Jobless claims, Capacity utilization, US Dollar index, US market cap, S&P 500, Nasdaq, HY credit spread, plus the chart history behind every tile |
| Calculated in the app | 10Y minus 2Y spread, Real fed funds, Real 10Y yield (built from the rates above) |

Tap any tile, then "View source": it opens that agency's own page for the exact series, so you
can check every number yourself. Every source is free public data, and no vendor holds a payment
method. (A one-page PDF of this table was shared 2026-08-21.)

**How do I know the numbers are right?** Every evening each published number is re-downloaded from its source and compared at the same observation date; mismatches page the developer that evening. Ask any time for the accuracy report — verdict first, exceptions listed, scope limitations included.
