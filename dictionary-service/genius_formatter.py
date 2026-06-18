"""
genius_formatter.py — ジーニアス和英辞典 MDX → XHTML para KOReader/MuPDF
"""
from __future__ import annotations
from bs4 import BeautifulSoup, Tag
from formatter_base import esc as _esc, wrap_body, CSS


def format_genius_to_html(definition: str) -> str:
    if not definition or not definition.strip():
        return ""

    soup = BeautifulSoup(definition, 'html.parser')
    item = soup.find('div', class_='item')
    if not item:
        return ""

    parts: list[str] = []

    # Header
    midashi = item.find('div', class_='midashi', recursive=False)
    if midashi:
        tk = midashi.find('span', class_='titlekana')
        mh = midashi.find('span', class_='m_hyoki')
        reading = tk.get_text().strip() if tk else ''
        kanji_raw = mh.get_text().strip() if mh else ''
        kanji = kanji_raw.strip('［］').strip()

        hdr = ''
        if reading:
            hdr += f'<font color="#777">{_esc(reading)}</font>'
        if kanji:
            hdr += f'&#x3010;<b>{_esc(kanji)}</b>&#x3011;'
        if hdr:
            parts.append(f'<p>{hdr}</p>')

    honbun = item.find('div', class_='honbun', recursive=False)
    if not honbun:
        body = '\n'.join(parts) if parts else '<p>No definition found.</p>'
        return wrap_body(body)

    # Definitions (collect all mean_eng)
    defs_html: list[str] = []
    for mean_eng in honbun.find_all('div', class_='mean_eng'):
        shiro = mean_eng.find('span', class_='shironuki')
        eng   = mean_eng.find('span', class_='eng')
        ctx      = shiro.get_text().strip() if shiro else ''
        eng_text = eng.get_text().strip() if eng else mean_eng.get_text().strip().rstrip(';').strip()
        if not eng_text:
            continue
        if ctx:
            defs_html.append(f'<font color="#666">{_esc(ctx)}</font> <b>{_esc(eng_text)}</b>')
        else:
            defs_html.append(f'<b>{_esc(eng_text)}</b>')

    if defs_html:
        parts.append(f'<p style="margin:0.1em 0 0.3em 0.6em">{"；　".join(defs_html)}</p>')

    # Examples (max 3)
    ex_count = 0
    for mean_yorei in honbun.find_all('div', class_='mean_yorei'):
        if ex_count >= 3:
            break
        jp_span = mean_yorei.find('span', class_='scope_exam_jp')
        en_span = mean_yorei.find('span', class_='scope_exam_en')
        jp = jp_span.get_text().strip() if jp_span else ''
        en = en_span.get_text().strip() if en_span else ''
        if jp:
            parts.append(
                f'<p style="margin:0.4em 0 0 0.5em; padding-left:0.5em; border-left:2px solid #bbb">'
                f'{_esc(jp)}</p>'
            )
            if en:
                parts.append(
                    f'<p style="margin:0 0 0.3em 0.5em; padding-left:0.5em; border-left:2px solid #bbb">'
                    f'<font color="#555">{_esc(en)}</font></p>'
                )
            ex_count += 1

    # Cross-references / notes
    for mean_normal in honbun.find_all('div', class_='mean_normal'):
        text = mean_normal.get_text().strip()
        if text:
            parts.append(f'<p style="color:#666; font-style:italic; margin:0.15em 0">{_esc(text)}</p>')

    body = '\n'.join(parts) if parts else '<p>No definition found.</p>'
    return wrap_body(body)
