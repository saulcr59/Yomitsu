"""
kindle_formatter.py  —  Jitendex → XHTML limpio para KOReader/MuPDF
====================================================================
En lugar de traducir el árbol structured-content nodo a nodo,
EXTRAE los datos semánticos (definiciones, tags POS, ejemplos)
y genera HTML propio mínimo que MuPDF renderiza sin problemas.

Prioridades de visualización:
  1. Definiciones limpias y legibles
  2. Etiquetas gramaticales (noun, adv…)
  3. Ejemplos de oraciones
  4. Formas alternativas (solo lista simple, sin tabla)
"""

from __future__ import annotations
import html as html_lib
from typing import Union

# ---------------------------------------------------------------------------
# Abreviaciones POS más legibles en pantalla pequeña
# ---------------------------------------------------------------------------
POS_SHORT = {
    "noun": "n.", "verb": "v.", "adverb": "adv.", "adjective": "adj.",
    "expression": "exp.", "pronoun": "pron.", "particle": "part.",
    "suffix": "suf.", "prefix": "pref.", "interjection": "int.",
    "conjunction": "conj.", "counter": "ctr.", "numeric": "num.",
    "auxiliary": "aux.", "to-adverb": "adv-to.", "suru": "suru",
    "na-adjective": "na-adj.", "i-adjective": "i-adj.",
    "no-adjective": "no-adj.", "adjectival noun": "adj-n.",
}

MISC_SKIP = {"kana"}   # misc tags que no aportan en pantalla pequeña

