import asyncio
import json
import os
import re
import time
import httpx
import logging
from collections import OrderedDict
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from openai import AsyncOpenAI

load_dotenv()

_SERVER_EPOCH = str(int(time.time()))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("YOMITSU-ORCHESTRATOR")

GRAMMAR_MODEL = os.environ.get("GRAMMAR_MODEL", "gpt-5.6-terra")
VISION_MODEL  = os.environ.get("VISION_MODEL",  "gpt-5.6-terra")

_http_client: httpx.AsyncClient = None  # type: ignore[assignment]
_oai_client: AsyncOpenAI | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client, _oai_client
    _http_client = httpx.AsyncClient(timeout=5.0)
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        _oai_client = AsyncOpenAI(api_key=api_key)
    _load_caches()
    yield
    _save_caches()
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

_CACHE_MAX  = 100
_CACHE_FILE = os.path.join(os.path.dirname(__file__), "sentence_cache.json")


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

# Vision page analysis keyed by "book:page" — small text blobs, kept unbounded
# so re-reading a volume never pays the vision call twice.
_pagectx_cache: dict[str, str] = {}


def _load_caches() -> None:
    if not os.path.exists(_CACHE_FILE):
        return
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k, v in data.get("trans", {}).items():
            _trans_cache.set(k, v)
        for k, v in data.get("gram", {}).items():
            _gram_cache.set(k, v)
        _pagectx_cache.update(data.get("pagectx", {}))
        logger.info(f"[CACHE] Restaurado: {len(_trans_cache._data)} traducciones + "
                    f"{len(_gram_cache._data)} gramáticas + "
                    f"{len(_pagectx_cache)} páginas desde disco")
    except Exception as e:
        logger.warning(f"[CACHE] No se pudo cargar el cache: {e}")


def _save_caches() -> None:
    try:
        data = {
            "trans":   dict(_trans_cache._data),
            "gram":    dict(_gram_cache._data),
            "pagectx": _pagectx_cache,
        }
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        logger.info(f"[CACHE] Guardado: {len(_trans_cache._data)} traducciones + "
                    f"{len(_gram_cache._data)} gramáticas + "
                    f"{len(_pagectx_cache)} páginas en disco")
    except Exception as e:
        logger.warning(f"[CACHE] No se pudo guardar el cache: {e}")


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


_GRAMMAR_ERROR_MARKER = "[Error al generar análisis]"  # emitted by grammar service on failure


def _strip_meta(stream_text: str) -> str:
    """Remove the leading \\x01{json}\\x01\\n metadata header from a stream body."""
    return re.sub(r'^\x01[^\x01]*\x01\n?', '', stream_text)


def _cacheable_translation(stream_text: str) -> bool:
    """A translation stream is cacheable only if there is real content after the
    meta header — a failed OpenAI call yields the header and nothing else."""
    return bool(_strip_meta(stream_text).strip())


def _cacheable_grammar(text: str) -> bool:
    return bool(text.strip()) and _GRAMMAR_ERROR_MARKER not in text


def _ctx_kind(page_context: str) -> str:
    """Classify the page_context used for an AI call: 'vision' when it is the
    vision model's output (always starts with a TRANSCRIPT: section), 'ocr'
    for raw Mokuro text, 'none' when there is no page context at all."""
    if not page_context:
        return "none"
    return "vision" if "TRANSCRIPT:" in page_context else "ocr"


def _remap_star(grammar_text: str, old_target: str, new_target: str) -> str:
    """Move the ★ marker from old_target's DESGLOSE entry to new_target's."""
    # Models sometimes emit "- ★ element" instead of "★ element" — normalize.
    grammar_text = re.sub(r'^\s*-\s*★\s*', '★ ', grammar_text, flags=re.MULTILINE)
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
    response_language: str = "English"


class PageContextRequest(BaseModel):
    image_b64: str | None = None
    ocr_text: str = ""
    manga_title: str = ""
    page_key: str = ""  # "book:page" — enables server-side caching per page


class WarmPageRequest(BaseModel):
    text: str
    page_context: str = ""  # vision page analysis; passed to prewarm calls
    response_language: str = "English"


class AskRequest(BaseModel):
    question: str
    context_phrase: str
    target_word: str = ""
    page_context: str = ""
    response_language: str = "English"
    history: list = []  # [{q: ..., a: ...}, ...] prior exchanges

    @field_validator("history", mode="before")
    @classmethod
    def _coerce_history(cls, v):
        # Lua's JSON encoder may produce {} for an empty table instead of [].
        return v if isinstance(v, list) else []


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


