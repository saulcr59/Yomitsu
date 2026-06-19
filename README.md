# Yomitsu

A self-hosted Japanese immersion ecosystem for KOReader. Long-press any word → dictionary entries (Jitendex, Kenkyusha, Wisdom, Genius) + contextual AI translation + grammar breakdown, all displayed inside KOReader's native dictionary widget.

## Architecture

| Service | Port | Role |
|---|---|---|
| `dictionary-service` | 8000 | SudachiPy tokenization · Jitendex · Kenkyusha · Wisdom · Genius · kanji breakdown |
| `translator-service` | 8001 | Ollama / Hy-MT2-7B — JP→ES contextual translation |
| `grammar-analysis-service` | 8003 | OpenAI GPT — sentence grammar breakdown in Spanish |
| `orchestrator-service` | 8002 | Fans out to all services, merges results |
| `yomitsu.koplugin` | — | KOReader Lua plugin |

## Requirements

- Ubuntu 20.04+ (server or desktop)
- An [OpenAI API key](https://platform.openai.com/api-keys) (for grammar analysis)
- ~8 GB of free disk space for the Hy-MT2 model
- Dictionary files (see [Dictionary files](#dictionary-files))

---

## Installation (Ubuntu — one command)

```bash
curl -fsSL https://raw.githubusercontent.com/saulcr59/Yomitsu/main/install.sh | sudo bash
```

The repo is cloned to `/opt/yomitsu`. The script will:

1. Install Docker Engine and the Compose plugin
2. Install Ollama and download the `Hy-MT2-7B` translation model
3. Ask for your `OPENAI_API_KEY` and write `.env`
4. Build and start all four services with `docker compose up -d`

> **Note:** The model download (~5 GB) can take several minutes depending on your connection.

---

## Dictionary files

The dictionary files are not included in this repository. Place them at the following paths **before** starting the services:

```
dictionary-service/dictionaries/
├── jitendex-yomitan/
│   └── term_bank_*.json          # Jitendex (Yomitan format)
├── 研究社和英大辞典/
│   └── 研究社新和英大辞典.mdx
├── 三省堂 ウィズダム和英辞典 第3版/
│   └── SANWIZJ3.mdx
├── 大修館 ジーニアス和英辞典 第3版/
│   └── GENIUSJ3.mdx
├── JPDB_v2.2_Frequency_Kana_2024-10-13/
│   └── term_meta_bank_1.json
├── BCCWJ_SUW_LUW_combined/
│   └── term_meta_bank_1.json
└── The Japan Times - Dictionary of Japanese Grammar (Jpn-Eng-Jpn) (MDX)/
    └── (The Japan Times) A Dictionary of Japanese Grammar [Complete Edition].mdx
```

When running via Docker the `dictionaries/` folder is mounted as a read-only volume, so you can place files there without rebuilding the image.

---

## KOReader plugin

1. Copy `yomitsu.koplugin/` to your KOReader `plugins/` directory.
2. Open `yomitsu.koplugin/main.lua` and set `ORCHESTRATOR_URL` to the LAN IP of your server:

```lua
local ORCHESTRATOR_URL = "http://192.168.1.X:8002/analyze-dict"
```

3. Restart KOReader. Long-press any Japanese word to trigger a lookup.

---

## Managing the services

```bash
cd /opt/yomitsu

docker compose logs -f          # live logs
docker compose restart          # restart all services
docker compose down             # stop
docker compose up -d            # start
docker compose up --build -d    # rebuild and start (after a git pull)
```

To update:

```bash
cd /opt/yomitsu
git pull
docker compose up --build -d
```

---

## Manual installation (without Docker)

Each service has its own virtualenv. Run from within each service directory:

```bash
cd <service-dir>
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port <port>
```

Ollama must be running separately (`ollama serve`) with the model pulled:

```bash
ollama pull hf.co/unsloth/Hy-MT2-7B-GGUF:UD-Q4_K_XL
```

Set `OPENAI_API_KEY` in your shell environment before starting `grammar-analysis-service`.
