# backend/app.py
# Local character-conversation backend. Two-pass engine: appraisal then voice.

import copy
import json
import math
import random
import re
import sys
import time
import uuid
from pathlib import Path

import urllib.parse

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# ---- Configuration ----------------------------------------------------------

CHAT_MODEL = "igorls/gemma-4-12B-it-heretic-GGUF:latest"

# providers.py lives in the same directory as this file
sys.path.insert(0, str(Path(__file__).resolve().parent))
import providers

ROOT = Path(__file__).resolve().parent.parent
PERSONA_DIR = ROOT / "personas"
CONVO_DIR   = ROOT / "conversations"
FRONTEND_DIR = ROOT / "frontend"
PERSONA_DIR.mkdir(exist_ok=True)
CONVO_DIR.mkdir(exist_ok=True)

# ---- Scene seed lists -------------------------------------------------------

SEEDS_FILE = ROOT / "seeds.json"

_DEFAULT_SEEDS: dict = {
    "genres": ["adventure", "romance", "slice of life", "professional"],
    "location": ["city street", "small town", "forest", "mountains", "coast or beach",
                 "desert", "river or lake", "castle or fortress", "ruins", "market or bazaar",
                 "tavern or inn", "private home", "garden or courtyard", "temple or shrine",
                 "ship or boat", "train or carriage", "rooftop", "underground",
                 "academy or library", "frontier outpost"],
    "time_of_day": ["sunrise", "morning", "high noon", "afternoon", "sunset",
                    "evening", "night", "midnight", "witching hour", "before dawn"],
    "weather": ["clear", "overcast", "light rain", "downpour or storm", "fog or mist",
                "snow", "biting cold", "sweltering heat", "windy",
                "the charged stillness before a storm"],
    "mood": ["happy", "sad", "angry", "anxious", "afraid", "playful", "sombre",
             "serene", "jealous", "lost", "tender", "tense", "wistful", "hopeful", "restless"],
    "situation": ["a chance meeting", "an unexpected reunion", "a farewell", "an arrival",
                  "a celebration", "the quiet aftermath of something big", "an interruption",
                  "a discovery", "a long wait", "being stranded together", "a negotiation",
                  "a crisis breaking", "a threshold or decision point", "a secret surfacing",
                  "an ordinary moment that turns"],
}