@app.post("/clear-cache")
async def clear_cache():
    """Empties the AI caches (translations, grammar, vision page analyses) and
    deletes the persisted sentence_cache.json. Triggered from the KOReader menu."""
    counts = {
        "trans":   len(_trans_cache._data),
        "gram":    len(_gram_cache._data),
        "pagectx": len(_pagectx_cache),
    }
    _trans_cache._data.clear()
    _gram_cache._data.clear()
    _pagectx_cache.clear()
    try:
        if os.path.exists(_CACHE_FILE):
            os.remove(_CACHE_FILE)
    except Exception as e:
        logger.warning(f"[CACHE] No se pudo borrar {_CACHE_FILE}: {e}")
    logger.info(f"[CACHE] Limpiado a petición del cliente: {counts}")
    return {"cleared": counts}


@app.post("/analyze-page-context")
async def analyze_page_context(request: PageContextRequest):
    """Analyzes a manga page with vision: corrected transcript of every bubble in
    reading order + speakers + scene description. Called once per page by Lua;
    the result becomes the page_context for every lookup on that page.
    Cached server-side by page_key so re-reading never pays vision twice."""
    if request.page_key and request.page_key in _pagectx_cache:
        logger.info(f"[PAGE-CTX-HIT] {request.page_key}")
        return {"page_context": _pagectx_cache[request.page_key], "cached": True}

    if not _oai_client:
        raise HTTPException(status_code=503, detail="OpenAI not configured")

    content: list = []
    if request.image_b64:
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{request.image_b64}",
                "detail": "high",
            }
        })

    prompt = ""
    if request.manga_title:
        prompt += f"Manga: {request.manga_title}\n"
    if request.ocr_text:
        prompt += f"OCR text extracted from this page (may contain errors):\n{request.ocr_text}\n\n"
    prompt += (
        "Look at this manga page and output exactly two sections:\n\n"
        "TRANSCRIPT:\n"
        "Every speech bubble and text box in Japanese reading order (right to left, "
        "top to bottom), one per line. Use the image to fix any OCR errors "
        "(wrong kanji, merged furigana, bad line order). If the speaker is "
        "identifiable, prefix the line with their name or role in brackets, "
        "e.g. [店員] or [girl].\n\n"
        "SCENE:\n"
        "One or two sentences in English: who is present, who is speaking to whom, "
        "their relationship, the emotional tone, and any relevant action or setting.\n\n"
        "Output only these two sections, nothing else."
    )
    content.append({"type": "text", "text": prompt})

    try:
        response = await _oai_client.chat.completions.create(
            model=VISION_MODEL,
            messages=[{"role": "user", "content": content}],
            max_completion_tokens=2000,
            reasoning_effort="low",
        )
        analysis = (response.choices[0].message.content or "").strip()
        if request.page_key and analysis:
            _pagectx_cache[request.page_key] = analysis
        logger.info(f"[PAGE-CTX] {request.page_key or request.manga_title}: {len(analysis)} chars")
        return {"page_context": analysis}
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
        "source":         "tap",
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
            full = "".join(chunks)
            if _cacheable_translation(full):
                _trans_cache.set(sentence, full)
                logger.info(f"[TRANS-CACHED] '{sentence[:50]}'")
            elif chunks:
                logger.warning(f"[TRANS-NOT-CACHED] respuesta vacía para '{sentence[:50]}'")

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
        "context_phrase":    request.context_phrase,
        "target_word":       request.target_word,
        "original_word":     request.original_word,
        "part_of_speech":    request.part_of_speech,
        "page_context":      request.page_context,
        "response_language": request.response_language,
    }
    chunks: list[str] = []
    meta_str: str = ""

    async def generate():
        nonlocal meta_str
        _, romaji_sentence = await _tokenize(request.context_phrase, request.target_word)
        meta = {"romaji": romaji_sentence, "model": GRAMMAR_MODEL,
                "src": "tap", "ctx": _ctx_kind(request.page_context)}
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
            text = "".join(chunks)
            if meta_str and _cacheable_grammar(text):
                _gram_cache.set(sentence, {
                    "meta":        meta_str,
                    "text":        text,
                    "target_word": request.target_word,
                })
                logger.info(f"[GRAM-CACHED] '{request.target_word}' in '{sentence[:50]}'")
            elif chunks:
                logger.warning(f"[GRAM-NOT-CACHED] error/vacío para '{sentence[:50]}'")

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


