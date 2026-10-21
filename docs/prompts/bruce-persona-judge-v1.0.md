# Bruce Persona Judge — v1.0 (Layer 3 proxy)

**Status:** 🟡 **Draft** — awaiting κ(persona, Bruce) calibration · **Jira:** [SCRUM-88](https://modeo-team-1.atlassian.net/browse/SCRUM-88)
**Feeds:** the CTQ rubric (SCRUM-86), the annotation queues (SCRUM-85) · **Does NOT feed:** any ship gate

## What this is

A second judge that scores a briefing on **utility to Bruce**, not craft. `judge.py` (Layer 2)
already scores quotability, tone, conditional fields and length, and owns the q26 ship gate. This
one asks a different question: *does this give Bruce the raw material to form his own read?*

A briefing can be beautifully written and useless to him. That gap is precisely what this
measures, and nothing measured it before.

**Why a persona judge at all.** Six of the sixteen KPIs — including the primary PMF metric,
"did you quote this out loud this week?" — depend on Bruce and have no mechanism. Real ratings
accumulate slowly (10% weekly sample; κ ≥ 0.6 targeted by week 8). This gives a daily signal
in the meantime.

**It does not replace him.** It is a proxy that must itself be validated by computing
**κ(persona, Bruce)** once real ratings land — the same bar the craft judge must clear. A persona
that disagrees with Bruce is a persona to re-prompt, not a verdict to trust. Until then it is a
co-witness twice removed, and it is deliberately kept out of every gate.

## The persona

70, retired. Spent his working life running business-management and accounting-**process**
engagements — month-end closes, handoffs, controls, the management reporting other people ran
their businesses on. He walked into other people's numbers for a living and asked where they came
from. Reads the paper and the economic news and enjoys it. Not a trader, never a finance
professional: a smart generalist who thinks in systems, control totals, leading versus lagging
indicators, and whether a number is decision-grade or decoration.

His reflex on any number is three questions: **as of when, measured how, and would I stake my name
repeating it?**

He reads twice a day for two reasons. He likes being able to say something real about the economy
when friends ask, without sounding like he is reciting a chart. And the trajectory bears on calls
he makes himself — how he votes, when he moves money, when he goes in with friends on a house,
when he replaces the car, what he tells family when they ask.

**He makes those calls. He does not want them made for him.**

## How the compliance trap is answered

Bruce's real use cases are decisions. The product is barred from advising (Investment Advisers Act
posture; validator O8 hard-blocks ten literal phrases). A naive *"is this useful for his
decisions?"* rubric would reward the model for advising him — the exact failure the whole product
is built to avoid.

Two structural answers, both from the adversarial review panel:

1. **Restraint is scored as Bruce's taste, not an external rule.** He laid out the process for
   clients and handed them the decision; he watched what happened to people who reached the
   client's conclusion for them. A source that presumes to tell him what to buy *reads to him like
   a salesman*, and he stops trusting it. Advice therefore lowers the score for a reason the
   persona itself holds — the model is agreeing with the rubric, not fighting it.
2. **The clamp (rule R3).** If `b4_restraint` ≤ 3, no other criterion may exceed it. Advice is
   *arithmetically* unable to buy usefulness. Enforced in **code** (`_apply_clamp`), not only in
   the prompt — a rubric whose integrity depends on the judged model choosing to obey it is not an
   integrity guarantee.

## Run config

```
model:               gemini-2.5-flash      # same family + determinism as the craft judge
temperature:         0.0                   # instability triggers a re-prompt (PRD §401)
response_schema:     BruceVerdict          # schemas/bruce-verdict.schema.json
failover:            Groq llama-3.3-70b    # same chain as production
```

## The four criteria

| id | Name | The question it answers |
|---|---|---|
| **B1** | A read he can repeat | Does it say what the economy is *doing* and put one of its own numbers, dated, under that claim? |
| **B2** | The right two numbers | Are these the two numbers that carry this regime, or two that merely exist? |
| **B3** | Movement, not a snapshot | Does he learn what moved and what is moving it — stated, and already happened? |
| **B4** | Restraint | Does it describe the economy and stop, **including the advice a phrase blocklist cannot see**? |

B1–B3 go `null` on a refusal envelope and only B4 is scored: Bruce reads a refusal as *a control
that worked*. He would rather be told the number is not there than be handed one somebody guessed
at, so the envelope earns trust rather than a deduction.

### What B4 catches that O8 cannot

O8 is ten literal strings. B4 scores five *patterns*, none of which contain a banned phrase:

- timing / urgency framing ("a window", "sets up")
- asset behaviour, index levels, tickers
- reader-directed second person or imperative ("worth watching")
- **beneficiary clauses** — naming a party and assigning it gain or loss ("good news for savers")
- **any forecast** of a future value, rate decision, regime or market outcome ("should keep easing")

## Validation (2026-07-28, live against Gemini)

| Case | B1 | B2 | B3 | B4 | Useful | Behaviour |
|---|---|---|---|---|---|---|
| The real briefing published today | 5 | 5 | 5 | 5 | 1.00 | clean read, both numbers are drivers |
| Subtle advice, **zero banned phrases** | 1 | 1 | 1 | 1 | 0.20 | caught all three patterns; clamp fired |
| Useless but impeccably written | 3 | 1 | 1 | 5 | 0.50 | perfect restraint, tells him nothing |

Case 2 is the load-bearing result: *"a favorable window for locking in rates"*, *"good news for
savers"*, *"should keep easing"* would all pass O8 and publish. The persona caught them and
clamped usefulness to 0.20. Case 3 is the complementarity — craft and utility are genuinely
different axes, and a briefing can ace one while failing the other.

## Known limitations (revisit at the first quarterly retro)

1. **κ not baselined.** Scores are advisory until κ(persona, Bruce) is measured. It has the same
   epistemic status the craft judge had at launch, minus the head start.
2. **A proxy can drift from the man.** The persona was written from the PRD, the charter and the
   sign-off list — not from Bruce's own voice. The first annotation round (SCRUM-85) is also the
   first evidence of whether this *is* him.
3. **B2 depends on `drivers` being populated.** When the classifier emits an empty driver list the
   rubric falls back to a fixed regime→domain map, which is coarser.
4. **Deliberately outside every gate.** A subjective judge must never block a publish. If that
   changes, it needs its own sign-off — scores that gate are a different risk class.
5. This prompt is a versioned artifact: any edit needs a re-run and a `prompt_history.csv` row
   (once that file exists — SCRUM-87).
