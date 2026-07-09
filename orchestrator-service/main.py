import asyncio
import json
import os
import re
import httpx
import logging
from collections import OrderedDict
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from openai import AsyncOpenAI

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("YOMITSU-ORCHESTRATOR")

GRAMMAR_MODEL = "gpt-4.1-mini"

_http_client: httpx.AsyncClient = None  # type: ignore[assignment]
_oai_client: AsyncOpenAI | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client, _oai_client
    _http_client = httpx.AsyncClient(timeout=5.0)
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        _oai_client = AsyncOpenAI(api_key=api_key)
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
TRANSLATOR_STREAM_URL    = f"{_TRANSLATOR_BASE}/stream-translate"
GRAMMAR_STREAM_URL       = f"{_GRAMMAR_BASE}/stream-grammar"

# ---------------------------------------------------------------------------
# Sentence-level LRU cache
# ---------------------------------------------------------------------------

_CACHE_MAX = 100


class _LRUCache:
    def __init__(self, maxsize: int = _CACHE_MAX):
        self._data: OrderedDict = OrderedDict()
        self._maxsize = maxsize

    def get(self, key: str):
        if key in self._data:
            self._data.move_to_end(key)
            return self._data[key]
        return None

    def set(self, key: str, value) -> None:
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = value
        if len(self._data) > self._maxsize:
            self._data.popitem(last=False)


_trans_cache = _LRUCache()
_gram_cache  = _LRUCache()


def _extract_sentence(context: str, target_word: str, original_word: str = "") -> str:
    """Return the single sentence from context that contains the target word."""
    parts = re.split(r'(?<=[。！？])', context)
    sentences: list[str] = []
    for part in parts:
        sentences.extend(part.split('\n'))
    candidates = [w for w in (target_word, original_word) if w]
    for sentence in sentences:
        s = sentence.strip()
        if s and any(w in s for w in candidates):
            return s
    return context.strip()


def _remap_star(grammar_text: str, old_target: str, new_target: str) -> str:
    """Move the ★ marker from old_target's DESGLOSE entry to new_target's."""
    if old_target == new_target:
        return grammar_text
    lines = grammar_text.split('\n')
    result = []
    star_placed = False
    for line in lines:
        if line.startswith('★ '):
            line = '- ' + line[2:]
        if not star_placed and line.startswith('- ') and new_target in line:
            line = '★ ' + line[2:]
            star_placed = True
        result.append(line)
    return '\n'.join(result)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class LookUpAndTranslateRequest(BaseModel):
    raw_text: str
    target_word: str
    word_offset: int | None = None


class AnalyzeAiRequest(BaseModel):
    context_phrase: str
    target_word: str
    original_word: str
    part_of_speech: str
    page_context: str = ""


class PageContextRequest(BaseModel):
    image_b64: str | None = None
    ocr_text: str = ""
    manga_title: str = ""


class WarmPageRequest(BaseModel):
    text: str


class AskRequest(BaseModel):
    question: str
    context_phrase: str
    target_word: str = ""
    page_context: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _tokenize(context_phrase: str, user_selection: str, char_offset=None) -> tuple[str, str]:
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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/analyze-page-context")
async def analyze_page_context(request: PageContextRequest):
    """Describes a manga page using GPT-4.1-mini vision. Called once per page by Lua,
    result is cached client-side and reused for all word lookups on that page."""
    if not _oai_client:
        raise HTTPException(status_code=503, detail="OpenAI not configured")

    content: list = []
    if request.image_b64:
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{request.image_b64}",
                "detail": "low",
            }
        })

    prompt = ""
    if request.manga_title:
        prompt += f"Manga: {request.manga_title}\n"
    if request.ocr_text:
        prompt += f"Text on this page: {request.ocr_text}\n"
    prompt += (
        "Briefly describe the scene in English (under 80 words): "
        "who is present, who is speaking to whom, their relationship, "
        "the emotional tone, and any relevant action or setting."
    )
    content.append({"type": "text", "text": prompt})

    try:
        response = await _oai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": content}],
            max_tokens=120,
            temperature=0.2,
        )
        description = (response.choices[0].message.content or "").strip()
        logger.info(f"[PAGE-CTX] {request.manga_title}: {description[:70]}")
        return {"page_context": description}
    except Exception as e:
        logger.error(f"[PAGE-CTX-ERROR] {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/analyze-translation-stream")
