# Architecture

## The two-pass pipeline

Every exchange runs two separate model calls.

**Pass 1: appraisal** (`POST /api/appraise`). The backend receives the user's message, persona, conversation history, scene-fact ledger, current agenda, emotional state, per-turn energy level, and scene objective. It renders these into the appraisal prompt and streams the response back as plain text. The frontend parses this into labeled fields (see "Appraisal output" below), stores the result as the `intent` object, and displays it in the inner-state panel.

**Pass 2: voice** (`POST /api/respond`). The backend receives the same message and history, but uses the voice prompt instead of the appraisal prompt. Rather than the full persona, the voice pass receives a condensed `voice_block` (identity, voice style, temperament, boundaries) plus the `intent` object from pass 1. The model writes the in-character reply conditioned on what the appraisal concluded.

The two passes are separate because the tasks are different. The appraisal pass is analytical: structured labeled output at a lower temperature (default 0.5). The voice pass is generative: free prose at a higher temperature (default 0.8). They can use different models and different providers.

## Theoretical grounding

The appraisal prompt draws on three frameworks, used practically rather than academically.

**Appraisal theory** from emotion research holds that emotion is not a direct response to an event but to the evaluation of that event against the person's goals, standards, and coping capacity. The prompt makes this explicit: `TOUCHED` identifies which desire or fear is activated; `APPRAISAL` rates the event as desirable or threatening; `TENSION LEVEL` captures how strongly two drives conflict at this moment.

**Belief-desire-intention (BDI) agent models** treat intentional agents as having beliefs about the world, desires as goals, and intentions as committed plans. The appraisal output maps directly onto this: the character's desires are the persona's `core_desires`; the `AGENDA` is the current intention toward the scene objective; `COURSE A` and `COURSE B` are candidate moves; `CHOSEN MOVE` is the committed action.

**Interdependence theory** from social psychology describes how a self-interested reaction (what you would do if only your own interests mattered) is transformed by the relationship. The `GIVEN` field captures the raw self-interested reaction; `TRANSFORMATION` captures how the relational stance toward this particular person reweights it; `EFFECTIVE` captures what gets acted on as a result.

## Appraisal output

The appraisal prompt produces exactly 16 labeled fields. The frontend parser (`eval/parser.py`) extracts them by matching label prefixes in order of label length, longest first, to avoid substring collisions (for example, "TENSION LEVEL" is matched before "TENSION"). The parser also strips `<think>` blocks emitted by reasoning models before parsing.

The fields, grouped by function:

- **Perception:** `USER'S WHY`, `TOUCHED`
- **Appraisal:** `APPRAISAL`, `TENSION LEVEL`, `TENSION`
- **Reaction:** `GIVEN`, `TRANSFORMATION`, `EFFECTIVE`
- **Plan:** `ACTION TENDENCY`, `AGENDA`, `COURSE A`, `COURSE B`, `CHOSEN MOVE`, `PULL BACK`
- **Status:** `OBJECTIVE STATUS`, `INITIATIVE`, `CONNECTION`, `EMOTIONAL STATE`

## The persona system

A persona is a JSON file in `personas/`. The full schema:

| Field | Purpose |
|---|---|
| `identity` | Who the character is |
| `core_desires` | What they deeply want and why |
| `standards` | What they believe is right; how they judge others |
| `fears` | What they anxiously avoid and the reason beneath it |
| `coping_style` | What they do when blocked or threatened |
| `beliefs_about_others` | Their assumptions about people (may be wrong) |
| `self_beliefs` | How they see themselves (may be inaccurate) |
| `tastes` | Specific likes and dislikes |
| `relational_stance` | What they seek from closeness with others |
| `internal_tensions` | Two drives that pull against each other |
| `temperament` | Their resting emotional tone |
| `voice` | How they actually speak (pace, word choice, habits) |
| `boundaries` | Hard lines they will not cross |
| `assertiveness` | One of: `meek`, `laid_back`, `balanced`, `strong_willed`, `dominant` |

The appraisal prompt receives most of these fields via `persona_text()`. The voice prompt receives only the condensed `voice_block`: identity, voice style, temperament, and boundaries. This keeps reasoning and speech cleanly separated.

The `assertiveness` field is not sent to the model. It controls the energy roll (see below).

Personas can be authored by hand in the persona editor, or imported by pasting a free-text description. The import endpoint (`POST /api/import`) calls the thought model with a structured extraction prompt and returns a JSON object with the canonical fields. The import prompt is editable in Settings.

## The scene and objective system

Scene setup draws from `seeds.json`, which supplies lists of locations, times of day, weather conditions, moods, and situations. The user can pick seeds manually or roll randomly (`GET /api/scene/roll`). Scenes also have a genre: adventure, romance, slice of life, or professional.

Objectives are generated by the thought model via `POST /api/objectives/generate`. The objective prompt enforces three rules: the objective must target the other person (not a state of the world), it must name a real obstacle, and it must be rooted in one of the character's core desires. The model returns three candidates in JSON; the user picks one. An objective has three parts: the objective itself, its obstacle, and the desire it serves.

Once a scene is configured, `POST /api/scene/open` calls the voice model to write the character's opening message, conditioned on the scene seeds and the chosen objective.

Each turn's appraisal output includes `OBJECTIVE STATUS` (one of: pursuing, advanced, stalled, achieved, blocked). The inner-state panel shows the current status and the objective text together.

## The resistance governor

The appraisal prompt contains two mechanisms that enforce realistic resistance to change.