async def _prewarm_sentence_grammar(sentence: str, target_word: str, part_of_speech: str, response_language: str = "English", page_context: str = "") -> None:
    """Analyze grammar for one sentence and store in _gram_cache (romaji included)."""
    if _gram_cache.get(sentence) is not None:
        return
    grammar_payload = {
        "context_phrase":    sentence,
        "target_word":       target_word,
        "original_word":     "",
        "part_of_speech":    part_of_speech,
        "page_context":      page_context,
        "response_language": response_language,
    }
    _, romaji_sentence = await _tokenize(sentence, target_word)
    meta_str = json.dumps({"romaji": romaji_sentence, "model": GRAMMAR_MODEL,
                           "src": "prewarm", "ctx": _ctx_kind(page_context)}, ensure_ascii=False)
    chunks: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=35.0) as client:
            async with client.stream("POST", GRAMMAR_STREAM_URL, json=grammar_payload) as resp:
                async for chunk in resp.aiter_text():
                    if chunk:
                        chunks.append(chunk)
        text = "".join(chunks)
        if _cacheable_grammar(text):
            _gram_cache.set(sentence, {
                "meta":        meta_str,
                "text":        text,
                "target_word": target_word,
            })
            logger.info(f"[GRAM-PREWARM] '{target_word}' | '{sentence[:40]}'")
        elif chunks:
            logger.warning(f"[GRAM-PREWARM-NOT-CACHED] error/vacío para '{sentence[:40]}'")
    except Exception as e:
        logger.error(f"[GRAM-PREWARM] '{sentence[:30]}': {e}")


async def _prewarm_sentence_translation(sentence: str, page_context: str = "") -> None:
    """Translate one sentence and store the result in _trans_cache.
    Called as a background task; errors are silently logged."""
    if _trans_cache.get(sentence) is not None:
        return
    payload = {
        "context_phrase": sentence,
        "target_word":    "",
        "original_word":  "",
        "part_of_speech": "",
        "page_context":   page_context,
        "source":         "prewarm",
    }
    chunks: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream("POST", TRANSLATOR_STREAM_URL, json=payload) as resp:
                async for chunk in resp.aiter_text():
                    if chunk:
                        chunks.append(chunk)
        full = "".join(chunks)
        if _cacheable_translation(full):
            _trans_cache.set(sentence, full)
            logger.info(f"[TRANS-PREWARM] '{sentence[:50]}'")
        elif chunks:
            logger.warning(f"[TRANS-PREWARM-NOT-CACHED] respuesta vacía para '{sentence[:50]}'")
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
            asyncio.create_task(_prewarm_sentence_translation(sentence, request.page_context))
            queued_trans += 1
        if _gram_cache.get(sentence) is None:
            asyncio.create_task(_prewarm_sentence_grammar(
                sentence, item["target_word"], item["part_of_speech"],
                request.response_language, request.page_context))
            queued_gram += 1

    logger.info(f"[WARM-PAGE] dict={dict_warmed} trans={queued_trans} gram={queued_gram}")
    return {"dict_warmed": dict_warmed, "trans_queued": queued_trans, "gram_queued": queued_gram, "epoch": _SERVER_EPOCH}


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
        "You are a Japanese language tutor. The student is reading Japanese and has a question "
        f"about a sentence or word. Answer concisely and clearly in {request.response_language}."
    )
    ctx_part  = f"Sentence: {request.context_phrase}" if request.context_phrase else ""
    word_part = f"Word: {request.target_word}" if request.target_word else ""
    page_part = f"Page context:\n{request.page_context}" if request.page_context else ""
    first_user_msg = "\n".join(p for p in [ctx_part, word_part, page_part] if p)

    # Build multi-turn message list with prior exchanges as context
    prior_history = request.history if isinstance(request.history, list) else []
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    for i, pair in enumerate(prior_history):
        q = pair.get("q", "")
        a = pair.get("a", "")
        if not q or not a:
            continue
        # Include sentence/word context only in the first user turn
        if i == 0 and first_user_msg:
            messages.append({"role": "user", "content": first_user_msg + "\nQuestion: " + q})
        else:
            messages.append({"role": "user", "content": q})
        messages.append({"role": "assistant", "content": a})
    # Current question
    if first_user_msg and not prior_history:
        messages.append({"role": "user", "content": first_user_msg + "\nQuestion: " + request.question})
    else:
        messages.append({"role": "user", "content": request.question})

    async def gen():
        try:
            stream = await _oai_client.chat.completions.create(
                model=GRAMMAR_MODEL,
                messages=messages,
                max_completion_tokens=1500,
                reasoning_effort="low",
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as e:
            logger.error(f"[ASK-STREAM] {e}")
            yield f"Error: {e}"

    return StreamingResponse(gen(), media_type="text/plain")
