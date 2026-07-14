# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

Yomitsu is a self-hosted Japanese immersion ecosystem for KOReader. A user long-presses a word in KOReader → the Lua plugin intercepts → calls the orchestrator → the orchestrator fans out concurrently to a dictionary service and an AI translator → the combined result appears as navigable dictionary entries inside KOReader's built-in `DictQuickLookup` widget.

## Architecture

Five components, each independent:

| Component | Language | Port | Role |
|---|---|---|---|
| `dictionary-service/` | Python / FastAPI | 8000 | Tokenizes with SudachiPy, looks up Jitendex + Kenkyusha MDX |
| `translator-service/` | Python / FastAPI | 8001 | OpenAI `gpt-4.1-mini` — JP→ES contextual translation |
| `orchestrator-service/` | Python / FastAPI | 8002 | Fans out to all services concurrently, merges results |
| `grammar-analysis-service/` | Python / FastAPI | 8003 | OpenAI `gpt-4.1-mini` — grammar breakdown + JLPT annotation |
| `yomitsu.koplugin/` | Lua | — | KOReader plugin, monkey-patches `ReaderDictionary` |

**Request flow:**
```
KOReader word tap
  → yomitsu.koplugin/main.lua (POST /analyze)
    → orchestrator-service/main.py
      ├─ POST /extract-word  → dictionary-service/main.py
      │     SudachiPy (normalize) → Jitendex lookup + Kenkyusha MDX lookup (parallel)
      │     → kindle_formatter.py / kenkyusha_formatter.py → XHTML
      ├─ POST /stream-translate  → translator-service/main.py
      │     OpenAI gpt-4.1-mini → JP→ES translation (streamed)
      └─ POST /stream-grammar  → grammar-analysis-service/main.py
            OpenAI gpt-4.1-mini → BREAKDOWN + STRUCTURE + ROMAJI (streamed)
  → DictQuickLookup: [Yomitsu IA] [Jitendex] [研究社]
```

## Running the services

Each service has its own `venv`. Run from within each service directory:

```bash
# Dictionary service (port 8000)
cd dictionary-service && source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000

# Translator service (port 8001)
cd translator-service && source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8001

# Orchestrator service (port 8002)
cd orchestrator-service && source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8002

# Grammar analysis service (port 8003)
cd grammar-analysis-service && source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8003
```

All AI services (translator, grammar, orchestrator) require `OPENAI_API_KEY` in their `.env`.

## Installing dependencies (first time)

```bash
cd <service-dir>
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Key implementation details

**Dictionary loading:** Both Jitendex and Kenkyusha are loaded entirely into memory at startup via FastAPI's `lifespan` hook. Jitendex is split across multiple `term_bank_*.json` files and indexed by surface form. Kenkyusha is read from MDX with `readmdict`. Lookup keys are always the **SudachiPy normalized form** (Mode C) of the user's selection.

**HTML output constraints:** Both `kindle_formatter.py` and `kenkyusha_formatter.py` produce XHTML 1.1 with inline CSS. CSS must be CSS 2.1 / MuPDF-compatible — no flexbox, no grid, no CSS variables.

**Lua plugin:** `yomitsu.koplugin/main.lua` monkey-patches `ReaderDictionary.onLookupWord` (or `.lookup` on older KOReader builds). The orchestrator URL is hardcoded as `ORCHESTRATOR_URL`; update this to your server's LAN IP. The plugin only activates for Japanese text (detected by Unicode range or document language metadata).

**Dictionary files are gitignored.** Place them at:
- `dictionary-service/dictionaries/jitendex-yomitan/term_bank_*.json` (Jitendex Yomitan format)
- `dictionary-service/dictionaries/研究社和英大辞典/研究社新和英大辞典.mdx` (Kenkyusha MDX)

**AI models:** All AI calls go to OpenAI, all on `gpt-5.6-terra` (env `TRANSLATOR_MODEL` / `GRAMMAR_MODEL` / `VISION_MODEL`). Do NOT use the bare `gpt-5.6` alias — it routes to Sol (the expensive flagship). GPT-5.x API: use `max_completion_tokens` (never `max_tokens`), no `temperature`, and set `reasoning_effort` explicitly ("none" for translation, "low" elsewhere) or the default medium reasoning eats the token budget and returns empty responses.

**Vision page context:** On each Mokuro page flip, the plugin screenshots the rendered page (`Screen:shot`), base64-encodes it, and POSTs it with the raw OCR to the orchestrator's `/analyze-page-context`. The vision model returns a corrected transcript (reading order, speakers) + scene description, which replaces the raw OCR as `page_context` for all lookups and prewarm calls on that page. Cached server-side in `sentence_cache.json` by `book:page` (`pagectx` key) so re-reads never pay vision twice. Warm-page **text** stays raw Mokuro OCR — server cache keys must match the sentences KOReader sends on tap.

**Service URLs** are hardcoded constants at the top of each `main.py`. The orchestrator calls `http://localhost:8000` and `http://localhost:8001`; these must be updated if services run on different hosts.
