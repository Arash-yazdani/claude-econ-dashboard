# Incident 2026-10-21 — Bedrock refused every Claude model on a new AWS account; the probe stayed green

- **Date / time (ET):** 2026-10-21 18:25 (first live run, pre-launch; schedules off)
- **Trigger:** manual `weekly-regression` dispatch (go-live step 12) → Slack "Weekly regression
  INCONCLUSIVE on prompt 1.5: only 0/12 cases measured".
- **Signal:** all 12 cases errored with `both providers failed — primary:
  anthropic.claude-haiku-5-5 HTTP 404`. The model-id probe step in the same run passed.
  A one-off `model-id-check` workflow then showed, from GitHub Actions in us-east-1:

  | Model id | Result |
  |---|---|
  | `anthropic.claude-haiku-5-5`, `us.` and `global.` forms | 404 "does not exist" |
  | `anthropic.claude-sonnet-5-5`, `us.` and `global.` forms | 404 "does not exist" |
  | `anthropic.claude-haiku-4-5`, `anthropic.claude-opus-5-5` | 403 "is not available for this account" |

  The Bedrock console playground returned the same 403 text for Haiku 5.5 (AccessDeniedException).
- **Impact:** none user-visible. Nothing was scheduled; the site kept the committed briefing.
  The run logged 12 `error` verdicts to `eval_log.csv` (commit 80e41df).
- **Root cause:**
  1. **Account-level block.** The AWS account was created the same day on the Free plan.
     Bedrock refused every Anthropic model for it. The model id was correct (it matches the
     Bedrock model catalog). Upgrading to the Paid plan did not clear the block within the
     first hour. Exact AWS policy unconfirmed; resolution pending with AWS.
  2. **Detection gap.** `scripts/probe_models.py` used `messages.count_tokens`, which succeeded
     for every model while generation was refused. The probe reported "probed: writer …,
     failover …, judge …" and passed over a dead writer and a dead failover. Both providers
     sat behind the same account block, so the writer→failover design had no independent
     path.
- **Fix:** commit 826a74d. The probe now sends a 1-token `messages.create` per model and
  treats 403 `PermissionDeniedError` as dead alongside 404. New test
  `tests/test_probe_models.py` covers 403, 404 and the non-verdict 500 path (448 tests pass).
- **Follow-up:**
  - Get Anthropic model access enabled on the account (AWS Support "Account and billing"
    case if the block persists). Owner: Arash.
  - Re-run `model-id-check`, then step 12, before enabling any schedule. Delete
    `.github/workflows/model-id-check.yml` once Claude responds.
  - Note for any new Bedrock deployment: run one real generation call per model on day 0.
    Model catalog listing, IAM permissions and token counting all succeed before the account
    can generate.
