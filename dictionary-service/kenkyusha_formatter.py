"""
kenkyusha_formatter.py
======================
Convierte entradas HTML del Kenkyusha (研究社新和英大辞典) MDX
a XHTML limpio compatible con KOReader/MuPDF.

Estructura del HTML original:
  Entrada simple:
    <font color=Firebrick>よみ【見出し語】</font>
    definición con ejemplos

  Entrada compuesta (múltiples sub-entradas):
    <b>I</b><font color=Firebrick>よみ【見出し語1】</font> ...
    <b>II</b><font color=Firebrick>よみ【見出し語2】</font> ...
    <b>III</b><font color=Firebrick>よみ【見出し語3】</font> ...

  Dentro de cada sub-entrada:
    <b>1</b> acepción 1
    <b>2</b> acepción 2
    〔ctx〕  → nota de contexto
    【campo】 → campo temático
    《uso》   → registro/uso
    <▲> nota → nota cultural
    [⇒word]  → referencia cruzada
    <font color=#151B8D>●ejemplo. translation</font>  → ejemplo principal
    <font color=#151B8D>・subejemplo. translation</font> → subejemplo
    <b>compuesto</b> traducción  → expresión derivada
"""

from __future__ import annotations
import re
from bs4 import BeautifulSoup, NavigableString, Tag
from formatter_base import esc as _esc, wrap_body

