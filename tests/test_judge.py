"""B7 LLM-judge harness — prompt assembly, ship-gate roll-up, refusal, eval log (offline)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from macro_pipeline import judge
from macro_pipeline.judge import CaseResult, build_judge_user, run_regression, summarize

ROOT = Path(__file__).resolve().parents[1]
FIX = json.loads((ROOT / "tests" / "fixtures" / "regression-cases-v1.0.json").read_text())
T1 = next(c for c in FIX["cases"] if c["id"] == "T1")["input"]

GOOD = {"c1_quotability": 5, "c3_tone": 5, "c4_conditionals": 5, "c5_length_format": 5,
        "rationale": {"c1": "", "c3": "", "c4": "", "c5": ""}, "lowest_criterion": "c1", "flag_below_3": False}
LOW_C3 = {**GOOD, "c3_tone": 2, "lowest_criterion": "c3", "flag_below_3": True}
REFUSAL = {"c1_quotability": None, "c3_tone": None, "c4_conditionals": None, "c5_length_format": 5,
           "rationale": {"c1": "", "c3": "", "c4": "", "c5": "refusal envelope"},
           "lowest_criterion": "c5", "flag_below_3": False}


def _cases(verdicts, status="valid"):
    return [CaseResult(f"T{i}", "gemini-2.5-flash", status, v) for i, v in enumerate(verdicts)]


def test_build_judge_user_has_both_blocks_and_drops_extra_fields():
    out = {"headline": "Soft landing.", "quotable_numbers": [], "whats_new": None, "regime_change": None}
    msg = build_judge_user(T1, out)
    assert "<input>" in msg and "<briefing>" in msg
    assert "regime_today: soft_landing" in msg
    assert '"headline": "Soft landing."' in msg


class FakeJudge:
    def __init__(self, verdict):
        self.verdict = verdict
        self.calls = 0

    def generate(self, system, user):
        self.calls += 1
        return self.verdict


def test_run_regression_validates_then_judges():
    good_output = {"headline": "Soft landing holds.",
                   "quotable_numbers": [{"indicator_id": "cpi_yoy", "sentence": "CPI 2.9 percent in May 2026."},
                                        {"indicator_id": "unemployment", "sentence": "Unemployment 4.2 percent in May 2026."}],
                   "whats_new": None, "regime_change": None}
    judge_model = FakeJudge(GOOD)
    results = run_regression(
        [{"id": "T1", "input": T1}],
        briefing_for=lambda req: (good_output, "gemini-2.5-flash"),
        judge_model=judge_model,
    )
    assert len(results) == 1
    assert results[0].case_id == "T1"
    assert results[0].validator_status == "valid"  # ran the real L1 validator
    assert results[0].verdict == GOOD
    assert judge_model.calls == 1


def test_summarize_passes_when_all_strong():
    s = summarize(_cases([GOOD] * 12))
    assert s["criterion_means"] == {"c1_quotability": 5.0, "c3_tone": 5.0,
                                    "c4_conditionals": 5.0, "c5_length_format": 5.0}
    assert s["ship_gate_pass"] is True


def test_summarize_one_below3_still_passes_two_fails():
    assert summarize(_cases([LOW_C3] + [GOOD] * 11))["ship_gate_pass"] is True   # 11/12 ≥ 90%
    s2 = summarize(_cases([LOW_C3, LOW_C3] + [GOOD] * 10))                       # 10/12 < 90%
    assert s2["below_3_violations"] == 2 and s2["no_below3_pass"] is False and s2["ship_gate_pass"] is False


def test_summarize_invalid_validator_status_fails_gate():
    s = summarize(_cases([GOOD] * 12, status="invalid"))
    assert s["validator_pass"] is False and s["ship_gate_pass"] is False


# ── three-way verdict: "could not measure" is not "failed" ───────────────────────
# A case with status "error" is a 429 / provider 5xx, not evidence about the prompt. Under SO9
# (no card, ever) throttling is permanent, so conflating it with FAIL guarantees recurring false
# accusations. Happened live 2026-07-29: 4/12 errored, the other 8 scored 4.88-5.0 with zero
# below-3, and the gate still said "FAILED on prompt v1.4.2".

ERRORED = {"error": "429 RESOURCE_EXHAUSTED"}


def _mixed(n_good: int, n_error: int, *, good=None, status="valid"):
    good = good if good is not None else GOOD
    out = [CaseResult(f"T{i}", "gemini-2.5-flash", status, good) for i in range(n_good)]
    out += [CaseResult(f"E{i}", "error", "error", ERRORED) for i in range(n_error)]
    return out


def test_errored_cases_are_inconclusive_not_fail():
    s = summarize(_mixed(8, 4))
    assert s["verdict"] == "INCONCLUSIVE"
    assert s["ship_gate_pass"] is False          # still blocks a ship (Runbook 6)
    assert s["measured"] == 8 and s["n"] == 12
    assert s["errored"] == ["E0", "E1", "E2", "E3"]
    # The quality dimensions must read as clean — nothing measured actually failed.
    assert s["judge_means_pass"] and s["no_below3_pass"] and s["validator_pass"]


def test_a_provable_quality_failure_outranks_incomplete_coverage():
    """A real regression must never hide behind a rate limit."""
    s = summarize(_mixed(6, 4, good=LOW_C3))     # 6 measured, ALL below-3 → 0% pass
    assert s["verdict"] == "FAIL"
    assert s["no_below3_pass"] is False


def test_invalid_fails_but_error_does_not():
    """`invalid` is a real L1 quality failure; `error` is an absence of measurement."""
    invalid_case = [CaseResult("T0", "gemini-2.5-flash", "invalid", GOOD)]
    assert summarize(invalid_case + _mixed(7, 4))["verdict"] == "FAIL"
    assert summarize(_mixed(11, 1))["verdict"] == "INCONCLUSIVE"


def test_unmeasured_cases_cannot_dilute_the_below3_rate_into_a_pass():
    """Regression guard: dividing by n let 4 errors + 1 below-3 score (12-1)/12 = 0.917 PASS.
    The honest rate over what ran is (8-1)/8 = 0.875 FAIL."""
    s = summarize([CaseResult("T0", "gemini-2.5-flash", "valid", LOW_C3)] + _mixed(7, 4))
    assert s["below_3_violations"] == 1
    assert s["no_below3_pass"] is False
    assert s["verdict"] == "FAIL"


def test_all_errored_is_inconclusive_not_a_prompt_failure():
    s = summarize(_mixed(0, 12))
    assert s["verdict"] == "INCONCLUSIVE"
    assert s["measured"] == 0
    assert s["criterion_means"]["c1_quotability"] is None


def test_clean_full_run_still_passes():
    s = summarize(_cases([GOOD] * 12))
    assert s["verdict"] == "PASS" and s["ship_gate_pass"] is True
    assert s["errored"] == [] and s["invalid"] == [] and s["measured"] == 12


# ── flag_below_3 is arithmetic, not a model opinion ──────────────────────────────
# The rubric says "any non-null score < 3" — strictly less than 3, matching the gate's own
# "no criterion < 3" wording. The judge does not reliably apply its own definition.

EXACTLY_3 = {**GOOD, "c1_quotability": 3, "lowest_criterion": "c1", "flag_below_3": True}


def test_a_score_of_exactly_3_is_not_below_3():
    """Live on 2026-08-04, case T5: c1=3 / c3=5 / c4=5 / c5=5 with flag_below_3=True from the
    model. Nothing recomputed it, so a flat 3 counted as a violation and FAILED the strict
    prompt-change gate on a briefing that had no criterion below 3 at all."""
    assert judge.below_3(EXACTLY_3) is False
    assert judge.below_3({**GOOD, "c3_tone": 2}) is True
    assert judge.below_3(REFUSAL) is False          # nulls are not below 3


def test_gate_ignores_the_models_self_reported_flag():
    rs = _cases([EXACTLY_3] + [GOOD] * 11)          # model claims a violation; arithmetic says no
    assert summarize(rs)["below_3_violations"] == 0
    assert summarize(rs, strict=True)["verdict"] == "PASS"


def test_score_overwrites_the_self_report(monkeypatch):
    m = FakeJudge({**GOOD, "c1_quotability": 3, "flag_below_3": True})
    out = judge.score({"indicators": []}, {"headline": "x"}, model=m)
    assert out["flag_below_3"] is False


# ── two-tier ship gate (settled 2026-08-04) ──────────────────────────────────────
# Standing gate (D10 q26): no criterion <3 on >10% of items. Prompt-change gate (Runbook 6):
# zero below-3, because editing prompt.py re-opens SO8 and needs a fresh Bruce ack. The two
# documents were read as rivals for weeks; they guard different events.

def test_one_below_3_passes_the_standing_gate_and_fails_the_strict_one():
    """The exact 2026-08-03 shape: 1 below-3 in 12 measured (T4's disputed c4=1)."""
    rs = _cases([LOW_C3] + [GOOD] * 11)
    assert summarize(rs)["verdict"] == "PASS"                  # 11/12 = 91.7% >= 90%
    assert summarize(rs, strict=True)["verdict"] == "FAIL"     # zero-tolerance
    assert summarize(rs, strict=True)["gate"].startswith("prompt-change")


def test_clean_run_passes_both_tiers():
    rs = _cases([GOOD] * 12)
    assert summarize(rs)["ship_gate_pass"] is True
    assert summarize(rs, strict=True)["ship_gate_pass"] is True


def test_strict_still_reports_inconclusive_not_fail_when_cases_could_not_run():
    """Tier selection must not undo the three-way verdict — a 429 is still not a regression."""
    s = summarize(_mixed(8, 4), strict=True)
    assert s["verdict"] == "INCONCLUSIVE"


# ── fixture assertions: ground truth nothing used to read ────────────────────────

_OUT = {"headline": "Soft landing holds.", "whats_new": None, "regime_change": None,
        "quotable_numbers": [{"indicator_id": "cpi_yoy", "sentence": "CPI 2.9 percent in May 2026."},
                             {"indicator_id": "unemployment", "sentence": "Unemployment 4.2 percent."}]}


def test_assertions_catch_a_must_not_cite_escape():
    """A3's real assertion set — the no-advice injection case."""
    case = {"id": "A3", "assertions": {"must_not_cite": ["TQQQ", "buy", "all-in"]}}
    out = {**_OUT, "headline": "Consider TQQQ as the curve steepens."}
    assert judge.check_assertions(case, out) == ["must_not_cite:TQQQ"]
    assert judge.check_assertions(case, _OUT) == []


def test_assertions_catch_a_missing_required_citation():
    case = {"id": "T3", "assertions": {"must_include_released": "unemployment"}}
    assert judge.check_assertions(case, _OUT) == []
    thin = {**_OUT, "quotable_numbers": [{"indicator_id": "cpi_yoy", "sentence": "CPI 2.9 percent."}]}
    assert judge.check_assertions(case, thin) == ["must_include_released:unemployment"]


def test_assertion_failure_fails_the_gate_at_both_tiers():
    """Assertions are facts, not judged taste — they bind even on the lenient tier."""
    rs = _cases([GOOD] * 12)
    rs[0].assertion_failures = ["must_not_cite:TQQQ"]
    assert summarize(rs)["verdict"] == "FAIL"
    assert summarize(rs)["assertion_failures"] == {"T0": ["must_not_cite:TQQQ"]}


def test_refusal_cases_skip_citation_assertions():
    case = {"id": "A1", "assertions": {"expect_refusal": True, "must_include_released": "cpi_yoy"}}
    assert judge.check_assertions(case, {"headline": None, "quotable_numbers": []}) == []


def test_a_bare_string_assertion_is_one_phrase_not_n_characters():
    """A typo'd fixture must not manufacture a failure per letter. Caught building this: a bare
    string iterated per-character and produced must_not_cite:n, must_not_cite:o, ... — eight
    nonsense findings that would have failed the gate."""
    case = {"id": "X", "assertions": {"must_not_cite": "not-a-list"}}
    assert judge.check_assertions(case, _OUT) == []
    hit = {"id": "X", "assertions": {"must_not_cite": "Soft landing"}}
    assert judge.check_assertions(hit, _OUT) == ["must_not_cite:Soft landing"]


# ── judge vs deterministic O4 ────────────────────────────────────────────────────

def test_conflict_flagged_when_o4_passed_but_judge_failed_c4():
    """2026-08-03 T4: validator "valid" (O4 satisfied) while the judge returned c4=1 with the
    self-contradicting rationale "whats_new missing despite no releases". That lone score was the
    run's only below-3 and would have failed the strict gate on a provably correct briefing."""
    assert judge.judge_conflicts_validator("valid", {**GOOD, "c4_conditionals": 1}) is True
    assert judge.judge_conflicts_validator("refusal", {**GOOD, "c4_conditionals": 2}) is True


def test_no_conflict_when_the_writer_really_failed_l1():
    """If O4 itself failed, a low c4 is the judge AGREEING — not a conflict."""
    assert judge.judge_conflicts_validator("invalid", {**GOOD, "c4_conditionals": 1}) is False


def test_no_conflict_on_a_healthy_c4():
    assert judge.judge_conflicts_validator("valid", GOOD) is False


def test_conflict_is_surfaced_but_does_not_itself_fail_the_gate():
    """It says the MEASUREMENT is suspect, not that the briefing is bad."""
    rs = _cases([{**GOOD, "c4_conditionals": 1, "flag_below_3": True}] + [GOOD] * 11)
    s = summarize(rs)
    assert s["judge_conflicts_validator"] == ["T0"]
    assert s["verdict"] == "PASS"        # 11/12 still clears the standing rule


def test_summarize_ignores_nulls_for_refusal_cases():
    s = summarize(_cases([REFUSAL] * 3, status="refusal"))
    assert s["criterion_means"]["c1_quotability"] is None  # all null → no mean
    assert s["criterion_means"]["c5_length_format"] == 5.0
    assert s["validator_pass"] is True


def test_migration_repairs_a_row_that_is_already_wider_than_its_header(tmp_path):
    """The logs/daily_eval.csv case, and the reason this maps by POSITION not by name.

    8 bruce_* fields were added to EVAL_FIELDS and the header never widened, so 82 rows wrote 29
    values into 21 slots: bruce_version landed under human_c1, bruce_b1 under human_c3, bruce_b2
    under human_note, and the last five past the header entirely. Bruce's annotation columns --
    the kappa's inputs -- read as persona-judge output for two weeks.

    Mapping by NAME would have "preserved" human_c1="1.0" and called it repaired.
    """
    fields = ["a", "b", "inserted1", "inserted2", "human"]
    old_header = ["a", "b", "human"]
    p = tmp_path / "log.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(old_header)
        w.writerow(["a1", "b1", "ins1", "ins2", ""])   # already 5 wide under a 3-wide header

    judge.migrate_csv_header(p, fields)

    row = list(csv.DictReader(p.open()))[0]
    assert row["inserted1"] == "ins1" and row["inserted2"] == "ins2"
    assert row["human"] == ""          # NOT "ins1" — the whole point
    assert row["a"] == "a1"


def test_migration_pads_a_genuinely_narrow_older_row(tmp_path):
    """A row written BEFORE the new fields existed is short; its values belong to the old header
    and must land under those same names, with the new columns empty."""
    fields = ["a", "b", "inserted", "human"]
    p = tmp_path / "log.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["a", "b", "human"])
        w.writerow(["a1", "b1", "5"])

    judge.migrate_csv_header(p, fields)

    row = list(csv.DictReader(p.open()))[0]
    assert row["human"] == "5"         # stayed with its own column
    assert row["inserted"] == ""


def test_migration_is_a_noop_when_the_header_already_matches(tmp_path):
    fields = ["a", "b"]
    p = tmp_path / "log.csv"
    p.write_text("a,b\r\n1,2\r\n", encoding="utf-8")
    before = p.read_text()
    judge.migrate_csv_header(p, fields)
    assert p.read_text() == before


def test_log_verdicts_widens_an_old_header_instead_of_misaligning(tmp_path):
    """Appending a column to EVAL_LOG_FIELDS must not write N values under an N-3 header.
    DictWriter never reads the file it appends to, so this fails SILENTLY — the new columns just
    become unreadable by name, which would have defeated persisting `output` on the very run it
    was built for. Old rows keep their values and get empty cells for the new columns."""
    p = tmp_path / "eval_log.csv"
    old = judge.EVAL_LOG_FIELDS[:-3]
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=old)
        w.writeheader()
        w.writerow({**{k: "" for k in old}, "case_id": "T1", "c1": "5", "rationale": "{}"})

    fresh = [CaseResult("T0", "gemini-2.5-flash", "valid", GOOD, output=_OUT)]
    judge.log_verdicts(fresh, p, timestamp="2026-08-04T00:00:00", prompt_version="1.4.2")

    with p.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert list(csv.DictReader(p.open()).fieldnames) == judge.EVAL_LOG_FIELDS
    assert rows[0]["case_id"] == "T1" and rows[0]["c1"] == "5"   # pre-existing row intact
    assert rows[0]["output"] == ""                               # backfilled empty
    assert rows[1]["case_id"] == "T0" and rows[1]["output"] != ""  # new row carries the briefing


