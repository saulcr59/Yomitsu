import json
import asyncio
import logging
import glob
import re
from fastapi import FastAPI
from pydantic import BaseModel
from sudachipy import dictionary, tokenizer
from contextlib import asynccontextmanager
from readmdict import MDX

from kindle_formatter import format_yomitan_to_html
from kenkyusha_formatter import format_kenkyusha_to_html
from wisdom_formatter import format_wisdom_to_html
from genius_formatter import format_genius_to_html
from grammar_formatter import format_grammar_to_html

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
WISDOM: dict[str, str] = {}
GENIUS: dict[str, str] = {}
GRAMMAR: dict[str, list[str]] = {}
FREQ_JPDB: dict[str, int] = {}
FREQ_BCCWJ: dict[str, int] = {}
KANJI_INDEX: dict[str, list[dict]] = {}  # single-kanji char → [{reading, glosses}]


_POS_MAP = {
    "名詞":     "noun",
    "代名詞":   "pronoun",
    "動詞":     "verb",
    "形容詞":   "i-adjective",
    "形状詞":   "na-adjective",
    "副詞":     "adverb",
    "助詞":     "particle",
    "助動詞":   "auxiliary verb",
    "感動詞":   "interjection",
    "接続詞":   "conjunction",
    "接頭辞":   "prefix",
    "接尾辞":   "suffix",
    "記号":     "symbol",
    "補助記号": "supplementary symbol",
}


def kata_to_hira(text: str) -> str:
    """Convert katakana to hiragana. Sudachi returns readings in katakana;
    Jitendex entry[1] is hiragana — must convert before comparing."""
    return "".join(
        chr(ord(c) - 0x60) if "ァ" <= c <= "ン" else c
        for c in text
    )


# ---------------------------------------------------------------------------
# Katakana → Hepburn romaji
# ---------------------------------------------------------------------------
_KATA_2: dict[str, str] = {
    'キャ': 'kya', 'キュ': 'kyu', 'キョ': 'kyo',
    'シャ': 'sha', 'シュ': 'shu', 'ショ': 'sho',
    'チャ': 'cha', 'チュ': 'chu', 'チョ': 'cho',
    'ニャ': 'nya', 'ニュ': 'nyu', 'ニョ': 'nyo',
    'ヒャ': 'hya', 'ヒュ': 'hyu', 'ヒョ': 'hyo',
    'ミャ': 'mya', 'ミュ': 'myu', 'ミョ': 'myo',
    'リャ': 'rya', 'リュ': 'ryu', 'リョ': 'ryo',
    'ギャ': 'gya', 'ギュ': 'gyu', 'ギョ': 'gyo',
    'ジャ': 'ja',  'ジュ': 'ju',  'ジョ': 'jo',
    'ビャ': 'bya', 'ビュ': 'byu', 'ビョ': 'byo',
    'ピャ': 'pya', 'ピュ': 'pyu', 'ピョ': 'pyo',
    'ファ': 'fa',  'フィ': 'fi',  'フェ': 'fe',  'フォ': 'fo',
    'ウィ': 'wi',  'ウェ': 'we',  'ウォ': 'wo',
    'ティ': 'ti',  'ディ': 'di',  'デュ': 'dyu',
    'ツァ': 'tsa', 'ツィ': 'tsi', 'ツェ': 'tse', 'ツォ': 'tso',
    'ヴァ': 'va',  'ヴィ': 'vi',  'ヴェ': 've',  'ヴォ': 'vo',
}

