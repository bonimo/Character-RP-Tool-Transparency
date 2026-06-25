# Character Tool — Security & Quality Review
**Date:** 2026-06-24 (supersedes 2026-06-22 review)
**Reviewer:** Claude (automated)
**Status markers:** `[APPLIED]` = fix is in the code now. `[JUDGMENT CALL]` = documented but left to you.

This review covers the full codebase after all prior-session changes (provider layer, key storage, UI, config migrations). Every category from the original spec was checked.

---

## How to read this

Each finding names the file and location, explains what goes wrong in plain terms, and describes the fix. Applied fixes are small, targeted, and do not change any feature's visible behaviour. Judgment calls are at the end.

---

## Critical findings

### [APPLIED] C-1 — Path traversal on `/api/appraise` and `/api/respond` can leak API keys to the LLM

**File:** `backend/app.py` — `appraise`, `respond`

**What goes wrong:** Both endpoints read `persona_id` from the request body and use it directly to build a file path: `PERSONA_DIR / f"{pid}.json"`. The previous review (I-1) fixed the same bug on the GET/PUT persona and conversation endpoints, but missed these two POST endpoints.

With `{"persona_id": "../secrets", ...}`, the path resolves to `secrets.json` (the API-key store). `json.loads()` reads it and returns `{"openai": "sk-proj-..."}`. The `persona_text()` function in `/api/appraise` iterates over **all** keys of the persona dict — it has no allowlist — so the raw API key would be inserted into the appraisal prompt and sent to the LLM. The LLM might echo it back. With `{"persona_id": "../config"}`, `config.json` is read; no key leak but arbitrary file content enters the prompt.

The `/api/respond` endpoint's `voice_block()` only reads specific persona fields (identity, voice, temperament, boundaries), so the key would not appear in that prompt — but the file traversal still occurs and the endpoint should be hardened consistently.

**Fix applied:** Changed `pid = body["persona_id"]` to `pid = body.get("persona_id", "")` and added `_validate_id(pid)` check returning HTTP 400 at the top of both endpoints, matching the pattern already on every other file-path endpoint. The existing `_validate_id` function allows only `[a-zA-Z0-9_-]+`, which rejects `..` and `/` entirely. All legitimate persona IDs (hex from uuid, underscores) continue to pass.

**Verified:** Sending `{"persona_id": "../secrets", ...}` to both endpoints now returns `{"error": "invalid id"}` HTTP 400 and never touches the file system.

---

## Important findings

### [APPLIED] I-1 — Voice-pass errors show generic "Reply failed (400)" instead of the helpful message

**File:** `frontend/app.js` — `runTwoPasses`

**What goes wrong:** When the **appraisal** pass fails (e.g. cloud provider needs a key), the frontend correctly parses the JSON body and throws the readable message: "OpenAI (ChatGPT) requires an API key. Add it in Settings → API Keys." — because the appraise block calls `.json()` on the error response.

When the **voice** pass fails for the same reason, the frontend does not parse the body: `throw new Error('Reply failed (' + streamRes.status + ')')`. The user sees "Reply failed (400)" with no hint about what to do. This matters whenever thought and voice models are set to different providers.

**Fix applied:** The voice-pass error now parses the response JSON the same way the appraise-pass does: `const err = await streamRes.json().catch(() => ({})); throw new Error(err.error || 'Reply failed (' + streamRes.status + ')')`.

---

### [APPLIED] I-2 — Anthropic adapter always sends `x-api-key` header even when key is empty

**File:** `backend/providers.py` — `_stream_anthropic`

**What goes wrong:** `_stream_openai` correctly only adds `Authorization: Bearer ...` when the key is non-empty — so Ollama calls go without any auth header. `_stream_anthropic` always set `"x-api-key": key` regardless of whether `key` is `""`. This means an unauthenticated call to Anthropic would send an empty `x-api-key: ` header rather than no header at all. The pre-flight key check in `app.py` prevents this in practice, but the inconsistency makes the adapter more fragile and harder to reason about.

**Fix applied:** Changed the header construction to conditionally add `x-api-key` only when `key` is non-empty, matching the `_stream_openai` pattern.

---

### [CONFIRMED OK] I-3 — Path traversal on persona and conversation GET/PUT endpoints

Fixed in the prior session's review (finding I-1, 2026-06-22). All persona and conversation endpoints validate their URL-path IDs with `_validate_id` before constructing file paths. Confirmed still in place.

---

## Minor findings

