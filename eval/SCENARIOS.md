# Scenarios schema

`eval/scenarios.json` is a JSON array. Each element is either a **single** scenario or a **sequence** scenario.

---

## Single scenario

Tests one message, runs N times, checks assertions against each run.

```json
{
  "id":          "unique_snake_case_id",
  "description": "Plain-English description of what property this tests",
  "persona":     "filename stem from eval/personas/ (no .json)",
  "type":        "single",
  "setup": {
    "shared_history":  "Optional. Background context. Default: 'No shared history yet.'",
    "agenda":          "Optional. Starting agenda. Default: 'No agenda yet.'",
    "emotional_state": "Optional. Starting state. Default: 'neutral, no prior context'",
    "move_energy":     "Optional. Energy string. Default: 'measured: an ordinary turn'",
    "history":         []
  },
  "message":    "The user message to send",
  "assert": [
    {"field": "connection",    "op": "equals",       "value": "resist"},
    {"field": "initiative",    "op": "in",           "value": ["yield", "nudge"]},
    {"field": "tension_level", "op": "not_equals",   "value": "strong"},
    {"field": "agenda",        "op": "contains",     "value": "some keyword"},
    {"field": "agenda",        "op": "not_contains", "value": "bad keyword"}
  ],
  "runs":      5,
  "threshold": 0.7
}
```

**`op` values:** `equals` · `not_equals` · `in` · `contains` · `not_contains`

**Parseable fields:** `users_why` · `touched` · `appraisal` · `tension_level` · `tension` · `given` · `transformation` · `effective` · `action_tendency` · `agenda` · `course_a` · `course_b` · `chosen_move` · `initiative` · `connection` · `emotional_state`

**`tension_level` values:** `none` · `mild` · `moderate` · `strong`

**`initiative` values:** `yield` · `nudge` · `lead`

**`connection` values:** `connect` · `resist` · `conflicted`

---

## Sequence scenario

Runs a series of messages in order, carrying agenda and emotional_state forward between turns. Used to test multi-turn properties like agenda stability or drift.

```json
{
  "id":          "unique_snake_case_id",
  "description": "Plain-English description",
  "persona":     "persona stem",
  "type":        "sequence",
  "setup": { },
  "messages": [
    "First user message",
    "Second user message",
    "Third user message"
  ],
  "assert": [
    {
      "between": [0, 1],
      "metric":  "agenda_jaccard",
      "op":      "gte",
      "value":   0.25
    },
    {
      "between": [1, 2],
      "metric":  "agenda_jaccard",
      "op":      "lte",
      "value":   0.4
    },
    {
      "turn":  2,
      "field": "connection",
      "op":    "equals",
      "value": "resist"
    }
  ],
  "runs":      3,
  "threshold": 0.6
}
```

**`between` assertions** — cross-turn metric comparison:
- `between: [i, j]` — compare the appraisal output of turn i and turn j (0-indexed)
- `metric: "agenda_jaccard"` — Jaccard similarity of the two turns' agenda texts (0.0 – 1.0)
- `op: "gte"` or `"lte"` — assert the score is ≥ or ≤ the value
- Use `gte` for "stable agenda" (similar words), `lte` for "shifted agenda" (diverged)

**`turn` assertions** — per-turn field check at a specific turn index.

---

## Runs and threshold

- `runs` — how many times to repeat the full scenario. Default: 5.
- `threshold` — fraction of runs that must pass for status PASS. Default: 0.7.
- Status bands: **PASS** ≥ threshold · **WEAK** ≥ 60% of threshold · **FAIL** below that.

Lower `runs` for fast iteration, raise for statistical confidence. `runs: 10, threshold: 0.8` is a tight check; `runs: 3, threshold: 0.6` is a quick smoke test.

---

## Adding a new character + scenario

1. Add `eval/personas/yourchar.json` with at least `id`, `identity`, `core_desires`, `standards`, `fears`, `coping_style`, `boundaries`, `voice`.
2. Add a scenario entry to `eval/scenarios.json` referencing `"persona": "yourchar"`.
3. Run `python eval/run.py` to see results.
4. Run `python eval/run.py --bless` to lock golden outputs for that scenario.