async def analyze_translation_stream_ep(request: AnalyzeAiRequest):
    sentence = _extract_sentence(request.context_phrase, request.target_word, request.original_word)

    cached = _trans_cache.get(sentence)
    if cached is not None:
        logger.info(f"[TRANS-HIT] '{sentence[:50]}'")
        async def from_cache():
            yield cached
        return StreamingResponse(from_cache(), media_type="text/plain")

    trans_payload = {
        "context_phrase": request.context_phrase,
        "target_word":    request.target_word,
        "original_word":  request.original_word,
        "part_of_speech": request.part_of_speech,
        "page_context":   request.page_context,
    }
    chunks: list[str] = []

    async def generate():
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                async with client.stream("POST", TRANSLATOR_STREAM_URL, json=trans_payload) as resp:
                    async for chunk in resp.aiter_text():
                        if chunk:
                            chunks.append(chunk)
                            yield chunk
        except Exception as e:
            logger.error(f"[TRANS-STREAM-PROXY] {e}")
        finally:
            if chunks:
                _trans_cache.set(sentence, "".join(chunks))
                logger.info(f"[TRANS-CACHED] '{sentence[:50]}'")

    return StreamingResponse(generate(), media_type="text/plain")


@app.post("/analyze-grammar-stream")
async def analyze_grammar_stream_ep(request: AnalyzeAiRequest):
    sentence = _extract_sentence(request.context_phrase, request.target_word, request.original_word)

    cached = _gram_cache.get(sentence)
    if cached is not None:
        logger.info(f"[GRAM-HIT] '{request.target_word}' in '{sentence[:50]}'")
        text = _remap_star(cached["text"], cached["target_word"], request.target_word)
        meta_str = cached["meta"]
        async def from_cache():
            yield f"\x01{meta_str}\x01\n"
            yield text
        return StreamingResponse(from_cache(), media_type="text/plain")

    grammar_payload = {
        "context_phrase": request.context_phrase,
        "target_word":    request.target_word,
        "original_word":  request.original_word,
        "part_of_speech": request.part_of_speech,
        "page_context":   request.page_context,
    }
    chunks: list[str] = []
    meta_str: str = ""

    async def generate():
        nonlocal meta_str
        _, romaji_sentence = await _tokenize(request.context_phrase, request.target_word)
        meta = {"romaji": romaji_sentence, "model": GRAMMAR_MODEL}
        meta_str = json.dumps(meta, ensure_ascii=False)
        yield f"\x01{meta_str}\x01\n"
        try:
            async with httpx.AsyncClient(timeout=35.0) as client:
                async with client.stream("POST", GRAMMAR_STREAM_URL, json=grammar_payload) as resp:
                    async for chunk in resp.aiter_text():
                        if chunk:
                            chunks.append(chunk)
                            yield chunk
        except Exception as e:
            logger.error(f"[GRAM-STREAM-PROXY] {e}")
        finally:
            if chunks and meta_str:
                _gram_cache.set(sentence, {
                    "meta":        meta_str,
                    "text":        "".join(chunks),
                    "target_word": request.target_word,
                })
                logger.info(f"[GRAM-CACHED] '{request.target_word}' in '{sentence[:50]}'")

    return StreamingResponse(generate(), media_type="text/plain")


def _split_page_sentences(text: str) -> list[str]:
    """Split page text into individual sentences for translation pre-warming.
    Splits on Japanese sentence-ending punctuation and on newlines (speech-bubble
    boundaries in Mokuro output)."""
    parts = re.split(r'(?<=[。！？\n])|(?<=\n)', text)
    seen: set[str] = set()
    result = []
    for p in parts:
        s = p.strip()
        if s and s not in seen:
            seen.add(s)
            result.append(s)
    return result


