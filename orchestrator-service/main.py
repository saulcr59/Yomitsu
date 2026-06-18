import httpx
import asyncio
import logging
from fastapi import FastAPI
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("YOMITSU-ORCHESTRATOR")

app = FastAPI(
    title="Yomitsu Orchestrator Service",
    description="The central brain of Yomitsu."
)

DICTIONARY_SERVICE_URL = "http://localhost:8000/extract-word"
TRANSLATOR_SERVICE_URL = "http://localhost:8001/translate"


class LookUpAndTranslateRequest(BaseModel):
    raw_text: str
    target_word: str


@app.post("/analyze")
async def analyze_word_with_context(request: LookUpAndTranslateRequest):
    logger.info("================== NUEVA PETICIÓN ==================")
    logger.info(f"[CORE] Palabra: '{request.target_word}'")

    dict_payload = {
        "context_phrase": request.raw_text,
        "user_selection": request.target_word
    }

    translator_payload = {
        "context_phrase": request.raw_text,
        "target_word": request.target_word,
        "part_of_speech": "Contextual"
    }

    async def fetch_dictionary(client: httpx.AsyncClient):
        logger.info("[ASYNC] -> Disparando petición a Diccionario...")
        try:
            res = await client.post(DICTIONARY_SERVICE_URL, json=dict_payload)
            res.raise_for_status()
            logger.info("[ASYNC] <- Diccionario OK.")
            return res.json()
        except Exception as e:
            logger.error(f"[DICT-ERROR] {str(e)}")
            return {}

    async def fetch_translator(client: httpx.AsyncClient):
        logger.info("[ASYNC] -> Disparando petición a LLM...")
        try:
            res = await client.post(TRANSLATOR_SERVICE_URL, json=translator_payload)
            res.raise_for_status()
            logger.info("[ASYNC] <- LLM OK.")
            return res.json()
        except Exception as e:
            logger.error(f"[LLM-ERROR] {str(e)}")
            return {}

    async with httpx.AsyncClient(timeout=25.0) as client:
        dict_data, trans_data = await asyncio.gather(
            fetch_dictionary(client),
            fetch_translator(client)
        )

    word_data       = dict_data.get("word_data", {})
    dictionary_data = dict_data.get("dictionary_data", {})
    normalized_word = word_data.get("normalized_word", request.target_word)

    final_response = {
        "word_searched":   request.target_word,
        "word_normalized": normalized_word,
        "reading":         dictionary_data.get("reading", ""),
        # HTMLs separados — el Lua construye una entrada por cada uno
        "dictionaries": {
            "jitendex": {
                "html":  dictionary_data.get("jitendex", {}).get("html_content", ""),
                "found": dictionary_data.get("jitendex", {}).get("found", False),
            },
            "kenkyusha": {
                "html":  dictionary_data.get("kenkyusha", {}).get("html_content", ""),
                "found": dictionary_data.get("kenkyusha", {}).get("found", False),
            },
        },
        "ai_contextual_analysis": {
            "translation_and_nuance": trans_data.get(
                "translation_raw", "Error en el análisis de la IA"
            ),
            "model_used": trans_data.get("model_used", "Unknown"),
        },
        "status": "success"
    }

    logger.info(f"[CORE] Flujo terminado para '{normalized_word}'.")
    logger.info("=====================================================")
    return final_response
