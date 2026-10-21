# Cloudflare Worker — reliable daily-briefing trigger

GitHub Actions `schedule:` triggers are best-effort and silently drop runs — on **2026-06-22**
GitHub dropped the entire AM session (zero runs fired), freezing the dashboard on the prior read.
This Worker fires on Cloudflare's **reliable** cron and dispatches the `daily-briefing` workflow via
the GitHub API. The repo's native `schedule:` crons stay as a fallback; `run_daily.py`'s
`already_published` de-dupe (the Worker dispatches with `force:false`) guarantees no double-publish.

## How it works
- Cron triggers (UTC, in `wrangler.toml`) fire the EDT+EST pair per session: AM `14:15`/`15:15`,
  PM `21:30`/`22:30` — same as the GitHub workflow.
- `worker.js` maps the cron hour to the run label (14/15 → `AM`, 21/22 → `PM`) and POSTs a
  `workflow_dispatch` with `inputs: { run_label, force: "false" }`.
- `force:false` → `run_daily.py` runs **without** `--force`, so it respects the de-dupe. Whichever
  trigger (this Worker or the GH schedule) reaches a session first publishes; the other self-skips.
- Manual recovery (`gh workflow run … -f run_label=PM`) still defaults `force:true` → bypasses
  de-dupe to force a re-publish.

> Prereq: the `force` input + `repository`/`workflow_dispatch` handling must already be on `main`
> (committed alongside this Worker in `.github/workflows/daily-briefing.yml`).

## One-time setup (you — I can't create tokens or deploy)
1. **Create a fine-grained GitHub PAT** (github.com → Settings → Developer settings →
   Fine-grained tokens → Generate new token):
   - **Repository access:** Only select repositories → `Arash-yazdani/Economy-Dashboard`
   - **Permissions:** Repository permissions → **Actions: Read and write** (leave everything else)
   - Generate and copy the token (`github_pat_…`).
2. **Deploy the Worker** — run these **from the `cron-worker/` directory** (where `wrangler.toml`
   lives; running elsewhere errors with "Required Worker name missing"):
   ```bash
   cd cron-worker            # the dir with wrangler.toml — required
   npm i -g wrangler          # if not installed
   wrangler login             # authorize your Cloudflare account
   wrangler deploy            # creates the Worker first
   wrangler secret put GITHUB_TOKEN   # then paste the PAT — applied live, no redeploy needed
   ```
3. **Verify** end-to-end:
   - `wrangler tail` and wait for the next cron fire (or temporarily add a near-future cron to test),
     OR just confirm at the next scheduled session that the run launches on time.
   - A successful fire shows `dispatched AM (…) -> 204` in the tail and a `workflow_dispatch` run in
     the repo's Actions tab launching within seconds of the scheduled time (vs GitHub's ~2h lag).

## Optional: retire the GitHub schedule
Once the Worker is proven reliable for a few days, you can delete the `schedule:` block from
`.github/workflows/daily-briefing.yml` to make the Worker the sole trigger (the de-dupe already
prevents doubles, so this is optional cleanup, not required).
