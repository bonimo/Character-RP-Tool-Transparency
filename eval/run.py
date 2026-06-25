"""
Character Tool — Evaluation Harness
====================================
python eval/run.py                  # property suite + logic tests
python eval/run.py --logic          # deterministic tests only (no model)
python eval/run.py --ablations      # property suite + ablation grid + logic
python eval/run.py --bless          # save golden outputs at temp 0
"""

import sys, json, re, argparse, datetime
from pathlib import Path
import ollama

EVAL_DIR = Path(__file__).resolve().parent
ROOT_DIR = EVAL_DIR.parent
sys.path.insert(0, str(EVAL_DIR))
from parser import parse_appraisal

# ── Config ────────────────────────────────────────────────────────────────────
CONFIG_PATH = ROOT_DIR / "config.json"
if not CONFIG_PATH.exists():
    print(f"ERROR: config.json not found at {CONFIG_PATH}", file=sys.stderr)
    sys.exit(1)

CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
APPRAISAL_PROMPT = CONFIG["prompts"]["appraisal"]
MODEL = CONFIG.get("thought_model") or CONFIG.get("model") or ""
if not MODEL:
    print("ERROR: no model tag in config.json (expected thought_model or model)", file=sys.stderr)
    sys.exit(1)

TEMP = 0.2

PERSONA_FIELDS = [
    "identity", "core_desires", "standards", "fears", "coping_style",
    "beliefs_about_others", "self_beliefs", "tastes", "relational_stance",
    "internal_tensions", "temperament", "voice", "boundaries",
]

# ── Rendering ─────────────────────────────────────────────────────────────────
def render(t, **v):
    for k, val in v.items():
        t = t.replace("{" + k + "}", str(val))
    return t

def persona_text(p):
    lines = []
    for f in PERSONA_FIELDS:
        v = p.get(f)
        if v:
            lines.append(f"{f.replace('_', ' ')}: {v}")
    return "\n".join(lines)

def fmt_history(h):
    if not h:
        return ""
    return "\n".join(f"{m['role'].upper()}: {m['content']}" for m in h)

# ── Model call ────────────────────────────────────────────────────────────────
def call_appraisal(prompt, persona, setup, message, temperature=TEMP):
    full = render(
        prompt,
        persona=persona_text(persona),
        shared_history=setup.get("shared_history", "No shared history yet."),
        agenda=setup.get("agenda", "No agenda yet; this is the start of the conversation."),
        emotional_state=setup.get("emotional_state", "neutral, no prior context"),
        move_energy=setup.get("move_energy", "measured: an ordinary turn, a modest purposeful move"),
        inspiration=setup.get("inspiration", ""),
        history=fmt_history(setup.get("history", [])),
        user_message=message,
    )
    resp = ollama.generate(
        model=MODEL,
        prompt=full,
        options={"temperature": temperature, "num_ctx": 8192},
    )
    return parse_appraisal(resp["response"])

# ── Assertions ────────────────────────────────────────────────────────────────
def jaccard(a, b):
    sa = set(re.findall(r"\w+", a.lower()))
    sb = set(re.findall(r"\w+", b.lower()))
    if not (sa or sb):
        return 1.0
    return len(sa & sb) / len(sa | sb)

def check(parsed, a):
    op     = a.get("op", "equals")
    val    = a.get("value")
    actual = parsed.get(a["field"], "")
    if op == "equals":       return actual == val
    if op == "not_equals":   return actual != val
    if op == "in":           return actual in val
    if op == "contains":     return str(val).lower() in str(actual).lower()
    if op == "not_contains": return str(val).lower() not in str(actual).lower()
    return False

def status(rate, thr):
    if rate >= thr:          return "PASS"
    if rate >= thr * 0.6:   return "WEAK"
    return "FAIL"

# ── Single scenario ───────────────────────────────────────────────────────────
def run_single(sc, personas, prompt, setup_overrides=None):
    persona    = personas[sc["persona"]]
    setup      = {**sc.get("setup", {}), **(setup_overrides or {})}
    message    = sc["message"]
    assertions = sc["assert"]
    runs       = sc.get("runs", 5)
    threshold  = sc.get("threshold", 0.7)

    passes = [0] * len(assertions)
    for _ in range(runs):
        try:
            parsed = call_appraisal(prompt, persona, setup, message)
        except Exception as e:
            print(f"    [model error] {e}", file=sys.stderr)
            continue
        for i, a in enumerate(assertions):
            if check(parsed, a):
                passes[i] += 1

    return passes, runs, threshold, assertions