_KATA_1: dict[str, str] = {
    'ア': 'a',  'イ': 'i',  'ウ': 'u',  'エ': 'e',  'オ': 'o',
    'カ': 'ka', 'キ': 'ki', 'ク': 'ku', 'ケ': 'ke', 'コ': 'ko',
    'サ': 'sa', 'シ': 'shi','ス': 'su', 'セ': 'se', 'ソ': 'so',
    'タ': 'ta', 'チ': 'chi','ツ': 'tsu','テ': 'te', 'ト': 'to',
    'ナ': 'na', 'ニ': 'ni', 'ヌ': 'nu', 'ネ': 'ne', 'ノ': 'no',
    'ハ': 'ha', 'ヒ': 'hi', 'フ': 'fu', 'ヘ': 'he', 'ホ': 'ho',
    'マ': 'ma', 'ミ': 'mi', 'ム': 'mu', 'メ': 'me', 'モ': 'mo',
    'ヤ': 'ya', 'ユ': 'yu', 'ヨ': 'yo',
    'ラ': 'ra', 'リ': 'ri', 'ル': 'ru', 'レ': 're', 'ロ': 'ro',
    'ワ': 'wa', 'ヲ': 'o',  'ン': 'n',
    'ガ': 'ga', 'ギ': 'gi', 'グ': 'gu', 'ゲ': 'ge', 'ゴ': 'go',
    'ザ': 'za', 'ジ': 'ji', 'ズ': 'zu', 'ゼ': 'ze', 'ゾ': 'zo',
    'ダ': 'da', 'ヂ': 'ji', 'ヅ': 'zu', 'デ': 'de', 'ド': 'do',
    'バ': 'ba', 'ビ': 'bi', 'ブ': 'bu', 'ベ': 'be', 'ボ': 'bo',
    'パ': 'pa', 'ピ': 'pi', 'プ': 'pu', 'ペ': 'pe', 'ポ': 'po',
    'ヴ': 'vu', 'ー': '-',
    'ァ': 'a',  'ィ': 'i',  'ゥ': 'u',  'ェ': 'e',  'ォ': 'o',
}


def _kata_to_romaji(kata: str) -> str:
    out: list[str] = []
    i = 0
    n = len(kata)
    while i < n:
        c = kata[i]
        if c == 'ッ':
            if i + 1 < n:
                peek = _kata_to_romaji(kata[i + 1: i + 3])
                if peek and peek[0].isalpha():
                    out.append(peek[0])
            i += 1
            continue
        if i + 1 < n:
            two = kata[i: i + 2]
            if two in _KATA_2:
                out.append(_KATA_2[two])
                i += 2
                continue
        if c in _KATA_1:
            out.append(_KATA_1[c])
        elif c.isascii():
            out.append(c)
        i += 1
    return ''.join(out)


def _extract_sentence(context: str, target_word: str) -> str:
    """Return the sentence in context that contains target_word."""
    parts = re.split(r'(?<=[。！？])', context)
    sentences: list[str] = []
    for p in parts:
        sentences.extend(p.split('\n'))
    for s in sentences:
        s = s.strip()
        if s and target_word in s:
            return s
    return context.strip()


def _sentence_to_romaji(sentence: str) -> str:
    """Tokenize sentence with SudachiPy and return Hepburn romanization."""
    tokens = tokenizer_obj.tokenize(sentence, mode)
    # Work in katakana segments before converting, so gemination across token
    # boundaries (ッ) and conjunctive particles (て/で) merge correctly.
    segments: list[str] = []

    for token in tokens:
        surface = token.surface()
        if not any(
            '぀' <= c <= 'ヿ' or '一' <= c <= '鿿' or c.isalpha()
            for c in surface
        ):
            continue

        reading = token.reading_form()  # katakana
        pos = token.part_of_speech()
        pos1 = pos[0] if len(pos) > 0 else ""
        pos2 = pos[1] if len(pos) > 1 else ""

        if not reading:
            if surface.isascii() and surface.strip():
                segments.append(surface)
            continue

        # Attach to previous segment (no space) when:
        # 1. Previous katakana ends with ッ — geminate consonant bridges the boundary
        # 2. This reading starts with ッ — geminate arrives from this side
        # 3. Conjunctive particle (接続助詞): て, で, ば — attaches to verb stem
        # 4. Auxiliary verb (助動詞): た, ない, れる, ます, etc. — BUT NOT だ/です (copula,
        #    always written separately in learning resources: "gakusei da", not "gakuseida")
        # 5. Non-independent verb (非自立可能): いる, おく, しまう after て-form
        attach = bool(segments) and (
            segments[-1].endswith("ッ")
            or reading.startswith("ッ")
            or pos2 == "接続助詞"
            or (pos1 == "助動詞" and reading not in ("ダ", "デス"))
            or pos2 == "非自立可能"
        )

        if attach:
            segments[-1] += reading
        else:
            segments.append(reading)

    parts = [_kata_to_romaji(s) for s in segments]
    return " ".join(p for p in parts if p)