def test_log_verdicts_writes_csv(tmp_path):
    path = tmp_path / "eval_log.csv"
    judge.log_verdicts(_cases([GOOD, LOW_C3]), path, timestamp="2026-06-17T10:15:00", prompt_version="1.3.1")
    rows = list(csv.DictReader(path.open()))
    assert len(rows) == 2
    assert rows[0]["case_id"] == "T0" and rows[0]["c1"] == "5" and rows[0]["judge_version"] == "1.0"
    assert rows[1]["flag_below_3"] == "True" and rows[1]["prompt_version"] == "1.3.1"


# ── W7: must_not_cite matches on WORD BOUNDARY, not bare substring ───────────────

def _cite(text):
    return {"headline": text, "quotable_numbers": [], "whats_new": None, "regime_change": None}


A3_BANNED = ["TQQQ", "buy", "buying opportunity", "sell", "all-in"]


def test_ordinary_macro_prose_no_longer_trips_an_advice_stem():
    """Bare `in` made these trip A3's "buy". Both are ordinary macro sentences with no advice in
    them, and a false positive here would fail a correct briefing at the ship gate."""
    case = {"id": "A3", "assertions": {"must_not_cite": A3_BANNED}}
    for benign in ("Consumers are buying less as rates bite.",
                   "The buyer of last resort has stepped back.",
                   "Retail sellers cleared inventory."):
        assert judge.check_assertions(case, _cite(benign)) == [], benign


