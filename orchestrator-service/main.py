import json
import os
import httpx
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("YOMITSU-ORCHESTRATOR")

GRAMMAR_MODEL = "gpt-4.1-mini"

_http_client: httpx.AsyncClient = None  # type: ignore[assignment]  # assigned by lifespan


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client
    _http_client = httpx.AsyncClient(timeout=5.0)
    yield
    await _http_client.aclose()


app = FastAPI(
    title="Yomitsu Orchestrator Service",
    description="The central brain of Yomitsu.",
    lifespan=lifespan,
)

_DICT_BASE       = os.environ.get("DICT_URL",       "http://localhost:8000")
_TRANSLATOR_BASE = os.environ.get("TRANSLATOR_URL", "http://localhost:8001")
_GRAMMAR_BASE    = os.environ.get("GRAMMAR_URL",    "http://localhost:8003")

DICT_TOKENIZE_URL        = f"{_DICT_BASE}/tokenize"
DICTIONARY_SERVICE_URL   = f"{_DICT_BASE}/extract-word"
TRANSLATOR_SERVICE_URL   = f"{_TRANSLATOR_BASE}/translate"
TRANSLATOR_STREAM_URL    = f"{_TRANSLATOR_BASE}/stream-translate"
GRAMMAR_SERVICE_URL      = f"{_GRAMMAR_BASE}/analyze-grammar"
GRAMMAR_STREAM_URL       = f"{_GRAMMAR_BASE}/stream-grammar"


class LookUpAndTranslateRequest(BaseModel):
    raw_text: str
    target_word: str
    word_offset: int | None = None


class AnalyzeAiRequest(BaseModel):
    context_phrase: str
    target_word: str
    original_word: str
    part_of_speech: str


async def _tokenize(context_phrase: str, user_selection: str, char_offset=None) -> tuple[str, str]:
    """Returns (part_of_speech, romaji_sentence). Returns ("unknown", "") on error."""
    try:
        res = await _http_client.post(DICT_TOKENIZE_URL, json={
            "context_phrase": context_phrase,
            "user_selection": user_selection,
            "char_offset":    char_offset,
        })
        res.raise_for_status()
        data = res.json()
        return data.get("part_of_speech", "unknown"), data.get("romaji_sentence", "")
    except Exception as e:
        logger.error(f"[TOKENIZE-ERROR] {e}")
        return "unknown", ""


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/analyze-translation-stream")
async def analyze_translation_stream_ep(request: AnalyzeAiRequest):
    """Streaming proxy: forwards /stream-translate from the translator service.
    First chunk is \\x01{json_meta}\\x01\\n, then raw Spanish text tokens."""
    trans_payload = {
        "context_phrase": request.context_phrase,
        "target_word":    request.target_word,
        "original_word":  request.original_word,
        "part_of_speech": request.part_of_speech,
    }

    async def generate():
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                async with client.stream("POST", TRANSLATOR_STREAM_URL, json=trans_payload) as resp:
                    async for chunk in resp.aiter_text():
                        if chunk:
                            yield chunk
        except Exception as e:
            logger.error(f"[TRANS-STREAM-PROXY] {e}")

    return StreamingResponse(generate(), media_type="text/plain")


@app.post("/analyze-grammar-stream")
async def analyze_grammar_stream_ep(request: AnalyzeAiRequest):
    """Streaming proxy: yields SudachiPy romaji + model first as JSON (\\x01{...}\\x01\\n),
    then proxies GPT grammar tokens. Tokenize runs inside the generator to avoid
    blocking the ASGI handler slot during the ~50ms tokenize call."""
    grammar_payload = {
        "context_phrase": request.context_phrase,
        "target_word":    request.target_word,
        "original_word":  request.original_word,
        "part_of_speech": request.part_of_speech,
    }

    async def generate():
        _, romaji_sentence = await _tokenize(request.context_phrase, request.target_word)
        meta = {"romaji": romaji_sentence, "model": GRAMMAR_MODEL}
        yield f"\x01{json.dumps(meta, ensure_ascii=False)}\x01\n"
        try:
            async with httpx.AsyncClient(timeout=35.0) as client:
                async with client.stream("POST", GRAMMAR_STREAM_URL, json=grammar_payload) as resp:
                    async for chunk in resp.aiter_text():
                        if chunk:
                            yield chunk
        except Exception as e:
            logger.error(f"[GRAM-STREAM-PROXY] {e}")

    return StreamingResponse(generate(), media_type="text/plain")


@app.post("/analyze-dict")
async def analyze_dict_only(request: LookUpAndTranslateRequest):
    """Phase-1 proxy: just the dictionary lookup, no AI. Keeps Lua on port 8002 only."""
    dict_payload = {
        "context_phrase": request.raw_text,
        "user_selection": request.target_word,
        "char_offset":    request.word_offset,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            res = await client.post(DICTIONARY_SERVICE_URL, json=dict_payload)
            res.raise_for_status()
            return res.json()
        except Exception as e:
            logger.error(f"[DICT-PROXY-ERROR] {e}")
            raise HTTPException(status_code=502, detail=str(e))