def _extract_glosses(node) -> list[str]:
    """Extract English gloss strings from a Yomitan structured-content node."""
    glosses: list[str] = []

    def _text(n) -> str:
        if isinstance(n, str): return n
        if isinstance(n, list): return " ".join(_text(i) for i in n)
        if isinstance(n, dict): return _text(n.get("content", ""))
        return ""

    def walk(n):
        if isinstance(n, str): return
        if isinstance(n, list):
            for item in n: walk(item)
            return
        if not isinstance(n, dict): return
        data = n.get("data", {})
        if isinstance(data, dict) and data.get("content") == "glossary":
            items = n.get("content", [])
            if not isinstance(items, list): items = [items]
            for li in items:
                if isinstance(li, dict) and li.get("tag") == "li":
                    text = _text(li.get("content", "")).strip()
                    if text and not any("぀" <= c <= "鿿" for c in text):
                        glosses.append(text)
            return
        walk(n.get("content", []))

    for item in (node if isinstance(node, list) else [node]):
        walk(item)
    return glosses


def load_jitendex():
    logger.info("Cargando Jitendex...")
    files = glob.glob("./dictionaries/jitendex-yomitan/term_bank_*.json")
    for file_path in files:
        with open(file_path, "r", encoding="utf-8") as f:
            for entry in json.load(f):
                word = entry[0]
                JITENDEX.setdefault(word, []).append(entry)
                # Index single kanji for breakdown feature
                if (len(word) == 1
                        and ("一" <= word <= "鿿" or "㐀" <= word <= "䶿")):
                    reading = entry[1]
                    glosses = _extract_glosses(entry[5]) if len(entry) > 5 else []
                    if reading and glosses:
                        KANJI_INDEX.setdefault(word, []).append(
                            {"reading": reading, "glosses": glosses}
                        )
    logger.info(
        f"Jitendex cargado: {len(JITENDEX)} entradas únicas. "
        f"Kanji indexados: {len(KANJI_INDEX)}."
    )


def load_kenkyusha():
    logger.info("Cargando Kenkyusha MDX...")
    _load_mdx_with_redirects(
        "./dictionaries/研究社和英大辞典/研究社新和英大辞典.mdx",
        KENKYUSHA, "Kenkyusha",
    )


def _load_mdx_with_redirects(mdx_path: str, target: dict, label: str) -> None:
    """Generic MDX loader: resolves @@@LINK redirects one level deep."""
    try:
        mdx = MDX(mdx_path)
        entries: dict[str, str] = {}
        redirects: dict[str, str] = {}
        for k, v in mdx.items():
            word = k.decode("utf-8").strip()
            val  = v.decode("utf-8").strip()
            if val.startswith("@@@LINK="):
                redirects[word] = val[8:].strip()
            elif val:
                entries[word] = val

        target.update(entries)
        resolved = 0
        for from_key, to_key in redirects.items():
            html = entries.get(to_key) or entries.get(redirects.get(to_key, ""), "")
            if html and from_key not in target:
                target[from_key] = html
                resolved += 1

        logger.info(f"{label} cargado: {len(entries)} entradas + {resolved} redirects resueltos.")
    except Exception as e:
        logger.error(f"Error cargando {label}: {e}")


def load_wisdom():
    logger.info("Cargando Wisdom MDX...")
    _load_mdx_with_redirects(
        "./dictionaries/三省堂 ウィズダム和英辞典 第3版/SANWIZJ3.mdx",
        WISDOM, "Wisdom",
    )