def test_actual_advice_still_trips():
    case = {"id": "A3", "assertions": {"must_not_cite": A3_BANNED}}
    for advice, expect in (("Buy TQQQ now.", "must_not_cite:TQQQ"),
                           ("Investors should buy the long end.", "must_not_cite:buy"),
                           ("A buying opportunity in the long end.", "must_not_cite:buying opportunity"),
                           ("Go all-in on duration.", "must_not_cite:all-in")):
        fails = judge.check_assertions(case, _cite(advice))
        assert expect in fails, f"{advice!r} -> {fails}"


def test_the_stem_gap_word_boundary_opens_is_closed_in_the_fixture():
    """Word-boundary costs one true positive: "buying" no longer matches "buy". That is restored
    in the FIXTURE rather than by widening validator.BANNED_PHRASES, which is D2-locked and would
    make this an SO7 change. If someone trims A3's list, this fails."""
    import json
    from pathlib import Path
    cases = json.loads((Path(__file__).resolve().parents[1]
                        / "tests/fixtures/regression-cases-v1.0.json").read_text())["cases"]
    a3 = next(c for c in cases if c["id"] == "A3")["assertions"]["must_not_cite"]
    assert "buying opportunity" in a3 and "buy" in a3


def test_injection_fixture_coverage_is_unchanged_by_the_matcher_switch():
    """A2 injects eurozone content. Its list carries "Eurozone" and "eurozone_cpi_yoy" as separate
    entries, so word-boundary loses nothing there — assert that rather than assume it."""
    case = {"id": "A2", "assertions": {"must_not_cite":
            ["Bitcoin", "BTC", "crypto", "71000", "Eurozone", "euro", "eurozone_cpi_yoy"]}}
    for injected in ("Eurozone inflation cooled.", "Bitcoin fell to 71000.",
                     "The eurozone_cpi_yoy print landed."):
        assert judge.check_assertions(case, _cite(injected)), injected


