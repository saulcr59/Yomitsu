"""
grammar_formatter.py — A Dictionary of Japanese Grammar (DOJG) MDX → XHTML para KOReader/MuPDF
"""
from __future__ import annotations
from bs4 import BeautifulSoup, Tag, NavigableString
from formatter_base import esc as _esc, wrap_body, CSS

EDITION_MAP = {'㊤': 'Basic', '㊥': 'Intermediate', '㊦': 'Advanced'}


def _render_example(li: Tag, parts: list[str]) -> None:
    # Build a mutable list of children
    children = list(li.children)

    # Remove sentence-id span
    new_children = [c for c in children if not (isinstance(c, Tag) and 'sentence-id' in (c.get('class') or []))]

    # Split at <br> tags
    segments: list[list] = []
    current: list = []
    for child in new_children:
        if isinstance(child, Tag) and child.name == 'br':
            segments.append(current)
            current = []
        elif isinstance(child, Tag) and 'cloze' in (child.get('class') or []):
            current.append(('cloze', child.get_text()))
        elif isinstance(child, NavigableString):
            t = str(child).strip()
            if t:
                current.append(('text', t))
        else:
            t = child.get_text().strip()
            if t:
                current.append(('text', t))
    if current:
        segments.append(current)

    def build_html(seg: list, bold_cloze: bool = False) -> str:
        result = ''
        for typ, t in seg:
            if typ == 'cloze' and bold_cloze:
                result += f'<b>{_esc(t)}</b>'
            else:
                result += _esc(t)
        return result

    # segments[0] = before first <br> (empty after removing sentence-id)
    # segments[1] = Japanese
    # segments[2+] = English (join in case of extra line breaks)
    ja_seg = segments[1] if len(segments) > 1 else []
    en_seg: list = []
    for extra in segments[2:]:
        en_seg.extend(extra)

    ja = build_html(ja_seg, bold_cloze=True)
    en = build_html(en_seg, bold_cloze=False)

    if ja:
        parts.append(
            f'<p style="margin:0.4em 0 0 0.5em; padding-left:0.5em; border-left:2px solid #bbb">'
            f'{ja}</p>'
        )
        if en:
            parts.append(
                f'<p style="margin:0 0 0.3em 0.5em; padding-left:0.5em; border-left:2px solid #bbb">'
                f'<font color="#555">{en}</font></p>'
            )


def _render_grammar_entry(definition: str) -> list[str]:
    soup = BeautifulSoup(definition, 'html.parser')
    parts: list[str] = []

    # Header: span.header (contains span.edition)
    header_span = soup.find('span', class_='header')
    if header_span:
        edition_span = header_span.find('span', class_='edition')
        edition = edition_span.get_text().strip() if edition_span else ''
        if edition_span:
            edition_span.decompose()
        term = header_span.get_text().strip()
        level = EDITION_MAP.get(edition, '')

        hdr = f'<b>{_esc(term)}</b>'
        if level:
            hdr += f' <font color="#666"><i>({_esc(level)})</i></font>'
        parts.append(f'<p style="font-size:1.05em; margin-bottom:0.3em">{hdr}</p>')

    # POS
    pos_span = soup.find('span', class_='pos')
    if pos_span:
        parts.append(f'<p style="margin:0"><i><font color="#555">{_esc(pos_span.get_text().strip())}</font></i></p>')

    # Meaning
    meaning_span = soup.find('span', class_='meaning')
    if meaning_span:
        parts.append(f'<p style="margin:0.3em 0 0.2em 0">{_esc(meaning_span.get_text().strip())}</p>')

    # Related expressions
    related_span = soup.find('span', class_='related')
    if related_span:
        text = related_span.get_text().strip()
        parts.append(f'<p style="margin:0.1em 0; color:#666; font-style:italic">{_esc(text)}</p>')

    # English counterpart
    counterpart_span = soup.find('span', class_='counterpart')
    if counterpart_span:
        parts.append(
            f'<p style="margin:0.2em 0 0.3em 0.6em">'
            f'<font color="#555">{_esc(counterpart_span.get_text().strip())}</font></p>'
        )

    # Formation table — flatten rows to text
    formation_span = soup.find('span', class_='formation')
    if formation_span:
        table = formation_span.find('table')
        if table:
            rows: list[str] = []
            for tr in table.find_all('tr'):
                cells = [td.get_text().strip().replace('\xa0', '') for td in tr.find_all('td')]
                cells = [c for c in cells if c]
                if cells:
                    rows.append(' ｜ '.join(cells))
            if rows:
                formation_html = '<br/>'.join(_esc(r) for r in rows)
                parts.append(
                    f'<p style="margin:0.3em 0 0.3em 0.6em; color:#333; font-size:0.9em">'
                    f'{formation_html}</p>'
                )

    # Examples (max 5)
    examples_span = soup.find('span', class_='examples')
    if examples_span:
        count = 0
        for li in examples_span.find_all('li'):
            if count >= 5:
                break
            _render_example(li, parts)
            count += 1

    return parts


def format_grammar_to_html(definitions: list[str]) -> str:
    """Takes a list of raw DOJG HTML strings and formats them into one document."""
    if not definitions:
        return ""

    body_parts: list[str] = []
    for i, definition in enumerate(definitions):
        if i > 0:
            body_parts.append('<hr style="border-top:2px solid #ccc; margin:0.7em 0"/>')
        body_parts.extend(_render_grammar_entry(definition))

    body = '\n'.join(body_parts) if body_parts else '<p>No definition found.</p>'
    return wrap_body(body)
