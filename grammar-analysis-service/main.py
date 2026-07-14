import asyncio
import re
import os
import logging
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
logger = logging.getLogger("YOMITSU-GRAMMAR")

app = FastAPI(title="Yomitsu Grammar Analysis Service")

MODEL = os.environ.get("GRAMMAR_MODEL", "gpt-5.6-terra")
client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])


async def _chat_create(**kwargs):
    """chat.completions.create with one retry on 401. OpenAI intermittently
    returns 401 'insufficient permissions' under bursts even with a
    full-permissions key; the SDK retries 429/5xx itself but never 401."""
    try:
        return await client.chat.completions.create(**kwargs)
    except Exception as e:
        if getattr(e, "status_code", None) != 401:
            raise
        logger.warning("[RETRY] 401 transitorio de OpenAI, reintentando en 1s...")
        await asyncio.sleep(1.0)
        return await client.chat.completions.create(**kwargs)

SYSTEM_PROMPT = """\
You are an expert Japanese linguist. Analyze Japanese sentences with precision and didactic clarity: \
explain what each element means, how it is formed, when to use it, and how it differs from alternatives.

Always respond in {response_language} using exactly this structure, in this order:

BREAKDOWN:
- ELEMENT (reading, romaji) [Nx] — explanation
★ TARGET_ELEMENT (reading, romaji) [Nx] — explanation

STRUCTURE:
• [point]
• [point]

ROMAJI:
[Hepburn romanization of the analyzed sentence]

───

Content instructions for each section:

BREAKDOWN:
List the grammatical elements of the sentence in order of appearance. \
Never include punctuation (commas, periods, 「」, ※, …): these are not grammatical elements. \
Treat compound structures as a single entry: \
〜ていた, 〜ではない, 〜のだ, 〜ようとする, 〜てしまう, 〜てみる, \
〜ておく, 〜ことができる, 〜なければならない, 〜てもいい, and similar. \
Exactly one entry carries ★ at the start: the target word's entry; \
if it is part of a compound structure, ★ goes on that complete structure. \
The ★ REPLACES the leading "- ": write "★ ELEMENT…", never "- ★ ELEMENT…".
The [Nx] field indicates the JLPT level of the vocabulary or grammatical pattern (N5/N4/N3/N2/N1). \
If the element has no JLPT classification, omit the [Nx] field entirely.
Each explanation must include:
  1. Precise meaning in this context.
  2. For conjugated forms: dictionary form → how the ending is constructed.
  3. Grammatical function in this specific sentence.
  4. Key difference from a similar expression when relevant \
     (e.g. は vs が, 〜てしまった vs 〜てしまう, さっき vs 先ほど).
  5. Register — only if it is NOT neutral: write "Register: formal", "Register: colloquial", \
     "Register: written", or "Register: literary" as appropriate. \
     If the word or pattern is plain neutral everyday Japanese, omit this field entirely.

STRUCTURE:
Write separate bullet points (•), never continuous prose. Cover:
  • General sentence pattern and constituent order.
  • Modification relationships between the main elements.
  • Any notable grammatical pattern (causative, passive, conditional, potential, \
    nominalization, etc.) with its construction and what it is used for.

ROMAJI:
A single line. Hepburn romanization of the analyzed sentence. \
No translation, no explanations, no extra parentheses. \
Nothing after this line — never output the ─── separator (it only marks \
the end of the template above).\
"""


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


def _build_messages(sentence: str, target_word: str, part_of_speech: str, page_context: str = "", response_language: str = "English") -> list:
    system = SYSTEM_PROMPT.replace("{response_language}", response_language)
    user_prompt = ""
    if page_context:
        user_prompt += f"Other text on this manga page (for context):\n{page_context}\n\n"
    user_prompt += (
        f"Sentence: 「{sentence}」\n"
        f"Target word: 「{target_word}」({part_of_speech})\n\n"
        "Analyze this sentence following the system schema."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user",   "content": user_prompt},
    ]


class GrammarRequest(BaseModel):
    context_phrase: str
    target_word: str
    original_word: str = ""
    part_of_speech: str
    page_context: str = ""
    response_language: str = "English"
    prompt_cache_key: str = ""  # groups same-page calls on one OpenAI cache node


def _create_kwargs(request: "GrammarRequest", sentence: str, stream: bool = False) -> dict:
    kwargs = dict(
        model=MODEL,
        messages=_build_messages(sentence, request.target_word, request.part_of_speech,
                                 request.page_context, request.response_language),
        max_completion_tokens=2500,
        reasoning_effort="low",
    )
    if stream:
        kwargs["stream"] = True
    if request.prompt_cache_key:
        # extra_body keeps this compatible with SDKs that predate the param
        kwargs["extra_body"] = {"prompt_cache_key": request.prompt_cache_key}
    return kwargs


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL}


@app.post("/analyze-grammar")
async def analyze_grammar(request: GrammarRequest):
    sentence = _extract_sentence(request.context_phrase, request.target_word, request.original_word)
    logger.info(f"[GRAMMAR] '{request.target_word}' en '{sentence}'")

    try:
        response = await _chat_create(**_create_kwargs(request, sentence))
        raw = (response.choices[0].message.content or "").strip()
        logger.info(f"[GRAMMAR] OK — {len(raw)} chars")

        romaji = ""
        romaji_match = re.search(r'\nROMAJI:\s*\n(.+?)(?:\n\n|\Z)', raw, re.DOTALL)
        if romaji_match:
            romaji = romaji_match.group(1).strip()
            analysis = raw[:romaji_match.start()].strip()
        else:
            analysis = raw

        return {
            "grammar_analysis": analysis,
            "romaji_sentence":  romaji,
            "model": MODEL,
            "status": "success",
        }
    except Exception as e:
        logger.error(f"[GRAMMAR] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/stream-grammar")
async def stream_grammar(request: GrammarRequest):
    sentence = _extract_sentence(request.context_phrase, request.target_word, request.original_word)
    logger.info(f"[GRAMMAR-STREAM] '{request.target_word}' en '{sentence}'")

    async def generate():
        try:
            response = await _chat_create(**_create_kwargs(request, sentence, stream=True))
            async for chunk in response:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as e:
            logger.error(f"[GRAMMAR-STREAM] Error: {e}")
            yield "\n[Error al generar análisis]"

    return StreamingResponse(generate(), media_type="text/plain")