### [APPLIED] M-1 — Dead code: `gen_json`, `gen_stream`, `OLLAMA_URL`, `MODEL_OPTIONS`, `ModelOutputError`

**File:** `backend/app.py`

**What goes wrong:** These five items were the original Ollama-direct calling layer. After the provider-abstraction refactor, all generation goes through `providers.stream_chat()` and `providers.complete()`. None of these are called anywhere. They take up ~45 lines, give the false impression that `OLLAMA_URL` is still used, and would confuse someone reading the code.

**Fix applied:** All five removed entirely.

---

### [APPLIED] M-2 — `'raw_text' in dir()` is a fragile existence check

**File:** `backend/app.py` — `import_sheet`

**What goes wrong:** The import endpoint's error handler uses `raw_text[:500] if 'raw_text' in dir() else ""` to safely include the partial model output in the error response. This works — Python's `dir()` includes names from the local scope — but it's an unusual and surprising pattern that could confuse a future reader, and it relies on `dir()` local-scope semantics that aren't universally known.

**Fix applied:** Initialized `raw_text = ""` before the `try` block. The reference in the except clause is always valid, and `raw_text[:500]` is just an empty string if the LLM call never ran.

---

### Findings confirmed OK from full checklist

**Provider and key security**
- Keys live only in `secrets.json` or env vars; never in any response, log, or URL. ✓
- `key_status()` returns only `{label, needs_key, key_set, masked}` — no raw key field. ✓
- Keys travel in request headers only (`Authorization: Bearer` / `x-api-key`). ✓
- Base URLs are fixed in the PROVIDERS registry; users cannot supply arbitrary hosts. ✓
- `secrets.json` is in `.gitignore`. ✓
- `run.bat` explicitly binds to `--host 127.0.0.1`; the server is not exposed on all interfaces. ✓

**Config migrations**
- All three migration paths (v1→v3, v2→v3, model string→provider object) are idempotent. ✓
- A freshly reset config restores `DEFAULT_CONFIG` with correct provider-object format. ✓
- Required placeholders (`{agenda}`, `{move_energy}`, `{inspiration}`) match the current prompts. ✓
- The `load_config()` secondary migration (on every load) handles any format `_migrate_config_once()` missed. ✓

**Branching and carry-forward**
- `getCarryForward()` scans backward for the last committed turn and reads from the chosen variant. ✓
- `getHistoryForApi()` only includes committed turns. ✓
- Regenerate adds a new variant and never touches committed turns. ✓
- `cycleVariant` is blocked on committed turns. ✓
- TENSION LEVEL is parsed correctly before TENSION — the position-based parser avoids the substring collision because `TENSION\s*:` does not match "TENSION LEVEL:". ✓

**Action-inspiration retrieval**
- Empty or missing library returns `[]` with no error. ✓
- Unknown energy level defaults to `["moderate", "minor", "major"]`. ✓
- Falls back to keyword matching when the embedding model is unavailable. ✓
- Library embedding warm-up runs in the background on startup; the main path is never blocked. ✓

**Endpoint input validation**
- All ID endpoints apply `_validate_id` before file path construction (now including appraise and respond). ✓
- Provider IDs are validated against the PROVIDERS registry. ✓
- Model names are validated with `_validate_model_name` (alphanumeric plus `._:/@-`, `..` rejected) before saving to config. ✓
- Prompt keys are filtered to the known set `{appraisal, voice, import}` before writing. ✓

**Error and empty states**
- Both `sendMessage` and `regenerate` use `finally` to re-enable the composer on any error path. ✓
- A failed send removes the orphaned turn from state so the turn counter stays accurate. ✓
- Save failures now display a visible "Not saved to disk" indicator (prior session fix). ✓
- Mid-stream provider errors yield an error token in the character bubble, not a silent hang. ✓

**Frontend safety**
- `escapeHtml()` is called consistently before any user-visible string becomes innerHTML. ✓
- Only theme, font, and panel-fold state are stored in localStorage — no API keys, no personas, no conversation content. ✓
- `formatMessage` HTML-escapes before adding `<em>` tags; no XSS path. ✓
- Model names in datalist options use `.value = m` (property), not innerHTML; no XSS path. ✓
- All DOM IDs referenced in `app.js` match the elements in `index.html`. ✓

**Stage 2 seams**
- `save_conversation()` writes a JSONL mirror of committed turns (chosen variant, intent, reply) on every save. ✓
- The `{shared_history}` placeholder exists in the appraisal prompt and is filled with `"No shared history yet."` pending the memory framework. ✓
- The Stage 2 integration comment is in place at `app.py` line ~780. ✓

