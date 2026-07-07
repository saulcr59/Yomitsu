# Orchestrator Service

Central entry point for the KOReader plugin. Receives lookup requests and fans out concurrently to the dictionary service, translator service, and grammar analysis service, then merges the results.

## Endpoints

- `POST /analyze-dict` — dictionary lookup (proxies to dictionary-service)
- `POST /analyze-translation-stream` — streaming translation (proxies to translator-service)
- `POST /analyze-grammar-stream` — streaming grammar breakdown (proxies to grammar-analysis-service)
- `GET /health`

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...
uvicorn main:app --host 0.0.0.0 --port 8002
```

Or via Docker Compose from the repo root (recommended).

Service URLs default to `localhost` and can be overridden with environment variables:
`DICT_URL`, `TRANSLATOR_URL`, `GRAMMAR_URL`.
