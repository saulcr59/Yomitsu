"""
wisdom_formatter.py — ウィズダム和英辞典 MDX → XHTML para KOReader/MuPDF
"""
from __future__ import annotations
from bs4 import BeautifulSoup, Tag, NavigableString
from formatter_base import esc as _esc, wrap_body, CSS


def _render_header(midashi: Tag) -> list[str]:
    tk = midashi.find('span', class_=lambda c: c and 'titlekana' in c.split())
    hy = midashi.find('span', class_=lambda c: c and 'hyouki' in c.split())
    hi = midashi.find('span', class_='hinshi')

    reading = tk.get_text().strip() if tk else ''
    kanji   = hy.get_text().strip() if hy else ''
    hinshi  = hi.get_text().strip() if hi else ''

    hdr = ''
    if reading and kanji:
        hdr = f'<font color="#777">{_esc(reading)}</font>&#x3010;<b>{_esc(kanji)}</b>&#x3011;'
    elif kanji:
        hdr = f'<b>{_esc(kanji)}</b>'
    elif reading:
        hdr = f'<font color="#777">{_esc(reading)}</font>'
    if hinshi:
        hdr += f' <i><font color="#555">{_esc(hinshi)}</font></i>'

    return [f'<p>{hdr}</p>'] if hdr else []


def _render_kaisetu(kaisetu: Tag, max_senses: int = 3, max_ex: int = 2) -> list[str]:
    parts: list[str] = []
    sense_count = 0

    for gogi in kaisetu.find_all('div', class_='gogi'):
        if sense_count >= max_senses:
            break
        sense_count += 1

        # Definitions
        yakugo_g = gogi.find('div', class_='yakugo_g')
        if yakugo_g:
            defs_html: list[str] = []
            for yakugo in yakugo_g.find_all('span', class_='yakugo'):
                kubun = yakugo.find('span', class_='gogikubun')
                full_text = yakugo.get_text().strip().rstrip(';').strip()
                if kubun:
                    ktext = kubun.get_text().strip()
                    rest = full_text.replace(ktext, '', 1).strip()
                    defs_html.append(f'<font color="#666">〔{_esc(ktext)}〕</font> {_esc(rest)}')
                else:
                    defs_html.append(_esc(full_text))
            if defs_html:
                parts.append(f'<p style="margin:0.1em 0 0.3em 0.6em">{"；　".join(defs_html)}</p>')

        # Examples
        yoorei_g = gogi.find('div', class_='yoorei_g')
        if yoorei_g:
            ex_count = 0
            for item in yoorei_g.find_all(['div'], class_=['yoorei', 'hukugou_g']):
                if ex_count >= max_ex:
                    break
                cls = item.get('class') or []
                if 'yoorei' in cls:
                    reibun_tag = item.find('span', class_='reibun')
                    yakubun_tag = item.find('span', class_='yakubun')
                elif 'hukugou_g' in cls:
                    reibun_tag = item.find('span', class_='hukugou')
                    yakubun_tag = item.find('span', class_='yakubun')
                else:
                    continue

                ja = reibun_tag.get_text().strip() if reibun_tag else ''
                en = yakubun_tag.get_text().strip() if yakubun_tag else ''
                if ja:
                    parts.append(
                        f'<p style="margin:0.4em 0 0 0.5em; padding-left:0.5em; border-left:2px solid #bbb">'
                        f'{_esc(ja)}</p>'
                    )
                    if en:
                        parts.append(
                            f'<p style="margin:0 0 0.3em 0.5em; padding-left:0.5em; border-left:2px solid #bbb">'
                            f'<font color="#555">{_esc(en)}</font></p>'
                        )
                    ex_count += 1

    return parts


def format_wisdom_to_html(definition: str) -> str:
    if not definition or not definition.strip():
        return ""

    soup = BeautifulSoup(definition, 'html.parser')
    body_parts: list[str] = []

    koumoku = soup.find('div', class_='koumoku')
    if koumoku:
        midashi = (
            koumoku.find('div', class_='midashi_pri2', recursive=False) or
            koumoku.find('div', class_='midashi', recursive=False)
        )
        if midashi:
            body_parts.extend(_render_header(midashi))
        kaisetu = koumoku.find('div', class_='kaisetu', recursive=False)
        if kaisetu:
            body_parts.extend(_render_kaisetu(kaisetu))

    for koko in soup.find_all('div', class_='kokoumoku'):
        body_parts.append('<hr/>')
        midashi = koko.find('div', class_='midashi', recursive=False)
        if midashi:
            body_parts.extend(_render_header(midashi))
        kaisetu = koko.find('div', class_='kaisetu', recursive=False)
        if kaisetu:
            body_parts.extend(_render_kaisetu(kaisetu))

    body = '\n'.join(body_parts) if body_parts else '<p>No definition found.</p>'
    return wrap_body(body)
