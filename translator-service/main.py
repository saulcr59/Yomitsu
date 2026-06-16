import httpx
import logging  # <-- 1. Importamos el módulo de logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# 2. Configuración global de logs con el mismo formato del diccionario
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("YOMITSU-TRANSLATOR")

app = FastAPI(
    title="Yomitsu Translator Service",
    description="Local LLM interface for contextual Japanese-to-Spanish translations using Hy-MT2"
)

OLLAMA_API_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "hf.co/unsloth/Hy-MT2-7B-GGUF:UD-Q4_K_XL"

logger.info(f"Iniciando servicio. Endpoint de Ollama configurado en: {OLLAMA_API_URL}")
logger.info(f"Modelo LLM seleccionado: {MODEL_NAME}")

class TranslationRequest(BaseModel):
    context_phrase: str
    target_word: str
    part_of_speech: str

@app.post("/translate")
async def translate_context(request: TranslationRequest):
    logger.info("--- Nueva petición de traducción recibida ---")
    logger.info(f"[ENDPOINT] Palabra objetivo: '{request.target_word}' ({request.part_of_speech})")
    logger.info(f"[ENDPOINT] Frase de contexto: '{request.context_phrase}'")

    # System instructions built directly into the prompt to prevent loop behaviors
    prompt = f"""<system>
    You are an expert Japanese-to-Spanish translator. Your task is to translate the sentence and explain the target word.
    </system>
    <user>
    Context sentence: {request.context_phrase}
    Target word: {request.target_word} ({request.part_of_speech})

    Provide:

    1. A natural Spanish translation of the full context sentence.
    2. A brief, 1-sentence explanation of the target word's nuance in this context.
    </user>
    <assistant>"""

    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.3,  # Low temperature ensures translation accuracy over creativity
            "stop": [
                "<user>",
                "</user>",
                "<system>",
                "</system>",
                "<assistant>",
                "\n\n\n",
            ],  # Safety stop tokens
        },
    }

    logger.info(f"[OLLAMA] Enviando prompt al modelo '{MODEL_NAME}'... (El LLM puede tardar unos segundos en procesar)")

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(OLLAMA_API_URL, json=payload)
            response.raise_for_status()

            result = response.json()
            llm_output = result.get("response", "").strip()

            logger.info("[OLLAMA] ¡Respuesta del LLM recibida con éxito!")
            logger.info(f"[OLLAMA] Output crudo generado:\n---\n{llm_output}\n---")

            return {
                "translation_raw": llm_output,
                "model_used": MODEL_NAME,
                "status": "success"
            }

        except httpx.HTTPError as e:
            logger.error(f"[OLLAMA] ERROR crítico de comunicación con la API de Ollama: {str(e)}")
            raise HTTPException(
                status_code=500, 
                detail=f"Failed to communicate with local Ollama instance: {str(e)}"
            )
