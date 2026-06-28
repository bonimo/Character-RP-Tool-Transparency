# backend/providers.py
# Provider registry, key storage, and streaming adapters for cloud and local LLMs.
# Security contract:
#   - API keys live server-side only: env vars or secrets.json
#   - Keys are NEVER sent to the browser, NEVER logged, NEVER placed in URLs
#   - key_status() returns only masked previews
#   - secrets.json is restricted to owner-only permissions where the OS supports it

import json
import os
import stat
from pathlib import Path

import httpx

SECRETS_FILE = Path(__file__).resolve().parent.parent / "secrets.json"

PROVIDERS: dict = {
    "ollama": {
        "label":     "Ollama (local)",
        "kind":      "openai",
        "base_url":  "http://localhost:11434/v1",
        "needs_key": False,
    },
    "openai": {
        "label":     "OpenAI (ChatGPT)",
        "kind":      "openai",
        "base_url":  "https://api.openai.com/v1",
        "needs_key": True,
        "env_var":   "OPENAI_API_KEY",
    },
    "anthropic": {
        "label":     "Anthropic (Claude)",
        "kind":      "anthropic",
        "base_url":  "https://api.anthropic.com",
        "needs_key": True,
        "env_var":   "ANTHROPIC_API_KEY",
    },
    "gemini": {
        "label":     "Google (Gemini)",
        "kind":      "openai",
        "base_url":  "https://generativelanguage.googleapis.com/v1beta/openai",
        "needs_key": True,
        "env_var":   "GEMINI_API_KEY",
    },
    "xai": {
        "label":     "xAI (Grok)",
        "kind":      "openai",
        "base_url":  "https://api.x.ai/v1",
        "needs_key": True,
        "env_var":   "XAI_API_KEY",
    },
    "groq": {
        "label":     "Groq",
        "kind":      "openai",
        "base_url":  "https://api.groq.com/openai/v1",
        "needs_key": True,
        "env_var":   "GROQ_API_KEY",
    },
    # ── OpenAI-compatible local servers ─────────────────────────────────────────
    # All share the same openai-kind streaming adapter; only the base URL differs.
    # The key is optional — a blank key sends a harmless "Bearer local" placeholder.
    "lmstudio": {
        "label":            "LM Studio",
        "kind":             "openai",
        "base_url":         "http://localhost:1234/v1",
        "needs_key":        False,
        "optional_key":     True,
        "configurable_url": True,
    },
    "llamacpp": {
        "label":            "llama.cpp server",
        "kind":             "openai",
        "base_url":         "http://localhost:8080/v1",
        "needs_key":        False,
        "optional_key":     True,
        "configurable_url": True,
    },
    "oobabooga": {
        "label":            "Oobabooga (text-generation-webui)",
        "kind":             "openai",
        "base_url":         "http://localhost:5000/v1",
        "needs_key":        False,
        "optional_key":     True,
        "configurable_url": True,
    },
    "koboldcpp": {
        "label":            "KoboldCpp",
        "kind":             "openai",
        "base_url":         "http://localhost:5001/v1",
        "needs_key":        False,
        "optional_key":     True,
        "configurable_url": True,
    },
    "vllm": {
        "label":            "vLLM",
        "kind":             "openai",
        "base_url":         "http://localhost:8000/v1",
        "needs_key":        False,
        "optional_key":     True,
        "configurable_url": True,
    },
    "custom_local": {
        "label":            "Custom (OpenAI-compatible)",
        "kind":             "openai",
        "base_url":         "",
        "needs_key":        False,
        "optional_key":     True,
        "configurable_url": True,
    },
}


class ProviderError(Exception):
    def __init__(self, provider: str, status: int, body: str = ""):
        self.provider = provider
        self.status   = status
        label = PROVIDERS.get(provider, {}).get("label", provider)
        super().__init__(f"{label} returned HTTP {status} — check your API key in Settings")


# ── Key storage ───────────────────────────────────────────────────────────────

def _load_secrets() -> dict:
    if SECRETS_FILE.exists():
        try:
            return json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def get_key(provider: str) -> str:
    prov = PROVIDERS.get(provider)
    if not prov:
        return ""
    env_var = prov.get("env_var", "")
    if env_var:
        val = os.environ.get(env_var, "").strip()
        if val:
            return val
    return _load_secrets().get(provider, "")


def set_key(provider: str, value: str) -> None:
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider}")
    secrets = _load_secrets()
    if value:
        secrets[provider] = value
    else:
        secrets.pop(provider, None)
    SECRETS_FILE.write_text(json.dumps(secrets, indent=2), encoding="utf-8")
    try:
        SECRETS_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return key[0] + "***"
    return key[:4] + "…" + key[-4:]


def key_status() -> dict:
    result = {}
    for pid, prov in PROVIDERS.items():
        key = get_key(pid)
        result[pid] = {
            "label":            prov["label"],
            "needs_key":        prov["needs_key"],
            "optional_key":     prov.get("optional_key", False),
            "configurable_url": prov.get("configurable_url", False),
            "default_base_url": prov.get("base_url", ""),
            "key_set":          bool(key),
            "masked":           _mask_key(key) if key else "",
        }
    return result


# ── Streaming ─────────────────────────────────────────────────────────────────

