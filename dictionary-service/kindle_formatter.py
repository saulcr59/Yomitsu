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
from typing import Union
from formatter_base import esc as _esc, wrap_body, CSS

MISC_SKIP = {"kana"}   # misc tags que no aportan en pantalla pequeña

# ---------------------------------------------------------------------------
# Extracción de texto plano de un nodo (para ruby, ejemplos, etc.)
# ---------------------------------------------------------------------------
def _text(node) -> str:
    """Extrae texto plano recursivamente, descartando <rt> (furigana) y footnotes."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_text(n) for n in node)
    if isinstance(node, dict):
        tag = node.get("tag", "")
        if tag == "rt":
            return ""
        dc = (node.get("data") or {}).get("content", "")
        if dc == "attribution-footnote":
            return ""          # ignorar [1], [2], etc. en las traducciones
        return _text(node.get("content", ""))
    return ""


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


def _extract_xref(xref_node) -> tuple[str, str]:
    """Extrae (término, glosario) de un nodo extra-box[content=xref]."""
    term = ""
    glossary = ""
    content = xref_node.get("content", [])
    if isinstance(content, dict):
        content = [content]
    for child in (content if isinstance(content, list) else []):
        if not isinstance(child, dict):
            continue
        dc = (child.get("data") or {}).get("content", "")
        if dc == "xref-content":
            inner = child.get("content", [])
            if isinstance(inner, dict):
                inner = [inner]
            for c in (inner if isinstance(inner, list) else []):
                if isinstance(c, dict) and c.get("tag") == "a":
                    term = _text(c.get("content", "")).strip()
                    break
        elif dc == "xref-glossary":
            glossary = _text(child.get("content", "")).strip()
    return term, glossary


def extract_sense_groups(sc_content) -> list[dict]:
    """
    Devuelve lista de sense-groups, cada uno con:
      - number: str  (①, ②, "" si no tiene)
      - pos_tags: list[str]
      - misc_tags: list[str]
      - glosses: list[str]
      - examples: list[(ja_text, en_text)]
      - xrefs: list[(term, glossary)]
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
        xrefs = []

        for child in (content if isinstance(content, list) else [content]):
            if not isinstance(child, dict):
                continue
            child_data = (child.get("data") or {})
            child_dc = child_data.get("content", "")
            child_class = child_data.get("class", "")

            # Restricción de lectura: span con title "valid only for..." → 〔こんにち only〕
            if child.get("tag") == "span" and not child_dc:
                title = child.get("title", "")
                if "valid only for" in title:
                    restr = _text(child.get("content", "")).strip()
                    if restr:
                        misc_tags.append(restr)

            # Tags POS — usar el label tal cual viene de Jitendex
            elif child_dc == "part-of-speech-info":
                raw = _text(child.get("content", "")).strip()
                pos_tags.append(raw)

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
                            elif ex_dc == "xref":
                                term, gloss = _extract_xref(ex_node)
                                if term:
                                    xrefs.append((term, gloss))

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
                        elif ex_dc == "xref":
                            term, gloss = _extract_xref(ex_node)
                            if term:
                                xrefs.append((term, gloss))

        if glosses or pos_tags:
            groups.append({
                "number": number,
                "pos_tags": pos_tags,
                "misc_tags": misc_tags,
                "glosses": glosses,
                "examples": examples,
                "xrefs": xrefs,
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

def _render_entry(sc_content, reading: str) -> str:
    """Genera HTML para una entrada, dado su structured-content."""

    redir = extract_redirect(sc_content)
    if redir:
        redir_clean = redir.lstrip('⟶').lstrip('→').strip()
        return f'<p style="font-size:1.1em; margin:0.3em 0">&#x27F6; {_esc(redir_clean)}</p>'

    groups = extract_sense_groups(sc_content)
    forms = extract_forms(sc_content)

    if not groups:
        return ""

    parts = []
    multi = len(groups) > 1

    for g in groups:
        # Cabecera POS/misc inline
        sh_inner = ""
        if multi and g["number"]:
            sh_inner += f'<b>{_esc(g["number"])}</b> '
        if g["pos_tags"]:
            sh_inner += f'<i><font color="#555">{_esc(" · ".join(g["pos_tags"]))}</font></i>'
        for mtag in g["misc_tags"]:
            if mtag.startswith("〔"):
                sh_inner += f' <font color="#666">{_esc(mtag)}</font>'
            else:
                sh_inner += f' <font color="#666">({_esc(mtag)})</font>'
        if sh_inner:
            parts.append(f'<p style="margin:0 0 0.2em 0">{sh_inner}</p>')

        # Definiciones
        if g["glosses"]:
            parts.append(
                f'<p style="margin:0.1em 0 0.4em 0.6em">'
                f'{_esc("; ".join(g["glosses"]))}</p>'
            )

        # Ejemplos (máx 2) — dos <p> con border-left igual que Kenkyusha
        for ja, en in g["examples"][:2]:
            parts.append(
                f'<p style="margin:0.5em 0 0 0.6em; padding-left:0.5em; border-left:2px solid #bbb">'
                f'{_esc(ja)}</p>'
            )
            if en:
                parts.append(
                    f'<p style="margin:0 0 0.5em 0.6em; padding-left:0.5em; border-left:2px solid #bbb">'
                    f'<font color="#555">{_esc(en)}</font></p>'
                )

        # Referencias cruzadas
        for term, gloss in g.get("xrefs", []):
            line = f'&#x2192; {_esc(term)}'
            if gloss:
                line += f': {_esc(gloss)}'
            parts.append(f'<p style="color:#666; font-style:italic; margin:0.15em 0 0.1em 0.6em">{line}</p>')

        parts.append('<hr/>')

    # Eliminar último <hr/>
    if parts and parts[-1] == '<hr/>':
        parts.pop()

    # Formas alternativas
    if forms:
        parts.append(
            f'<p><i><font color="#555">alternative forms</font></i></p>'
            f'<p style="color:#666; margin-top:0.1em">'
            f'{_esc(" / ".join(forms[:6]))}</p>'
        )

    return "\n".join(parts)


def format_yomitan_to_html(results: list, word: str = "", reading: str = "") -> str:
    """
    Punto de entrada principal.
    results: lista de entradas del diccionario (cada una es entry completa).
    word: forma kanji para el encabezado con furigana (opcional).
    reading: lectura hiragana para el encabezado con furigana (opcional).
    """
    if not reading:
        reading = results[0][1] if results else ""

    # entry[2] = definition tags (space-separated string). "★" means high-priority/common word.
    is_common = any("★" in (e[2] or "") for e in results if len(e) > 2)

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

    wh_style = (
        'text-align:center; font-size:1.1em; '
        'margin-bottom:0.5em; padding-bottom:0.3em; border-bottom:1px solid #ccc'
    )
    common_badge = ' <font color="#c8a000">&#x2605;</font>' if is_common else ""
    header = ""
    if word and reading:
        header = (
            f'<p style="{wh_style}"><b>{_esc(word)}</b>'
            f' <font color="gray">({_esc(reading)})</font>{common_badge}</p>\n'
        )
    elif word:
        header = f'<p style="{wh_style}"><b>{_esc(word)}</b>{common_badge}</p>\n'

    body = header + ("\n".join(body_parts) if body_parts else "<p>No definition found.</p>")
    return wrap_body(body)