def load_genius():
    logger.info("Cargando Genius MDX...")
    _load_mdx_with_redirects(
        "./dictionaries/大修館 ジーニアス和英辞典 第3版/GENIUSJ3.mdx",
        GENIUS, "Genius",
    )


def _kata_to_hira(text: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)


def load_freq_jpdb():
    logger.info("Cargando JPDB frequency...")
    path = "./dictionaries/JPDB_v2.2_Frequency_Kana_2024-10-13/term_meta_bank_1.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for entry in data:
            word = _kata_to_hira(entry[0])
            meta = entry[2]
            val = None
            if isinstance(meta, dict):
                val = meta.get("value")           # {'value': N, 'displayValue': '...'} — JPDB format
                if val is None:
                    freq = meta.get("frequency")
                    if isinstance(freq, dict):
                        val = freq.get("value")   # nested {'frequency': {'value': N}} fallback
                    elif isinstance(freq, int):
                        val = freq
            elif isinstance(meta, int):
                val = meta
            if val is not None:
                if word not in FREQ_JPDB or val < FREQ_JPDB[word]:
                    FREQ_JPDB[word] = val
        logger.info(f"JPDB frequency cargado: {len(FREQ_JPDB)} entradas.")
    except Exception as e:
        logger.error(f"Error cargando JPDB frequency: {e}")


def load_freq_bccwj():
    logger.info("Cargando BCCWJ frequency...")
    path = "./dictionaries/BCCWJ_SUW_LUW_combined/term_meta_bank_1.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            for entry in json.load(f):
                word = entry[0]
                meta = entry[2]
                val = meta.get("frequency")
                if isinstance(val, int):
                    if word not in FREQ_BCCWJ or val < FREQ_BCCWJ[word]:
                        FREQ_BCCWJ[word] = val
        logger.info(f"BCCWJ frequency cargado: {len(FREQ_BCCWJ)} entradas.")
    except Exception as e:
        logger.error(f"Error cargando BCCWJ frequency: {e}")


def load_grammar():
    logger.info("Cargando Grammar (DOJG) MDX...")
    mdx_path = (
        "./dictionaries/The Japan Times - Dictionary of Japanese Grammar (Jpn-Eng-Jpn) (MDX)/"
        "(The Japan Times) A Dictionary of Japanese Grammar [Complete Edition].mdx"
    )
    try:
        mdx = MDX(mdx_path)
        entries: dict[str, str] = {}
        for k, v in mdx.items():
            word = k.decode("utf-8").strip()
            val  = v.decode("utf-8").strip()
            if val and not val.startswith("@@@LINK="):
                entries[word] = val

        for key, html in entries.items():
            # Strip edition marker ㊤/㊥/㊦, then optional " (N)" sense number
            base = re.sub(r'[㊤㊥㊦]$', '', key).strip()
            base = re.sub(r'\s*\(\d+\)$', '', base).strip()
            no_edition = re.sub(r'[㊤㊥㊦]$', '', key).strip()

            GRAMMAR.setdefault(base, []).append(html)
            if no_edition != base:
                GRAMMAR.setdefault(no_edition, []).append(html)

        logger.info(f"Grammar cargado: {len(entries)} entradas, {len(GRAMMAR)} claves.")
    except Exception as e:
        logger.error(f"Error cargando Grammar: {e}")


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
tokenizer_obj = None
mode = tokenizer.Tokenizer.SplitMode.C


@asynccontextmanager
async def lifespan(app: FastAPI):
    global tokenizer_obj
    logger.info("Iniciando aplicación...")
    logger.info("Cargando Sudachi...")
    tokenizer_obj = dictionary.Dictionary().create()
    logger.info("Sudachi listo (Modo C).")
    load_jitendex()
    load_kenkyusha()
    load_wisdom()
    load_genius()
    load_grammar()
    load_freq_jpdb()
    load_freq_bccwj()
    yield
    logger.info("Apagando aplicación...")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="Yomitsu Dictionary Service", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------
