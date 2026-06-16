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
import html as html_lib
from bs4 import BeautifulSoup, NavigableString, Tag

# ---------------------------------------------------------------------------
# CSS — compatible CSS 2.1 / MuPDF
# ---------------------------------------------------------------------------
CSS = """\
body {
    margin: 0;
    padding: 0.4em 0.6em;
    font-size: 1em;
    line-height: 1.6;
}

/* Sub-entrada (I ほん【本】, II ほん-【本-】...) */
.k-subentry {
    margin-bottom: 0.8em;
}
.k-subentry + .k-subentry {
    border-top: 2px solid #ccc;
    padding-top: 0.6em;
    margin-top: 0.6em;
}

/* Cabecera */
.k-header {
    margin-bottom: 0.3em;
}
.k-roman {
    font-weight: bold;
    font-size: 0.85em;
    color: #888;
    margin-right: 0.3em;
}
.k-reading {
    color: #888;
    font-size: 0.85em;
    margin-right: 0.2em;
}
.k-headword {
    font-weight: bold;
    font-size: 1.1em;
}

/* Acepciones numeradas */
.k-sense {
    margin-top: 0.4em;
    margin-bottom: 0.3em;
}
.k-num {
    font-weight: bold;
    color: #333;
    margin-right: 0.3em;
}

/* Definición principal */
.k-def {
    margin: 0.1em 0 0.15em 0;
}

/* Etiquetas inline */
.k-field {
    display: inline-block;
    font-size: 0.75em;
    font-weight: bold;
    background-color: #505050;
    color: #fff;
    padding: 0.1em 0.3em;
    border-radius: 0.25em;
    margin-right: 0.3em;
    vertical-align: middle;
}
.k-ctx {
    color: #666;
    font-size: 0.9em;
}
.k-register {
    color: #777;
    font-size: 0.85em;
    font-style: italic;
}
.k-note {
    color: #666;
    font-size: 0.85em;
    margin: 0.2em 0 0.2em 0.5em;
    padding-left: 0.5em;
    border-left: 2px solid #ccc;
}
.k-xref {
    color: #666;
    font-size: 0.9em;
    font-style: italic;
}

/* Ejemplos */
.k-examples {
    margin: 0.3em 0 0.1em 0;
}
.k-ex-main {
    margin: 0.2em 0 0.1em 0.4em;
    padding-left: 0.5em;
    border-left: 3px solid #bbb;
}
.k-ex-sub {
    margin: 0.15em 0 0.1em 0.8em;
    padding-left: 0.4em;
    border-left: 2px solid #ddd;
    font-size: 0.95em;
}
.k-ex-ja { display: block; }
.k-ex-en { display: block; color: #555; font-size: 0.9em; }

/* Compuestos y expresiones derivadas */
.k-compounds {
    margin-top: 0.5em;
    padding-top: 0.3em;
    border-top: 1px dashed #ddd;
}
.k-compound {
    margin: 0.25em 0;
}
.k-comp-head {
    font-weight: bold;
    margin-right: 0.3em;
}
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _esc(s: str) -> str:
    return html_lib.escape(str(s), quote=False)


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
    """
    soup = BeautifulSoup(html_chunk, 'html.parser')

    def_parts = []
    examples = []
    compounds = []
    pending_compound_head = None

    for node in soup.children:
        if isinstance(node, NavigableString):
            t = str(node).replace('\r', '').replace('\n', ' ')
            if t.strip():
                if pending_compound_head:
                    # El texto que sigue a un <b>compuesto</b>
                    compounds.append({
                        'head': pending_compound_head,
                        'text': _clean(t)
                    })
                    pending_compound_head = None
                else:
                    def_parts.append(t)

        elif isinstance(node, Tag):
            if node.name == 'font':
                color = (node.get('color') or '').lower().replace(' ', '')
                if color in ('#151b8d', '151b8d', '#000080', 'navy'):
                    # Ejemplo o subejemplo
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
                    # Sub-cabecera (no debería llegar aquí normalmente)
                    pass
                else:
                    def_parts.append(node.get_text())

            elif node.name == 'b':
                t = node.get_text().strip()
                if t and not _is_arabic(t) and not _is_roman(t):
                    # Es un compuesto en negrita
                    pending_compound_head = t
                elif t and (_is_arabic(t) or _is_roman(t)):
                    # Número de acepción que no se filtró — ignorar
                    pass
                else:
                    def_parts.append(node.get_text())

            elif node.name == 'sub':
                def_parts.append(node.get_text())

            elif node.name == 'br':
                pass

            else:
                def_parts.append(node.get_text())

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

    # Heurística: mayúscula ASCII tras 2+ espacios o tras punto latino
    m = re.search(r'\.\s{2,}([A-Z])', text)
    if m:
        split_pos = m.start() + 1
        return text[:split_pos].strip(), text[split_pos:].strip()

    # Último recurso: primera mayúscula ASCII con espacio previo
    m = re.search(r'\s([A-Z][a-z])', text)
    if m:
        ja_candidate = text[:m.start()].strip()
        en_candidate = text[m.start():].strip()
        # Solo separar si el japonés tiene al menos 4 chars y contiene japonés real
        has_jp = any('\u3040' <= c <= '\u9fff' for c in ja_candidate)
        if has_jp and len(ja_candidate) >= 4 and len(en_candidate) >= 4:
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

    # 〔contexto〕
    result = re.sub(
        r'〔([^〕]+)〕',
        r'<span class="k-ctx">〔\1〕</span>',
        result
    )
    # 【campo】
    result = re.sub(
        r'【([^】]+)】',
        r'<span class="k-field">\1</span>',
        result
    )
    # 《uso》
    result = re.sub(
        r'《([^》]+)》',
        r'<span class="k-register">《\1》</span>',
        result
    )
    # <▲> nota cultural (ya escapado como &lt;▲&gt;)
    result = re.sub(
        r'&lt;▲&gt;\s*',
        r'<span class="k-note">▲ </span>',
        result
    )
    # [⇒word]
    result = re.sub(
        r'\[⇒([^\]]+)\]',
        r'<span class="k-xref">⇒\1</span>',
        result
    )
    # ⇒word sin corchetes
    result = re.sub(
        r'⇒([\w･ぁ-ん゛゜ァ-ヶ一-龯・]+)',
        r'<span class="k-xref">⇒\1</span>',
        result
    )
    # ＝word (equivalencia)
    result = re.sub(
        r'＝([\w･ぁ-ん゛゜ァ-ヶ一-龯・]+)',
        r'<span class="k-xref">＝\1</span>',
        result
    )

    return result