# ── Sequence scenario ─────────────────────────────────────────────────────────
def run_sequence(sc, personas, prompt, setup_overrides=None):
    persona    = personas[sc["persona"]]
    base_setup = {**sc.get("setup", {}), **(setup_overrides or {})}
    messages   = sc["messages"]
    assertions = sc["assert"]
    runs       = sc.get("runs", 5)
    threshold  = sc.get("threshold", 0.7)

    passes = [0] * len(assertions)

    for _ in range(runs):
        setup  = dict(base_setup)
        turns  = []  # parsed output per turn
        for msg in messages:
            try:
                parsed = call_appraisal(prompt, persona, setup, msg)
            except Exception as e:
                print(f"    [model error] {e}", file=sys.stderr)
                parsed = {}
            turns.append(parsed)
            if parsed.get("agenda"):
                setup["agenda"] = parsed["agenda"]
            if parsed.get("emotional_state"):
                setup["emotional_state"] = parsed["emotional_state"]

        for i, a in enumerate(assertions):
            if "between" in a:
                ti, tj = a["between"]
                if ti < len(turns) and tj < len(turns):
                    if a.get("metric") == "agenda_jaccard":
                        score = jaccard(
                            turns[ti].get("agenda", ""),
                            turns[tj].get("agenda", ""),
                        )
                        op = a.get("op", "gte")
                        thr_val = a.get("value", 0.25)
                        passed = (score >= thr_val) if op == "gte" else (score <= thr_val)
                        if passed:
                            passes[i] += 1
            elif "turn" in a:
                idx = a["turn"]
                if idx < len(turns) and check(turns[idx], a):
                    passes[i] += 1
            else:
                if turns and check(turns[-1], a):
                    passes[i] += 1

    return passes, runs, threshold, assertions

# ── Suite runner ──────────────────────────────────────────────────────────────
def run_suite(scenarios, personas, prompt, label="baseline", setup_overrides=None):
    rows = []
    for sc in scenarios:
        sc_id = sc["id"]
        sc_type = sc.get("type", "single")
        print(f"  {sc_id} ({sc_type}) ...", end="", flush=True)

        if sc_type == "sequence":
            passes, runs, thr, assertions = run_sequence(
                sc, personas, prompt, setup_overrides)
        else:
            passes, runs, thr, assertions = run_single(
                sc, personas, prompt, setup_overrides)

        for i, a in enumerate(assertions):
            rate = passes[i] / runs if runs else 0
            st   = status(rate, thr)
            if "between" in a:
                desc = f"agenda_jaccard[{a['between'][0]}<>{a['between'][1]}] {a.get('op')} {a.get('value')}"
            else:
                desc = f"{a.get('field','?')} {a.get('op','?')} {str(a.get('value','?'))[:20]}"
            rows.append({
                "scenario": sc_id,
                "assert":   desc,
                "passed":   passes[i],
                "runs":     runs,
                "rate":     rate,
                "threshold":thr,
                "status":   st,
                "ablation": label,
            })

        statuses = [r["status"] for r in rows if r["scenario"] == sc_id]
        print(f" {', '.join(statuses)}")
    return rows

# ── Ablations ─────────────────────────────────────────────────────────────────
def remove_lines(prompt, labels):
    out = []
    for line in prompt.splitlines():
        if any(line.strip().upper().startswith(lbl.upper()) for lbl in labels):
            continue
        out.append(line)
    return "\n".join(out)

ABLATIONS = {
    "baseline":           lambda p: p,
    "no_tension":         lambda p: remove_lines(p, ["TENSION LEVEL:", "TENSION:"]),
    "no_transformation":  lambda p: remove_lines(p, ["GIVEN:", "TRANSFORMATION:", "EFFECTIVE:"]),
    "no_action_tendency": lambda p: remove_lines(p, ["ACTION TENDENCY:"]),
    "no_agenda":          lambda p: remove_lines(p, ["AGENDA:"]),
    "no_initiative":      lambda p: remove_lines(p, ["INITIATIVE:"]),
    "no_inspiration":     lambda p: p,
}

ABLATION_SETUP_OVERRIDES = {
    "no_agenda":      {"agenda": ""},
    "no_inspiration": {"inspiration": ""},
}

# ── Golden diff ───────────────────────────────────────────────────────────────
def run_golden_diff(scenarios, personas, prompt):
    golden_dir = EVAL_DIR / "golden"
    drifts = []
    for sc in scenarios:
        if sc.get("type", "single") != "single":
            continue
        gf = golden_dir / f"{sc['id']}.json"
        if not gf.exists():
            continue
        blessed = json.loads(gf.read_text(encoding="utf-8"))
        persona = personas[sc["persona"]]
        setup   = dict(sc.get("setup", {}))
        try:
            current = call_appraisal(prompt, persona, setup, sc["message"], temperature=0.0)
        except Exception as e:
            drifts.append({"scenario": sc["id"], "field": "ERROR", "error": str(e)})
            continue
        for field, bval in blessed.items():
            cval = current.get(field, "")
            if jaccard(str(bval), str(cval)) < 0.5:
                drifts.append({
                    "scenario": sc["id"], "field": field,
                    "blessed": bval, "current": cval,
                })
    return drifts