class TokenizeRequest(BaseModel):
    context_phrase: str
    user_selection: str
    char_offset: int | None = None  # byte offset of tapped word in context_phrase


class WarmPageRequest(BaseModel):
    text: str


# In-memory cache populated by /warm-page. Keyed by normalized form; value is
# the dictionary_data dict ready to embed in an /extract-word response.
_page_warm_cache: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
async def extract_word_with_sudachi(phrase: str, selection: str, char_offset: int | None = None) -> dict:
    await asyncio.sleep(0)
    logger.info(f"[SUDACHI] Frase: '{phrase[:60]}...' | Selección: '{selection}' | offset: {char_offset}")

    tokens = tokenizer_obj.tokenize(phrase, mode)

    # char_offset from Lua is a UTF-8 BYTE offset (Lua strings are byte arrays).
    # Python indexes Unicode strings by codepoint, not byte.
    # Convert: count characters in the UTF-8 prefix up to that byte.
    sel_start = -1
    if char_offset is not None:
        try:
            phrase_bytes = phrase.encode("utf-8")
            if 0 <= char_offset < len(phrase_bytes):
                prefix_chars = len(phrase_bytes[:char_offset].decode("utf-8", errors="ignore"))
                # Verify selection is actually at this codepoint position
                if phrase[prefix_chars:prefix_chars + len(selection)] == selection:
                    sel_start = prefix_chars
                    logger.info(f"[SUDACHI] Offset exacto: byte {char_offset} → char {prefix_chars}")
                else:
                    # Search in a small window around the estimated position (encoding drift)
                    for delta in range(-3, 4):
                        i = prefix_chars + delta
                        if i >= 0 and phrase[i:i + len(selection)] == selection:
                            sel_start = i
                            logger.info(f"[SUDACHI] Offset ajustado: byte {char_offset} → char {i} (delta {delta})")
                            break
        except Exception as e:
            logger.warning(f"[SUDACHI] Error convirtiendo offset: {e}")
    if sel_start < 0:
        sel_start = phrase.find(selection)
    sel_end = sel_start + len(selection) if sel_start >= 0 else -1
    logger.info(f"[SUDACHI] sel_start={sel_start}")

    best_token = None
    for token in tokens:
        if sel_start >= 0:
            tok_begin = token.begin()
            tok_end   = token.end()
            # Accept any token whose span overlaps with the user's selection.
            if tok_begin <= sel_start < tok_end or sel_start <= tok_begin < sel_end:
                best_token = token
                break
    # Fallback: substring match (handles cases where phrase == selection)
    if best_token is None:
        for token in tokens:
            if selection in token.surface():
                best_token = token
                break

    if best_token:
        pos_list = best_token.part_of_speech()
        pos_jp   = pos_list[0] if pos_list else ""
        pos_en   = _POS_MAP.get(pos_jp, pos_jp or "unknown")
        result = {
            "original_word":   best_token.surface(),
            "normalized_word": best_token.normalized_form(),
            "reading":         best_token.reading_form(),  # katakana — used for disambiguation
            "part_of_speech":  pos_en,
            "found": True,
        }
        logger.info(
            f"[SUDACHI] Match: '{result['original_word']}' → "
            f"'{result['normalized_word']}' [{result['reading']}] POS={pos_en}"
        )
        return result

    logger.warning(f"[SUDACHI] No se encontró '{selection}'.")
    return {"original_word": selection, "normalized_word": selection, "reading": "", "part_of_speech": "unknown", "found": False}