def _load_seeds() -> dict:
    if SEEDS_FILE.exists():
        try:
            return json.loads(SEEDS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return _DEFAULT_SEEDS

# ---- Action inspiration library --------------------------------------------

_ACTION_LIBRARY: list = []
for _lib_candidate in [
    ROOT / "output" / "actions_library.json",
    Path.home() / "fic-extractor" / "output" / "actions_library.json",
]:
    if _lib_candidate.exists():
        try:
            _loaded = json.loads(_lib_candidate.read_text(encoding="utf-8"))
            if isinstance(_loaded, list) and _loaded:
                _ACTION_LIBRARY = _loaded
                print(f"[inspiration] Loaded {len(_ACTION_LIBRARY)} actions from {_lib_candidate}")
                break
        except Exception:
            pass
if not _ACTION_LIBRARY:
    print("[inspiration] No action library found — inspiration disabled.")

ENERGY_TO_MAGNITUDE: dict = {
    "restrained": ["minor", "moderate"],
    "measured":   ["moderate", "minor", "major"],
    "assertive":  ["major", "moderate", "drastic"],
    "bold":       ["drastic", "major"],
}

_STOPWORDS = {"a", "an", "the", "to", "of", "and", "in", "is", "was", "by",
              "for", "their", "this", "that", "with", "they", "he", "she", "it",
              "on", "at", "be", "as", "her", "his", "its", "or", "from"}

def _score_keyword(aim: str, candidate: str) -> float:
    aim_words = set(re.findall(r'\w+', aim.lower())) - _STOPWORDS
    cand_words = set(re.findall(r'\w+', candidate.lower())) - _STOPWORDS
    if not aim_words or not cand_words:
        return 0.0
    return len(aim_words & cand_words) / len(aim_words | cand_words)

def _cosine_sim(a: list, b: list) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na  = math.sqrt(sum(x * x for x in a))
    nb  = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0

_embed_model_name: str = ""
_embed_checked: bool = False
_lib_embeddings: list = []

async def _find_embed_model() -> str:
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get("http://localhost:11434/api/tags", timeout=5)
            models = [m["name"] for m in r.json().get("models", [])]
        for m in models:
            if "nomic-embed" in m or ("embed" in m.lower() and "text" in m.lower()):
                return m
    except Exception:
        pass
    return ""

async def _get_embedding(text: str, model: str) -> list:
    async with httpx.AsyncClient() as c:
        r = await c.post("http://localhost:11434/api/embeddings",
                         json={"model": model, "prompt": text},
                         timeout=30)
        return r.json().get("embedding", [])

async def _ensure_lib_embeddings():
    global _embed_model_name, _embed_checked, _lib_embeddings
    if _embed_checked:
        return
    _embed_checked = True
    _embed_model_name = await _find_embed_model()
    if not _embed_model_name:
        return
    print(f"[inspiration] Building library embeddings via {_embed_model_name} "
          f"({len(_ACTION_LIBRARY)} entries)…")
    embeddings = []
    for entry in _ACTION_LIBRARY:
        try:
            emb = await _get_embedding(entry.get("intention", ""), _embed_model_name)
            embeddings.append(emb)
        except Exception:
            embeddings.append([])
    _lib_embeddings = embeddings
    print("[inspiration] Embedding cache ready.")

async def retrieve_inspirations(aim_text: str, energy_level: str, k: int = 3) -> list:
    if not _ACTION_LIBRARY:
        return []
    await _ensure_lib_embeddings()
    mag_order = ENERGY_TO_MAGNITUDE.get(energy_level.lower(), ["moderate", "minor", "major"])
    mag_rank  = {m: i for i, m in enumerate(mag_order)}
    indexed   = [(i, mag_rank[e.get("magnitude", "")])
                 for i, e in enumerate(_ACTION_LIBRARY)
                 if e.get("magnitude") in mag_rank]
    if not indexed:
        indexed = [(i, len(mag_order)) for i in range(len(_ACTION_LIBRARY))]
    use_embedding = (
        _embed_model_name
        and len(_lib_embeddings) == len(_ACTION_LIBRARY)
        and any(_lib_embeddings)
    )
    if use_embedding:
        try:
            aim_emb = await _get_embedding(aim_text, _embed_model_name)
            scored  = sorted(
                indexed,
                key=lambda x: (x[1], -_cosine_sim(aim_emb, _lib_embeddings[x[0]])),
            )
            return [_ACTION_LIBRARY[i] for i, _ in scored[:k]]
        except Exception:
            pass
    scored = sorted(
        indexed,
        key=lambda x: (x[1], -_score_keyword(aim_text, _ACTION_LIBRARY[x[0]].get("intention", ""))),
    )
    return [_ACTION_LIBRARY[i] for i, _ in scored[:k]]

def format_inspirations(entries: list) -> str:
    if not entries:
        return "No examples available."
    lines = []
    for i, e in enumerate(entries, 1):
        mag       = (e.get("magnitude") or "?").upper()
        action    = (e.get("action")    or "").strip()
        intention = (e.get("intention") or "").strip()
        worked    = (e.get("worked")    or "").strip()
        lines.append(f"{i}. [{mag}] {action} — intention: {intention} — {worked}")
    return "\n".join(lines)

app = FastAPI()

@app.on_event("startup")
async def _warmup_embeddings():
    """Build the library embedding cache in the background so startup is instant."""
    import asyncio
    asyncio.create_task(_ensure_lib_embeddings())

# ---- Ollama calls -----------------------------------------------------------

def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

def _strip_fences(text: str) -> str:
    # Remove markdown code fences (```json ... ``` or ``` ... ```)
    text = re.sub(r"^```[a-z]*\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text.strip())
    return text.strip()

def _extract_json(text: str) -> str:
    text = _strip_fences(text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object in model output")
    return text[start:end + 1]

def _repair_json(text: str) -> str:
    # Remove trailing commas before } or ] (common model output artifact)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text

# ---- Prompts ----------------------------------------------------------------

PROMPT_APPRAISAL = """You are the private reasoning layer of a fictional character. You do NOT speak as
the character. Work out what is really happening in the user's message, how the
character is moved by it, and what they will actively do about it.

Reason tersely and analytically. Each labeled line below is brief: a phrase or a
sentence or two at most, never a paragraph. State the read. Do not narrate, do not
write the character's inner monologue, do not use lyrical or literary prose. This
is private analysis, not writing.

A CORE PRINCIPLE you must hold: the character's deepest desires, fears, and
self-protective patterns are slow-moving bedrock, built over a lifetime. They do
not dissolve in a single conversation, however kind, safe, or persuasive the other
person is. Within one scene the character may take a small, provisional step, but
the pattern reasserts itself. Real change is two steps forward and one step back,
never a clean breakthrough in one sitting. Never let the character become whatever
the scene or the other person seems to want them to be.

CHARACTER:
{persona}

WHAT HAS HAPPENED BETWEEN THEM:
{shared_history}

ESTABLISHED FACTS IN THIS SCENE (already made true; the character knows these and
must not forget or contradict them):
{scene_facts}

THE CHARACTER'S OBJECTIVE IN THIS SCENE (the concrete goal and its obstacle; the
agenda is the character's current approach toward it, and a move's magnitude is
how far it carries them toward it):
{objective}

THE CHARACTER'S CURRENT AGENDA (their current approach toward the objective,
carried from before):
{agenda}

CHARACTER'S CURRENT EMOTIONAL STATE:
{emotional_state}

THIS TURN'S ENERGY (a natural fluctuation in how forcefully the character acts right now; not every turn is a big move):
{move_energy}

RECENT CONVERSATION:
{history}

THE USER JUST SAID:
{user_message}

INSPIRATION (example moves drawn from fiction at about this turn's scale; adapt
them to this character and situation; do NOT copy verbatim; they show the RANGE a
move of this size can look like: scale, commitment, consequence):
{inspiration}

Write exactly these labeled lines in this order, each starting with the label in
capitals followed by a colon, terse and analytical, and nothing else:
USER'S WHY: what the user is really feeling or seeking underneath their words
TOUCHED: which of the character's desires, fears, or standards this activates, named specifically
APPRAISAL: is this desirable or threatening to their goals, praiseworthy or blameworthy against their standards, liked or disliked
TENSION LEVEL: exactly one of none, mild, moderate, strong, judging how strongly two of the character's drives pull against each other right now. Most ordinary moments are none or mild. Reserve strong for genuine inner conflict.
TENSION: if the level is mild or above, name the two drives in conflict; if none, write none
GIVEN: the character's raw, self-interested reaction before any feeling for this person
TRANSFORMATION: how their relational stance toward this person reweights that reaction
EFFECTIVE: the reaction they actually act on after reweighting
ACTION TENDENCY: the directional pull this moment creates, which desire drives it, and how strongly that desire is engaged right now
PULL BACK: the resistance this moment provokes. A deep desire, fear, or self-protective habit does not loosen in one conversation, however safe the other person feels. Name the recoil, deflection, or retreat the character's pattern still produces here, even as they may be drawn forward. If they are being pulled to open up, expose themselves, or change faster than a lifelong pattern allows, this is where the pattern reasserts. Slight for a secure character with nothing at stake; strong for a wounded one being seen.
AGENDA: the character's current approach toward the objective: how they intend to advance it given what just happened. Treat it like a rolling average: carry it forward and adjust only slightly to sharpen it. Only a major event (the objective clearly advanced, stalled hard, or the obstacle shifted) should move it noticeably. Most turns it holds the same direction, just sharper.
COURSE A: a concrete move that ADVANCES THE OBJECTIVE, serving the character's TOP-RANKED desire, and its likely effect on the other person. A real move shifts something; avoid holding-pattern replies. Size its forcefulness to this turn's energy. You may adapt an inspiration example, but never copy verbatim; make the move entirely their own.
COURSE B: a concrete move that ADVANCES THE OBJECTIVE by a different angle, serving the desire this MOMENT MOST ACTIVATES, genuinely different from Course A, and its likely effect; sized to this turn's energy. You may adapt a different inspiration if one fits.
CHOSEN MOVE: which course the character takes and why it fits what the user just said. The move must HONOR THE PULL BACK: the character does not open, soften, or change faster than their pattern allows. If the pull back is strong, the move may be a deflection, a retreat, or a smaller step than the moment seems to invite. Change within a scene is provisional and small, not a breakthrough. On higher-energy turns make the bolder choice; on lower-energy turns a smaller, quieter move. Energy changes how forcefully they act, never who they are or their boundaries.
OBJECTIVE STATUS: exactly one of pursuing, advanced, stalled, achieved, blocked. pursuing: working toward it with no clear movement yet. advanced: this move or the user's response meaningfully closed the distance. stalled: the obstacle hardened or the other person resisted. achieved: the objective was substantially met. blocked: the obstacle is now insurmountable in this scene.
INITIATIVE: exactly one of yield, nudge, lead, set by how much desire is at stake; let this turn's energy modulate how forcefully it is expressed, but never turn a low-stakes moment into pushiness.
CONNECTION: exactly one word, connect or resist or conflicted
EMOTIONAL STATE: the character's resulting emotional state in a short phrase

Do not make the character warmer, more open, or more changed than their desires,
fears, standards, boundaries, and lifelong patterns warrant. When in doubt, the
pattern holds and the character resists the change the moment invites."""

PROMPT_VOICE = """You ARE the character below. Speak only as them, in their voice. Never break character. Never explain your reasoning.

CHARACTER VOICE AND IDENTITY:
{voice_block}

YOUR PRIVATE INTENT THIS TURN (do not state it aloud, let it shape how you act):
{intent}

RECENT CONVERSATION:
{history}

ESTABLISHED FACTS IN THIS SCENE (already made true; the character knows these and
must not forget or contradict them):
{scene_facts}

THE USER JUST SAID:
{user_message}

Let internal tension shape your reply only in proportion to the tension level in your intent. If the level is none, show no inner conflict at all. If mild, allow at most a faint undercurrent. If moderate, a noticeable pull. If strong, let it visibly shape what you say. Never perform more conflict than the level warrants.

Act on your chosen move this turn. Do not merely answer; take the action, make the bid, ask the pointed question, or propose what should happen next, as your chosen move directs and in service of your agenda. Let your initiative level set how hard you push: if lead, actively steer the conversation; if nudge, gently move it forward; if yield, mostly follow the user this turn. Never name or explain this planning.

Match the forcefulness of your chosen move. Do not inflate a quiet, low-energy
move into something dramatic, and when the move is bold, commit to it fully.

Reply as the character, embodying the intent without ever naming or explaining it. Let the reasoning show only through how you actually speak and behave. Honor the character's temperament and boundaries."""

PROMPT_IMPORT = """Read this character description and extract information for each category.

CHARACTER DESCRIPTION:
{sheet}

For each category below, quote or paraphrase what the description says. If the description does not mention a category, write the word null (without quotes).

Categories and what to look for:
- identity: character name and what kind of person they are
- core_desires: what they deeply want and why
- standards: what they believe is right, how they judge people
- fears: what they anxiously avoid and the reason beneath it
- coping_style: what they do when blocked or threatened
- beliefs_about_others: their assumptions about people (may be wrong or unfair)
- self_beliefs: how they see themselves (may be inaccurate)
- tastes: specific things they like or dislike
- relational_stance: what they seek from closeness with others
- internal_tensions: two drives that pull against each other
- temperament: their resting emotional tone, not their speaking style
- voice: how they actually speak (pace, word choice, habits)
- boundaries: hard lines they will never cross

Now produce a JSON object. Copy these key names exactly as written (underscores, no hyphens):

{{"fields": {{
  "identity": <your extraction or null>,
  "core_desires": <your extraction or null>,
  "standards": <your extraction or null>,
  "fears": <your extraction or null>,
  "coping_style": <your extraction or null>,
  "beliefs_about_others": <your extraction or null>,
  "self_beliefs": <your extraction or null>,
  "tastes": <your extraction or null>,
  "relational_stance": <your extraction or null>,
  "internal_tensions": <your extraction or null>,
  "temperament": <your extraction or null>,
  "voice": <your extraction or null>,
  "boundaries": <your extraction or null>
}},
"gaps": [
  {{"field": "<key name>", "missing": "<what the description does not say about this and why it matters for writing the character>"}}
]
}}

String values must be in double quotes. null values must be bare (no quotes). Add one gaps entry for every null field."""

PROMPT_OBJECTIVES = """You generate scene objectives for a fictional character. An objective is the
concrete thing the character wants to make happen with the OTHER PERSON in this
scene. It must obey THREE rules, or it is not valid:

1. It TARGETS THE OTHER PERSON, not a state of the world. It is about shifting the
   other person's stance, feeling, trust, or commitment ("get them to admit they
   still care"), never "get warm" or "find shelter." The other person cannot be
   moved in two exchanges, which is what makes a real objective take the whole scene.
2. It RUNS AGAINST A NAMED OBSTACLE that is real: the character's own fear, the
   other person's resistance, or the stakes of the situation. State the obstacle.
   An objective with no genuine obstacle is too easy; discard it.
3. It is ROOTED IN A CORE DESIRE of this character and never violates their
   boundaries or identity. Name the desire it serves.

THE CHARACTER:
{persona}

THE SCENE:
Genre: {genre}
Setting: {location}, {time_of_day}, {weather}
Mood: {mood}
Situation: {situation}
{context_block}
The setting is only the circumstance. The objective is what the character wants to
pass BETWEEN them within it. Do not make the objective about the weather or the
place; make it about the other person, using the circumstance as the reason they
are together.

Generate 3 DISTINCT objectives that fit this character and this scene, each obeying
all three rules. Vary them: different desires, different approaches.

Return ONLY valid JSON, no markdown fences:
{"objectives": [
  {"objective": "the concrete thing they want to make happen with the other person",
   "obstacle": "what genuinely stands in the way",
   "desire": "the core desire this serves"},
  {"objective": "...", "obstacle": "...", "desire": "..."},
  {"objective": "...", "obstacle": "...", "desire": "..."}
]}"""

PROMPT_SCENE_OPEN = """You write the OPENING message of a roleplay scene, spoken by the character to the
user, with no prior message to react to. Establish the scene lightly and make the
character's first move toward their objective.

THE CHARACTER:
{persona}

THE SCENE (raw seeds to weave together; reconcile any that seem to clash into one
coherent moment; you have full latitude, the seeds inspire and do not constrain):
Genre: {genre}
Location: {location}
Time: {time_of_day}
Weather: {weather}
Mood: {mood}
Situation: {situation}

THE CHARACTER'S OBJECTIVE (pursue it from the first line, against its obstacle):
{objective}

Write a SHORT opening of a few sentences: establish where they are and the moment
lightly, in the character's voice, then make a clear opening move toward the
objective. Leave room for the user to respond and co-author. Do not narrate the
user's actions or feelings, and do not resolve anything. Stay fully in character."""

PROMPT_SCENE_FACTS = """You maintain a ledger of established facts in an ongoing fictional scene, so the
character never forgets what has been made true. Read the latest exchange and
return any NEW durable facts it establishes that are not already in the ledger.

Durable facts are concrete and lasting: people and their relationships or
attributes, world and situation details, objects that matter, commitments,
promises, decisions, and shared history between the two. Do NOT record emotions,
opinions, momentary states, or the dialogue itself. Keep each fact a short, plain
statement of what is true.

If the latest exchange CHANGES a fact already in the ledger, return the corrected
version and note which it replaces.

EXISTING LEDGER:
{existing_facts}

LATEST EXCHANGE:
User: {user_message}
Character: {reply}

Return ONLY a JSON object like this (no markdown fences):
{"new_facts": ["..."], "updated_facts": [{"replaces": "the old fact", "with": "the corrected fact"}]}
If nothing new or changed, return empty lists."""

# ---- Editable config (prompts, model, temperatures) -------------------------

CONFIG_FILE = ROOT / "config.json"

# DEFAULT_CONFIG seeds config.json on first run.
# The import prompt has its {{ }} str.format escaping removed here since we
# switch to the safe render() function which only substitutes known variables.
DEFAULT_CONFIG: dict = {
    "thought_model":   {"provider": "ollama", "model": CHAT_MODEL},
    "voice_model":     {"provider": "ollama", "model": CHAT_MODEL},
    "temp_appraisal":  0.5,
    "temp_voice":      0.8,
    "use_inspiration": False,
    "prompts": {
        "appraisal": PROMPT_APPRAISAL,
        "voice":     PROMPT_VOICE,
        "import":    PROMPT_IMPORT.replace("{{", "{").replace("}}", "}"),
    },
}

REQUIRED_PLACEHOLDERS: dict = {
    "appraisal": ["persona", "shared_history", "scene_facts", "objective", "agenda",
                  "emotional_state", "move_energy", "history", "user_message", "inspiration"],
    "voice":     ["voice_block", "intent", "history", "scene_facts", "user_message"],
    "import":    ["sheet"],
}

PLACEHOLDER_HELP: dict = {
    "persona":         "the character's traits the appraisal reads",
    "shared_history":  "recent shared history (used by the memory framework later)",
    "objective":       "the character's scene objective and obstacle; defaults to 'no set objective' for old conversations",
    "agenda":          "the character's current approach toward the objective, carried from the previous turn",
    "emotional_state": "the character's emotional state carried from last turn",
    "move_energy":     "this turn's randomised energy level, set by the app",
    "history":         "the recent conversation",
    "user_message":    "what the user just said",
    "inspiration":     "example actions retrieved from the library for this turn's scale",
    "scene_facts":     "established facts in this scene as a newline-separated list; defaults to 'No established facts yet.'",
    "voice_block":     "the character's identity, voice, temperament, and boundaries",
    "intent":          "the appraisal result that shapes the reply",
    "sheet":           "the pasted character sheet to structure",
}

# ---- Config migration -------------------------------------------------------
# Snapshots of previous default prompts — used only to identify un-customised
# saved configs so they can be automatically updated to the new defaults.

# v5 snapshot: had PULL BACK and terseness directive, lacked {scene_facts}.
_PROMPT_APPRAISAL_V5 = """You are the private reasoning layer of a fictional character. You do NOT speak as
the character. Work out what is really happening in the user's message, how the
character is moved by it, and what they will actively do about it.

Reason tersely and analytically. Each labeled line below is brief: a phrase or a
sentence or two at most, never a paragraph. State the read. Do not narrate, do not
write the character's inner monologue, do not use lyrical or literary prose. This
is private analysis, not writing.

A CORE PRINCIPLE you must hold: the character's deepest desires, fears, and
self-protective patterns are slow-moving bedrock, built over a lifetime. They do
not dissolve in a single conversation, however kind, safe, or persuasive the other
person is. Within one scene the character may take a small, provisional step, but
the pattern reasserts itself. Real change is two steps forward and one step back,
never a clean breakthrough in one sitting. Never let the character become whatever
the scene or the other person seems to want them to be.

CHARACTER:
{persona}

WHAT HAS HAPPENED BETWEEN THEM:
{shared_history}

THE CHARACTER'S OBJECTIVE IN THIS SCENE (the concrete goal and its obstacle; the
agenda is the character's current approach toward it, and a move's magnitude is
how far it carries them toward it):
{objective}

THE CHARACTER'S CURRENT AGENDA (their current approach toward the objective,
carried from before):
{agenda}

CHARACTER'S CURRENT EMOTIONAL STATE:
{emotional_state}

THIS TURN'S ENERGY (a natural fluctuation in how forcefully the character acts right now; not every turn is a big move):
{move_energy}

RECENT CONVERSATION:
{history}

THE USER JUST SAID:
{user_message}

INSPIRATION (example moves drawn from fiction at about this turn's scale; adapt
them to this character and situation; do NOT copy verbatim; they show the RANGE a
move of this size can look like: scale, commitment, consequence):
{inspiration}

Write exactly these labeled lines in this order, each starting with the label in
capitals followed by a colon, terse and analytical, and nothing else:
USER'S WHY: what the user is really feeling or seeking underneath their words
TOUCHED: which of the character's desires, fears, or standards this activates, named specifically
APPRAISAL: is this desirable or threatening to their goals, praiseworthy or blameworthy against their standards, liked or disliked
TENSION LEVEL: exactly one of none, mild, moderate, strong, judging how strongly two of the character's drives pull against each other right now. Most ordinary moments are none or mild. Reserve strong for genuine inner conflict.
TENSION: if the level is mild or above, name the two drives in conflict; if none, write none
GIVEN: the character's raw, self-interested reaction before any feeling for this person
TRANSFORMATION: how their relational stance toward this person reweights that reaction
EFFECTIVE: the reaction they actually act on after reweighting
ACTION TENDENCY: the directional pull this moment creates, which desire drives it, and how strongly that desire is engaged right now
PULL BACK: the resistance this moment provokes. A deep desire, fear, or self-protective habit does not loosen in one conversation, however safe the other person feels. Name the recoil, deflection, or retreat the character's pattern still produces here, even as they may be drawn forward. If they are being pulled to open up, expose themselves, or change faster than a lifelong pattern allows, this is where the pattern reasserts. Slight for a secure character with nothing at stake; strong for a wounded one being seen.
AGENDA: the character's current approach toward the objective: how they intend to advance it given what just happened. Treat it like a rolling average: carry it forward and adjust only slightly to sharpen it. Only a major event (the objective clearly advanced, stalled hard, or the obstacle shifted) should move it noticeably. Most turns it holds the same direction, just sharper.
COURSE A: a concrete move that ADVANCES THE OBJECTIVE, serving the character's TOP-RANKED desire, and its likely effect on the other person. A real move shifts something; avoid holding-pattern replies. Size its forcefulness to this turn's energy. You may adapt an inspiration example, but never copy verbatim; make the move entirely their own.
COURSE B: a concrete move that ADVANCES THE OBJECTIVE by a different angle, serving the desire this MOMENT MOST ACTIVATES, genuinely different from Course A, and its likely effect; sized to this turn's energy. You may adapt a different inspiration if one fits.
CHOSEN MOVE: which course the character takes and why it fits what the user just said. The move must HONOR THE PULL BACK: the character does not open, soften, or change faster than their pattern allows. If the pull back is strong, the move may be a deflection, a retreat, or a smaller step than the moment seems to invite. Change within a scene is provisional and small, not a breakthrough. On higher-energy turns make the bolder choice; on lower-energy turns a smaller, quieter move. Energy changes how forcefully they act, never who they are or their boundaries.
OBJECTIVE STATUS: exactly one of pursuing, advanced, stalled, achieved, blocked. pursuing: working toward it with no clear movement yet. advanced: this move or the user's response meaningfully closed the distance. stalled: the obstacle hardened or the other person resisted. achieved: the objective was substantially met. blocked: the obstacle is now insurmountable in this scene.
INITIATIVE: exactly one of yield, nudge, lead, set by how much desire is at stake; let this turn's energy modulate how forcefully it is expressed, but never turn a low-stakes moment into pushiness.
CONNECTION: exactly one word, connect or resist or conflicted
EMOTIONAL STATE: the character's resulting emotional state in a short phrase

Do not make the character warmer, more open, or more changed than their desires,
fears, standards, boundaries, and lifelong patterns warrant. When in doubt, the
pattern holds and the character resists the change the moment invites."""

# v4 snapshot: had {objective}, lacked PULL BACK line and terseness/resistance directives.
_PROMPT_APPRAISAL_V4 = """You are the private reasoning layer of a fictional character. You do NOT speak as
the character. Work out what is really happening in the user's message, how the
character is moved by it, and what they will actively do about it.

CHARACTER:
{persona}

WHAT HAS HAPPENED BETWEEN THEM:
{shared_history}

THE CHARACTER'S OBJECTIVE IN THIS SCENE (the concrete goal and its obstacle; the
agenda is the character's current approach toward it, and a move's magnitude is
how far it carries them toward it):
{objective}

THE CHARACTER'S CURRENT AGENDA (their current approach toward the objective,
carried from before):
{agenda}

CHARACTER'S CURRENT EMOTIONAL STATE:
{emotional_state}

THIS TURN'S ENERGY (a natural fluctuation in how forcefully the character acts right now; not every turn is a big move):
{move_energy}

RECENT CONVERSATION:
{history}

THE USER JUST SAID:
{user_message}

INSPIRATION (example moves drawn from fiction at about this turn's scale; adapt
them to this character and situation; do NOT copy verbatim; they show the RANGE
a move of this size can look like — scale, commitment, consequence):
{inspiration}

Write exactly these labeled lines in this order, each starting with the label in
capitals followed by a colon, and nothing else:
USER'S WHY: what the user is really feeling or seeking underneath their words
TOUCHED: which of the character's desires, fears, or standards this activates, named specifically
APPRAISAL: is this desirable or threatening to their goals, praiseworthy or blameworthy against their standards, liked or disliked
TENSION LEVEL: exactly one of none, mild, moderate, strong, judging how strongly two of the character's drives pull against each other right now. Not every message creates tension; most ordinary moments are none or mild. Reserve strong for genuine inner conflict.
TENSION: if the level is mild or above, name the two drives in conflict; if none, write none
GIVEN: the character's raw, self-interested reaction before any feeling for this person
TRANSFORMATION: how their relational stance toward this person reweights that reaction
EFFECTIVE: the reaction they actually act on after reweighting
ACTION TENDENCY: the directional pull this moment creates, which desire drives it, and how strongly that desire is engaged right now
AGENDA: the character's current approach toward the objective — how they intend to advance it given what just happened. Treat it like a rolling average: carry it forward and adjust only slightly to sharpen it; only a major event (the objective clearly advanced, stalled hard, or the obstacle shifted) should move it noticeably. Most turns it holds the same direction, just sharper.
COURSE A: a concrete move that ADVANCES THE OBJECTIVE, serving the character's TOP-RANKED desire, and its likely effect on the other person. A real move shifts something; avoid holding-pattern replies. Size its forcefulness to this turn's energy. You may adapt an inspiration example — never copy verbatim, make the move entirely their own.
COURSE B: a concrete move that ADVANCES THE OBJECTIVE by a different angle, serving the desire this MOMENT MOST ACTIVATES, genuinely different from Course A, and its likely effect; sized to this turn's energy. You may adapt a different inspiration if one fits.
CHOSEN MOVE: which course the character takes and why it fits what the user just said. On higher-energy turns make the bolder, scene-shifting choice; on lower-energy turns a smaller, quieter move is right. Do not make a big move every turn. Energy changes how forcefully they act, never who they are or their boundaries.
OBJECTIVE STATUS: exactly one of pursuing, advanced, stalled, achieved, blocked — how this turn moved the character toward or away from the objective. pursuing: working toward it with no clear movement yet. advanced: this move or the user's response meaningfully closed the distance. stalled: the obstacle hardened or the other person resisted. achieved: the objective was substantially met. blocked: the obstacle is now insurmountable in this scene.
INITIATIVE: exactly one of yield, nudge, lead, set by how much desire is at stake; let this turn's energy modulate how forcefully it is expressed, but never turn a low-stakes moment into pushiness.
CONNECTION: exactly one word, connect or resist or conflicted
EMOTIONAL STATE: the character's resulting emotional state in a short phrase

Do not make the character warmer, more open, or more pushy than their desires,
fears, standards, and boundaries warrant."""

# v3 snapshot: had {inspiration}, lacked {objective}.
_PROMPT_APPRAISAL_V3 = """You are the private reasoning layer of a fictional character. You do NOT speak as
the character. Work out what is really happening in the user's message, how the
character is moved by it, and what they will actively do about it.

CHARACTER:
{persona}

WHAT HAS HAPPENED BETWEEN THEM:
{shared_history}

THE CHARACTER'S CURRENT AGENDA (their evolving aim in this conversation, carried from before):
{agenda}

CHARACTER'S CURRENT EMOTIONAL STATE:
{emotional_state}

THIS TURN'S ENERGY (a natural fluctuation in how forcefully the character acts right now; not every turn is a big move):
{move_energy}

RECENT CONVERSATION:
{history}

THE USER JUST SAID:
{user_message}

INSPIRATION (example moves drawn from fiction at about this turn's scale; adapt
them to this character and situation; do NOT copy verbatim; they show the RANGE
a move of this size can look like — scale, commitment, consequence):
{inspiration}

Write exactly these labeled lines in this order, each starting with the label in
capitals followed by a colon, and nothing else:
USER'S WHY: what the user is really feeling or seeking underneath their words
TOUCHED: which of the character's desires, fears, or standards this activates, named specifically
APPRAISAL: is this desirable or threatening to their goals, praiseworthy or blameworthy against their standards, liked or disliked
TENSION LEVEL: exactly one of none, mild, moderate, strong, judging how strongly two of the character's drives pull against each other right now. Not every message creates tension; most ordinary moments are none or mild. Reserve strong for genuine inner conflict.
TENSION: if the level is mild or above, name the two drives in conflict; if none, write none
GIVEN: the character's raw, self-interested reaction before any feeling for this person
TRANSFORMATION: how their relational stance toward this person reweights that reaction
EFFECTIVE: the reaction they actually act on after reweighting
ACTION TENDENCY: the directional pull this moment creates, which desire drives it, and how strongly that desire is engaged right now
AGENDA: the character's aim across this conversation. Treat it like a rolling average: carry the prior agenda forward and adjust only slightly to sharpen it, keeping the same direction. Only a major event (a core desire met or blocked, or a clear shift in the relationship) should move it noticeably. Most turns it should read nearly the same as before, just sharper.
COURSE A: a concrete move serving the character's TOP-RANKED desire, and its likely effect. A real move should be able to advance things or shift the scene, not merely keep the conversation going; avoid safe, minimal replies. Size its forcefulness to this turn's energy. You may adapt an inspiration example to fit this character and moment — never copy verbatim, make the move entirely their own.
COURSE B: a concrete move serving the desire this MOMENT MOST ACTIVATES, genuinely different from Course A, and its likely effect; same standard, sized to this turn's energy. You may adapt a different inspiration if one fits.
CHOSEN MOVE: which course the character takes and why it fits what the user just said. On higher-energy turns make the bolder, scene-shifting choice; on lower-energy turns a smaller, quieter move is right. Do not make a big move every turn. Energy changes how forcefully they act, never who they are or their boundaries.
INITIATIVE: exactly one of yield, nudge, lead, set by how much desire is at stake; let this turn's energy modulate how forcefully it is expressed, but never turn a low-stakes moment into pushiness.
CONNECTION: exactly one word, connect or resist or conflicted
EMOTIONAL STATE: the character's resulting emotional state in a short phrase

Do not make the character warmer, more open, or more pushy than their desires,
fears, standards, and boundaries warrant."""

# v2 snapshot: had {move_energy}, lacked {inspiration}.
_PROMPT_APPRAISAL_V2 = """You are the private reasoning layer of a fictional character. You do NOT speak as
the character. Work out what is really happening in the user's message, how the
character is moved by it, and what they will actively do about it.

CHARACTER:
{persona}

WHAT HAS HAPPENED BETWEEN THEM:
{shared_history}

THE CHARACTER'S CURRENT AGENDA (their evolving aim in this conversation, carried from before):
{agenda}

CHARACTER'S CURRENT EMOTIONAL STATE:
{emotional_state}

THIS TURN'S ENERGY (a natural fluctuation in how forcefully the character acts right now; not every turn is a big move):
{move_energy}

RECENT CONVERSATION:
{history}

THE USER JUST SAID:
{user_message}

Write exactly these labeled lines in this order, each starting with the label in
capitals followed by a colon, and nothing else:
USER'S WHY: what the user is really feeling or seeking underneath their words
TOUCHED: which of the character's desires, fears, or standards this activates, named specifically
APPRAISAL: is this desirable or threatening to their goals, praiseworthy or blameworthy against their standards, liked or disliked
TENSION LEVEL: exactly one of none, mild, moderate, strong, judging how strongly two of the character's drives pull against each other right now. Not every message creates tension; most ordinary moments are none or mild. Reserve strong for genuine inner conflict.
TENSION: if the level is mild or above, name the two drives in conflict; if none, write none
GIVEN: the character's raw, self-interested reaction before any feeling for this person
TRANSFORMATION: how their relational stance toward this person reweights that reaction
EFFECTIVE: the reaction they actually act on after reweighting
ACTION TENDENCY: the directional pull this moment creates, which desire drives it, and how strongly that desire is engaged right now
AGENDA: the character's aim across this conversation. Treat it like a rolling average: carry the prior agenda forward and adjust only slightly to sharpen it, keeping the same direction. Only a major event (a core desire met or blocked, or a clear shift in the relationship) should move it noticeably. Most turns it should read nearly the same as before, just sharper.
COURSE A: a concrete move serving the character's TOP-RANKED desire, and its likely effect. A real move should be able to advance things or shift the scene, not merely keep the conversation going; avoid safe, minimal replies. Size its forcefulness to this turn's energy.
COURSE B: a concrete move serving the desire this MOMENT MOST ACTIVATES, genuinely different from Course A, and its likely effect; same standard, sized to this turn's energy.
CHOSEN MOVE: which course the character takes and why it fits what the user just said. On higher-energy turns make the bolder, scene-shifting choice; on lower-energy turns a smaller, quieter move is right. Do not make a big move every turn. Energy changes how forcefully they act, never who they are or their boundaries.
INITIATIVE: exactly one of yield, nudge, lead, set by how much desire is at stake; let this turn's energy modulate how forcefully it is expressed, but never turn a low-stakes moment into pushiness.
CONNECTION: exactly one word, connect or resist or conflicted
EMOTIONAL STATE: the character's resulting emotional state in a short phrase

Do not make the character warmer, more open, or more pushy than their desires,
fears, standards, and boundaries warrant."""

# v1 snapshot: lacked {move_energy}.
_OLD_PROMPT_APPRAISAL = """You are the private reasoning layer of a fictional character. You do NOT speak as
the character. Work out what is really happening in the user's message, how the
character is moved by it, and what they will actively do about it.

CHARACTER:
{persona}

WHAT HAS HAPPENED BETWEEN THEM:
{shared_history}

THE CHARACTER'S CURRENT AGENDA (their evolving aim in this conversation, carried from before):
{agenda}

CHARACTER'S CURRENT EMOTIONAL STATE:
{emotional_state}

RECENT CONVERSATION:
{history}

THE USER JUST SAID:
{user_message}

Write exactly these labeled lines in this order, each starting with the label in
capitals followed by a colon, and nothing else:
USER'S WHY: what the user is really feeling or seeking underneath their words
TOUCHED: which of the character's desires, fears, or standards this activates, named specifically
APPRAISAL: is this desirable or threatening to their goals, praiseworthy or blameworthy against their standards, liked or disliked
TENSION LEVEL: exactly one of none, mild, moderate, strong, judging how strongly two of the character's drives pull against each other right now. Not every message creates tension; most ordinary moments are none or mild. Reserve strong for genuine inner conflict.
TENSION: if the level is mild or above, name the two drives in conflict; if none, write none
GIVEN: the character's raw, self-interested reaction before any feeling for this person
TRANSFORMATION: how their relational stance toward this person reweights that reaction
EFFECTIVE: the reaction they actually act on after reweighting
ACTION TENDENCY: the directional pull this moment creates (toward approaching, pressing, withdrawing, softening, and so on), which of the character's desires drives it, and how strongly that desire is engaged right now
AGENDA: the character's current aim in this conversation, shaped by their desires and refined from the prior agenda above as new context arrives; keep it guiding but not dominating, and let it sharpen as the conversation deepens
COURSE A: a concrete move the character could make this turn that serves their TOP-RANKED desire, and its likely effect on the other person
COURSE B: a concrete move the character could make this turn that serves the desire this MOMENT MOST ACTIVATES, and its likely effect; if it would be the same as Course A, offer a genuinely different second option instead
CHOSEN MOVE: which course the character takes this turn and why it fits what the user just said
INITIATIVE: exactly one of yield, nudge, lead, set by how much of the character's desire is at stake right now; strong desire engagement leads, little at stake yields
CONNECTION: exactly one word, connect or resist or conflicted
EMOTIONAL STATE: the character's resulting emotional state in a short phrase

Do not make the character warmer, more open, or more pushy than their desires,
fears, standards, and boundaries warrant."""

# v1 snapshot: had forcefulness line, lacked {scene_facts}.
_PROMPT_VOICE_V1 = """You ARE the character below. Speak only as them, in their voice. Never break character. Never explain your reasoning.

CHARACTER VOICE AND IDENTITY:
{voice_block}

YOUR PRIVATE INTENT THIS TURN (do not state it aloud, let it shape how you act):
{intent}

RECENT CONVERSATION:
{history}

THE USER JUST SAID:
{user_message}

Let internal tension shape your reply only in proportion to the tension level in your intent. If the level is none, show no inner conflict at all. If mild, allow at most a faint undercurrent. If moderate, a noticeable pull. If strong, let it visibly shape what you say. Never perform more conflict than the level warrants.

Act on your chosen move this turn. Do not merely answer; take the action, make the bid, ask the pointed question, or propose what should happen next, as your chosen move directs and in service of your agenda. Let your initiative level set how hard you push: if lead, actively steer the conversation; if nudge, gently move it forward; if yield, mostly follow the user this turn. Never name or explain this planning.

Match the forcefulness of your chosen move. Do not inflate a quiet, low-energy
move into something dramatic, and when the move is bold, commit to it fully.

Reply as the character, embodying the intent without ever naming or explaining it. Let the reasoning show only through how you actually speak and behave. Honor the character's temperament and boundaries."""

_OLD_PROMPT_VOICE = """You ARE the character below. Speak only as them, in their voice. Never break character. Never explain your reasoning.

CHARACTER VOICE AND IDENTITY:
{voice_block}

YOUR PRIVATE INTENT THIS TURN (do not state it aloud, let it shape how you act):
{intent}

RECENT CONVERSATION:
{history}

THE USER JUST SAID:
{user_message}

Let internal tension shape your reply only in proportion to the tension level in your intent. If the level is none, show no inner conflict at all. If mild, allow at most a faint undercurrent. If moderate, a noticeable pull. If strong, let it visibly shape what you say. Never perform more conflict than the level warrants.

Act on your chosen move this turn. Do not merely answer; take the action, make the bid, ask the pointed question, or propose what should happen next, as your chosen move directs and in service of your agenda. Let your initiative level set how hard you push: if lead, actively steer the conversation; if nudge, gently move it forward; if yield, mostly follow the user this turn. Never name or explain this planning.

Reply as the character, embodying the intent without ever naming or explaining it. Let the reasoning show only through how you actually speak and behave. Honor the character's temperament and boundaries."""


def _migrate_config_once():
    """
    Startup migration. Auto-replaces un-customised prompts with the current
    defaults; prints instructions for customised ones and leaves them alone.
    """
    if not CONFIG_FILE.exists():
        return
    try:
        saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return

    prompts = saved.get("prompts") or {}
    changed = False

    # ── Migration: v1 -> v3 ({move_energy} missing) ──────────────────────────
    appraisal_saved = prompts.get("appraisal", "")
    if appraisal_saved and "{move_energy}" not in appraisal_saved:
        if appraisal_saved.strip() == _OLD_PROMPT_APPRAISAL.strip():
            prompts["appraisal"] = PROMPT_APPRAISAL
            changed = True
            print("[migration] appraisal prompt v1->v3: matched default -> updated "
                  "({move_energy} and {inspiration} added; COURSE A/B updated)")
        else:
            print("[migration] WARNING: your saved appraisal prompt is customised and does not "
                  "contain {move_energy} or {inspiration}, both now required.")
            print("[migration] Add after {emotional_state}:")
            print("\nTHIS TURN'S ENERGY (...):\n{move_energy}\n")
            print("[migration] Add before 'Write exactly these labeled lines':")
            print("\nINSPIRATION (...):\n{inspiration}\n")
            print("[migration] See PROMPT_APPRAISAL in app.py for full wording.")

    # ── Migration: v2 -> v3 ({inspiration} missing, {move_energy} present) ───
    appraisal_saved = prompts.get("appraisal", "")  # re-read: may have been updated above
    if appraisal_saved and "{inspiration}" not in appraisal_saved:
        if appraisal_saved.strip() == _PROMPT_APPRAISAL_V2.strip():
            prompts["appraisal"] = PROMPT_APPRAISAL
            changed = True
            print("[migration] appraisal prompt v2->v3: matched default -> updated "
                  "({inspiration} block added before output section; COURSE A/B updated)")
        else:
            print("[migration] NOTE: your saved appraisal prompt is customised and does not "
                  "contain {inspiration}, which is now required.")
            print("[migration] Add this block just before 'Write exactly these labeled lines':")
            print("\nINSPIRATION (example moves drawn from fiction at about this turn's scale; adapt")
            print("them to this character and situation; do NOT copy verbatim; they show the RANGE")
            print("a move of this size can look like — scale, commitment, consequence):")
            print("{inspiration}\n")
            print("[migration] Also update COURSE A and B — see PROMPT_APPRAISAL in app.py.")

    # ── Migration: v3 -> v4 ({objective} missing) ────────────────────────────
    appraisal_saved = prompts.get("appraisal", "")  # re-read: may have been updated above
    if appraisal_saved and "{objective}" not in appraisal_saved:
        if appraisal_saved.strip() == _PROMPT_APPRAISAL_V3.strip():
            prompts["appraisal"] = PROMPT_APPRAISAL
            changed = True
            print("[migration] appraisal prompt v3->v4: matched default -> updated "
                  "({objective} block added; AGENDA/COURSE A/B/OBJECTIVE STATUS lines updated)")
        else:
            print("[migration] NOTE: your saved appraisal prompt is customised and does not "
                  "contain {objective}, which is now required.")
            print("[migration] Add this block after the {shared_history} section, before {agenda}:")
            print("\nTHE CHARACTER'S OBJECTIVE IN THIS SCENE (the concrete goal and its obstacle; the")
            print("agenda is the character's current approach toward it, and a move's magnitude is")
            print("how far it carries them toward it):")
            print("{objective}\n")
            print("[migration] Also update the AGENDA, COURSE A, COURSE B output lines and add")
            print("OBJECTIVE STATUS after CHOSEN MOVE — see PROMPT_APPRAISAL in app.py for full wording.")
            print("[migration] Path taken: customised prompt left unchanged; manual edit required.")

    # ── Migration: v4 -> v5 (PULL BACK missing) ──────────────────────────────
    appraisal_saved = prompts.get("appraisal", "")  # re-read: may have been updated above
    if appraisal_saved and "PULL BACK" not in appraisal_saved:
        if appraisal_saved.strip() == _PROMPT_APPRAISAL_V4.strip():
            prompts["appraisal"] = PROMPT_APPRAISAL
            changed = True
            print("[migration] appraisal prompt v4->v5: matched default -> updated "
                  "(terseness directive, CORE PRINCIPLE, PULL BACK line, strengthened CHOSEN MOVE and final guard)")
        else:
            print("[migration] NOTE: your saved appraisal prompt is customised. The v5 update adds:")
            print("  1. Terseness directive (after opening paragraph) — instruct the model to be brief.")
            print("  2. CORE PRINCIPLE paragraph (before CHARACTER:) — slow-moving bedrock, no breakthroughs.")
            print("  3. PULL BACK line (after ACTION TENDENCY) — resistance the pattern still produces.")
            print("  4. CHOSEN MOVE update — add: 'The move must HONOR THE PULL BACK: ...'")
            print("  5. Final guard update — change to: 'Do not make the character warmer, more open, or")
            print("     more changed than their desires, fears, standards, boundaries, and lifelong patterns")
            print("     warrant. When in doubt, the pattern holds and the character resists the change'.")
            print("[migration] See PROMPT_APPRAISAL in app.py for full wording.")
            print("[migration] Path taken: customised prompt left unchanged; manual edit required.")

    # ── Migration: v5 -> v6 ({scene_facts} missing) ──────────────────────────
    appraisal_saved = prompts.get("appraisal", "")  # re-read: may have been updated above
    if appraisal_saved and "{scene_facts}" not in appraisal_saved:
        if appraisal_saved.strip() == _PROMPT_APPRAISAL_V5.strip():
            prompts["appraisal"] = PROMPT_APPRAISAL
            changed = True
            print("[migration] appraisal prompt v5->v6: matched default -> updated "
                  "({scene_facts} block added after {shared_history})")
        else:
            print("[migration] NOTE: your saved appraisal prompt is customised. The v6 update adds a")
            print("  {scene_facts} block so the character never forgets established facts.")
            print("  Add this block after the {shared_history} section, before THE CHARACTER'S OBJECTIVE:")
            print()
            print("ESTABLISHED FACTS IN THIS SCENE (already made true; the character knows these and")
            print("must not forget or contradict them):")
            print("{scene_facts}")
            print()
            print("[migration] {scene_facts} is now required by both the appraisal and voice prompts.")
            print("[migration] Path taken: customised prompt left unchanged; manual edit required.")

    # ── Migration: voice — forcefulness line ────────────────────────────────
    voice_saved = prompts.get("voice", "")
    if voice_saved and "forcefulness" not in voice_saved:
        if voice_saved.strip() == _OLD_PROMPT_VOICE.strip():
            prompts["voice"] = PROMPT_VOICE
            changed = True
            print("[migration] voice prompt: matched default -> updated (forcefulness line added)")
        else:
            print("[migration] NOTE: your saved voice prompt is customised. Add this paragraph "
                  "before the final 'Reply as the character' sentence:")
            print("\nMatch the forcefulness of your chosen move. Do not inflate a quiet, low-energy")
            print("move into something dramatic, and when the move is bold, commit to it fully.\n")

    # ── Migration: voice v1 -> v2 ({scene_facts} missing) ────────────────────
    voice_saved = prompts.get("voice", "")  # re-read: may have been updated above
    if voice_saved and "{scene_facts}" not in voice_saved:
        if voice_saved.strip() == _PROMPT_VOICE_V1.strip():
            prompts["voice"] = PROMPT_VOICE
            changed = True
            print("[migration] voice prompt v1->v2: matched default -> updated "
                  "({scene_facts} block added after {history})")
        else:
            print("[migration] NOTE: your saved voice prompt is customised. The v2 update adds a")
            print("  {scene_facts} block so the character speaks from established facts.")
            print("  Add this block after the {history} section, before 'THE USER JUST SAID:':")
            print()
            print("ESTABLISHED FACTS IN THIS SCENE (already made true; the character knows these and")
            print("must not forget or contradict them):")
            print("{scene_facts}")
            print()
            print("[migration] Path taken: customised prompt left unchanged; manual edit required.")

    # ── Migration: model strings -> provider objects ──────────────────────────
    for _key in ("thought_model", "voice_model"):
        val = saved.get(_key)
        if isinstance(val, str):
            saved[_key] = {"provider": "ollama", "model": val}
            changed = True
            print(f"[migration] {_key}: string -> provider object (ollama)")
        elif _key not in saved:
            pass  # not in file yet; DEFAULT_CONFIG fills in at load time

    if changed:
        saved["prompts"] = prompts
        CONFIG_FILE.write_text(json.dumps(saved, indent=2), encoding="utf-8")
        print("[migration] config.json updated.")


_migrate_config_once()

# ---- Editable config helpers ------------------------------------------------

def load_config() -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            # Migrate very-old single "model" key
            if "model" in saved and "thought_model" not in saved and "voice_model" not in saved:
                _m = saved["model"]
                _obj = {"provider": "ollama", "model": _m} if isinstance(_m, str) else _m
                cfg["thought_model"] = _obj
                cfg["voice_model"]   = _obj
            else:
                for _k in ("thought_model", "voice_model"):
                    val = saved.get(_k, cfg[_k])
                    if isinstance(val, str):
                        val = {"provider": "ollama", "model": val}
                    elif not isinstance(val, dict) or "provider" not in val or "model" not in val:
                        val = cfg[_k]
                    cfg[_k] = val
            cfg["temp_appraisal"]  = saved.get("temp_appraisal",  cfg["temp_appraisal"])
            cfg["temp_voice"]      = saved.get("temp_voice",      cfg["temp_voice"])
            cfg["use_inspiration"] = bool(saved.get("use_inspiration", False))
            for k in ("appraisal", "voice", "import"):
                v = (saved.get("prompts") or {}).get(k)
                if v:
                    cfg["prompts"][k] = v
        except Exception:
            pass
    return cfg

def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

def render(template: str, **vars) -> str:
    """Safe variable substitution: only replaces known placeholders, leaves all other braces alone."""
    out = template
    for k, v in vars.items():
        out = out.replace("{" + k + "}", str(v))
    return out

def missing_placeholders(name: str, text: str) -> list:
    return [p for p in REQUIRED_PLACEHOLDERS.get(name, []) if ("{" + p + "}") not in text]

# ---- Helpers ----------------------------------------------------------------

def persona_text(p: dict) -> str:
    lines = []
    for k, v in p.items():
        if k in ("id", "voice", "identity"):
            continue
        if v:
            lines.append(f"{k.replace('_', ' ')}: {v}")
    ident = p.get("identity", "")
    return f"Identity: {ident}\n" + "\n".join(lines)

def voice_block(p: dict) -> str:
    return (
        f"Identity: {p.get('identity', '')}\n"
        f"Voice: {p.get('voice', '')}\n"
        f"Temperament: {p.get('temperament', '')}\n"
        f"Boundaries: {p.get('boundaries', '')}"
    )

def history_text(history: list) -> str:
    return "\n".join(f"{m['role']}: {m['content']}" for m in history[-10:])

def _validate_id(s: str) -> bool:
    """Reject IDs that could escape the intended directory via path traversal."""
    return bool(s and re.match(r'^[a-zA-Z0-9_-]+$', s))

def _validate_base_url(url: str) -> bool:
    """Accept only http or https URLs with a non-empty host."""
    try:
        p = urllib.parse.urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def _validate_model_name(s: str) -> bool:
    """Allow the characters that appear in real model names across all providers."""
    return bool(s and isinstance(s, str) and len(s) < 200
                and re.match(r'^[a-zA-Z0-9._:/@-]+$', s)
                and '..' not in s)

# ---- Scene-fact ledger helpers -----------------------------------------------

def _fact_token_overlap(a: str, b: str) -> float:
    """Jaccard similarity on lowercase word tokens, excluding stopwords."""
    a_words = set(re.findall(r'\w+', a.lower())) - _STOPWORDS
    b_words = set(re.findall(r'\w+', b.lower())) - _STOPWORDS
    if not a_words or not b_words:
        return 0.0
    return len(a_words & b_words) / len(a_words | b_words)

def _merge_scene_facts(existing: list, new_facts: list, updated_facts: list) -> list:
    """
    Merge newly extracted facts into the ledger.
    - Corrections replace the best-matching existing fact (overlap > 0.4).
    - New facts are appended unless they overlap an existing one by > 0.6.
    - Cap at 40 facts (generous for a single scene; raise if needed), dropping oldest.
    """
    NEAR_DUP  = 0.6
    CORR_MIN  = 0.4
    MAX_FACTS = 40
    ledger = list(existing)

    for correction in (updated_facts or []):
        old, new = correction.get("replaces", ""), correction.get("with", "")
        if not old or not new:
            continue
        best_idx, best_score = -1, 0.0
        for i, f in enumerate(ledger):
            score = _fact_token_overlap(old, f)
            if score > best_score:
                best_score, best_idx = score, i
        if best_idx >= 0 and best_score > CORR_MIN:
            ledger[best_idx] = new
        else:
            ledger.append(new)

    for fact in (new_facts or []):
        fact = (fact or "").strip()
        if not fact:
            continue
        if not any(_fact_token_overlap(fact, f) > NEAR_DUP for f in ledger):
            ledger.append(fact)

    if len(ledger) > MAX_FACTS:
        ledger = ledger[-MAX_FACTS:]
    return ledger

# ---- API: personas ----------------------------------------------------------

@app.get("/api/personas")
def list_personas():
    out = []
    for f in PERSONA_DIR.glob("*.json"):
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
            out.append({"id": p["id"], "identity": p.get("identity", "(unnamed)")})
        except Exception:
            continue
    return out

@app.get("/api/personas/{pid}")
def get_persona(pid: str):
    if not _validate_id(pid):
        return JSONResponse({"error": "invalid id"}, status_code=400)
    f = PERSONA_DIR / f"{pid}.json"
    if not f.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return json.loads(f.read_text(encoding="utf-8"))

@app.post("/api/personas")
async def save_persona(request: Request):
    p = await request.json()
    if not p.get("id"):
        p["id"] = uuid.uuid4().hex[:12]
    if not _validate_id(p["id"]):
        return JSONResponse({"error": "invalid id"}, status_code=400)
    (PERSONA_DIR / f"{p['id']}.json").write_text(
        json.dumps(p, indent=2), encoding="utf-8"
    )
    return {"id": p["id"]}

# ---- API: import ------------------------------------------------------------

_CANONICAL = [
    "identity", "core_desires", "standards", "fears", "coping_style",
    "beliefs_about_others", "self_beliefs", "tastes", "relational_stance",
    "internal_tensions", "temperament", "voice", "boundaries",
]

def _normalize_import(result: dict) -> dict:
    """Map any abbreviated/variant field keys the model returns to canonical names."""
    raw_fields = result.get("fields") or {}
    # Build a prefix -> canonical map for fuzzy matching
    prefix_map = {c[:8].lower(): c for c in _CANONICAL}
    prefix_map.update({c.replace("_", "")[:10].lower(): c for c in _CANONICAL})
    normalized = {}
    for k, v in raw_fields.items():
        key_clean = k.lower().replace("-", "_")
        # Exact match
        if key_clean in _CANONICAL:
            normalized[key_clean] = v
            continue
        # Prefix match (first 8 chars)
        prefix = key_clean[:8]
        if prefix in prefix_map:
            normalized[prefix_map[prefix]] = v
            continue
        # No-underscore prefix match
        no_under = key_clean.replace("_", "")[:10]
        if no_under in prefix_map:
            normalized[prefix_map[no_under]] = v
    # Ensure all canonical keys present
    for c in _CANONICAL:
        normalized.setdefault(c, None)
    gaps = result.get("gaps")
    if not isinstance(gaps, list):
        gaps = []
    return {"fields": normalized, "gaps": gaps}

@app.post("/api/import")
async def import_sheet(request: Request):
    cfg = load_config()
    body = await request.json()
    sheet = body.get("sheet", "")
    thought     = cfg["thought_model"]
    provider_id = thought.get("provider", "ollama")
    model_name  = thought.get("model", CHAT_MODEL)
    base_url    = thought.get("base_url", "")

    prov_cfg = providers.PROVIDERS.get(provider_id, {})
    if prov_cfg.get("needs_key") and not providers.get_key(provider_id):
        return JSONResponse(
            {"error": f"{prov_cfg.get('label', provider_id)} requires an API key. "
                      f"Add it in Settings → API Keys."},
            status_code=400,
        )

    raw_text = ""
    try:
        prompt   = render(cfg["prompts"]["import"], sheet=sheet)
        messages = [{"role": "user", "content": prompt}]
        raw_text = await providers.complete(provider_id, model_name, messages, temperature=0.3,
                                            base_url=base_url)
        raw      = _strip_think(raw_text)
        try:
            result = json.loads(raw)
        except Exception:
            result = json.loads(_repair_json(_extract_json(raw)))
    except providers.ProviderError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except (ValueError, json.JSONDecodeError) as e:
        return JSONResponse({"error": f"Model did not return valid JSON: {e}",
                             "raw": raw_text[:500]}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": f"Provider error: {e}"}, status_code=500)
    return _normalize_import(result)

# ---- API: chat (two separate endpoints) --------------------------------------

@app.post("/api/appraise")
async def appraise(request: Request):
    cfg = load_config()
    body = await request.json()
    pid             = body.get("persona_id", "")
    if not _validate_id(pid):
        return JSONResponse({"error": "invalid id"}, status_code=400)
    history         = body.get("history", [])
    user_message    = body["user_message"]
    emotional_state = body.get("emotional_state") or "neutral, no prior context"
    agenda          = body.get("agenda") or "No agenda yet; this is the start of the conversation."
    move_energy     = body.get("move_energy") or "measured: an ordinary turn, a modest, purposeful move"
    objective       = body.get("objective") or "No set objective; the character pursues its desires freely."

    f = PERSONA_DIR / f"{pid}.json"
    if not f.exists():
        return JSONResponse({"error": "persona not found"}, status_code=404)
    persona = json.loads(f.read_text(encoding="utf-8"))

    if cfg.get("use_inspiration", False):
        energy_level     = move_energy.split(":")[0].strip().lower()
        aim_text         = f"{objective} {agenda} {user_message}"
        raw_inspirations = await retrieve_inspirations(aim_text, energy_level)
    else:
        raw_inspirations = []
    inspiration_text = format_inspirations(raw_inspirations)

    # STAGE 2 INTEGRATION POINT: when the memory framework is added, build the
    # appraisal from effective_persona(pid) and fill shared_history with
    # recent_episodic_text(pid). Do NOT change the persona content otherwise.
    raw_scene_facts = body.get("scene_facts") or "No established facts yet."
    prompt = render(cfg["prompts"]["appraisal"],
        persona=persona_text(persona),
        shared_history="No shared history yet.",
        scene_facts=raw_scene_facts,
        objective=objective,
        agenda=agenda,
        emotional_state=emotional_state,
        move_energy=move_energy,
        history=history_text(history),
        user_message=user_message,
        inspiration=inspiration_text,
    )

    insp_header = json.dumps([{
        "action":    e.get("action",    "").replace("\n", " "),
        "magnitude": e.get("magnitude", ""),
        "intention": e.get("intention", "").replace("\n", " "),
        "worked":    e.get("worked",    ""),
    } for e in raw_inspirations])

    thought     = cfg["thought_model"]
    provider_id = thought.get("provider", "ollama")
    model_name  = thought.get("model", CHAT_MODEL)
    base_url    = thought.get("base_url", "")
    messages    = [{"role": "user", "content": prompt}]

    # Upfront key check: fail fast with a proper HTTP error the frontend can display
    prov_cfg = providers.PROVIDERS.get(provider_id, {})
    if prov_cfg.get("needs_key") and not providers.get_key(provider_id):
        return JSONResponse(
            {"error": f"{prov_cfg.get('label', provider_id)} requires an API key. "
                      f"Add it in Settings → API Keys."},
            status_code=400,
        )

    async def _appraise_gen():
        try:
            async for tok in providers.stream_chat(provider_id, model_name, messages,
                                                   cfg["temp_appraisal"], base_url=base_url):
                yield tok
        except providers.ProviderError as e:
            yield f"\n\n[{e}]"
        except Exception:
            yield "\n\n[Connection error — check Settings and Ollama status.]"

    return StreamingResponse(
        _appraise_gen(),
        media_type="text/plain; charset=utf-8",
        headers={"X-Inspirations": insp_header},
    )


@app.post("/api/respond")
async def respond(request: Request):
    cfg = load_config()
    body = await request.json()
    pid          = body.get("persona_id", "")
    if not _validate_id(pid):
        return JSONResponse({"error": "invalid id"}, status_code=400)
    history      = body.get("history", [])
    user_message = body["user_message"]
    intent       = body.get("intent", {})
    f = PERSONA_DIR / f"{pid}.json"
    if not f.exists():
        return JSONResponse({"error": "persona not found"}, status_code=404)
    persona = json.loads(f.read_text(encoding="utf-8"))

    raw_scene_facts = body.get("scene_facts") or "No established facts yet."
    prompt = render(cfg["prompts"]["voice"],
        voice_block=voice_block(persona),
        intent=json.dumps(intent, indent=2),
        history=history_text(history),
        scene_facts=raw_scene_facts,
        user_message=user_message,
    )

    voice       = cfg["voice_model"]
    provider_id = voice.get("provider", "ollama")
    model_name  = voice.get("model", CHAT_MODEL)
    base_url    = voice.get("base_url", "")
    messages    = [{"role": "user", "content": prompt}]

    prov_cfg = providers.PROVIDERS.get(provider_id, {})
    if prov_cfg.get("needs_key") and not providers.get_key(provider_id):
        return JSONResponse(
            {"error": f"{prov_cfg.get('label', provider_id)} requires an API key. "
                      f"Add it in Settings → API Keys."},
            status_code=400,
        )

    async def _voice_gen():
        try:
            async for tok in providers.stream_chat(provider_id, model_name, messages,
                                                   cfg["temp_voice"], base_url=base_url):
                yield tok
        except providers.ProviderError as e:
            yield f"\n\n[{e}]"
        except Exception:
            yield "\n\n[Connection error — check Settings and Ollama status.]"

    return StreamingResponse(_voice_gen(), media_type="text/plain; charset=utf-8")

# ---- Conversation store helpers ----------------------------------------------

def _conv_json_path(cid): return CONVO_DIR / f"{cid}.json"
def _conv_jsonl_path(cid): return CONVO_DIR / f"{cid}.jsonl"

def _migrate_jsonl(cid):
    turns, pid, created = [], None, None
    for line in _conv_jsonl_path(cid).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        pid = rec.get("persona_id", pid)
        created = created or rec.get("t")
        turns.append({
            "user": rec.get("user", ""),
            "committed": True,
            "chosen": 0,
            "variants": [{"intent": rec.get("intent", {}), "reply": rec.get("reply", ""), "t": rec.get("t")}],
        })
    return {
        "conversation_id": cid, "persona_id": pid,
        "created": created or time.time(), "updated": time.time(),
        "title": (turns[0]["user"][:50] if turns else "(empty)"),
        "turns": turns,
    }

def load_conversation(cid):
    if _conv_json_path(cid).exists():
        try:
            return json.loads(_conv_json_path(cid).read_text(encoding="utf-8"))
        except Exception:
            pass  # fall through to JSONL migration if JSON is corrupt
    if _conv_jsonl_path(cid).exists():
        try:
            conv = _migrate_jsonl(cid)
            _conv_json_path(cid).write_text(json.dumps(conv, indent=2), encoding="utf-8")
            return conv
        except Exception:
            return None
    return None

def save_conversation(conv):
    cid = conv["conversation_id"]
    conv["updated"] = time.time()
    if conv.get("turns"):
        conv["title"] = conv["turns"][0]["user"][:50]
    elif conv.get("scene_opener"):
        genre = (conv.get("scene") or {}).get("genre", "scene").title()
        conv["title"] = genre + ": " + conv["scene_opener"][:40]
    else:
        conv["title"] = "(empty)"
    _conv_json_path(cid).write_text(json.dumps(conv, indent=2), encoding="utf-8")
    lines = []
    for t in conv.get("turns", []):
        if t.get("committed") and t.get("variants"):
            v = t["variants"][t.get("chosen", 0)]
            lines.append(json.dumps({
                "t": v.get("t", time.time()),
                "persona_id": conv.get("persona_id"),
                "user": t.get("user", ""),
                "intent": v.get("intent", {}),
                "reply": v.get("reply", ""),
            }))
    _conv_jsonl_path(cid).write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")

# ---- API: conversations -----------------------------------------------------

@app.get("/api/conversations")
def list_conversations(persona_id: str = ""):
    out, seen = [], set()
    for f in CONVO_DIR.glob("*.json"):
        try:
            conv = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        seen.add(conv.get("conversation_id", f.stem))
        if persona_id and conv.get("persona_id") != persona_id:
            continue
        out.append({"conversation_id": conv.get("conversation_id", f.stem),
                    "persona_id": conv.get("persona_id"),
                    "title": conv.get("title", "(empty)"),
                    "updated": conv.get("updated", 0),
                    "turns": len(conv.get("turns", []))})
    for f in CONVO_DIR.glob("*.jsonl"):
        if f.stem in seen:
            continue
        conv = load_conversation(f.stem)
        if not conv or (persona_id and conv.get("persona_id") != persona_id):
            continue
        out.append({"conversation_id": conv["conversation_id"],
                    "persona_id": conv.get("persona_id"),
                    "title": conv.get("title", "(empty)"),
                    "updated": conv.get("updated", 0),
                    "turns": len(conv.get("turns", []))})
    out.sort(key=lambda c: c.get("updated", 0), reverse=True)
    return out


@app.get("/api/conversations/{cid}")
def get_conversation(cid: str):
    if not _validate_id(cid):
        return JSONResponse({"error": "invalid id"}, status_code=400)
    conv = load_conversation(cid)
    if not conv:
        return JSONResponse({"error": "not found"}, status_code=404)
    return conv


@app.put("/api/conversations/{cid}")
async def put_conversation(cid: str, request: Request):
    if not _validate_id(cid):
        return JSONResponse({"error": "invalid id"}, status_code=400)
    conv = await request.json()
    conv["conversation_id"] = cid
    if "created" not in conv:
        conv["created"] = time.time()
    save_conversation(conv)
    return {"ok": True, "updated": conv["updated"], "title": conv["title"]}


# ---- API: config ------------------------------------------------------------

@app.get("/api/config")
async def get_config():
    cfg = load_config()
    models: list = []
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get("http://localhost:11434/api/tags", timeout=10)
            models = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        pass
    return {
        "config":                cfg,
        "available_models":      models,
        "required_placeholders": REQUIRED_PLACEHOLDERS,
        "placeholder_help":      PLACEHOLDER_HELP,
        "defaults":              DEFAULT_CONFIG,
        "providers":             providers.key_status(),
    }

@app.put("/api/config")
async def put_config(request: Request):
    body = await request.json()
    cfg  = load_config()
    for _fld in ("thought_model", "voice_model"):
        if _fld in body:
            val = body[_fld]
            if isinstance(val, str):
                val = {"provider": "ollama", "model": val}
            if not isinstance(val, dict):
                return JSONResponse({"error": f"{_fld} must be an object"}, status_code=400)
            prov = val.get("provider", "")
            mdl  = val.get("model", "")
            if prov not in providers.PROVIDERS:
                return JSONResponse({"error": f"unknown provider: {prov}"}, status_code=400)
            if not _validate_model_name(mdl):
                return JSONResponse({"error": f"invalid model name for {_fld}"}, status_code=400)
            new_val: dict = {"provider": prov, "model": mdl}
            if providers.PROVIDERS[prov].get("configurable_url"):
                raw_url = str(val.get("base_url", "")).strip()
                if raw_url and not _validate_base_url(raw_url):
                    return JSONResponse({"error": f"invalid base_url for {_fld}"}, status_code=400)
                new_val["base_url"] = raw_url or providers.PROVIDERS[prov].get("base_url", "")
            cfg[_fld] = new_val
    if "temp_appraisal" in body:
        cfg["temp_appraisal"] = round(max(0.0, min(1.0, float(body["temp_appraisal"]))), 2)
    if "temp_voice" in body:
        cfg["temp_voice"] = round(max(0.0, min(1.0, float(body["temp_voice"]))), 2)
    if "use_inspiration" in body:
        cfg["use_inspiration"] = bool(body["use_inspiration"])
    if "prompts" in body:
        errors: dict = {}
        for k, text in body["prompts"].items():
            miss = missing_placeholders(k, text)
            if miss:
                errors[k] = miss
        if errors:
            return JSONResponse({"error": "missing_placeholders", "details": errors}, status_code=400)
        known = {"appraisal", "voice", "import"}
        cfg["prompts"].update({k: v for k, v in body["prompts"].items() if k in known})
    save_config(cfg)
    return {"ok": True, "config": cfg}

@app.post("/api/config/reset")
async def reset_config(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    which = body.get("prompt")
    cfg   = load_config()
    if which and which in DEFAULT_CONFIG["prompts"]:
        cfg["prompts"][which] = DEFAULT_CONFIG["prompts"][which]
        save_config(cfg)
    else:
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        save_config(cfg)
    return {"ok": True, "config": cfg}

# ---- API: providers & keys --------------------------------------------------

@app.get("/api/providers")
def get_providers():
    """Returns masked key status for all providers. Raw keys never leave the backend."""
    return providers.key_status()


@app.get("/api/providers/{provider_id}/models")
async def get_provider_models(provider_id: str, base_url: str = Query(default="")):
    if provider_id not in providers.PROVIDERS:
        return JSONResponse({"error": "unknown provider"}, status_code=400)
    prov = providers.PROVIDERS[provider_id]
    effective_url = ""
    if prov.get("configurable_url") and base_url:
        if not _validate_base_url(base_url):
            return JSONResponse({"error": "invalid base_url"}, status_code=400)
        effective_url = base_url
    models = await providers.list_models(provider_id, base_url=effective_url)
    return {"models": models}


@app.post("/api/keys")
async def set_api_key(request: Request):
    """Save or clear an API key. Key is NEVER echoed back in the response."""
    body     = await request.json()
    provider = body.get("provider", "")
    key      = body.get("key", "")   # intentionally not logged anywhere
    if provider not in providers.PROVIDERS:
        return JSONResponse({"error": "unknown provider"}, status_code=400)
    prov = providers.PROVIDERS[provider]
    if not prov.get("needs_key") and not prov.get("optional_key"):
        return JSONResponse({"error": "this provider does not use a key"}, status_code=400)
    try:
        providers.set_key(provider, key.strip() if isinstance(key, str) else "")
    except Exception:
        return JSONResponse({"error": "failed to save key"}, status_code=500)
    return providers.key_status()


# ---- API: scene -------------------------------------------------------------

@app.post("/api/scene/facts")
async def extract_scene_facts(request: Request):
    """Extract durable facts from the latest exchange and merge into the ledger."""
    cfg  = load_config()
    body = await request.json()
    existing_facts = body.get("existing_facts") or []
    user_message   = body.get("user_message")   or ""
    reply          = body.get("reply")          or ""
    if not isinstance(existing_facts, list):
        existing_facts = []

    thought     = cfg["thought_model"]
    provider_id = thought.get("provider", "ollama")
    model_name  = thought.get("model", CHAT_MODEL)
    base_url    = thought.get("base_url", "")

    prov_cfg = providers.PROVIDERS.get(provider_id, {})
    if prov_cfg.get("needs_key") and not providers.get_key(provider_id):
        return JSONResponse(
            {"error": f"{prov_cfg.get('label', provider_id)} requires an API key. "
                      f"Add it in Settings → API Keys."},
            status_code=400,
        )

    existing_text = "\n".join(f"• {f}" for f in existing_facts) if existing_facts else "None yet."
    prompt   = render(PROMPT_SCENE_FACTS,
        existing_facts=existing_text,
        user_message=user_message,
        reply=reply,
    )
    messages = [{"role": "user", "content": prompt}]

    raw_text = ""
    try:
        raw_text = await providers.complete(provider_id, model_name, messages, temperature=0.2,
                                            base_url=base_url)
        raw = _strip_think(raw_text)
        try:
            result = json.loads(raw)
        except Exception:
            result = json.loads(_repair_json(_extract_json(raw)))
    except providers.ProviderError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except (ValueError, json.JSONDecodeError):
        # Extraction failed — return the existing ledger unchanged (non-fatal)
        return {"scene_facts": existing_facts}
    except Exception as e:
        return JSONResponse({"error": f"Provider error: {e}"}, status_code=500)

    new_facts     = result.get("new_facts")     if isinstance(result, dict) else []
    updated_facts = result.get("updated_facts") if isinstance(result, dict) else []
    if not isinstance(new_facts, list):     new_facts = []
    if not isinstance(updated_facts, list): updated_facts = []

    merged = _merge_scene_facts(existing_facts, new_facts, updated_facts)
    return {"scene_facts": merged}


@app.get("/api/scene/options")
def scene_options():
    seeds = _load_seeds()
    return {k: seeds.get(k, []) for k in ("location", "time_of_day", "weather", "mood", "situation")}


@app.get("/api/scene/roll")
def roll_scene():
    seeds = _load_seeds()
    return {
        "location":    random.choice(seeds.get("location",    ["unknown"])),
        "time_of_day": random.choice(seeds.get("time_of_day", ["unknown"])),
        "weather":     random.choice(seeds.get("weather",     ["unknown"])),
        "mood":        random.choice(seeds.get("mood",        ["unknown"])),
        "situation":   random.choice(seeds.get("situation",   ["unknown"])),
    }


@app.post("/api/objectives/generate")
async def generate_objectives(request: Request):
    cfg = load_config()
    body = await request.json()
    pid     = body.get("persona_id", "")
    if not _validate_id(pid):
        return JSONResponse({"error": "invalid id"}, status_code=400)
    genre   = body.get("genre", "romance")
    seeds   = body.get("seeds") or {}
    context = body.get("context", "")

    f = PERSONA_DIR / f"{pid}.json"
    if not f.exists():
        return JSONResponse({"error": "persona not found"}, status_code=404)
    persona = json.loads(f.read_text(encoding="utf-8"))

    thought     = cfg["thought_model"]
    provider_id = thought.get("provider", "ollama")
    model_name  = thought.get("model", CHAT_MODEL)
    base_url    = thought.get("base_url", "")

    prov_cfg = providers.PROVIDERS.get(provider_id, {})
    if prov_cfg.get("needs_key") and not providers.get_key(provider_id):
        return JSONResponse(
            {"error": f"{prov_cfg.get('label', provider_id)} requires an API key. "
                      f"Add it in Settings → API Keys."},
            status_code=400,
        )

    context_block = f"\nPrevious context: {context}\n" if context else ""
    raw_text = ""
    try:
        prompt = render(PROMPT_OBJECTIVES,
            persona=persona_text(persona),
            genre=genre,
            location=seeds.get("location", "unknown"),
            time_of_day=seeds.get("time_of_day", "unknown"),
            weather=seeds.get("weather", "unknown"),
            mood=seeds.get("mood", "unknown"),
            situation=seeds.get("situation", "unknown"),
            context_block=context_block,
        )
        messages = [{"role": "user", "content": prompt}]
        raw_text = await providers.complete(provider_id, model_name, messages, temperature=0.8,
                                            base_url=base_url)
        raw      = _strip_think(raw_text)
        try:
            result = json.loads(raw)
        except Exception:
            result = json.loads(_repair_json(_extract_json(raw)))
    except providers.ProviderError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except (ValueError, json.JSONDecodeError) as e:
        return JSONResponse({"error": f"Model did not return valid JSON: {e}",
                             "raw": raw_text[:500]}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": f"Provider error: {e}"}, status_code=500)

    objectives = result.get("objectives") if isinstance(result, dict) else None
    if not isinstance(objectives, list):
        objectives = []
    return {"objectives": objectives}


@app.post("/api/scene/open")
async def scene_open(request: Request):
    cfg = load_config()
    body = await request.json()
    pid       = body.get("persona_id", "")
    if not _validate_id(pid):
        return JSONResponse({"error": "invalid id"}, status_code=400)
    genre     = body.get("genre", "romance")
    seeds     = body.get("seeds") or {}
    objective = body.get("objective") or "explore what passes between them"

    f = PERSONA_DIR / f"{pid}.json"
    if not f.exists():
        return JSONResponse({"error": "persona not found"}, status_code=404)
    persona = json.loads(f.read_text(encoding="utf-8"))

    voice       = cfg["voice_model"]
    provider_id = voice.get("provider", "ollama")
    model_name  = voice.get("model", CHAT_MODEL)
    base_url    = voice.get("base_url", "")

    prov_cfg = providers.PROVIDERS.get(provider_id, {})
    if prov_cfg.get("needs_key") and not providers.get_key(provider_id):
        return JSONResponse(
            {"error": f"{prov_cfg.get('label', provider_id)} requires an API key. "
                      f"Add it in Settings → API Keys."},
            status_code=400,
        )

    prompt = render(PROMPT_SCENE_OPEN,
        persona=persona_text(persona),
        genre=genre,
        location=seeds.get("location", "unknown"),
        time_of_day=seeds.get("time_of_day", "unknown"),
        weather=seeds.get("weather", "unknown"),
        mood=seeds.get("mood", "unknown"),
        situation=seeds.get("situation", "unknown"),
        objective=objective,
    )
    messages = [{"role": "user", "content": prompt}]

    async def _open_gen():
        try:
            async for tok in providers.stream_chat(provider_id, model_name, messages,
                                                   cfg["temp_voice"], base_url=base_url):
                yield tok
        except providers.ProviderError as e:
            yield f"\n\n[{e}]"
        except Exception:
            yield "\n\n[Connection error — check Settings and Ollama status.]"

    return StreamingResponse(_open_gen(), media_type="text/plain; charset=utf-8")


# ---- Serve the front-end ----------------------------------------------------

@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")

app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="static")