---

## Judgment calls — unfixed, your decision

### [JUDGMENT CALL] J-1 — `@app.on_event("startup")` is deprecated in newer FastAPI

**File:** `backend/app.py` line 168

**What goes wrong:** FastAPI deprecated `@app.on_event("startup")` in v0.93 in favour of a `lifespan` context manager. The startup event still fires and the embedding warm-up works, but FastAPI logs a deprecation warning in the console on every server start. This is cosmetic today; it would become a breaking error in a future FastAPI major version.

**Why a judgment call:** The fix — converting to a `lifespan` function — changes the module-level structure of the app slightly and requires testing. It's purely cosmetic right now. Worth doing before the next major FastAPI upgrade, not urgently.

---

### [JUDGMENT CALL] J-2 — `available_models` fetches Ollama model list on every Settings open for no benefit

**File:** `backend/app.py` — `get_config`; `frontend/app.js` — `loadSettings`

**What goes wrong:** `GET /api/config` makes an extra HTTP call to `localhost:11434/api/tags` and returns the result as `available_models`. In `loadSettings`, this is stored in `_settingsModels` — which is set once and never read again. Datalists are now populated via `GET /api/providers/{id}/models`, making this list completely redundant. The effect is an extra ~100ms Ollama round-trip on every Settings open and a dead variable in the frontend.

**Why a judgment call:** Removing `available_models` from the API response is a surface-area change. If any external script or integration is consuming `GET /api/config` and using `available_models`, removing it would be a breaking change. For a personal tool that's not yet public, removing both the backend call and the frontend variable is the right cleanup — but you may want to do it deliberately, not as part of a review patch.

---

### [JUDGMENT CALL] J-3 — No input size limits on user-supplied data

**Files:** `backend/app.py` — `import_sheet`, `save_persona`

**What goes wrong:** The `/api/import` endpoint reads the entire request body and passes the `sheet` field to the LLM. The `/api/personas` (POST) endpoint writes the full persona dict to disk. There are no size limits on either. A 50MB paste would be sent to the LLM (which would likely error or truncate) and a 50MB persona object would be written to disk.

**Why a judgment call:** This is a local, single-user tool. The browser is the only realistic client, and it won't accidentally send 50MB. If you ever put this on a shared server or add any public endpoint, size limits (FastAPI's `max_upload_size` or a simple `len(body)` check) become important. Flagged for when the deployment model changes.

---

### [JUDGMENT CALL] J-4 — Sync I/O in async endpoint handlers (carried from prior review)

**File:** `backend/app.py` — `save_persona`, `save_conversation`, config write functions

**What goes wrong:** File writes block the async event loop briefly. Invisible for a local, single-user tool; would matter under concurrent load. The clean fix requires `aiofiles` or `run_in_executor`. Not worth the dependency for a local tool.

---

### [JUDGMENT CALL] J-5 — Send uses the dropdown persona; regenerate uses the conversation's stored persona (carried from prior review)

**File:** `frontend/app.js` — `sendMessage` vs `regenerate`

**What goes wrong:** If you switch the character dropdown mid-conversation, new sends use the newly-selected character; regenerations use whoever the conversation was started with. The two paths can diverge. Whether "switch dropdown = switch character mid-conversation" is desirable is a product decision, not a clear bug.

---

## Overall assessment

The codebase is in good shape. The two-pass streaming engine, carry-forward logic, commit/regen branching, config migration stack, provider abstraction, and key security model are all sound. The Stage 2 seams (JSONL mirror and `{shared_history}` placeholder) are correctly in place.

**What was fixed in this review:** One critical path-traversal that could have leaked raw API keys to the LLM (C-1); two important provider-layer hardening issues (I-1, I-2); and two minor code-quality fixes (M-1 dead code, M-2 initialization pattern). None of these change any user-visible feature.

**Security-specific verdict:** The key handling is solid. Keys are stored server-side only, never returned to the browser, never logged, never in URLs. The one gap — path traversal that could send keys to the LLM — is now closed. The server is bound to `127.0.0.1` by `run.bat`. The frontend stores nothing sensitive in localStorage. This is ready to be open-sourced from a secrets-safety standpoint.

**Readiness for Stage 2:** The two seams Stage 2 will use are healthy. `save_conversation()` correctly writes the committed-path JSONL mirror on every save, with the chosen variant's intent and reply. The `{shared_history}` placeholder is in the live appraisal prompt and the integration point is commented. Nothing in this review changed either seam.
