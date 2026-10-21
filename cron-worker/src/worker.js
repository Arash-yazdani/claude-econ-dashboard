// Reliable daily-briefing trigger (see ../wrangler.toml + README.md).
//
// On each Cloudflare cron fire, POST a workflow_dispatch to the GitHub API to launch the
// daily-briefing workflow. We pass force:"false" so run_daily.py respects its already_published
// de-dupe — that's what makes it safe to keep GitHub's native `schedule:` crons as a fallback
// (whichever fires first publishes; the other self-skips). Manual recovery via `gh workflow run`
// passes force:true to bypass the de-dupe.

// Ping the Worker's own healthchecks.io check. Set HEALTHCHECKS_URL as a Wrangler secret:
//   wrangler secret put HEALTHCHECKS_URL
// Unset -> pure no-op (the Worker must never fail because monitoring is unconfigured).
async function ping(env, state = "success", detail = "") {
  const base = env.HEALTHCHECKS_URL;
  if (!base) return;
  const url = base + (state === "fail" ? "/fail" : "");
  try {
    await fetch(url, {
      method: detail ? "POST" : "GET",
      body: detail || undefined,
      headers: { "User-Agent": "claude-econ-briefing-cron" },
    });
  } catch (err) {
    // A missed ping must never break the dispatch it is reporting on.
    console.error(`healthcheck ping (${state}) failed: ${err}`);
  }
}

export default {
  async scheduled(event, env, ctx) {
    // event.cron is the UTC cron string that fired, e.g. "45 13 * * *". Hour 13/14 = AM, 21/22 = PM.
    // (run_daily re-derives AM/PM from the ET window for non-forced runs, so this label is a hint.)
    const hour = parseInt((event.cron || "").split(" ")[1], 10);
    const runLabel = Number.isFinite(hour) && hour < 18 ? "AM" : "PM";

    const url = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}`
      + `/actions/workflows/${env.WORKFLOW_FILE}/dispatches`;

    const dispatch = fetch(url, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "claude-econ-briefing-cron",
      },
      // workflow_dispatch inputs are strings; force:"false" -> the workflow omits --force.
      body: JSON.stringify({ ref: "main", inputs: { run_label: runLabel, force: "false" } }),
    }).then(async (res) => {
      if (!res.ok) {
        const txt = await res.text().catch(() => "");
        // Surface to `wrangler tail` / Worker logs so a broken token/permission is visible.
        console.error(`dispatch ${runLabel} (${event.cron}) failed: ${res.status} ${txt}`);
        // ...and to a human. console.error alone is only visible to someone actively tailing,
        // so an expired PAT killed the PRIMARY scheduler silently — the exact mechanism that
        // dropped the 2026-06-22 AM session. /fail pings the dead-man check immediately.
        await ping(env, "fail", `${res.status} ${txt}`.slice(0, 200));
        throw new Error(`GitHub dispatch failed: ${res.status}`);
      }
      console.log(`dispatched ${runLabel} (${event.cron}) -> ${res.status}`);
      // PAT EXPIRY. A fine-grained PAT expires on a date nobody wrote down; the last one killed
      // the PRIMARY scheduler and the /fail ping above only fires AFTER it is dead. GitHub returns
      // the expiry on every authenticated response, so warn (via /fail, which pages) for the last
      // 14 days while the dispatch still works — a page with a working scheduler beats one without.
      const exp = Date.parse(res.headers.get("github-authentication-token-expiration") || "");
      const daysLeft = Number.isFinite(exp) ? (exp - Date.now()) / 86400000 : NaN;
      if (daysLeft < 14) {
        console.error(`GITHUB_TOKEN expires in ${daysLeft.toFixed(1)} days — rotate it: wrangler secret put GITHUB_TOKEN`);
        await ping(env, "fail", `PAT expires in ${daysLeft.toFixed(1)} days; rotate GITHUB_TOKEN`);
        return;
      }
      // Success ping: if these stop arriving, healthchecks.io emails hello@modeo.io after the
      // grace period — catching a dead Worker even when nothing here ever runs to report it.
      await ping(env, "success");
    });

    ctx.waitUntil(dispatch);
  },
};
