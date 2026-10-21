"""Daily US-macro briefing pipeline (Phase 1).

B1 covers the data layer: FRED ingest → cache → deltas → pre-LLM plausibility →
staleness flagging → assembly of the data half of a `BriefingRequest`
(see docs/schemas/briefing-io-v1.0.md). The regime/drivers half is filled by the
B2 classifier; the LLM call + output validator are B3/B4.
"""

__version__ = "0.1.0"
