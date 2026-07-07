# Dictionary Service

Tokenizes Japanese text with SudachiPy (Mode C normalization) and looks up the normalized form in Jitendex (Yomitan JSON format) and Kenkyusha (MDX). Both dictionaries are loaded entirely into memory at startup.

## Endpoints

- `POST /extract-word` — full dictionary lookup, returns XHTML entries
- `POST /tokenize` — tokenize only, returns part of speech and romaji
- `GET /health`

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Or via Docker Compose from the repo root (recommended).

## Dictionary files

Place files at:

```
dictionaries/
├── jitendex-yomitan/
│   └── term_bank_*.json
└── 研究社和英大辞典/
    └── 研究社新和英大辞典.mdx
```