async def lookup_jitendex(word: str, reading: str = "", original: str = "") -> dict:
    logger.info(f"[JITENDEX] Buscando '{word}' (lectura: {reading})...")

    # Fallback chain: normalized form first, then surface form.
    # Handles cases like normalized 為る not being a Jitendex headword.
    candidates = list(dict.fromkeys(filter(None, [word, original])))

    for candidate in candidates:
        entries = JITENDEX.get(candidate, [])
        if not entries:
            continue

        # Fix: filter by reading to resolve homographs (今日 → きょう vs こんにち).
        # Sudachi returns katakana; Jitendex entry[1] is hiragana.
        if reading:
            hira = kata_to_hira(reading)
            matched = [e for e in entries if e[1] == hira]
            if matched:
                logger.info(f"[JITENDEX] Lectura '{hira}' filtró a {len(matched)} entrada(s).")
                entries = matched

        reading_display = entries[0][1] if entries else ""
        html = format_yomitan_to_html(entries, word=candidate, reading=reading_display)
        logger.info(f"[JITENDEX] Encontrado para '{candidate}' ({len(entries)} entradas).")
        return {"html_content": html, "reading": reading_display, "found": True}

    logger.info(f"[JITENDEX] No encontrado. Candidatos probados: {candidates}")
    return {"html_content": "", "reading": "", "found": False}


async def lookup_kenkyusha(word: str, original: str = "") -> dict:
    logger.info(f"[KENKYUSHA] Buscando '{word}'...")

    for candidate in list(dict.fromkeys(filter(None, [word, original]))):
        definition = KENKYUSHA.get(candidate)
        if definition:
            html = format_kenkyusha_to_html(definition)
            logger.info(f"[KENKYUSHA] Encontrado para '{candidate}'.")
            return {"html_content": html, "found": True}

    logger.info(f"[KENKYUSHA] No encontrado.")
    return {"html_content": "", "found": False}


async def lookup_wisdom(word: str, original: str = "") -> dict:
    await asyncio.sleep(0)
    logger.info(f"[WISDOM] Buscando '{word}'...")
    for candidate in list(dict.fromkeys(filter(None, [word, original]))):
        definition = WISDOM.get(candidate)
        if definition:
            html = format_wisdom_to_html(definition)
            logger.info(f"[WISDOM] Encontrado para '{candidate}'.")
            return {"html_content": html, "found": True}
    logger.info(f"[WISDOM] No encontrado.")
    return {"html_content": "", "found": False}


async def lookup_genius(word: str, original: str = "") -> dict:
    await asyncio.sleep(0)
    logger.info(f"[GENIUS] Buscando '{word}'...")
    for candidate in list(dict.fromkeys(filter(None, [word, original]))):
        definition = GENIUS.get(candidate)
        if definition:
            html = format_genius_to_html(definition)
            logger.info(f"[GENIUS] Encontrado para '{candidate}'.")
            return {"html_content": html, "found": True}
    logger.info(f"[GENIUS] No encontrado.")
    return {"html_content": "", "found": False}


async def lookup_grammar(word: str, original: str = "") -> dict:
    await asyncio.sleep(0)
    logger.info(f"[GRAMMAR] Buscando '{word}'...")
    for candidate in list(dict.fromkeys(filter(None, [word, original]))):
        entries = GRAMMAR.get(candidate)
        if entries:
            html = format_grammar_to_html(entries)
            logger.info(f"[GRAMMAR] Encontrado para '{candidate}' ({len(entries)} entrada(s)).")
            return {"html_content": html, "found": True}
    logger.info(f"[GRAMMAR] No encontrado.")
    return {"html_content": "", "found": False}


def get_kanji_breakdown(word: str) -> list[dict]:
    """Return per-kanji reading+meaning data for each CJK character in word."""
    result: list[dict] = []
    seen: set[str] = set()
    for char in word:
        if char in seen:
            continue
        seen.add(char)
        if not ("一" <= char <= "鿿" or "㐀" <= char <= "䶿"):
            continue
        entries = KANJI_INDEX.get(char)
        if not entries:
            continue
        reading_map: dict[str, list[str]] = {}
        for e in entries:
            r = e["reading"]
            if r not in reading_map:
                reading_map[r] = []
            for g in e["glosses"]:
                if g not in reading_map[r]:
                    reading_map[r].append(g)
        result.append({
            "char": char,
            "readings": [
                {"reading": r, "glosses": g[:3]}
                for r, g in reading_map.items()
            ],
        })
    return result


