import httpx, re, json

CHAT_MODEL = "mdq100/Gemma3-Instruct-Abliterated:12b"
OLLAMA_URL = "http://localhost:11434/api/generate"

PROMPT = """You are helping a writer structure a character. Read their character sheet and map it onto the schema fields below. Extract only what the sheet supports. Where the sheet does not give enough to fill a field, do NOT invent it.

SCHEMA FIELDS: identity, core_desires, standards, fears, coping_style, beliefs_about_others, self_beliefs, tastes, relational_stance, internal_tensions, temperament, voice, boundaries

CHARACTER SHEET:
Mara is a 34-year-old archivist. She values order and precision. She fears losing control. She speaks in short precise sentences and will not lie.

Return ONLY a JSON object:
{"fields": {"identity": null, "core_desires": null, "standards": null, "fears": null, "coping_style": null, "beliefs_about_others": null, "self_beliefs": null, "tastes": null, "relational_stance": null, "internal_tensions": null, "temperament": null, "voice": null, "boundaries": null},
  "gaps": [{"field": "field name", "missing": "what the sheet does not establish"}]}

Fill each field with the extracted string value if present, or null if not."""

r = httpx.post(OLLAMA_URL, json={
    "model": CHAT_MODEL, "prompt": PROMPT,
    "stream": False, "options": {"temperature": 0.3},
}, timeout=300)

raw = r.json()["response"]
# Strip think blocks
clean = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

print("=== RAW RESPONSE (first 2000 chars) ===")
print(repr(clean[:2000]))
print()
print("=== VISIBLE ===")
print(clean[:2000])
