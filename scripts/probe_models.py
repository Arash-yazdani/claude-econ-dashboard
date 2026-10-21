"""Are the Claude models we depend on still served on Bedrock in AWS_REGION? (Runbook: model decommissions.)

The original repo lost its Groq failover twice to silent decommissions (llama-4-scout, then
llama-3.3-70b-versatile, found dead 2026-08-25), so it probed each provider's model list weekly.
Bedrock has no Models API on the Mantle endpoint, so this probe sends one `messages.count_tokens`
per model (writer, failover, judge): a 404 means the model is not served / not enabled in this
region; any other error is printed as a warning and is not a verdict. No generation is requested.

Daily: alert only, exit 0 — a dead model must never block a publish.
Weekly (weekly-regression.yml): --fail so the job goes red and the regression healthcheck pages.
Never raises.
"""
from __future__ import annotations

import os
import sys

from macro_pipeline import observability
from macro_pipeline._env import load_dotenv
from macro_pipeline.llm_client import (FAILOVER_MODEL, FAILOVER_MODEL_ENV, JUDGE_MODEL, JUDGE_MODEL_ENV,
                                       PRIMARY_MODEL, PRIMARY_MODEL_ENV, bedrock_region)


def models() -> dict[str, str]:
    return {
        "writer": os.environ.get(PRIMARY_MODEL_ENV) or PRIMARY_MODEL,
        "failover": os.environ.get(FAILOVER_MODEL_ENV) or FAILOVER_MODEL,
        "judge": os.environ.get(JUDGE_MODEL_ENV) or JUDGE_MODEL,
    }


def probe(client=None) -> list[str]:
    import anthropic

    dead: list[str] = []
    if client is None:
        try:
            client = anthropic.AnthropicBedrockMantle(aws_region=bedrock_region(), timeout=20.0)
        except Exception as exc:  # noqa: BLE001 — BriefingLLMError (no region) or SDK auth setup
            print(f"::warning::Bedrock probe not run: {exc}")
            return dead
    for role, model in models().items():
        try:
            client.messages.count_tokens(model=model, messages=[{"role": "user", "content": "ping"}])
        except anthropic.NotFoundError:
            dead.append(f"{role} {model}")
        except Exception as exc:  # noqa: BLE001
            print(f"::warning::Bedrock probe for {role} {model} not conclusive: {exc}")
    return dead


def main() -> int:
    load_dotenv()
    dead = probe()
    if dead:
        msg = ("Claude model NOT SERVED on Bedrock: " + "; ".join(dead)
               + ". Check model access in the Bedrock console for this region and swap the id.")
        observability.send_alert(msg)
        print(("::error::" if "--fail" in sys.argv else "::warning::") + msg)
        return 1 if "--fail" in sys.argv else 0
    print("probed: " + ", ".join(f"{r} {m}" for r, m in models().items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