def lookup_freq(word: str, original: str = "") -> dict:
    result: dict[str, int] = {}
    for candidate in list(dict.fromkeys(filter(None, [word, original]))):
        if "jpdb" not in result:
            val = FREQ_JPDB.get(candidate)
            if val is not None:
                result["jpdb"] = val
        if "bccwj" not in result:
            val = FREQ_BCCWJ.get(candidate)
            if val is not None:
                result["bccwj"] = val
        if len(result) == 2:
            break
    if result:
        logger.info(f"[FREQ] '{word}': {result}")
    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "dictionaries": {
            "jitendex":   len(JITENDEX),
            "kenkyusha":  len(KENKYUSHA),
            "wisdom":     len(WISDOM),
            "genius":     len(GENIUS),
            "grammar":    len(GRAMMAR),
            "freq_jpdb":  len(FREQ_JPDB),
            "freq_bccwj": len(FREQ_BCCWJ),
        },
    }


@app.post("/tokenize")
async def tokenize(request: TokenizeRequest):
    """Sudachi-only: returns normalized form, reading, part_of_speech, and romaji_sentence."""
    result = await extract_word_with_sudachi(
        request.context_phrase, request.user_selection, request.char_offset
    )
    sentence = _extract_sentence(request.context_phrase, request.user_selection)
    result["romaji_sentence"] = _sentence_to_romaji(sentence)
    return result


@app.post("/extract-word")
async def extract_word(request: TokenizeRequest):
    logger.info("--- Nueva petición ---")
    logger.info(f"[ENDPOINT] Phrase: '{request.context_phrase}' | Selection: '{request.user_selection}'")

    # 1. Sudachi — now returns reading (katakana) for disambiguation
    sudachi_result = await extract_word_with_sudachi(
        request.context_phrase, request.user_selection, request.char_offset
    )
    target_word    = sudachi_result["normalized_word"]
    target_reading = sudachi_result.get("reading", "")
    original_word  = sudachi_result["original_word"]

    # 2a. Warm-cache hit — skip all dict lookups
    if target_word in _page_warm_cache:
        logger.info(f"[WARM-HIT] '{target_word}'")
        return {"word_data": sudachi_result, "dictionary_data": _page_warm_cache[target_word]}

    # 2b. Lookups en paralelo
    (
        jitendex_result,
        kenkyusha_result,
        wisdom_result,
        genius_result,
        grammar_result,
    ) = await asyncio.gather(
        lookup_jitendex(target_word, target_reading, original_word),
        lookup_kenkyusha(target_word, original_word),
        lookup_wisdom(target_word, original_word),
        lookup_genius(target_word, original_word),
        lookup_grammar(target_word, original_word),
    )

    # 3. Respuesta con HTMLs separados
    reading_display = (
        jitendex_result.get("reading", "")
        or kata_to_hira(target_reading)
    )
    any_found = any(
        r["found"] for r in [jitendex_result, kenkyusha_result, wisdom_result, genius_result, grammar_result]
    )
    freq_result   = lookup_freq(target_word, original_word)
    kanji_result  = get_kanji_breakdown(target_word)
    response_payload = {
        "word_data": sudachi_result,
        "dictionary_data": {
            "reading":         reading_display,
            "found":           any_found,
            "frequency":       freq_result,
            "kanji_breakdown": kanji_result,
            "jitendex":  {"html_content": jitendex_result["html_content"],  "found": jitendex_result["found"]},
            "kenkyusha": {"html_content": kenkyusha_result["html_content"], "found": kenkyusha_result["found"]},
            "wisdom":    {"html_content": wisdom_result["html_content"],    "found": wisdom_result["found"]},
            "genius":    {"html_content": genius_result["html_content"],    "found": genius_result["found"]},
            "grammar":   {"html_content": grammar_result["html_content"],   "found": grammar_result["found"]},
        },
    }

    logger.info(
        f"[ENDPOINT] Listo para '{target_word}' [{target_reading}]. "
        f"Jitendex={jitendex_result['found']} Kenkyusha={kenkyusha_result['found']} "
        f"Wisdom={wisdom_result['found']} Genius={genius_result['found']} Grammar={grammar_result['found']}"
    )
    return response_payload