# ---------------------------------------------------------------------------
# Extracción de texto plano de un nodo (para ruby, ejemplos, etc.)
# ---------------------------------------------------------------------------
def _text(node) -> str:
    """Extrae texto plano recursivamente, descartando <rt> (furigana)."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_text(n) for n in node)
    if isinstance(node, dict):
        tag = node.get("tag", "")
        if tag == "rt":
            return ""          # ignorar furigana en texto plano
        return _text(node.get("content", ""))
    return ""


def _esc(s: str) -> str:
    return html_lib.escape(str(s), quote=False)


# ---------------------------------------------------------------------------
# Extracción semántica del structured-content
# ---------------------------------------------------------------------------

def _find_data(node, target_content: str) -> list:
    """Devuelve todos los nodos cuyo data.content == target_content."""
    results = []
    if isinstance(node, list):
        for item in node:
            results.extend(_find_data(item, target_content))
    elif isinstance(node, dict):
        dc = (node.get("data") or {}).get("content", "")
        if dc == target_content:
            results.append(node)
        results.extend(_find_data(node.get("content", []), target_content))
    return results


def _find_data_class(node, target_class: str) -> list:
    results = []
    if isinstance(node, list):
        for item in node:
            results.extend(_find_data_class(item, target_class))
    elif isinstance(node, dict):
        dc = (node.get("data") or {}).get("class", "")
        if dc == target_class:
            results.append(node)
        results.extend(_find_data_class(node.get("content", []), target_class))
    return results


def extract_sense_groups(sc_content) -> list[dict]:
    """
    Devuelve lista de sense-groups, cada uno con:
      - number: str  (①, ②, "" si no tiene)
      - pos_tags: list[str]
      - misc_tags: list[str]
      - glosses: list[str]
      - examples: list[(ja_text, en_text)]
    """
    groups = []

    # sense-groups puede estar en ul[data-content=sense-groups] como li
    # o directamente como div[data-content=sense-group]
    sense_group_nodes = _find_data(sc_content, "sense-group")

    for sg_node in sense_group_nodes:
        style = sg_node.get("style") or {}
        # ej: "\"①\"" → "①"
        number = style.get("listStyleType", "").replace('"', "").replace("'", "").strip()

        content = sg_node.get("content", [])
        if isinstance(content, dict):
            content = [content]

        pos_tags = []
        misc_tags = []
        glosses = []
        examples = []

        for child in (content if isinstance(content, list) else [content]):
            if not isinstance(child, dict):
                continue
            child_data = (child.get("data") or {})
            child_dc = child_data.get("content", "")
            child_class = child_data.get("class", "")

            # Tags POS
            if child_dc == "part-of-speech-info":
                raw = _text(child.get("content", "")).strip()
                pos_tags.append(POS_SHORT.get(raw, raw))

            # Tags misc (kana, archaic, etc.)
            elif child_dc == "misc-info":
                raw = _text(child.get("content", "")).strip()
                if raw and raw not in MISC_SKIP:
                    misc_tags.append(raw)

            # field-info (Buddhism, geology…)
            elif child_dc == "field-info":
                raw = _text(child.get("content", "")).strip()
                if raw:
                    misc_tags.append(raw)

            # sense block → glossary + examples
            elif child_dc == "sense":
                inner = child.get("content", [])
                if isinstance(inner, dict):
                    inner = [inner]

                for sense_child in (inner if isinstance(inner, list) else [inner]):
                    if not isinstance(sense_child, dict):
                        glosses.append(_text(sense_child))
                        continue
                    sc2 = (sense_child.get("data") or {}).get("content", "")
                    sc2_class = (sense_child.get("data") or {}).get("class", "")

                    if sc2 == "glossary":
                        # lista de definiciones
                        gl_content = sense_child.get("content", [])
                        if isinstance(gl_content, dict):
                            gl_content = [gl_content]
                        for gl in (gl_content if isinstance(gl_content, list) else [gl_content]):
                            t = _text(gl).strip()
                            if t:
                                glosses.append(t)

                    elif sc2 == "extra-info":
                        # ejemplos de oraciones
                        ex_nodes = _find_data_class(sense_child, "extra-box")
                        for ex_node in ex_nodes:
                            ex_dc = (ex_node.get("data") or {}).get("content", "")
                            if ex_dc == "example-sentence":
                                ex_a_nodes = _find_data(ex_node, "example-sentence-a")
                                ex_b_nodes = _find_data(ex_node, "example-sentence-b")
                                ja = _text(ex_a_nodes[0].get("content") if ex_a_nodes else "").strip()
                                en = _text(ex_b_nodes[0].get("content") if ex_b_nodes else "").strip()
                                if ja:
                                    examples.append((ja, en))

            # sense directo con ol (alternate format)
            elif child.get("tag") in ("ol", "ul") and not child_dc:
                for li in _find_data(child, "sense"):
                    g_nodes = _find_data(li, "glossary")
                    for gn in g_nodes:
                        gl_content = gn.get("content", [])
                        if isinstance(gl_content, dict):
                            gl_content = [gl_content]
                        for gl in (gl_content if isinstance(gl_content, list) else [gl_content]):
                            t = _text(gl).strip()
                            if t:
                                glosses.append(t)
                    # ejemplos dentro de sense alternativo
                    ex_nodes = _find_data_class(li, "extra-box")
                    for ex_node in ex_nodes:
                        ex_dc = (ex_node.get("data") or {}).get("content", "")
                        if ex_dc == "example-sentence":
                            ex_a_nodes = _find_data(ex_node, "example-sentence-a")
                            ex_b_nodes = _find_data(ex_node, "example-sentence-b")
                            ja = _text(ex_a_nodes[0].get("content") if ex_a_nodes else "").strip()
                            en = _text(ex_b_nodes[0].get("content") if ex_b_nodes else "").strip()
                            if ja:
                                examples.append((ja, en))

        if glosses or pos_tags:
            groups.append({
                "number": number,
                "pos_tags": pos_tags,
                "misc_tags": misc_tags,
                "glosses": glosses,
                "examples": examples,
            })

    return groups


def extract_forms(sc_content) -> list[str]:
    """Extrae formas alternativas como lista de strings simples."""
    forms = []
    forms_nodes = _find_data(sc_content, "forms")
    for fn in forms_nodes:
        # Solo queremos los li dentro de ul simples (no tablas de conjugación)
        ul_nodes = []
        c = fn.get("content", [])
        if isinstance(c, dict):
            c = [c]
        for item in (c if isinstance(c, list) else [c]):
            if isinstance(item, dict) and item.get("tag") == "ul":
                ul_nodes.append(item)
        for ul in ul_nodes:
            ul_c = ul.get("content", [])
            if isinstance(ul_c, dict):
                ul_c = [ul_c]
            for li in (ul_c if isinstance(ul_c, list) else [ul_c]):
                t = _text(li).strip()
                if t:
                    forms.append(t)
    return list(dict.fromkeys(forms))  # deduplicar manteniendo orden


def extract_redirect(sc_content) -> str | None:
    """Para entradas tipo redirect (variantes ortográficas)."""
    nodes = _find_data(sc_content, "redirect-glossary")
    if nodes:
        return _text(nodes[0].get("content", "")).strip()
    return None


# ---------------------------------------------------------------------------
# Generación de HTML limpio
# ---------------------------------------------------------------------------

CSS = """\
body { margin:0; padding:0.3em 0.5em; font-size:1em; line-height:1.4; }
.word-header { font-size:1.1em; margin-bottom:0.4em; }
.reading { color:#555; font-size:0.9em; margin-left:0.3em; }
.sense-block { margin-bottom:0.6em; }
.sense-num { font-weight:bold; margin-right:0.3em; }
.tags { margin-bottom:0.2em; }
.tag { display:inline-block; font-size:0.75em; font-weight:bold;
       padding:0.1em 0.35em; border-radius:0.25em; margin-right:0.3em;
       vertical-align:middle; }
.tag-pos  { background-color:#444; color:#fff; }
.tag-misc { background-color:#6a3; color:#fff; }
.glosses { margin:0 0 0.2em 0.2em; }
.gloss { margin-bottom:0.1em; }
.example { margin:0.3em 0 0.2em 0.2em; padding:0.25em 0.4em;
           border-left:2px solid #999; font-size:0.9em; }
.ex-ja { margin-bottom:0.1em; }
.ex-en { color:#555; }
.forms-block { margin-top:0.4em; font-size:0.85em; color:#555; }
.redirect { font-size:1.3em; margin:0.3em 0; }
hr { border:none; border-top:1px solid #ccc; margin:0.4em 0; }
"""


def _render_entry(sc_content, reading: str) -> str:
    """Genera HTML para una entrada, dado su structured-content."""

    # ¿Es un redirect?
    redir = extract_redirect(sc_content)
    if redir:
        redir_clean = redir.lstrip('⟶').lstrip('→').strip()
        return f'<p class="redirect">&#x27F6; {_esc(redir_clean)}</p>'  

    groups = extract_sense_groups(sc_content)
    forms = extract_forms(sc_content)

    if not groups:
        return ""

    parts = []
    multi = len(groups) > 1

    for g in groups:
        block = ['<div class="sense-block">']

        # Número + tags en la misma línea
        tags_html = ""
        for t in g["pos_tags"]:
            tags_html += f'<span class="tag tag-pos">{_esc(t)}</span>'
        for t in g["misc_tags"]:
            tags_html += f'<span class="tag tag-misc">{_esc(t)}</span>'

        header = ""
        if multi and g["number"]:
            header = f'<span class="sense-num">{_esc(g["number"])}</span>'

        if header or tags_html:
            block.append(f'<div class="tags">{header}{tags_html}</div>')

        # Definiciones
        if g["glosses"]:
            block.append('<div class="glosses">')
            for gl in g["glosses"]:
                block.append(f'<div class="gloss">&#x2022; {_esc(gl)}</div>')
            block.append('</div>')

        # Ejemplos (máx 1 por sense-group para no saturar)
        if g["examples"]:
            ja, en = g["examples"][0]
            block.append('<div class="example">')
            block.append(f'<div class="ex-ja">{_esc(ja)}</div>')
            if en:
                block.append(f'<div class="ex-en">{_esc(en)}</div>')
            block.append('</div>')

        block.append('</div>')
        parts.append("\n".join(block))

    # Formas alternativas (solo si hay y son pocas)
    if forms:
        forms_str = " / ".join(_esc(f) for f in forms[:6])
        parts.append(f'<div class="forms-block">Forms: {forms_str}</div>')

    return "\n<hr/>\n".join(parts)


def format_yomitan_to_html(results: list) -> str:
    """
    Punto de entrada principal.
    results: lista de entradas del diccionario (cada una es entry completa).
    """
    reading = results[0][1] if results else ""

    body_parts = []
    for entry in results:
        sc_list = entry[5]
        if isinstance(sc_list, list):
            for item in sc_list:
                if isinstance(item, dict) and item.get("type") == "structured-content":
                    rendered = _render_entry(item.get("content", []), reading)
                    if rendered:
                        body_parts.append(rendered)
                    break
        elif isinstance(sc_list, dict) and sc_list.get("type") == "structured-content":
            rendered = _render_entry(sc_list.get("content", []), reading)
            if rendered:
                body_parts.append(rendered)

    body = "\n".join(body_parts) if body_parts else "<p>No definition found.</p>"

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
