"""Debug script: manually run the two-pass engine and time each step."""
import asyncio, httpx, json, re, time
from pathlib import Path

CHAT_MODEL = "igorls/gemma-4-12B-it-heretic-GGUF:latest"
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_OPTIONS = {"temperature": 1.0, "top_k": 64, "top_p": 0.95}

def _strip_think(text):
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

async def call(prompt, label):
    print(f"[{label}] sending ({len(prompt)} chars)...")
    t0 = time.time()
    async with httpx.AsyncClient() as client:
        r = await client.post(OLLAMA_URL, json={
            "model": CHAT_MODEL, "prompt": prompt,
            "stream": False, "options": MODEL_OPTIONS,
        }, timeout=120)
        r.raise_for_status()
    elapsed = time.time() - t0
    raw = _strip_think(r.json()["response"])
    print(f"[{label}] done in {elapsed:.1f}s — {len(raw)} chars returned")
    print(f"[{label}] first 300: {repr(raw[:300])}")
    return raw

persona = json.loads(Path("personas/test_mara_001.json").read_text(encoding="utf-8"))

APPRAISAL_PROMPT = f"""You are the private reasoning layer of a fictional character. Return ONLY a JSON object.

CHARACTER:
Identity: {persona['identity']}
core desires: {persona['core_desires']}
fears: {persona['fears']}
standards: {persona['standards']}

THE USER JUST SAID: Hello.

Return ONLY a JSON object with keys: user_emotional_why, touched, appraisal, internal_tension, given_read, transformation, effective_read, connection, emotional_state, response_intent"""

VOICE_PROMPT = f"""You ARE this character. Reply in their voice. One or two sentences only.

Identity: {persona['identity']}
Voice: {persona['voice']}
Temperament: {persona['temperament']}

THE USER JUST SAID: Hello."""

async def main():
    print("=== Step 1: Appraisal ===")
    appraisal_raw = await call(APPRAISAL_PROMPT, "appraisal")
    print()
    print("=== Step 2: Voice ===")
    voice_raw = await call(VOICE_PROMPT, "voice")
    print()
    print("Done.")

asyncio.run(main())