First, a standing paragraph at the top of the prompt states that the character's deep desires, fears, and self-protective patterns are slow-moving bedrock built over a lifetime. They do not dissolve in a single conversation, however kind or persuasive the other person is. Change within a scene is provisional and small, never a breakthrough.

Second, `PULL BACK` is a required output field that must be filled before `CHOSEN MOVE`. It asks: what recoil, deflection, or retreat does this character's pattern still produce here, even as they may be drawn forward? `CHOSEN MOVE` must honor what `PULL BACK` surfaces. The voice pass receives both the pull-back and the chosen move as part of `intent`, so the resistance shapes how the character speaks, not just what they plan.

## The scene-fact ledger

After each committed turn, the frontend calls `POST /api/scene/facts`, sending the last user message, the character's reply, and the current ledger. The thought model reads the exchange and returns two lists: `new_facts` (things newly established as true) and `updated_facts` (corrections to existing facts, each carrying a `replaces` and a `with` field).

The backend merges these into the ledger using token-overlap deduplication (`_merge_scene_facts`): a new fact is appended only if no existing fact exceeds 0.6 Jaccard similarity with it; a correction replaces the best-matching existing fact if the match exceeds 0.4. The ledger is capped at 40 facts.

On subsequent turns, the current ledger is passed to both the appraisal and voice prompts in the `{scene_facts}` slot.

## The energy and assertiveness system

Before each turn is sent, the frontend rolls a random energy level weighted by the persona's `assertiveness` disposition:

| Disposition | restrained | measured | assertive | bold |
|---|---|---|---|---|
| meek | 40% | 30% | 20% | 10% |
| laid_back | 25% | 40% | 20% | 15% |
| balanced | 20% | 30% | 30% | 20% |
| strong_willed | 15% | 20% | 40% | 25% |
| dominant | 10% | 20% | 30% | 40% |

The rolled level and a short description are passed to the appraisal prompt as `{move_energy}`. "Restrained" produces a quiet, low-key turn; "bold" produces a charged, scene-shifting turn. The prompt instructs the model to size the forcefulness of the chosen move to this energy level. Energy changes how boldly the character acts; it does not change who they are or their limits.

## The provider layer

`backend/providers.py` defines a single interface for all model providers. The `PROVIDERS` dict declares each provider with its API base URL, whether it requires a key, and the environment variable name for that key.

Two streaming adapters handle protocol differences: `_stream_openai` uses the OpenAI-compatible streaming format (used by OpenAI, Gemini, xAI, Groq, and Ollama); `_stream_anthropic` uses the Anthropic messages format. A non-streaming `complete()` function accumulates a stream into a single string for calls that do not need incremental output: import, scene-fact extraction, and objective generation.

Keys are looked up by reading the environment variable first, then falling back to `secrets.json`. Keys are stored and used server-side only. The frontend receives only a masked preview from `GET /api/providers`.

## Storage and branching

Conversations are stored as JSON files in `conversations/`. Each file holds the full conversation object: persona ID, scene configuration (genre, seeds, objective), scene-fact ledger, and a `turns` array. Each turn holds the user's message and a `variants` array. Regenerating a turn appends a new variant without discarding the original. The `chosen` index per turn records which variant is displayed. The frontend shows navigation arrows when more than one variant exists.

A parallel JSONL file (one JSON line per committed turn) is maintained for portability. An older JSONL-only format from earlier versions is auto-migrated to the JSON format on load.

## The eval harness

`eval/run.py` runs a property test suite against the appraisal prompt. It reads `config.json` for the current prompt template and thought model, loads persona and scenario files from `eval/`, calls Ollama directly using the `ollama` Python package, and asserts that the appraisal output has specific properties across multiple runs.

Scenarios are defined in `eval/scenarios.json` and documented in `eval/SCENARIOS.md`. Two types exist: `single` (one message, N runs, pass-rate threshold) and `sequence` (multi-turn, testing stability or drift across turns using Jaccard similarity on agenda text).

Flags: `--logic` runs only the deterministic tests in `eval/logic_tests.py`, which require no model and complete in milliseconds. `--ablations` runs the full suite with specific prompt sections removed, to measure which sections drive which properties. `--bless` saves golden outputs for drift detection.

Results are written to `eval/report.md`.

Note: the eval harness talks to Ollama directly and does not go through the FastAPI app. It requires Ollama to be running with the model in `config.json` available. It does not support cloud providers.

## File and folder layout

```
backend/
  app.py            main FastAPI application: prompts, API endpoints, config migration
  providers.py      provider registry, key storage, streaming adapters

frontend/
  index.html        single-page app shell
  app.js            all frontend logic: scene setup, two-pass chat, inner-state panel
  style.css         styles

eval/
  run.py            evaluation harness (property suite, ablations, golden diff)
  logic_tests.py    deterministic tests, no model required
  parser.py         appraisal output parser, shared by harness and logic tests
  scenarios.json    test scenario definitions
  SCENARIOS.md      schema documentation for scenarios.json
  personas/         eval-only persona files (separate from user personas)

personas/           user persona files (one JSON file per character)
conversations/      conversation files (JSON + JSONL, one pair per conversation)
output/             contains corpus files from a removed feature; not used by the running tool
seeds.json          scene seed lists: location, time of day, weather, mood, situation
config.json         user configuration: model selection, temperatures, prompt overrides
secrets.json        API keys (gitignored; must not be committed)
run.bat             Windows launch script
```