async def _warm_token(normalized: str, reading: str, original: str) -> None:
    jitendex_result, kenkyusha_result, wisdom_result, genius_result, grammar_result = await asyncio.gather(
        lookup_jitendex(normalized, reading, original),
        lookup_kenkyusha(normalized, original),
        lookup_wisdom(normalized, original),
        lookup_genius(normalized, original),
        lookup_grammar(normalized, original),
    )
    reading_display = jitendex_result.get("reading", "") or kata_to_hira(reading)
    any_found = any(r["found"] for r in [jitendex_result, kenkyusha_result, wisdom_result, genius_result, grammar_result])
    _page_warm_cache[normalized] = {
        "reading":         reading_display,
        "found":           any_found,
        "frequency":       lookup_freq(normalized, original),
        "kanji_breakdown": get_kanji_breakdown(normalized),
        "jitendex":  {"html_content": jitendex_result["html_content"],  "found": jitendex_result["found"]},
        "kenkyusha": {"html_content": kenkyusha_result["html_content"], "found": kenkyusha_result["found"]},
        "wisdom":    {"html_content": wisdom_result["html_content"],    "found": wisdom_result["found"]},
        "genius":    {"html_content": genius_result["html_content"],    "found": genius_result["found"]},
        "grammar":   {"html_content": grammar_result["html_content"],   "found": grammar_result["found"]},
    }


_GRAMMAR_POS_PRIORITY = {
    "verb": 1, "i-adjective": 2, "na-adjective": 2, "noun": 3,
    "adverb": 4, "conjunction": 5,
}
_GRAMMAR_POS_SKIP = {"particle", "auxiliary verb", "symbol", "supplementary symbol", "suffix", "prefix"}


def _best_token_for_grammar(tokens) -> dict | None:
    """Return {normalized, part_of_speech} for the most interesting token in a
    sentence: prefer verbs → adjectives → nouns → other content words."""
    best = None
    best_pri = 999
    for tok in tokens:
        pos_en = _POS_MAP.get(tok.part_of_speech()[0], "other")
        if pos_en in _GRAMMAR_POS_SKIP:
            continue
        pri = _GRAMMAR_POS_PRIORITY.get(pos_en, 6)
        if pri < best_pri:
            best_pri = pri
            best = {"normalized": tok.normalized_form(), "part_of_speech": pos_en}
    return best


@app.post("/warm-page")
async def warm_page(request: WarmPageRequest):
    # Tokenize sentence by sentence so we can report the best target per sentence
    # for the orchestrator to use when pre-warming grammar.
    raw_sentences = re.split(r'(?<=[。！？])|(?<=\n)', request.text)

    seen_forms: set[str] = set()
    dict_tasks: list = []
    sentence_targets: list[dict] = []

    for raw in raw_sentences:
        sentence = raw.strip()
        if not sentence:
            continue
        tokens = tokenizer_obj.tokenize(sentence, mode)
        for tok in tokens:
            nf = tok.normalized_form()
            if nf not in seen_forms and nf not in _page_warm_cache:
                seen_forms.add(nf)
                dict_tasks.append(_warm_token(nf, tok.reading_form(), tok.surface()))
        best = _best_token_for_grammar(tokens)
        if best:
            sentence_targets.append({
                "sentence":      sentence,
                "target_word":   best["normalized"],
                "part_of_speech": best["part_of_speech"],
            })

    if dict_tasks:
        await asyncio.gather(*dict_tasks, return_exceptions=True)

    logger.info(f"[WARM-PAGE] {len(dict_tasks)} tokens dict | {len(sentence_targets)} frases para gramática")
    return {"warmed": len(dict_tasks), "sentence_targets": sentence_targets}
