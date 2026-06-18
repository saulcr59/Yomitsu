"""
formatter_base.py — Shared utilities for all Yomitsu dictionary formatters.
"""
from __future__ import annotations
import html as html_lib

CSS = """\
body {
    margin: 0;
    padding: 0.5em 0.7em;
    font-size: 1em;
    line-height: 1.6;
    text-align: left;
}
hr { border: none; border-top: 1px solid #ddd; margin: 0.5em 0; }
"""

_XHTML_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN"'
    ' "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">\n'
    '<html xmlns="http://www.w3.org/1999/xhtml">\n'
    '<head>\n'
    '  <meta http-equiv="Content-Type" content="text/html; charset=utf-8" />\n'
    '  <style type="text/css">{css}</style>\n'
    '</head>\n'
    '<body>\n{body}\n</body>\n</html>\n'
)


def esc(s: str) -> str:
    return html_lib.escape(str(s), quote=False)


def wrap_body(body: str, css: str = CSS) -> str:
    """Wrap a body string in the XHTML 1.1 template."""
    return _XHTML_TEMPLATE.format(css=css, body=body)
