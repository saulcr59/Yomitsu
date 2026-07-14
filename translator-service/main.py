import asyncio
import os
import re
import json
import logging
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from openai import AsyncOpenAI

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("YOMITSU-TRANSLATOR")

MODEL = os.environ.get("TRANSLATOR_MODEL", "gpt-5.6-terra")
client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])


async def _chat_create(**kwargs):
    """chat.completions.create with retries on 401. OpenAI intermittently
    returns 401 'insufficient permissions' under bursts even with a
    full-permissions key — sometimes twice in a row — the SDK retries
    429/5xx itself but never 401."""
    for delay in (1.0, 3.0, None):
        try:
            return await client.chat.completions.create(**kwargs)
        except Exception as e:
            if getattr(e, "status_code", None) != 401 or delay is None:
                raise
            logger.warning(f"[RETRY] 401 transitorio de OpenAI, reintento en {delay:.0f}s...")
            await asyncio.sleep(delay)

app = FastAPI(title="Yomitsu Translator Service")

SYSTEM_PROMPT = """\
You are an expert Japanese-to-Spanish translator specializing in manga and anime.
Translate naturally and concisely, preserving the character's voice, speech register,
and personality (casual, rough, polite, childlike, archaic, etc.).
Use the page context ONLY to resolve ambiguity: who is speaking, who is addressed,
tone, and what pronouns or omitted subjects refer to.
STRICT FIDELITY RULE: every content word (verb, noun, adjective) in your Spanish
must correspond to a word actually present in the Japanese line. Never import
verbs, actions or objects from the page context. If the line expresses a state or
emotion, translate the state itself — never the visible action that expresses it
elsewhere on the page. The reader is a Japanese learner comparing your translation
word-by-word against the original line.
Return ONLY the Spanish translation — no explanations, no notes, no alternatives."""


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


def _ctx_kind(page_context: str) -> str:
    """Classify the page_context received: 'vision' when it is the vision
    model's output (always starts with a TRANSCRIPT: section), 'ocr' for raw
    Mokuro text, 'none' when there is no page context at all."""
    if not page_context:
        return "none"
    return "vision" if "TRANSCRIPT:" in page_context else "ocr"


class TranslationRequest(BaseModel):
    context_phrase: str
    target_word: str
    original_word: str = ""
    part_of_speech: str
    page_context: str = ""
    source: str = "tap"  # "tap" | "prewarm" — echoed into the stream meta


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL}


@app.post("/translate")
async def translate_context(request: TranslationRequest):
    sentence = _extract_sentence(request.context_phrase, request.target_word, request.original_word)
    user_msg = _build_user_msg(sentence, request.page_context)
    logger.info(f"[TRANSLATE] '{request.target_word}' | ctx={bool(request.page_context)}")
    response = await _chat_create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg},
        ],
        max_completion_tokens=1000,
        reasoning_effort="none",
    )
    translation = (response.choices[0].message.content or "").strip()
    return {
        "translation_raw": translation,
        "source_sentence": sentence,
        "model_used": MODEL,
        "status": "success",
    }


@app.post("/stream-translate")
async def stream_translate(request: TranslationRequest):
    sentence = _extract_sentence(request.context_phrase, request.target_word, request.original_word)
    user_msg = _build_user_msg(sentence, request.page_context)
    logger.info(f"[TRANS-STREAM] '{request.target_word}' | page_ctx={bool(request.page_context)}")

    meta = json.dumps({"s": sentence, "m": MODEL,
                       "src": request.source,
                       "ctx": _ctx_kind(request.page_context)}, ensure_ascii=False)

    async def generate():
        yield f"\x01{meta}\x01\n"
        try:
            stream = await _chat_create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": user_msg},
                ],
                max_completion_tokens=1000,
                reasoning_effort="none",
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as e:
            logger.error(f"[TRANS-STREAM] Error: {e}")

    return StreamingResponse(generate(), media_type="text/plain")


PAGE_SYSTEM_PROMPT = """\
You are an expert Japanese-to-Spanish translator specializing in manga and anime.
You will receive every line of one manga page, numbered, plus page context.
Translate EACH numbered line to Spanish independently, preserving the character's
voice, speech register and personality (casual, rough, polite, childlike, archaic, etc.).
Use the page context ONLY to resolve ambiguity: who is speaking, who is addressed,
tone, and what pronouns or omitted subjects refer to.
STRICT FIDELITY RULE: every content word (verb, noun, adjective) in your Spanish
must correspond to a word actually present in that Japanese line. Never import
verbs, actions or objects from other lines or from the page context. If a line
expresses a state or emotion, translate the state itself — never the visible
action that expresses it elsewhere on the page.
Output format: one line per input line, same numbering, ONLY the translation:
1. <Spanish translation of line 1>
2. <Spanish translation of line 2>
No explanations, no notes, no omissions — every input number must appear."""


class PageTranslationRequest(BaseModel):
    sentences: list[str]
    page_context: str = ""


@app.post("/translate-page")
async def translate_page(request: PageTranslationRequest):
    """Translate every line of a manga page in ONE model call (used by the
    orchestrator's warm-page prewarm). Returns {"1": "...", "2": "..."} keyed
    by the 1-based index of each input sentence."""
    if not request.sentences:
        return {"translations": {}, "model": MODEL}
    numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(request.sentences, 1))
    msg = ""
    if request.page_context:
        msg += f"Page context:\n{request.page_context}\n\n"
    msg += f"Translate these lines to Spanish:\n{numbered}"
    logger.info(f"[TRANS-PAGE] {len(request.sentences)} líneas | ctx={_ctx_kind(request.page_context)}")

    response = await _chat_create(
        model=MODEL,
        messages=[
            {"role": "system", "content": PAGE_SYSTEM_PROMPT},
            {"role": "user",   "content": msg},
        ],
        max_completion_tokens=4000,
        reasoning_effort="none",
    )
    raw = (response.choices[0].message.content or "").strip()
    translations: dict[str, str] = {}
    for line in raw.splitlines():
        m = re.match(r'^\s*(\d+)[.)]\s*(.+)$', line)
        if m:
            translations[m.group(1)] = m.group(2).strip()
    logger.info(f"[TRANS-PAGE] OK — {len(translations)}/{len(request.sentences)} líneas")
    return {"translations": translations, "model": MODEL}


def _build_user_msg(sentence: str, page_context: str) -> str:
    msg = ""
    if page_context:
        msg += f"Other text on this manga page (for context):\n{page_context}\n\n"
    msg += f"Translate this line to Spanish:\n「{sentence}」"
    return msg
