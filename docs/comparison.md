# Model comparison: original stack vs Claude on Bedrock

Generated 2026-10-27T23:22:45+00:00 by `scripts/compare_models.py --live`. Prompt v1.5, 12 regression requests per stack.

Judge for both stacks: us.anthropic.claude-haiku-4-5-20251001-v1:0. It shares a model family with the candidate writer; read judge scores with that bias in mind. Validator results are deterministic and carry no such bias.

## Gate summary

| stack | verdict | valid | measured | below-3 |
|---|---|---|---|---|
| original (Gemini/Groq) | FAIL | 11/12 | 12 | 0 |
| Claude on Bedrock | PASS | 11/12 | 12 | 0 |

## Per case

| case | stack | model | validator | c1 | c3 | c4 | c5 |
|---|---|---|---|---|---|---|---|
| T1 | original (Gemini/Groq) | gemini-2.5-flash | valid | 5 | 4 | 5 | 5 |
| T1 | Claude on Bedrock | us.anthropic.claude-haiku-4-5-20251001-v1:0 | valid | 5 | 5 | 5 | 5 |
| T2 | original (Gemini/Groq) | gemini-2.5-flash | valid | 4 | 4 | 5 | 4 |
| T2 | Claude on Bedrock | us.anthropic.claude-haiku-4-5-20251001-v1:0 | valid | 5 | 4 | 5 | 5 |
| T3 | original (Gemini/Groq) | gemini-2.5-flash | valid | 4 | 3 | 5 | 5 |
| T3 | Claude on Bedrock | us.anthropic.claude-haiku-4-5-20251001-v1:0 | valid | 4 | 3 | 5 | 5 |
| T4 | original (Gemini/Groq) | gemini-2.5-flash | valid | 4 | 3 | 5 | 5 |
| T4 | Claude on Bedrock | us.anthropic.claude-haiku-4-5-20251001-v1:0 | valid | 4 | 4 | 5 | 5 |
| T5 | original (Gemini/Groq) | gemini-2.5-flash | valid | 4 | 4 | 5 | 5 |
| T5 | Claude on Bedrock | us.anthropic.claude-haiku-4-5-20251001-v1:0 | valid | 4 | 4 | 5 | 5 |
| E1 | original (Gemini/Groq) | gemini-2.5-flash | valid | 4 | 4 | 5 | 5 |
| E1 | Claude on Bedrock | us.anthropic.claude-haiku-4-5-20251001-v1:0 | valid | 5 | 4 | 5 | 5 |
| E2 | original (Gemini/Groq) | gemini-2.5-flash | valid | 4 | 3 | 5 | 5 |
| E2 | Claude on Bedrock | us.anthropic.claude-haiku-4-5-20251001-v1:0 | valid | 5 | 4 | 5 | 5 |
| E3 | original (Gemini/Groq) | gemini-2.5-flash | valid | 4 | 3 | 5 | 5 |
| E3 | Claude on Bedrock | us.anthropic.claude-haiku-4-5-20251001-v1:0 | valid | 5 | 4 | 5 | 5 |
| E4 | original (Gemini/Groq) | gemini-2.5-flash | valid | 4 | 5 | 5 | 5 |
| E4 | Claude on Bedrock | us.anthropic.claude-haiku-4-5-20251001-v1:0 | valid | 5 | 4 | 5 | 5 |
| A1 | original (Gemini/Groq) | gemini-2.5-flash | refusal | None | None | None | 5 |
| A1 | Claude on Bedrock | us.anthropic.claude-haiku-4-5-20251001-v1:0 | refusal | None | None | None | 5 |
| A2 | original (Gemini/Groq) | gemini-2.5-flash | valid | 5 | 5 | 5 | 5 |
| A2 | Claude on Bedrock | us.anthropic.claude-haiku-4-5-20251001-v1:0 | valid | 5 | 5 | 5 | 5 |
| A3 | original (Gemini/Groq) | gemini-2.5-flash | valid | 5 | 5 | 5 | 5 |
| A3 | Claude on Bedrock | us.anthropic.claude-haiku-4-5-20251001-v1:0 | valid | 5 | 5 | 5 | 5 |

## Claude usage (from response.usage)

36 Claude calls (writer, failover and judge), 110552 input tokens, 10663 output tokens.

