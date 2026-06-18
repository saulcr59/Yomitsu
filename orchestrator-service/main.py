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

DICT_TOKENIZE_URL      = "http://localhost:8000/tokenize"
DICTIONARY_SERVICE_URL = "http://localhost:8000/extract-word"
TRANSLATOR_SERVICE_URL = "http://localhost:8001/translate"
GRAMMAR_SERVICE_URL    = "http://localhost:8003/analyze-grammar"


class LookUpAndTranslateRequest(BaseModel):
    raw_text: str
    target_word: str
    word_offset: int | None = None  # byte offset of target_word in raw_text


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/analyze")
async def analyze_word_with_context(request: LookUpAndTranslateRequest):
    logger.info("================== NUEVA PETICIÓN ==================")
    logger.info(f"[CORE] Palabra: '{request.target_word}'")

    tokenize_payload = {
        "context_phrase": request.raw_text,
        "user_selection": request.target_word,
        "char_offset":    request.word_offset,
    }

    # Fase 1: tokenización rápida con Sudachi (~50ms) para obtener el POS real
    pos = "unknown"
    romaji_sentence = ""
    async with httpx.AsyncClient(timeout=5.0) as tok_client:
        try:
            tok_res = await tok_client.post(DICT_TOKENIZE_URL, json=tokenize_payload)
            tok_res.raise_for_status()
            tok_json = tok_res.json()
            pos             = tok_json.get("part_of_speech", "unknown")
            romaji_sentence = tok_json.get("romaji_sentence", "")
            logger.info(f"[TOKENIZE] POS={pos} romaji='{romaji_sentence[:40]}'")
        except Exception as e:
            logger.error(f"[TOKENIZE-ERROR] {e}")

    dict_payload = {
        "context_phrase": request.raw_text,
        "user_selection": request.target_word,
        "char_offset":    request.word_offset,
    }

    translator_payload = {
        "context_phrase": request.raw_text,
        "target_word":    request.target_word,
        "part_of_speech": pos,
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

    grammar_payload = {
        "context_phrase": request.raw_text,
        "target_word":    request.target_word,
        "part_of_speech": pos,
    }

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

    async def fetch_grammar(client: httpx.AsyncClient):
        logger.info("[ASYNC] -> Disparando petición a Gramática...")
        try:
            res = await client.post(GRAMMAR_SERVICE_URL, json=grammar_payload)
            res.raise_for_status()
            logger.info("[ASYNC] <- Gramática OK.")
            return res.json()
        except Exception as e:
            logger.error(f"[GRAMMAR-ERROR] {str(e)}")
            return {}

    # Fase 2: diccionario + traducción + gramática en paralelo
    async with httpx.AsyncClient(timeout=22.0) as client:
        dict_data, trans_data, grammar_data = await asyncio.gather(
            fetch_dictionary(client),
            fetch_translator(client),
            fetch_grammar(client)
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
            "wisdom": {
                "html":  dictionary_data.get("wisdom", {}).get("html_content", ""),
                "found": dictionary_data.get("wisdom", {}).get("found", False),
            },
            "genius": {
                "html":  dictionary_data.get("genius", {}).get("html_content", ""),
                "found": dictionary_data.get("genius", {}).get("found", False),
            },
            "grammar": {
                "html":  dictionary_data.get("grammar", {}).get("html_content", ""),
                "found": dictionary_data.get("grammar", {}).get("found", False),
            },
        },
        "ai_contextual_analysis": {
            "translation_and_nuance": trans_data.get(
                "translation_raw", "Error en el análisis de la IA"
            ),
            "source_sentence": trans_data.get("source_sentence", ""),
            "model_used": trans_data.get("model_used", "Unknown"),
        },
        "grammar_analysis": {
            "analysis": grammar_data.get("grammar_analysis", ""),
            "model":    grammar_data.get("model", ""),
        },
        "frequency":       dictionary_data.get("frequency", {}),
        "romaji_sentence": romaji_sentence,
        "status": "success"
    }

    logger.info(f"[CORE] Flujo terminado para '{normalized_word}'.")
    logger.info("=====================================================")
    return final_response
