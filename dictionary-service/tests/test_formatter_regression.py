"""
Regression tests for all Yomitsu dictionary formatters.
Uses synthetic minimal HTML/JSON — no dictionary files required.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from kenkyusha_formatter import format_kenkyusha_to_html
from wisdom_formatter    import format_wisdom_to_html
from genius_formatter    import format_genius_to_html
from grammar_formatter   import format_grammar_to_html
from kindle_formatter    import format_yomitan_to_html


# ---------------------------------------------------------------------------
# Kenkyusha
# ---------------------------------------------------------------------------
KENKYUSHA_SIMPLE = (
    '<font color=Firebrick>ぶじ【無事】</font><br>'
    'safety; peace.<br>'
    '<font color=#151B8D>●家族の無事を祈る pray for the safety of one\'s family</font><br>'
    '<font color=#151B8D>・無事に帰る return safely</font><br>'
)

KENKYUSHA_COMPOUND = (
    '<b>I</b><font color=Firebrick>ほん【本】</font><br>'
    'a book.<br>'
    '<font color=#151B8D>●本を読む read a book</font><br>'
    '<b>II</b><font color=Firebrick>もと【元】</font><br>'
    'origin; source.<br>'
)

def test_kenkyusha_basic_structure():
    html = format_kenkyusha_to_html(KENKYUSHA_SIMPLE)
    assert '<?xml' in html
    assert 'XHTML 1.1' in html
    assert 'ぶじ' in html or '無事' in html

def test_kenkyusha_border_on_main_example():
    html = format_kenkyusha_to_html(KENKYUSHA_SIMPLE)
    assert 'border-left:3px solid #aaa' in html
    assert '家族の無事を祈る' in html

def test_kenkyusha_sub_example_thinner_border():
    html = format_kenkyusha_to_html(KENKYUSHA_SIMPLE)
    assert 'border-left:2px solid #ccc' in html
    assert '無事に帰る' in html

def test_kenkyusha_english_translation_colored():
    html = format_kenkyusha_to_html(KENKYUSHA_SIMPLE)
    assert '#555' in html
    assert 'pray for the safety' in html

def test_kenkyusha_compound_subentries():
    html = format_kenkyusha_to_html(KENKYUSHA_COMPOUND)
    assert '本' in html
    assert '元' in html
    assert 'a book' in html
    assert 'origin' in html

def test_kenkyusha_empty_returns_empty():
    assert format_kenkyusha_to_html("") == ""
    assert format_kenkyusha_to_html("   ") == ""


# ---------------------------------------------------------------------------
# Wisdom
# ---------------------------------------------------------------------------
WISDOM_SAMPLE = (
    '<div class="koumoku">'
    '  <div class="midashi_pri2">'
    '    <span class="titlekana">ぶじ</span>'
    '    <span class="hyouki">無事</span>'
    '    <span class="hinshi">名</span>'
    '  </div>'
    '  <div class="kaisetu">'
    '    <div class="gogi">'
    '      <div class="yakugo_g">'
    '        <span class="yakugo"><span class="gogikubun">安全</span> safety</span>'
    '        <span class="yakugo">peace</span>'
    '      </div>'
    '      <div class="yoorei_g">'
    '        <div class="yoorei">'
    '          <span class="reibun">家族の無事を祈る</span>'
    '          <span class="yakubun">pray for the safety of the family</span>'
    '        </div>'
    '      </div>'
    '    </div>'
    '  </div>'
    '</div>'
)

def test_wisdom_header():
    html = format_wisdom_to_html(WISDOM_SAMPLE)
    assert 'ぶじ' in html
    assert '無事' in html
    assert '名' in html

def test_wisdom_context_label():
    html = format_wisdom_to_html(WISDOM_SAMPLE)
    assert '安全' in html
    assert '#666' in html

def test_wisdom_example_border():
    html = format_wisdom_to_html(WISDOM_SAMPLE)
    assert 'border-left:2px solid #bbb' in html
    assert '家族の無事を祈る' in html
    assert 'pray for the safety' in html

def test_wisdom_empty_returns_empty():
    assert format_wisdom_to_html("") == ""


# ---------------------------------------------------------------------------
# Genius
# ---------------------------------------------------------------------------
GENIUS_SAMPLE = (
    '<div class="item">'
    '  <div class="midashi">'
    '    <span class="titlekana">ぶじ</span>'
    '    <span class="m_hyoki">［無事］</span>'
    '  </div>'
    '  <div class="honbun">'
    '    <div class="mean_eng">'
    '      <span class="shironuki">《安全に》</span>'
    '      <span class="eng">safely</span>'
    '    </div>'
    '    <div class="mean_yorei">'
    '      <span class="scope_exam_jp">彼女は無事に帰宅した</span>'
    '      <span class="scope_exam_en">She came home safely.</span>'
    '    </div>'
    '  </div>'
    '</div>'
)

def test_genius_header():
    html = format_genius_to_html(GENIUS_SAMPLE)
    assert 'ぶじ' in html
    assert '無事' in html

def test_genius_context_label():
    html = format_genius_to_html(GENIUS_SAMPLE)
    assert '安全に' in html
    assert '#666' in html

def test_genius_definition_bold():
    html = format_genius_to_html(GENIUS_SAMPLE)
    assert '<b>safely</b>' in html

def test_genius_example_border():
    html = format_genius_to_html(GENIUS_SAMPLE)
    assert 'border-left:2px solid #bbb' in html
    assert '彼女は無事に帰宅した' in html
    assert 'She came home safely' in html

def test_genius_no_div_item_returns_empty():
    assert format_genius_to_html("<div>no item</div>") == ""


# ---------------------------------------------------------------------------
# Grammar (DOJG)
# ---------------------------------------------------------------------------
GRAMMAR_SAMPLE = (
    '<span class="header">あえて<span class="edition">㊤</span></span>'
    '<span class="pos">Adverb</span>'
    '<span class="meaning">daringly; boldly; daring to do</span>'
    '<span class="examples"><ul>'
    '<li><br/>あえて反対する<br/>Someone dares to disagree.</li>'
    '</ul></span>'
)

def test_grammar_term_and_level():
    html = format_grammar_to_html([GRAMMAR_SAMPLE])
    assert '<b>あえて</b>' in html
    assert 'Basic' in html

def test_grammar_pos():
    html = format_grammar_to_html([GRAMMAR_SAMPLE])
    assert 'Adverb' in html

def test_grammar_meaning():
    html = format_grammar_to_html([GRAMMAR_SAMPLE])
    assert 'daringly' in html

def test_grammar_example_border():
    html = format_grammar_to_html([GRAMMAR_SAMPLE])
    assert 'border-left:2px solid #bbb' in html
    assert 'あえて反対する' in html

def test_grammar_multiple_entries_separated():
    html = format_grammar_to_html([GRAMMAR_SAMPLE, GRAMMAR_SAMPLE])
    assert 'border-top:2px solid #ccc' in html

def test_grammar_empty_list_returns_empty():
    assert format_grammar_to_html([]) == ""


# ---------------------------------------------------------------------------
# Kindle / Jitendex (structured-content JSON)
# ---------------------------------------------------------------------------
JITENDEX_ENTRY = [
    "食べる",
    "たべる",
    "★",
    "v1",
    200,
    [
        {
            "type": "structured-content",
            "content": [
                {
                    "tag": "div",
                    "data": {"content": "sense-group"},
                    "content": [
                        {
                            "tag": "span",
                            "title": "Ichidan verb",
                            "data": {"class": "tag", "code": "v1", "content": "part-of-speech-info"},
                            "content": "1-dan"
                        },
                        {
                            "tag": "div",
                            "data": {"content": "sense"},
                            "content": [
                                {
                                    "tag": "ul",
                                    "data": {"content": "glossary"},
                                    "content": {"tag": "li", "content": "to eat"}
                                }
                            ]
                        }
                    ]
                }
            ]
        }
    ],
    1000001,
    ""
]

def test_kindle_header_with_reading():
    html = format_yomitan_to_html([JITENDEX_ENTRY], word="食べる", reading="たべる")
    assert '食べる' in html
    assert 'たべる' in html

def test_kindle_common_word_badge():
    html = format_yomitan_to_html([JITENDEX_ENTRY], word="食べる", reading="たべる")
    assert '&#x2605;' in html        # ★ Unicode star
    assert 'c8a000' in html          # gold color

def test_kindle_no_badge_for_uncommon():
    uncommon = list(JITENDEX_ENTRY)
    uncommon[2] = "old kanji form"
    html = format_yomitan_to_html([uncommon], word="食傷氣味", reading="しょくしょうぎみ")
    assert '&#x2605;' not in html

def test_kindle_definition_present():
    html = format_yomitan_to_html([JITENDEX_ENTRY], word="食べる", reading="たべる")
    assert 'to eat' in html

def test_kindle_pos_present():
    html = format_yomitan_to_html([JITENDEX_ENTRY], word="食べる", reading="たべる")
    assert '1-dan' in html

def test_kindle_xhtml_structure():
    html = format_yomitan_to_html([JITENDEX_ENTRY], word="食べる", reading="たべる")
    assert '<?xml' in html
    assert 'XHTML 1.1' in html
    assert '</html>' in html