# ── Report writer ─────────────────────────────────────────────────────────────
def write_report(model, logic_results, prop_rows, ablation_data, golden_drifts):
    now  = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# Eval Report",
        "",
        f"**Model:** `{model}`  ",
        f"**Date:** {now}",
        "",
    ]

    if logic_results is not None:
        passed = sum(1 for r in logic_results if r["passed"])
        lines += [
            "## Logic Tests",
            "",
            f"{passed}/{len(logic_results)} passed",
            "",
            "| Test | Result | Detail |",
            "|------|--------|--------|",
        ]
        for r in logic_results:
            lines.append(f"| {r['name']} | {'PASS' if r['passed'] else 'FAIL'} | {r.get('detail','')} |")
        lines.append("")

    if prop_rows:
        lines += [
            "## Property Suite",
            "",
            "| Scenario | Assertion | Rate | Status |",
            "|----------|-----------|------|--------|",
        ]
        for r in prop_rows:
            rate_str = f"{r['passed']}/{r['runs']} ({r['rate']:.0%})"
            lines.append(f"| {r['scenario']} | {r['assert']} | {rate_str} | {r['status']} |")
        lines.append("")

    if ablation_data:
        abl_names = list(ABLATIONS.keys())
        all_props  = list({(r["scenario"], r["assert"]): None for r in ablation_data["baseline"]})

        lines += [
            "## Ablation Grid",
            "",
            "Rows = properties · Columns = ablations · Cells = pass rate.  ",
            "A large drop under an ablation means that mechanism drives the property.",
            "",
        ]
        header = "| Property | " + " | ".join(abl_names) + " |"
        sep    = "|" + "---|" * (len(abl_names) + 1)
        lines += [header, sep]
        for (sc_id, assert_desc) in all_props:
            cell_vals = []
            for abl in abl_names:
                row = next((r for r in ablation_data.get(abl, [])
                            if r["scenario"] == sc_id and r["assert"] == assert_desc), None)
                cell_vals.append(f"{row['rate']:.0%}" if row else "—")
            lines.append(f"| {sc_id} / {assert_desc} | " + " | ".join(cell_vals) + " |")
        lines.append("")

        lines += [
            "### Interpretation guide",
            "",
            "- **High baseline → low under ablation**: property is causally driven by that mechanism.",
            "- **Stable across all ablations**: property is robust (or no single mechanism carries it).",
            "- **Low everywhere including baseline**: property may need a stronger prompt signal.",
            "",
        ]

    if golden_drifts:
        lines += ["## Golden Drift", ""]
        for d in golden_drifts:
            if "error" in d:
                lines.append(f"- **{d['scenario']}**: model error — {d['error']}")
            else:
                lines.append(f"- **{d['scenario']}.{d['field']}**: was `{d['blessed']}` → now `{d['current']}`")
        lines.append("")
    elif golden_drifts is not None:
        lines += ["## Golden Diff", "", "No drift detected.", ""]

    (EVAL_DIR / "report.md").write_text("\n".join(lines), encoding="utf-8")

# ── Print helpers ─────────────────────────────────────────────────────────────
def print_table(rows):
    W_SC, W_AS, W_RT, W_ST = 32, 44, 10, 6
    print(f"\n{'Scenario':<{W_SC}} {'Assertion':<{W_AS}} {'Rate':<{W_RT}} Status")
    print("-" * (W_SC + W_AS + W_RT + W_ST + 3))
    for r in rows:
        rate_str = f"{r['passed']}/{r['runs']}={r['rate']:.0%}"
        print(f"{r['scenario']:<{W_SC}} {r['assert']:<{W_AS}} {rate_str:<{W_RT}} {r['status']}")

def print_ablation_grid(ablation_data):
    abl_names = list(ABLATIONS.keys())
    all_props  = list({(r["scenario"], r["assert"]): None
                       for r in ablation_data["baseline"]})
    col_w = 9
    prop_w = 56
    print(f"\n{'Property':<{prop_w}}", end="")
    for a in abl_names:
        print(f" {a[:col_w-1]:<{col_w}}", end="")
    print()
    print("-" * (prop_w + col_w * len(abl_names) + 1))
    for (sc_id, assert_desc) in all_props:
        label = f"{sc_id} / {assert_desc}"[:prop_w - 1]
        print(f"{label:<{prop_w}}", end="")
        for abl in abl_names:
            row = next((r for r in ablation_data.get(abl, [])
                        if r["scenario"] == sc_id and r["assert"] == assert_desc), None)
            cell = f"{row['rate']:.0%}" if row else "—"
            print(f" {cell:<{col_w}}", end="")
        print()

# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Character Tool eval harness")
    ap.add_argument("--logic",     action="store_true", help="Logic tests only (no model)")
    ap.add_argument("--ablations", action="store_true", help="Add ablation grid to the run")
    ap.add_argument("--bless",     action="store_true", help="Save golden outputs at temp 0")
    args = ap.parse_args()

    # ── Load personas and scenarios ───────────────────────────────────────────
    personas = {}
    for pf in (EVAL_DIR / "personas").glob("*.json"):
        p = json.loads(pf.read_text(encoding="utf-8"))
        personas[pf.stem] = p
    print(f"Personas loaded: {', '.join(sorted(personas))}")

    scenarios_path = EVAL_DIR / "scenarios.json"
    scenarios = json.loads(scenarios_path.read_text(encoding="utf-8"))
    print(f"Scenarios loaded: {len(scenarios)}")
    print(f"Model: {MODEL}\n")

    logic_results  = None
    prop_rows      = None
    ablation_data  = None
    golden_drifts  = None

    # ── Logic tests ───────────────────────────────────────────────────────────
    run_logic = args.logic or (not args.bless)  # always run unless --bless only
    if run_logic:
        print("=== LOGIC TESTS ===")
        from logic_tests import run_logic_tests
        logic_results = run_logic_tests()
        passed = sum(1 for r in logic_results if r["passed"])
        for r in logic_results:
            sym = "OK  " if r["passed"] else "FAIL"
            print(f"  [{sym}] {r['name']}: {r['detail']}")
        print(f"\nLogic: {passed}/{len(logic_results)} passed")

    if args.logic:
        write_report(MODEL, logic_results, None, None, None)
        print("\nReport -> eval/report.md")
        return

    # ── Bless mode ────────────────────────────────────────────────────────────
    if args.bless:
        print("\n=== BLESSING GOLDEN OUTPUTS ===")
        golden_dir = EVAL_DIR / "golden"
        golden_dir.mkdir(exist_ok=True)
        for sc in scenarios:
            if sc.get("type", "single") != "single":
                continue
            persona = personas[sc["persona"]]
            setup   = dict(sc.get("setup", {}))
            try:
                parsed = call_appraisal(APPRAISAL_PROMPT, persona, setup, sc["message"], temperature=0.0)
                out_path = golden_dir / f"{sc['id']}.json"
                out_path.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
                print(f"  [OK  ] {sc['id']}")
            except Exception as e:
                print(f"  [FAIL] {sc['id']}: {e}")
        print("Done. Run without --bless to check for drift.")
        return

    # ── Property suite ────────────────────────────────────────────────────────
    print("\n=== PROPERTY SUITE ===")
    prop_rows = run_suite(scenarios, personas, APPRAISAL_PROMPT)
    print_table(prop_rows)

    # ── Golden diff ───────────────────────────────────────────────────────────
    if any((EVAL_DIR / "golden" / f"{sc['id']}.json").exists()
           for sc in scenarios if sc.get("type","single") == "single"):
        print("\n=== GOLDEN DIFF ===")
        golden_drifts = run_golden_diff(scenarios, personas, APPRAISAL_PROMPT)
        if golden_drifts:
            print(f"  [WARN] {len(golden_drifts)} drift(s) detected:")
            for d in golden_drifts:
                if "error" in d:
                    print(f"    {d['scenario']}: ERROR - {d['error']}")
                else:
                    print(f"    {d['scenario']}.{d['field']}: '{d['blessed']}' -> '{d['current']}'")
        else:
            print("  [OK  ] No drift.")
    else:
        golden_drifts = []

    # ── Ablations ─────────────────────────────────────────────────────────────
    if args.ablations:
        print(f"\n=== ABLATION GRID ({len(ABLATIONS)} variants x {len(scenarios)} scenarios) ===")
        ablation_data = {}
        for abl_name, abl_fn in ABLATIONS.items():
            ablated   = abl_fn(APPRAISAL_PROMPT)
            overrides = ABLATION_SETUP_OVERRIDES.get(abl_name, {})
            print(f"\n  [{abl_name}]")
            ablation_data[abl_name] = run_suite(
                scenarios, personas, ablated,
                label=abl_name, setup_overrides=overrides)
        print_ablation_grid(ablation_data)

    # ── Report ────────────────────────────────────────────────────────────────
    write_report(MODEL, logic_results, prop_rows, ablation_data, golden_drifts)
    print("\nReport -> eval/report.md")

if __name__ == "__main__":
    main()