# ---------------------------------------------------------------------------
# CSS — compatible CSS 2.1 / MuPDF
# ---------------------------------------------------------------------------
CSS = """\
body {
    margin: 0;
    padding: 0.4em 0.6em;
    font-size: 1em;
    line-height: 1.6;
    text-align: left;
}
p { margin: 0.15em 0; }
hr { border: none; border-top: 2px solid #ccc; margin: 0.5em 0; }
.k-ctx   { color: #666; font-size: 0.9em; }
.k-field { font-weight: bold; color: #333; }
.k-reg   { color: #777; font-style: italic; font-size: 0.9em; }
.k-xref  { color: #666; font-style: italic; }
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(s: str) -> str:
    return re.sub(r'\s+', ' ', s).strip()


# Números romanos que usa el Kenkyusha para sub-entradas
ROMAN = {'I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X'}


def _is_roman(text: str) -> bool:
    return text.strip() in ROMAN


def _is_arabic(text: str) -> bool:
    return bool(re.match(r'^\d+$', text.strip()))


# ---------------------------------------------------------------------------
# Separar el HTML en sub-entradas (I, II, III, IV...)
# ---------------------------------------------------------------------------

def _split_subentries(raw: str) -> list[tuple[str, str]]:
    """
    Divide el HTML crudo en sub-entradas según los marcadores <b>I</b>, <b>II</b>...
    Devuelve lista de (roman_numeral, html_chunk).
    Si no hay marcadores romanos, devuelve [("", raw)].
    """
    # Marcamos los <b>ROMAN</b> con un separador único
    def replace_roman(m):
        text = BeautifulSoup(m.group(0), 'html.parser').get_text()
        if _is_roman(text):
            return f'\x00ROMAN\x00{text.strip()}\x00'
        return m.group(0)

    marked = re.sub(r'<b>[^<]{1,10}</b>', replace_roman, raw)

    if '\x00ROMAN\x00' not in marked:
        return [("", raw)]

    parts = re.split(r'\x00ROMAN\x00([^\x00]+)\x00', marked)
    # parts = [pre_text, roman1, chunk1, roman2, chunk2, ...]

    result = []
    i = 1
    while i < len(parts) - 1:
        roman = parts[i]
        chunk = parts[i + 1]
        result.append((roman, chunk))
        i += 2

    return result if result else [("", raw)]


# ---------------------------------------------------------------------------
# Parsear cabecera de una sub-entrada
# ---------------------------------------------------------------------------

def _parse_header(chunk: str) -> tuple[str, str]:
    """
    Extrae (reading, headword) del primer <font color=Firebrick> del chunk.
    """
    soup = BeautifulSoup(chunk, 'html.parser')
    tag = soup.find('font', color=lambda c: c and c.lower() in ('firebrick', '#b22222'))
    if not tag:
        return "", ""

    text = tag.get_text()
    m = re.search(r'【(.+?)】', text)
    headword = m.group(1) if m else text.strip()
    reading = re.sub(r'【.+】.*', '', text).strip()
    # Limpiar guiones de prefijo/sufijo en el headword display
    headword = headword.replace('-', '')
    return reading, headword


# ---------------------------------------------------------------------------
# Separar acepciones numeradas dentro de un chunk
# ---------------------------------------------------------------------------

def _split_senses(chunk: str) -> list[tuple[str, str]]:
    """
    Divide en acepciones por <b>N</b> donde N es número arábigo.
    Devuelve lista de (num_str, html_content).
    Si no hay numeración, devuelve [("", chunk)].
    """
    def replace_num(m):
        text = BeautifulSoup(m.group(0), 'html.parser').get_text()
        if _is_arabic(text):
            return f'\x00NUM\x00{text.strip()}\x00'
        return m.group(0)

    marked = re.sub(r'<b>[^<]{1,5}</b>', replace_num, chunk)

    if '\x00NUM\x00' not in marked:
        return [("", chunk)]

    parts = re.split(r'\x00NUM\x00([^\x00]+)\x00', marked)
    # Texto antes del primer número (definición breve sin número)
    pre = parts[0] if parts else ""

    result = []
    if pre.strip():
        result.append(("", pre))

    i = 1
    while i < len(parts) - 1:
        num = parts[i]
        content = parts[i + 1]
        result.append((num, content))
        i += 2

    return result if result else [("", chunk)]


# ---------------------------------------------------------------------------
# Parsear el contenido de una acepción
# ---------------------------------------------------------------------------

def _parse_sense_content(html_chunk: str) -> dict:
    """
    Extrae de un chunk de acepción:
      - def_text: texto de la definición
      - examples: lista de {type: 'main'|'sub', ja: str, en: str}
      - compounds: lista de {head: str, text: str}

    Los compuestos en negrita recogen TODO hasta el siguiente <br> para
    capturar correctamente inline tags como <sub>2</sub> en cross-refs.
    """
    soup = BeautifulSoup(html_chunk, 'html.parser')

    def_parts = []
    examples = []
    compounds = []
    pending_compound_head = None
    compound_text_parts: list[str] = []

    for node in soup.children:
        # Modo recogida de texto de compuesto: acumular hasta <br>
        if pending_compound_head is not None:
            if isinstance(node, Tag) and node.name == 'br':
                compounds.append({
                    'head': pending_compound_head,
                    'text': _clean(''.join(compound_text_parts))
                })
                pending_compound_head = None
                compound_text_parts = []
            elif isinstance(node, NavigableString):
                compound_text_parts.append(str(node).replace('\r', '').replace('\n', ' '))
            elif isinstance(node, Tag):
                compound_text_parts.append(node.get_text())
            continue

        if isinstance(node, NavigableString):
            t = str(node).replace('\r', '').replace('\n', ' ')
            if t.strip():
                def_parts.append(t)

        elif isinstance(node, Tag):
            if node.name == 'font':
                color = (node.get('color') or '').lower().replace(' ', '')
                if color in ('#151b8d', '151b8d', '#000080', 'navy'):
                    raw_text = node.get_text()
                    if raw_text.startswith('●'):
                        ex_text = raw_text[1:].strip()
                        ja, en = _split_example(ex_text)
                        examples.append({'type': 'main', 'ja': ja, 'en': en})
                    elif raw_text.startswith('・'):
                        ex_text = raw_text[1:].strip()
                        ja, en = _split_example(ex_text)
                        examples.append({'type': 'sub', 'ja': ja, 'en': en})
                    else:
                        def_parts.append(raw_text)
                elif color in ('firebrick', '#b22222'):
                    pass
                else:
                    def_parts.append(node.get_text())

            elif node.name == 'b':
                t = node.get_text().strip()
                if t and not _is_arabic(t) and not _is_roman(t):
                    pending_compound_head = t
                    compound_text_parts = []
                elif t and (_is_arabic(t) or _is_roman(t)):
                    pass
                else:
                    def_parts.append(node.get_text())

            elif node.name in ('sub', 'sup'):
                def_parts.append(node.get_text())

            elif node.name == 'br':
                pass

            else:
                def_parts.append(node.get_text())

    # Compuesto sin <br> de cierre al final de la entrada
    if pending_compound_head is not None and compound_text_parts:
        compounds.append({
            'head': pending_compound_head,
            'text': _clean(''.join(compound_text_parts))
        })

    def_text = _clean(''.join(def_parts))

    return {
        'def_text': def_text,
        'examples': examples,
        'compounds': compounds,
    }


def _split_example(text: str) -> tuple[str, str]:
    """
    Separa japonés de la traducción inglesa en un ejemplo.
    Heurística mejorada: busca el primer punto japonés 。o el primer
    bloque de texto ASCII significativo precedido de espacio.
    """
    # Si hay 。buscar el inglés después
    m = re.search(r'。\s+([A-Z])', text)
    if m:
        split_pos = m.start() + 1
        return text[:split_pos].strip(), text[split_pos:].strip()

    # Punto latino + espacio + mayuscula, solo si hay japones antes del punto
    m = re.search(r'\.\s+([A-Z])', text)
    if m and any('぀' <= c <= '鿿' or '一' <= c <= '鿿' for c in text[:m.start()]):
        split_pos = m.start() + 1
        return text[:split_pos].strip(), text[split_pos:].strip()

    # Buscar primera palabra ASCII tras caracter japones (mayuscula o minuscula)
    m = re.search(r'([぀-鿿゠-ヿ一-鿿])\s+([A-Za-z])', text)
    if m:
        split_pos = m.start(2)
        ja_candidate = text[:split_pos].strip()
        en_candidate = text[split_pos:].strip()
        if len(ja_candidate) >= 2 and len(en_candidate) >= 2:
            return ja_candidate, en_candidate

    return text, ""


# ---------------------------------------------------------------------------
# Renderizar a HTML
# ---------------------------------------------------------------------------

def _render_def_text(text: str) -> str:
    """Aplica marcadores especiales del Kenkyusha al texto de definición."""
    if not text:
        return ""
    result = _esc(text)
    result = re.sub(r'〔([^〕]+)〕', r'<font color="#666">〔\1〕</font>', result)
    result = re.sub(r'【([^】]+)】', r'<b>\1</b>', result)
    result = re.sub(r'《([^》]+)》', r'<i>\1</i>', result)
    result = re.sub(r'&lt;▲&gt;\s*', r'▲ ', result)
    result = re.sub(r'\[⇒([^\]]+)\]', r'<font color="#666">⇒\1</font>', result)
    result = re.sub(r'⇒([\w･ぁ-ん゛゜ァ-ヶ一-龯・]+)', r'<font color="#666">⇒\1</font>', result)
    result = re.sub(r'＝([\w･ぁ-ん゛゜ァ-ヶ一-龯・]+)', r'<font color="#666">＝\1</font>', result)
    return result


def _render_example(ex: dict) -> str:
    if ex['type'] == 'main':
        style_ja = 'margin:0.4em 0 0 0.5em; padding-left:0.5em; border-left:3px solid #aaa'
        style_en = 'margin:0 0 0.3em 0.5em; padding-left:0.5em; border-left:3px solid #aaa'
        marker = '&#x25CF;'
    else:
        style_ja = 'margin:0.3em 0 0 1em; padding-left:0.4em; border-left:2px solid #ccc'
        style_en = 'margin:0 0 0.25em 1em; padding-left:0.4em; border-left:2px solid #ccc'
        marker = '&#xB7;'
    html = f'<p style="{style_ja}">{marker} {_esc(ex["ja"])}</p>'
    if ex['en']:
        html += f'\n<p style="{style_en}"><font color="#555">{_esc(ex["en"])}</font></p>'
    return html


def _render_subentry(roman: str, chunk: str) -> str:
    """Renderiza una sub-entrada completa usando <p> para bloques garantizados."""
    parts = []

    # Cabecera — todo inline en un <p>
    reading, headword = _parse_header(chunk)
    hdr = '<p>'
    if roman:
        hdr += f'<b>{_esc(roman)}</b> '
    if reading:
        hdr += f'<font color="#777">{_esc(reading)}</font>'
    if headword:
        hdr += f'&#x3010;<b>{_esc(headword)}</b>&#x3011;'
    hdr += '</p>'
    parts.append(hdr)

    # Eliminar cabecera del chunk
    soup = BeautifulSoup(chunk, 'html.parser')
    header_tag = soup.find('font', color=lambda c: c and c.lower() in ('firebrick', '#b22222'))
    if header_tag:
        header_tag.decompose()
    remaining = str(soup)

    # ¿Redirect simple?
    clean = _clean(soup.get_text())
    if re.match(r'^[＝=]?⇒', clean):
        parts.append(f'<p><font color="#666">{_esc(clean)}</font></p>')
        return '\n'.join(parts)

    # Dividir en acepciones
    senses = _split_senses(remaining)
    all_compounds = []

    for num, sense_chunk in senses:
        parsed = _parse_sense_content(sense_chunk)
        all_compounds.extend(parsed['compounds'])

        if not parsed['def_text'] and not parsed['examples']:
            continue

        # Número + definición en el mismo <p> para que sean bloque conjunto
        if parsed['def_text']:
            def_line = ''
            if num:
                def_line += f'<b>{_esc(num)}.</b> '
            def_line += _render_def_text(parsed['def_text'])
            parts.append(f'<p>{def_line}</p>')

        for ex in parsed['examples']:
            parts.append(_render_example(ex))

    # Compuestos
    if all_compounds:
        parts.append('<p><font color="#aaa">&#x2500;&#x2500;&#x2500;</font></p>')
        for c in all_compounds:
            line = f'<b>{_esc(c["head"])}</b>'
            if c['text']:
                line += f' {_render_def_text(c["text"])}'
            parts.append(f'<p>{line}</p>')

    return '\n'.join(parts)


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

def format_kenkyusha_to_html(definition: str) -> str:
    """
    Convierte el HTML crudo de una entrada Kenkyusha MDX
    a XHTML 1.1 limpio para KOReader/MuPDF.
    """
    if not definition or not definition.strip():
        return ""

    definition = definition.replace('\r\n', '\n').replace('\r', '\n')

    subentries = _split_subentries(definition)

    body_parts = []
    for roman, chunk in subentries:
        rendered = _render_subentry(roman, chunk)
        if rendered.strip():
            body_parts.append(rendered)

    body = '\n<hr/>\n'.join(body_parts) if body_parts else '<p>No definition found.</p>'
    return wrap_body(body, css=CSS)
