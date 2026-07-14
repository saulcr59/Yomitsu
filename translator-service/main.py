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


class TranslationRequest(BaseModel):
    context_phrase: str
    target_word: str
    original_word: str = ""
    part_of_speech: str
    page_context: str = ""


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL}


@app.post("/translate")
async def translate_context(request: TranslationRequest):
    sentence = _extract_sentence(request.context_phrase, request.target_word, request.original_word)
    user_msg = _build_user_msg(sentence, request.page_context)
    logger.info(f"[TRANSLATE] '{request.target_word}' | ctx={bool(request.page_context)}")
    response = await client.chat.completions.create(
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

    meta = json.dumps({"s": sentence, "m": MODEL}, ensure_ascii=False)

    async def generate():
        yield f"\x01{meta}\x01\n"
        try:
            stream = await client.chat.completions.create(
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


def _build_user_msg(sentence: str, page_context: str) -> str:
    msg = ""
    if page_context:
        msg += f"Other text on this manga page (for context):\n{page_context}\n\n"
    msg += f"Translate this line to Spanish:\n「{sentence}」"
    return msg