async def _prewarm_sentence_grammar(sentence: str, target_word: str, part_of_speech: str) -> None:
    """Analyze grammar for one sentence and store in _gram_cache (romaji included)."""
    if _gram_cache.get(sentence) is not None:
        return
    grammar_payload = {
        "context_phrase": sentence,
        "target_word":    target_word,
        "original_word":  "",
        "part_of_speech": part_of_speech,
        "page_context":   "",
    }
    _, romaji_sentence = await _tokenize(sentence, target_word)
    meta_str = json.dumps({"romaji": romaji_sentence, "model": GRAMMAR_MODEL}, ensure_ascii=False)
    chunks: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=35.0) as client:
            async with client.stream("POST", GRAMMAR_STREAM_URL, json=grammar_payload) as resp:
                async for chunk in resp.aiter_text():
                    if chunk:
                        chunks.append(chunk)
        if chunks:
            _gram_cache.set(sentence, {
                "meta":        meta_str,
                "text":        "".join(chunks),
                "target_word": target_word,
            })
            logger.info(f"[GRAM-PREWARM] '{target_word}' | '{sentence[:40]}'")
    except Exception as e:
        logger.error(f"[GRAM-PREWARM] '{sentence[:30]}': {e}")


async def _prewarm_sentence_translation(sentence: str) -> None:
    """Translate one sentence and store the result in _trans_cache.
    Called as a background task; errors are silently logged."""
    if _trans_cache.get(sentence) is not None:
        return
    payload = {
        "context_phrase": sentence,
        "target_word":    "",
        "original_word":  "",
        "part_of_speech": "",
        "page_context":   "",
    }
    chunks: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream("POST", TRANSLATOR_STREAM_URL, json=payload) as resp:
                async for chunk in resp.aiter_text():
                    if chunk:
                        chunks.append(chunk)
        if chunks:
            _trans_cache.set(sentence, "".join(chunks))
            logger.info(f"[TRANS-PREWARM] '{sentence[:50]}'")
    except Exception as e:
        logger.error(f"[TRANS-PREWARM] '{sentence[:30]}': {e}")


@app.post("/warm-page")
async def warm_page_ep(request: WarmPageRequest):
    # 1. Pre-warm dict cache (fast, awaited so we can report count)
    dict_result = {}
    dict_warmed = 0
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.post(f"{_DICT_BASE}/warm-page", json={"text": request.text})
            res.raise_for_status()
            dict_result = res.json()
            dict_warmed = dict_result.get("warmed", 0)
    except Exception as e:
        logger.error(f"[WARM-PAGE-DICT] {e}")

    # 2. Pre-warm translation and grammar caches for every sentence.
    #    Grammar targets come from the dict service (best token per sentence).
    #    Launched as background tasks so this endpoint returns immediately.
    queued_trans = 0
    queued_gram  = 0

    for item in dict_result.get("sentence_targets", []):
        sentence = item["sentence"]
        if _trans_cache.get(sentence) is None:
            asyncio.create_task(_prewarm_sentence_translation(sentence))
            queued_trans += 1
        if _gram_cache.get(sentence) is None:
            asyncio.create_task(_prewarm_sentence_grammar(
                sentence, item["target_word"], item["part_of_speech"]))
            queued_gram += 1

    logger.info(f"[WARM-PAGE] dict={dict_warmed} trans={queued_trans} gram={queued_gram}")
    return {"dict_warmed": dict_warmed, "trans_queued": queued_trans, "gram_queued": queued_gram}


@app.post("/analyze-dict")
async def analyze_dict_only(request: LookUpAndTranslateRequest):
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


@app.post("/ask-stream")
async def ask_stream_ep(request: AskRequest):
    """Stream a tutoring answer from ChatGPT about a specific Japanese phrase/word."""
    if not _oai_client:
        async def _no_key():
            yield "OpenAI API key not configured."
        return StreamingResponse(_no_key(), media_type="text/plain")

    system_prompt = (
        "Eres un tutor de japonés. El estudiante está leyendo en japonés y tiene una pregunta "
        "sobre una frase o palabra. Responde de forma concisa y clara en español."
    )
    ctx_part = f"Frase: {request.context_phrase}" if request.context_phrase else ""
    word_part = f"Palabra: {request.target_word}" if request.target_word else ""
    page_part = f"Contexto de página:\n{request.page_context}" if request.page_context else ""
    user_msg = "\n".join(p for p in [ctx_part, word_part, page_part, f"Pregunta: {request.question}"] if p)

    async def gen():
        try:
            async with _oai_client.chat.completions.stream(
                model=GRAMMAR_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_msg},
                ],
                max_tokens=400,
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception as e:
            logger.error(f"[ASK-STREAM] {e}")
            yield f"Error: {e}"

    return StreamingResponse(gen(), media_type="text/plain")
