import re
import os
import logging
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import AsyncOpenAI

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("YOMITSU-GRAMMAR")

app = FastAPI(title="Yomitsu Grammar Analysis Service")

MODEL = "gpt-4o-mini"
client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

SYSTEM_PROMPT = """\
Eres un lingüista japonés especializado que explica en español con el rigor de un buen \
diccionario académico. Estructura SIEMPRE tu respuesta con estas secciones exactas, \
separadas por línea en blanco:

DESGLOSE:
Enumera cada elemento gramatical relevante de la frase en orden: palabras, partículas y \
formas verbales/adjetivales. OMITE signos de puntuación (comas, puntos, 「」, etc.). \
Usa el formato: "- ELEMENTO (lectura, romaji) — significado y función". \
Para formas verbales y adjetivales conjugadas (て形, た形, ている, てしまう, ば条件, \
連用形, 可能形, 受身形, 使役形, etc.) explica siempre: (1) desde qué forma de diccionario \
se deriva y cómo se forma la terminación, (2) qué función gramatical cumple en esta frase. \
Para cada elemento relevante añade matices: diferencias con expresiones similares \
(ej: は vs が, さっき vs 先ほど, 〜てしまう vs 〜てしまった), \
registro (formal/coloquial/escrito), connotaciones, restricciones de uso. \
Marca la palabra objetivo con ★ al inicio de su línea.

ESTRUCTURA:
Describe la arquitectura completa: qué modifica a qué, tipo de cláusulas, \
orden de constituyentes, y si hay algún patrón gramatical notable \
(potencial, causativa, pasiva, condicional, etc.) explica su construcción y uso.

Sin introducción. Directo al análisis. Tan extenso como sea necesario para ser preciso.\
"""


def _extract_sentence(context: str, target_word: str) -> str:
    parts = re.split(r'(?<=[。！？])', context)
    sentences = []
    for part in parts:
        sentences.extend(part.split('\n'))
    for sentence in sentences:
        s = sentence.strip()
        if s and target_word in s:
            return s
    return context.strip()


class GrammarRequest(BaseModel):
    context_phrase: str
    target_word: str
    part_of_speech: str


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL}


@app.post("/analyze-grammar")
async def analyze_grammar(request: GrammarRequest):
    sentence = _extract_sentence(request.context_phrase, request.target_word)
    logger.info(f"[GRAMMAR] '{request.target_word}' en '{sentence}'")

    user_prompt = (
        f"Frase: 「{sentence}」\n"
        f"Palabra objetivo: 「{request.target_word}」({request.part_of_speech})\n\n"
        "Analiza esta frase siguiendo el esquema del sistema."
    )

    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            max_tokens=2500,
            temperature=0.3,
        )
        analysis = response.choices[0].message.content.strip()
        logger.info(f"[GRAMMAR] OK — {len(analysis)} chars")
        return {
            "grammar_analysis": analysis,
            "model": MODEL,
            "status": "success",
        }
    except Exception as e:
        logger.error(f"[GRAMMAR] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
