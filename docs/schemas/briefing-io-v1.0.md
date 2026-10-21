# Data Schemas — BriefingRequest / BriefingOutput — v1.0

**Status:** ✅ Accepted (2026-06-11) · **Jira:** [SCRUM-20 / D8](https://modeo-team-1.atlassian.net/browse/SCRUM-20) · **Feeds:** B1 (pipeline), B3 (LLM call), B4 (validator), D10 (fixtures)

The two JSON contracts at the LLM boundary. `BriefingRequest` is assembled by the pipeline and rendered into the prompt's `<todays_data>`; `BriefingOutput` is the Gemini `responseSchema` and the validator's target.

## BriefingRequest (input contract)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "BriefingRequest",
  "type": "object",
  "additionalProperties": false,
  "required": ["as_of_date", "regime_today", "regime_yesterday", "drivers", "indicators", "releases_last_24h"],
  "properties": {
    "as_of_date":       { "type": "string", "format": "date" },
    "regime_today":     { "type": "string", "enum": ["soft_landing","late_cycle_expansion","stagflation_risk","recovery","slowdown"] },
    "regime_yesterday": { "type": ["string","null"], "enum": ["soft_landing","late_cycle_expansion","stagflation_risk","recovery","slowdown", null] },
    "drivers":          { "type": "array", "items": { "type": "string" }, "maxItems": 3 },
    "indicators":       { "type": "array", "minItems": 1, "items": { "$ref": "#/$defs/indicator" } },
    "releases_last_24h":{ "type": "array", "items": { "$ref": "#/$defs/release" } }
  },
  "$defs": {
    "indicator": {
      "type": "object", "additionalProperties": false,
      "required": ["id","display_name","value","unit","period_label","last_release_date"],
      "properties": {
        "id":               { "type": "string" },
        "display_name":      { "type": "string" },
        "value":            { "type": ["number","null"] },
        "unit":             { "type": "string", "enum": ["%","index","USD"] },
        "period_label":     { "type": "string" },
        "last_release_date":{ "type": "string", "format": "date" },
        "delta":            { "type": "number" },
        "note":             { "type": "string" }
      }
    },
    "release": {
      "type": "object", "additionalProperties": false,
      "required": ["id","prior_value","new_value"],
      "properties": {
        "id":         { "type": "string" },
        "prior_value":{ "type": ["number","null"] },
        "new_value":  { "type": ["number","null"] },
        "note":       { "type": "string" }
      }
    }
  }
}
```

`value: null` means "data unavailable" — the prompt must not narrate around it (tested by E1). `drivers` are indicator ids the classifier flagged as responsible for the current regime (see D9).

## Plausibility ranges — pre-LLM input validation

The pipeline validates each indicator **before** calling the LLM. A value outside its valid range → **reject, skip the LLM, render the deterministic fallback**. These ranges are tighter than the prompt's in-LLM refusal backstop (which catches anything that slips through).

| Indicator | id | Valid input range | In-LLM refusal backstop |
|---|---|---|---|
| CPI YoY | `cpi_yoy` | 0 – 10 % | > 50 % |
| Core CPI YoY | `core_cpi_yoy` | 0 – 10 % | > 50 % |
| Unemployment | `unemployment` | 0 – 30 % | > 30 % |
| Real GDP YoY | `real_gdp_yoy` | −10 – 10 % | < −20 or > 20 % |
| 10-Year Treasury | `treasury_10y` | 0 – 20 % | < 0 % |
| Fed Funds | `fed_funds` | 0 – 25 % | — |
| U-Mich Sentiment | `umich_sentiment` | 0 – 150 | — |

## BriefingOutput (output contract = Gemini `responseSchema`)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "BriefingOutput",
  "type": "object",
  "additionalProperties": false,
  "required": ["headline","quotable_numbers","whats_new","regime_change"],
  "properties": {
    "headline":        { "type": ["string","null"], "maxLength": 140 },
    "quotable_numbers":{
      "type": "array", "maxItems": 2,
      "items": { "type": "object", "additionalProperties": false,
        "required": ["indicator_id","sentence"],
        "properties": { "indicator_id": { "type": "string" }, "sentence": { "type": "string", "maxLength": 200 } } }
    },
    "whats_new":     { "type": ["string","null"] },
    "regime_change": { "type": ["string","null"] },
    "error":         { "type": "string", "enum": ["data_unavailable"] }
  }
}
```

**Two valid shapes:**
- **Normal:** `headline` non-null, `quotable_numbers` has **exactly 2**, `error` absent. `whats_new`/`regime_change` are conditional.
- **Refusal:** `headline: null`, `quotable_numbers: []`, `whats_new: null`, `regime_change: null`, `error: "data_unavailable"`.

## Validator mapping (the 17 O-checks → schema)

| Check | Enforces |
|---|---|
| O1 JSON parse / O2 schema match | this `BriefingOutput` shape |
| O5 number grounding (±0.05) | every number in output traces to a `BriefingRequest.indicators[].value` or `releases_last_24h` |
| period grounding | every period in output appears in some `period_label` / `last_release_date` |
| O3 indicator-id whitelist | `quotable_numbers[].indicator_id` ∈ request indicator ids |
| O4 conditional emptiness | `whats_new` non-null **iff** `releases_last_24h` non-empty; `regime_change` non-null **iff** `regime_today != regime_yesterday` |
| O8 banned phrases | output contains none of the O8 list |
| length cap | total ≤ 80 words; `headline` ≤ 140 chars / ≤ 22 words |
| O16/O17 refusal envelope | refusal shape is exact, only on malformed/implausible input |

## Open items
1. `regime_*` enums are the v1.0 label set — keep in lockstep with D9.
2. `unit: "USD"` exists only to let the scope guard recognise and ignore out-of-scope inputs (e.g., a BTC row in A2); no US-macro indicator uses it.