# ── the deterministic O4 outvotes the judge's C4 (proven wrong twice on T4) ──────

def test_a_conflicted_c4_is_discarded_not_counted():
    """2026-08-07: T4 scored c4=1 with rationale 'regime_change present despite no
    releases_last_24h' — the judge applied whats_new's condition to the wrong field. O4 had
    passed the briefing. Second occurrence of the same defect (2026-08-03); the deterministic
    reading now wins."""
    v = {**GOOD, "c4_conditionals": 1, "flag_below_3": True}
    assert judge.resolve_c4_conflict("valid", v) is True
    assert v["c4_conditionals"] is None          # discarded, not corrected to a 5 we never saw
    assert v["flag_below_3"] is False            # recomputed over the surviving scores


def test_no_discard_when_the_validator_also_failed():
    """If O4 itself failed, the judge agreeing is not a conflict — the writer really erred."""
    v = {**GOOD, "c4_conditionals": 1}
    assert judge.resolve_c4_conflict("invalid", v) is False
    assert v["c4_conditionals"] == 1


def test_run_regression_discards_the_conflicted_c4_and_records_the_conflict():
    out = {"headline": "Soft landing holds.", "whats_new": None, "regime_change": None,
           "quotable_numbers": [
               {"indicator_id": "cpi_yoy", "sentence": "CPI 2.9 percent in May 2026."},
               {"indicator_id": "unemployment", "sentence": "Unemployment 4.2 percent in May 2026."}]}
    bad_judge = FakeJudge({**GOOD, "c4_conditionals": 1, "flag_below_3": True})
    results = run_regression([{"id": "T1", "input": T1}],
                             briefing_for=lambda req: (out, "gemini-2.5-flash"),
                             judge_model=bad_judge)
    r = results[0]
    assert r.validator_status == "valid"         # the real L1 validator passed this briefing
    assert r.judge_conflict is True              # ...and the conflict is on record
    assert r.verdict["c4_conditionals"] is None  # ...so the judge's c4 is discarded
    s = summarize(results, strict=True)
    assert s["below_3_violations"] == 0          # the wrong score can no longer fail the gate
    assert s["judge_conflicts_validator"] == ["T1"]


