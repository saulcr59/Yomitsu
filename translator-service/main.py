import os
import re
import json
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
logger = logging.getLogger("YOMITSU-TRANSLATOR")

OLLAMA_API_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
MODEL_NAME = "hf.co/unsloth/Hy-MT2-7B-GGUF:UD-Q4_K_XL"

logger.info(f"Iniciando servicio. Endpoint de Ollama configurado en: {OLLAMA_API_URL}")
logger.info(f"Modelo LLM seleccionado: {MODEL_NAME}")

_http_client: httpx.AsyncClient = None  # type: ignore[assignment]  # assigned by lifespan


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client
    _http_client = httpx.AsyncClient(timeout=30.0)
    yield
    await _http_client.aclose()


app = FastAPI(
    title="Yomitsu Translator Service",
    description="Local LLM interface for contextual Japanese-to-Spanish translations using Hy-MT2",
    lifespan=lifespan,
)


def _extract_sentence(context: str, target_word: str, original_word: str = "") -> str:
    parts = re.split(r'(?<=[。！？])', context)
    sentences = []
    for part in parts:
        sentences.extend(part.split('\n'))
    candidates = [w for w in (target_word, original_word) if w]
    for sentence in sentences:
        s = sentence.strip()
        if s and any(w in s for w in candidates):
            return s
    return context.strip()


def _build_prompt(sentence: str) -> str:
    return (
        f"<system>\n    You are an expert Japanese-to-Spanish translator. "
        f"Translate naturally and concisely.\n    </system>\n"
        f"    <user>\n    Translate this Japanese sentence to Spanish: {sentence}\n    </user>\n"
        f"    <assistant>"
    )


_STOP_TOKENS = ["<user>", "</user>", "<system>", "</system>", "<assistant>", "\n\n\n"]


class TranslationRequest(BaseModel):
    context_phrase: str
    target_word: str
    original_word: str = ""
    part_of_speech: str


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL_NAME, "ollama_url": OLLAMA_API_URL}


@app.post("/translate")
async def translate_context(request: TranslationRequest):
    logger.info("--- Nueva petición de traducción recibida ---")
    logger.info(f"[ENDPOINT] Palabra objetivo: '{request.target_word}' ({request.part_of_speech})")

    source_sentence = _extract_sentence(request.context_phrase, request.target_word, request.original_word)
    logger.info(f"[ENDPOINT] Frase extraída: '{source_sentence}'")

    payload = {
        "model": MODEL_NAME,
        "prompt": _build_prompt(source_sentence),
        "stream": False,
        "options": {"temperature": 0.3, "stop": _STOP_TOKENS},
    }

    logger.info(f"[OLLAMA] Enviando prompt al modelo '{MODEL_NAME}'...")

    try:
        response = await _http_client.post(OLLAMA_API_URL, json=payload)
        response.raise_for_status()

        llm_output = response.json().get("response", "").strip()
        logger.info("[OLLAMA] ¡Respuesta del LLM recibida con éxito!")
        logger.info(f"[OLLAMA] Output crudo:\n---\n{llm_output}\n---")

        return {
            "translation_raw": llm_output,
            "source_sentence": source_sentence,
            "model_used": MODEL_NAME,
            "status": "success"
        }
    except httpx.HTTPError as e:
        logger.error(f"[OLLAMA] ERROR crítico de comunicación: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to communicate with Ollama: {str(e)}")


@app.post("/stream-translate")
async def stream_translate(request: TranslationRequest):
    source_sentence = _extract_sentence(request.context_phrase, request.target_word, request.original_word)
    logger.info(f"[TRANS-STREAM] '{request.target_word}'")

    payload = {
        "model": MODEL_NAME,
        "prompt": _build_prompt(source_sentence),
        "stream": True,
        "options": {"temperature": 0.3, "stop": _STOP_TOKENS},
    }

    meta = json.dumps({"s": source_sentence, "m": MODEL_NAME}, ensure_ascii=False)

    async def generate():
        yield f"\x01{meta}\x01\n"
        try:
            async with _http_client.stream("POST", OLLAMA_API_URL, json=payload) as resp:
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        text = json.loads(line).get("response", "")
                        if text:
                            yield text
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"[TRANS-STREAM] Error: {e}")

    return StreamingResponse(generate(), media_type="text/plain")
