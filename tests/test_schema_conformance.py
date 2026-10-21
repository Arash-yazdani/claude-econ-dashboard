"""The produced data conforms to the locked BriefingRequest contract.

Two guarantees:
1. The machine-readable schema copy faithfully accepts all 12 locked regression
   fixtures (which ARE full BriefingRequests) — guards schema/​doc drift.
2. The pipeline's indicators + releases validate against the schema's sub-defs, and a
   classifier-completed BriefingRequest validates whole.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from macro_pipeline.pipeline import run_pipeline

from conftest import AS_OF

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas" / "briefing-request.schema.json").read_text())
FIXTURES = json.loads((ROOT / "tests" / "fixtures" / "regression-cases-v1.0.json").read_text())


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    Draft202012Validator.check_schema(SCHEMA)
    return Draft202012Validator(SCHEMA)


@pytest.mark.parametrize("case", FIXTURES["cases"], ids=lambda c: c["id"])
def test_locked_fixtures_validate(case, validator):
    assert list(validator.iter_errors(case["input"])) == []


def test_pipeline_output_completes_to_valid_request(source, validator):
    # The B2 classifier now fills the regime half; validate the complete request.
    result = run_pipeline(source, as_of=AS_OF, cache_path="/dev/null", write_indicators_to=None)
    assert list(validator.iter_errors(result.briefing_request())) == []


def test_pipeline_indicators_match_subschema(source):
    ind_validator = Draft202012Validator(SCHEMA["$defs"]["indicator"])
    result = run_pipeline(source, as_of=AS_OF, cache_path="/dev/null", write_indicators_to=None)
    for ind in result.briefing_request_data()["indicators"]:
        assert list(ind_validator.iter_errors(ind)) == []