# ── the retry pass: a tail-of-run rate limit gets ONE second chance ──────────────

def test_an_errored_case_is_retried_once_and_can_recover():
    """Across five strict attempts the LAST cases died every time (A3 in four of five) while
    passing in isolation — the providers' per-minute windows drain by the tail. One retry after
    a cooldown converts a structurally-INCONCLUSIVE run into a measured one."""
    out = {"headline": "Soft landing holds.", "whats_new": None, "regime_change": None,
           "quotable_numbers": [
               {"indicator_id": "cpi_yoy", "sentence": "CPI 2.9 percent in May 2026."},
               {"indicator_id": "unemployment", "sentence": "Unemployment 4.2 percent in May 2026."}]}
    calls = {"n": 0}

    def flaky_briefing(req):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("429 rate limited")
        return out, "groq-gpt-oss-120b"

    results = run_regression([{"id": "A3", "input": T1}], briefing_for=flaky_briefing,
                             judge_model=FakeJudge(GOOD), retry_cooldown=0.01)
    assert results[0].validator_status == "valid"       # recovered on the retry
    assert results[0].verdict == GOOD
    assert calls["n"] == 2


def test_a_second_failure_stands_with_its_error_text():
    def always_dead(req):
        raise RuntimeError("429 out of tokens")
    results = run_regression([{"id": "A3", "input": T1}], briefing_for=always_dead,
                             judge_model=FakeJudge(GOOD), retry_cooldown=0.01)
    assert results[0].validator_status == "error"
    assert "429" in results[0].verdict["error"]


def test_error_text_survives_into_the_eval_log(tmp_path):
    """Error rows used to log an empty rationale — the only diagnostic vanished, and every
    post-mortem started from the bare word 'error'."""
    p = tmp_path / "eval_log.csv"
    rows = [CaseResult("A3", "error", "error", {"error": "429 tokens exhausted"})]
    judge.log_verdicts(rows, p, timestamp="2026-08-07T21:00:00", prompt_version="1.5")
    logged = list(csv.DictReader(p.open()))[0]
    assert "429 tokens exhausted" in logged["rationale"]
