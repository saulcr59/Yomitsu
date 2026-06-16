import json
import asyncio
import logging
import glob
from fastapi import FastAPI
from pydantic import BaseModel
from sudachipy import dictionary, tokenizer
from contextlib import asynccontextmanager
from readmdict import MDX

from kindle_formatter import format_yomitan_to_html
from kenkyusha_formatter import format_kenkyusha_to_html

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("YOMITSU-BACKEND")

# ---------------------------------------------------------------------------
# Diccionarios en memoria
# ---------------------------------------------------------------------------
JITENDEX: dict[str, list] = {}
KENKYUSHA: dict[str, str] = {}


def load_jitendex():
    logger.info("Cargando Jitendex...")
    files = glob.glob("./dictionaries/jitendex-yomitan/term_bank_*.json")
    for file_path in files:
        with open(file_path, "r", encoding="utf-8") as f:
            for entry in json.load(f):
                word = entry[0]
                JITENDEX.setdefault(word, []).append(entry)
    logger.info(f"Jitendex cargado: {len(JITENDEX)} entradas únicas.")


def load_kenkyusha():
    logger.info("Cargando Kenkyusha MDX...")
    mdx_path = "./dictionaries/研究社和英大辞典/研究社新和英大辞典.mdx"
    try:
        mdx = MDX(mdx_path)
        for word_bytes, definition_bytes in mdx.items():
            try:
                word = word_bytes.decode("utf-8").strip()
                definition = definition_bytes.decode("utf-8").strip()
                if definition and not definition.startswith("@@@LINK"):
                    KENKYUSHA[word] = definition
            except Exception:
                pass
        logger.info(f"Kenkyusha cargado: {len(KENKYUSHA)} entradas.")
    except Exception as e:
        logger.error(f"Error cargando Kenkyusha: {e}")


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Iniciando aplicación...")
    load_jitendex()
    load_kenkyusha()
    yield
    logger.info("Apagando aplicación...")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="Yomitsu Dictionary Service", lifespan=lifespan)

logger.info("Cargando Sudachi...")
tokenizer_obj = dictionary.Dictionary().create()
mode = tokenizer.Tokenizer.SplitMode.C
logger.info("Sudachi listo (Modo C).")


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------
class TokenizeRequest(BaseModel):
    context_phrase: str
    user_selection: str


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
async def extract_word_with_sudachi(phrase: str, selection: str) -> dict:
    await asyncio.sleep(0)
    logger.info(f"[SUDACHI] Frase: '{phrase}' | Selección: '{selection}'")

    tokens = tokenizer_obj.tokenize(phrase, mode)
    for token in tokens:
        if selection in token.surface():
            result = {
                "original_word": token.surface(),
                "normalized_word": token.normalized_form(),
                "found": True,
            }
            logger.info(f"[SUDACHI] Match: '{result['original_word']}' → '{result['normalized_word']}'")
            return result

    logger.warning(f"[SUDACHI] No se encontró '{selection}'.")
    return {"original_word": selection, "normalized_word": selection, "found": False}


async def lookup_jitendex(word: str) -> dict:
    logger.info(f"[JITENDEX] Buscando '{word}'...")
    entries = JITENDEX.get(word)
    if entries:
        html = format_yomitan_to_html(entries)
        reading = entries[0][1] if entries else ""
        logger.info(f"[JITENDEX] Encontrado ({len(entries)} entradas).")
        return {"html_content": html, "reading": reading, "found": True}
    logger.info(f"[JITENDEX] No encontrado.")
    return {"html_content": "", "reading": "", "found": False}


async def lookup_kenkyusha(word: str) -> dict:
    logger.info(f"[KENKYUSHA] Buscando '{word}'...")
    definition = KENKYUSHA.get(word)
    if definition:
        html = format_kenkyusha_to_html(definition)
        logger.info(f"[KENKYUSHA] Encontrado.")
        return {"html_content": html, "found": True}
    logger.info(f"[KENKYUSHA] No encontrado.")
    return {"html_content": "", "found": False}


# ---------------------------------------------------------------------------
# Endpoint — devuelve HTMLs separados, sin combinar
# ---------------------------------------------------------------------------
@app.post("/extract-word")
async def extract_word(request: TokenizeRequest):
    logger.info("--- Nueva petición ---")
    logger.info(f"[ENDPOINT] Phrase: '{request.context_phrase}' | Selection: '{request.user_selection}'")

    # 1. Sudachi
    sudachi_result = await extract_word_with_sudachi(
        request.context_phrase, request.user_selection
    )
    target_word = sudachi_result["normalized_word"]

    # 2. Lookups en paralelo
    jitendex_result, kenkyusha_result = await asyncio.gather(
        lookup_jitendex(target_word),
        lookup_kenkyusha(target_word),
    )

    # 3. Respuesta con HTMLs separados
    response_payload = {
        "word_data": sudachi_result,
        "dictionary_data": {
            "reading": jitendex_result.get("reading", ""),
            "found": jitendex_result["found"] or kenkyusha_result["found"],
            "jitendex": {
                "html_content": jitendex_result["html_content"],
                "found": jitendex_result["found"],
            },
            "kenkyusha": {
                "html_content": kenkyusha_result["html_content"],
                "found": kenkyusha_result["found"],
            },
        },
    }

    logger.info(
        f"[ENDPOINT] Listo para '{target_word}'. "
        f"Jitendex={jitendex_result['found']} Kenkyusha={kenkyusha_result['found']}"
    )
    return response_payload