async def stream_chat(provider: str, model: str, messages: list, temperature: float = 0.7,
                      base_url: str = ""):
    """Async generator yielding text tokens. Dispatches by provider kind."""
    prov = PROVIDERS.get(provider)
    if not prov:
        raise ProviderError(provider, 400)
    if prov["kind"] == "anthropic":
        async for tok in _stream_anthropic(provider, prov, model, messages, temperature):
            yield tok
    else:
        async for tok in _stream_openai(provider, prov, model, messages, temperature,
                                        base_url=base_url):
            yield tok


async def _stream_openai(provider: str, prov: dict, model: str, messages: list, temperature: float,
                         base_url: str = ""):
    key = get_key(provider)
    headers: dict = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    elif prov.get("configurable_url"):
        # Some local servers require a non-empty bearer even when they ignore it.
        headers["Authorization"] = "Bearer local"
    effective_url = (base_url or prov["base_url"]).rstrip("/")
    body = {
        "model":       model,
        "messages":    messages,
        "temperature": temperature,
        "stream":      True,
    }
    url = effective_url + "/chat/completions"
    async with httpx.AsyncClient() as client:
        async with client.stream("POST", url, headers=headers, json=body, timeout=None) as r:
            if r.status_code >= 400:
                err_body = await r.aread()
                raise ProviderError(provider, r.status_code,
                                    err_body.decode("utf-8", errors="replace"))
            async for line in r.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                    tok = (obj.get("choices") or [{}])[0].get("delta", {}).get("content") or ""
                    if tok:
                        yield tok
                except Exception:
                    continue


async def _stream_anthropic(provider: str, prov: dict, model: str, messages: list, temperature: float):
    key = get_key(provider)
    headers: dict = {
        "Content-Type":      "application/json",
        "anthropic-version": "2023-06-01",
    }
    if key:
        headers["x-api-key"] = key
    system_parts = [m["content"] for m in messages if m.get("role") == "system"]
    chat_messages = [m for m in messages if m.get("role") != "system"]
    body: dict = {
        "model":       model,
        "max_tokens":  4096,
        "messages":    chat_messages,
        "temperature": temperature,
        "stream":      True,
    }
    if system_parts:
        body["system"] = "\n\n".join(system_parts)
    url = prov["base_url"].rstrip("/") + "/v1/messages"
    async with httpx.AsyncClient() as client:
        async with client.stream("POST", url, headers=headers, json=body, timeout=None) as r:
            if r.status_code >= 400:
                err_body = await r.aread()
                raise ProviderError(provider, r.status_code,
                                    err_body.decode("utf-8", errors="replace"))
            async for line in r.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                try:
                    obj = json.loads(data)
                    if obj.get("type") == "content_block_delta":
                        tok = obj.get("delta", {}).get("text") or ""
                        if tok:
                            yield tok
                except Exception:
                    continue


async def complete(provider: str, model: str, messages: list, temperature: float = 0.3,
                   base_url: str = "") -> str:
    """Accumulates the stream into a single string. For non-streaming uses (import)."""
    parts: list = []
    async for tok in stream_chat(provider, model, messages, temperature, base_url=base_url):
        parts.append(tok)
    return "".join(parts)


# ── Model listing ─────────────────────────────────────────────────────────────

async def list_models(provider: str, base_url: str = "") -> list:
    """Returns sorted list of model IDs, or [] if unavailable / no key / not implemented."""
    prov = PROVIDERS.get(provider)
    if not prov:
        return []

    # Configurable-URL providers: try GET /models against the effective base URL.
    # Falls back to [] silently if the server does not implement the endpoint.
    if prov.get("configurable_url"):
        effective_url = (base_url or prov.get("base_url", "")).rstrip("/")
        if not effective_url:
            return []
        key = get_key(provider)
        hdrs: dict = {}
        if key:
            hdrs["Authorization"] = f"Bearer {key}"
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get(effective_url + "/models", headers=hdrs, timeout=5)
                if r.status_code >= 400:
                    return []
                data = r.json().get("data", [])
                return sorted(m["id"] for m in data if isinstance(m, dict) and "id" in m)
        except Exception:
            return []

    if not prov["needs_key"]:
        # Ollama uses its own tags endpoint instead of /models
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get("http://localhost:11434/api/tags", timeout=5)
                return sorted(m["name"] for m in r.json().get("models", []))
        except Exception:
            return []

    key = get_key(provider)
    if not key:
        return []

    try:
        if prov["kind"] == "anthropic":
            url = prov["base_url"].rstrip("/") + "/v1/models"
            hdrs = {"x-api-key": key, "anthropic-version": "2023-06-01"}
            async with httpx.AsyncClient() as c:
                r = await c.get(url, headers=hdrs, timeout=10)
                if r.status_code >= 400:
                    return []
                return sorted(m["id"] for m in r.json().get("data", []))
        else:
            url = prov["base_url"].rstrip("/") + "/models"
            hdrs = {"Authorization": f"Bearer {key}"}
            async with httpx.AsyncClient() as c:
                r = await c.get(url, headers=hdrs, timeout=10)
                if r.status_code >= 400:
                    return []
                return sorted(m["id"] for m in r.json().get("data", []))
    except Exception:
        return []
