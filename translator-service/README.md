# Translator Service

Translates Japanese sentences to Spanish using GPT-4.1-mini. Accepts the sentence to translate, the target word, its part of speech, and optional manga page OCR context for better accuracy.

## Endpoints

- `POST /translate` — single translation response
- `POST /stream-translate` — streaming response (used by the plugin)
- `GET /health`

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...
uvicorn main:app --host 0.0.0.0 --port 8001
```

Or via Docker Compose from the repo root (recommended).
