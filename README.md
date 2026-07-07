# Yomitsu

A self-hosted Japanese immersion ecosystem for KOReader. Long-press any word → dictionary entries (Jitendex, Kenkyusha) + contextual AI translation + grammar breakdown, all displayed inside KOReader's native dictionary widget.

## Architecture

| Service | Port | Role |
|---|---|---|
| `dictionary-service` | 8000 | SudachiPy tokenization · Jitendex · Kenkyusha MDX lookup |
| `translator-service` | 8001 | GPT-4.1-mini — JP→ES contextual translation |
| `grammar-analysis-service` | 8003 | GPT-4.1-mini — sentence grammar breakdown in Spanish |
| `orchestrator-service` | 8002 | Fans out to all services, merges results |
| `yomitsu.koplugin` | — | KOReader Lua plugin |

**Request flow:**
```
KOReader word tap
  → yomitsu.koplugin (POST /analyze-dict + streaming AI calls)
    → orchestrator-service
      ├─ POST /analyze-dict  → dictionary-service
      │     SudachiPy (normalize) → Jitendex + Kenkyusha lookup
      │     → XHTML entries
      ├─ POST /analyze-translation-stream  → translator-service
      │     GPT-4.1-mini + manga page OCR context → Spanish translation
      └─ POST /analyze-grammar-stream  → grammar-analysis-service
            GPT-4.1-mini + manga page OCR context → grammar breakdown
```

## Requirements

- Docker + Docker Compose
- An [OpenAI API key](https://platform.openai.com/api-keys)
- Dictionary files (see [Dictionary files](#dictionary-files))

---

## Installation

```bash
git clone https://github.com/saulcr59/Yomitsu.git
cd Yomitsu
cp .env.example .env   # then add your OPENAI_API_KEY
docker compose up -d --build
```

---

## Dictionary files

The dictionary files are not included in this repository. Place them at the following paths **before** starting the services:

```
dictionary-service/dictionaries/
├── jitendex-yomitan/
│   └── term_bank_*.json          # Jitendex (Yomitan format)
└── 研究社和英大辞典/
    └── 研究社新和英大辞典.mdx
```

When running via Docker the `dictionaries/` folder is mounted as a read-only volume, so you can place files there without rebuilding the image.

---

## KOReader plugin

1. Copy `yomitsu.koplugin/` to your KOReader `plugins/` directory.
2. In KOReader, open the Yomitsu menu and set the server URL:
   - **Servidor**: your LAN IP (e.g. `192.168.1.X:8002`) for home use
   - **Servidor secundario**: your DDNS hostname and external port for remote use
   - Tap **Usar servidor secundario** to switch between them
3. Restart KOReader. Long-press any Japanese word to trigger a lookup.

> The plugin detects mokuro OCR data when reading manga (CBZ with `.json` sidecar) and passes the full page text to GPT for better translation and grammar context.

---

## Managing the services

```bash
docker compose logs -f          # live logs
docker compose restart          # restart all services
docker compose down             # stop
docker compose up -d            # start
docker compose up --build -d    # rebuild and start (after a git pull)
```

To update:

```bash
git pull
docker compose up --build -d
```

---

## Manual installation (without Docker)

Each service has its own virtualenv:

```bash
cd <service-dir>
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port <port>
```

Set `OPENAI_API_KEY` in your environment before starting `translator-service` and `grammar-analysis-service`.
