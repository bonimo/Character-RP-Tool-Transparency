# Setup

## Prerequisites

- Python 3.10 or later. The repository does not pin a version, but 3.10 or later is required by the dependency set.
- A way to run a model: either [Ollama](https://ollama.com) for local inference with no key required, or an API key for one of the supported cloud providers.

The server binds to `127.0.0.1` only, so it is accessible from your own machine and not exposed to your local network.

## Local path: Ollama

1. Install Ollama from [ollama.com](https://ollama.com).
2. Pull a model. The tool works with any model Ollama can serve. Example: `ollama pull gemma3:12b`.
3. Ollama runs as a background service on port 11434. Make sure it is running before starting the tool.

The appraisal pass (the reasoning call) and the voice pass (the reply call) can each be set to a different model. Both default to Ollama on first run. You change them independently in Settings under "Model and Temperature".

## Local path: other OpenAI-compatible servers

Any server that speaks the OpenAI chat-completions API works. In Settings → Model and Temperature, pick the preset for your server or choose "Custom (OpenAI-compatible)" and type the address.

| Preset | Default address | Notes |
|---|---|---|
| LM Studio | `http://localhost:1234/v1` | Enable the local server from LM Studio's "Local Server" tab |
| llama.cpp server | `http://localhost:8080/v1` | Start with `./llama-server -m model.gguf` |
| Oobabooga (text-generation-webui) | `http://localhost:5000/v1` | Enable the OpenAI extension in the Extensions tab |
| KoboldCpp | `http://localhost:5001/v1` | Exposed automatically when KoboldCpp runs |
| vLLM | `http://localhost:8000/v1` | Start with `python -m vllm.entrypoints.openai.api_server` |
| Custom (OpenAI-compatible) | *(you type it)* | Any other server that implements the OpenAI chat-completions spec |

The base URL is editable — the presets just prefill a sensible default. If your server listens on a non-standard port, change it in the Base URL field and save.

**API key**: local servers usually ignore authentication. Leave the key blank; the tool sends a harmless `Bearer local` placeholder so the HTTP header is well-formed. If your server requires a real key, paste it in Settings → API Keys under the server's name.

**Model list**: the tool tries to fetch the model list from `/models`. If the server does not implement that endpoint it returns an empty list and you can type the model name directly in the Model field.

## Cloud path

Supported cloud providers:

| Provider | Key environment variable |
|---|---|
| OpenAI (ChatGPT) | `OPENAI_API_KEY` |
| Anthropic (Claude) | `ANTHROPIC_API_KEY` |
| Google (Gemini) | `GEMINI_API_KEY` |
| xAI (Grok) | `XAI_API_KEY` |
| Groq | `GROQ_API_KEY` |

You can add keys through the Settings panel in the UI, or by creating a `secrets.json` file at the repository root (see below). Ollama requires no key.

## Install dependencies

The repository does not include a `requirements.txt` or a pre-built virtual environment. Create one yourself:

```
python -m venv .venv
.venv\Scripts\activate       # Windows
.venv\Scripts\pip install fastapi==0.138.0 uvicorn==0.49.0 httpx==0.28.1
```

If you plan to use the evaluation harness (`eval/run.py`), also install the Ollama Python package:

```
.venv\Scripts\pip install ollama
```

## API keys

API keys are stored server-side in `secrets.json` at the repository root. This file is listed in `.gitignore` and must never be committed.

To add keys manually, create `secrets.json` with this structure (include only the keys you need):

```json
{
  "openai":    "sk-...",
  "anthropic": "sk-ant-...",
  "gemini":    "...",
  "xai":       "...",
  "groq":      "..."
}
```

Alternatively, set keys through the Settings panel in the UI. Saving a key through the UI writes it to the same `secrets.json` file. You can also provide keys as environment variables; the backend reads environment variables first and falls back to `secrets.json`.

Keys are never sent to the frontend. The UI receives only a masked preview showing the first four characters and the last four.

## Run

Double-click `run.bat` from the repository root, or run this command from a terminal at the root:

```
.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

`run.bat` also opens `http://127.0.0.1:8000` in your default browser automatically.

## First use

Select or create a character in the left sidebar. Click "New Scene" to choose a genre, configure or roll scene seeds, and optionally select a generated objective. The tool will write an opening message and you can start responding. The inner-state panel on the right updates after each exchange.

## Troubleshooting

**Model not found (Ollama).** The model name in Settings must exactly match what `ollama list` shows. Pull the model with `ollama pull <name>` if it is missing.

**API key rejected.** Open Settings, go to API Keys, and re-enter the key. Verify the key is active and has credits in your provider's dashboard.

**Port 8000 already in use.** Another process is listening on that port. Either stop it, or start the server on a different port:

```
.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8001
```

Then open `http://127.0.0.1:8001` instead.