def _render_example(ex: dict) -> str:
    css_class = "k-ex-main" if ex['type'] == 'main' else "k-ex-sub"
    marker = "●" if ex['type'] == 'main' else "・"
    html = f'<div class="{css_class}">'
    if ex['ja']:
        html += f'<span class="k-ex-ja">{marker} {_esc(ex["ja"])}</span>'
    if ex['en']:
        html += f'<span class="k-ex-en">{_esc(ex["en"])}</span>'
    html += '</div>'
    return html


def _render_subentry(roman: str, chunk: str) -> str:
    """Renderiza una sub-entrada completa."""
    parts = []

    # Cabecera
    reading, headword = _parse_header(chunk)
    header = '<div class="k-header">'
    if roman:
        header += f'<span class="k-roman">{_esc(roman)}</span>'
    if reading:
        header += f'<span class="k-reading">{_esc(reading)}</span>'
    if headword:
        header += f'<span class="k-headword">{_esc(headword)}</span>'
    header += '</div>'
    parts.append(header)

    # Eliminar cabecera del chunk para procesar el resto
    soup = BeautifulSoup(chunk, 'html.parser')
    header_tag = soup.find('font', color=lambda c: c and c.lower() in ('firebrick', '#b22222'))
    if header_tag:
        header_tag.decompose()
    remaining = str(soup)

    # ¿Redirect simple?
    clean = _clean(soup.get_text())
    if re.match(r'^[＝=]?⇒', clean):
        parts.append(f'<div class="k-def"><span class="k-xref">{_esc(clean)}</span></div>')
        return '\n'.join(parts)

    # Dividir en acepciones
    senses = _split_senses(remaining)
    all_compounds = []

    sense_parts = []
    for num, sense_chunk in senses:
        parsed = _parse_sense_content(sense_chunk)
        all_compounds.extend(parsed['compounds'])

        if not parsed['def_text'] and not parsed['examples']:
            continue

        sense_html = '<div class="k-sense">'
        if num:
            sense_html += f'<span class="k-num">{_esc(num)}.</span>'

        if parsed['def_text']:
            sense_html += f'<span class="k-def">{_render_def_text(parsed["def_text"])}</span>'

        if parsed['examples']:
            sense_html += '<div class="k-examples">'
            for ex in parsed['examples']:
                sense_html += _render_example(ex)
            sense_html += '</div>'

        sense_html += '</div>'
        sense_parts.append(sense_html)

    parts.extend(sense_parts)

    # Compuestos y expresiones derivadas
    if all_compounds:
        comp_html = '<div class="k-compounds">'
        for c in all_compounds:
            comp_html += '<div class="k-compound">'
            comp_html += f'<span class="k-comp-head">{_esc(c["head"])}</span>'
            if c['text']:
                comp_html += _render_def_text(c['text'])
            comp_html += '</div>'
        comp_html += '</div>'
        parts.append(comp_html)

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
            body_parts.append(f'<div class="k-subentry">{rendered}</div>')

    body = '\n'.join(body_parts) if body_parts else '<p>No definition found.</p>'

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN"'
        ' "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        '<head>\n'
        '  <meta http-equiv="Content-Type" content="text/html; charset=utf-8" />\n'
        f'  <style type="text/css">{CSS}</style>\n'
        '</head>\n'
        '<body>\n'
        f'{body}\n'
        '</body>\n'
        '</html>\n'
    )
