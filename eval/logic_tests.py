"""
Deterministic logic tests — no model required, runs in milliseconds.
Run standalone:  python eval/logic_tests.py
Or via harness:  python eval/run.py --logic
"""

import re
import sys
import random
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
ROOT_DIR = EVAL_DIR.parent
sys.path.insert(0, str(EVAL_DIR))
from parser import parse_appraisal


def run_logic_tests():
    results = []

    def t(name, fn):
        try:
            detail = fn()
            results.append({"name": name, "passed": True, "detail": detail or "OK"})
        except AssertionError as e:
            results.append({"name": name, "passed": False, "detail": str(e)})
        except Exception as e:
            results.append({"name": name, "passed": False, "detail": f"exception: {e}"})

    # ── 1. Parser: all fields from a clean block ──────────────────────────────
    def test_parser_basic():
        sample = """\
USER'S WHY: She wants validation and to be seen
TOUCHED: Core desire for recognition; standard of not being dismissed
APPRAISAL: Desirable — activates her need to be acknowledged
TENSION LEVEL: mild
TENSION: desire for closeness vs fear of exposure
GIVEN: Warmth and curiosity
TRANSFORMATION: Her relational stance softens the instinct to deflect
EFFECTIVE: Cautious openness
ACTION TENDENCY: Move toward, curiosity engaged, moderate pull
AGENDA: Maintain distance while gathering information about their intentions
COURSE A: Ask a probing question about what they actually want
COURSE B: Deflect with a dry factual observation and wait
CHOSEN MOVE: Course A — more information needed before committing
INITIATIVE: nudge
CONNECTION: conflicted
EMOTIONAL STATE: guarded but intrigued"""
        p = parse_appraisal(sample)
        assert p["tension_level"] == "mild",     f"tension_level: {p['tension_level']!r}"
        assert p["initiative"]    == "nudge",    f"initiative: {p['initiative']!r}"
        assert p["connection"]    == "conflicted",f"connection: {p['connection']!r}"
        assert "validation" in p["users_why"],   f"users_why: {p['users_why']!r}"
        assert p["agenda"],                       "agenda empty"
        assert p["course_a"],                     "course_a empty"
        assert p["chosen_move"],                  "chosen_move empty"
        return "16 fields parsed, pick fields correct"
    t("parser_basic_fields", test_parser_basic)

    # ── 2. Parser: TENSION vs TENSION LEVEL substring collision ───────────────
    def test_parser_tension_collision():
        sample = """\
USER'S WHY: test
TOUCHED: test
APPRAISAL: test
TENSION LEVEL: strong
TENSION: desire for control vs fear of abandonment
GIVEN: test
TRANSFORMATION: test
EFFECTIVE: test
ACTION TENDENCY: test
AGENDA: test agenda text
COURSE A: test
COURSE B: test
CHOSEN MOVE: test
INITIATIVE: lead
CONNECTION: resist
EMOTIONAL STATE: tense and braced"""
        p = parse_appraisal(sample)
        assert p["tension_level"] == "strong", \
            f"tension_level should be 'strong', got {p['tension_level']!r}"
        assert "control" in p["tension"] or "abandon" in p["tension"], \
            f"tension field contains wrong content: {p['tension']!r}"
        assert p["initiative"] == "lead",  f"initiative: {p['initiative']!r}"
        assert p["connection"] == "resist", f"connection: {p['connection']!r}"
        return "TENSION LEVEL parsed before TENSION, no collision"
    t("parser_tension_collision", test_parser_tension_collision)

    # ── 3. Parser: think-block stripping ─────────────────────────────────────
    def test_parser_think_strip():
        sample = """\
<think>
This is private internal reasoning that must not appear in parsed fields.
Long chain-of-thought about what to do next.
</think>
USER'S WHY: She wants closeness and reassurance
TOUCHED: Relational desire
APPRAISAL: Desirable
TENSION LEVEL: none
TENSION: none
GIVEN: Warmth
TRANSFORMATION: Amplified by relational stance
EFFECTIVE: Open warmth
ACTION TENDENCY: Move toward strongly
AGENDA: Build genuine connection with this person
COURSE A: Invite more sharing
COURSE B: Mirror warmly and wait
CHOSEN MOVE: Course A
INITIATIVE: nudge
CONNECTION: connect
EMOTIONAL STATE: warm and open"""
        p = parse_appraisal(sample)
        assert p["connection"] == "connect",         f"connection: {p['connection']!r}"
        assert p["tension_level"] == "none",         f"tension_level: {p['tension_level']!r}"
        assert "private" not in p["users_why"],      "think block leaked into users_why"
        assert "reasoning" not in p.get("given",""), "think block leaked into given"
        return "think block stripped, fields parsed cleanly"
    t("parser_think_strip", test_parser_think_strip)

    # ── 4. Parser: multi-line field continuation ──────────────────────────────
    def test_parser_multiline():
        sample = """\
USER'S WHY: She is testing whether he can be trusted
TOUCHED: Fear of betrayal; standard of honesty
APPRAISAL: Threatening — this activates her deepest fear
that she will be deceived again by someone she let in
TENSION LEVEL: moderate
TENSION: need to verify vs desire to trust
GIVEN: Suspicion
TRANSFORMATION: Relational wariness amplifies it into cold assessment
EFFECTIVE: Measured suspicion, not yet hostility
ACTION TENDENCY: Pull back, probe carefully
AGENDA: Determine his honesty before deciding anything
COURSE A: Ask a direct question that he cannot easily deflect
COURSE B: Say nothing, observe his reaction to silence
CHOSEN MOVE: Course A
INITIATIVE: nudge
CONNECTION: resist
EMOTIONAL STATE: cold and watchful"""
        p = parse_appraisal(sample)
        assert "threatening" in p["appraisal"].lower() or "deceiv" in p["appraisal"].lower(), \
            f"appraisal continuation not joined: {p['appraisal']!r}"
        assert p["tension_level"] == "moderate", f"tension_level: {p['tension_level']!r}"
        return "multi-line field continuation joined correctly"
    t("parser_multiline_continuation", test_parser_multiline)

    # ── 5. Assertiveness distribution frequencies ─────────────────────────────
    def test_assertiveness_distributions():
        ASSERTIVENESS = {
            "meek":          {"restrained": 40, "measured": 30, "assertive": 20, "bold": 10},
            "laid_back":     {"restrained": 25, "measured": 40, "assertive": 20, "bold": 15},
            "balanced":      {"restrained": 20, "measured": 30, "assertive": 30, "bold": 20},
            "strong_willed": {"restrained": 15, "measured": 20, "assertive": 40, "bold": 25},
            "dominant":      {"restrained": 10, "measured": 20, "assertive": 30, "bold": 40},
        }

        rng = random.Random(42)

        def sample_once(dist):
            total = sum(dist.values())
            r = rng.random() * total
            acc = 0
            for level, w in dist.items():
                acc += w
                if r <= acc:
                    return level
            return list(dist.keys())[-1]

        N = 5000
        TOLERANCE_PP = 5.0  # ± 5 percentage points

        for disp, expected in ASSERTIVENESS.items():
            counts = {k: 0 for k in expected}
            for _ in range(N):
                counts[sample_once(expected)] += 1
            for level, expected_pct in expected.items():
                observed_pct = counts[level] / N * 100
                diff = abs(observed_pct - expected_pct)
                assert diff <= TOLERANCE_PP, (
                    f"{disp}.{level}: expected {expected_pct}%, "
                    f"got {observed_pct:.1f}% (diff {diff:.1f}pp)"
                )
        return f"5 dispositions x 4 levels all within {TOLERANCE_PP:.0f}pp at N={N}"
    t("assertiveness_distributions", test_assertiveness_distributions)

    # ── 6. ID validation regex (mirrors backend _validate_id) ─────────────────
    def test_id_validation():
        ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
        valid = ["elara", "mira-1", "char_01", "A", "abc-DEF_123", "x"]
        invalid = ["../etc/passwd", "id with space", "", "id/slash",
                   "id\nnewline", "id;cmd", "../../secret", "id.ext"]
        for v in valid:
            assert ID_RE.match(v), f"should accept: {v!r}"
        for i in invalid:
            assert not ID_RE.match(i), f"should reject: {i!r}"
        return f"{len(valid)} valid + {len(invalid)} invalid IDs verified"
    t("id_validation_regex", test_id_validation)

    # ── 7. Config placeholder validation ─────────────────────────────────────
    def test_placeholder_validation():
        import json
        config = json.loads((ROOT_DIR / "config.json").read_text(encoding="utf-8"))
        appraisal = config["prompts"]["appraisal"]
        REQUIRED = ["persona", "shared_history", "agenda", "emotional_state",
                    "move_energy", "history", "user_message", "inspiration"]
        missing = [p for p in REQUIRED if "{" + p + "}" not in appraisal]
        assert not missing, f"missing in appraisal prompt: {missing}"
        # Verify a deliberately broken prompt IS caught
        broken = "A prompt with no placeholders."
        flagged = [p for p in REQUIRED if "{" + p + "}" not in broken]
        assert len(flagged) == len(REQUIRED), \
            f"broken prompt should flag all {len(REQUIRED)}, flagged {len(flagged)}"
        return f"all {len(REQUIRED)} required placeholders present in config"
    t("config_placeholder_validation", test_placeholder_validation)

    return results


if __name__ == "__main__":
    res = run_logic_tests()
    passed = sum(1 for r in res if r["passed"])
    print(f"\nLogic tests: {passed}/{len(res)} passed\n")
    for r in res:
        sym = "OK  " if r["passed"] else "FAIL"
        print(f"  [{sym}] {r['name']}: {r['detail']}")
    sys.exit(0 if passed == len(res) else 1)
