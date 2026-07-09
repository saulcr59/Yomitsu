local WidgetContainer  = require("ui/widget/container/widgetcontainer")
local ButtonDialog     = require("ui/widget/buttondialog")
local InfoMessage      = require("ui/widget/infomessage")
local InputDialog      = require("ui/widget/inputdialog")
local DictQuickLookup  = require("ui/widget/dictquicklookup")
local UIManager        = require("ui/uimanager")
local ReaderDictionary = require("apps/reader/modules/readerdictionary")
local Device           = require("device")
local json             = require("json")
local logger           = require("logger")
local DataStorage      = require("datastorage")
local Screen = Device.screen

-- KOReader does not auto-load plugin l10n catalogs. _plugin_i18n is
-- populated in Yomitsu:init() where self.path is reliably set.
local _sys_gt = require("gettext")
local _plugin_i18n = {}
local function _(str) return _plugin_i18n[str] or _sys_gt(str) or str end

local Yomitsu = WidgetContainer:extend{ name = "yomitsu", is_doc_only = true }

-- ---------------------------------------------------------------------------
-- Historial de búsquedas (últimas 20, guardadas en JSON)
-- ---------------------------------------------------------------------------
local HISTORY_PATH = DataStorage:getSettingsDir() .. "/yomitsu_history.json"
local HISTORY_MAX  = 20

local function load_history()
    local f = io.open(HISTORY_PATH, "r")
    if not f then return {} end
    local raw = f:read("*a")
    f:close()
    local ok, data = pcall(json.decode, raw)
    return (ok and type(data) == "table") and data or {}
end

local function save_to_history(word, reading)
    local hist = load_history()
    local prev_count = 0
    for i = #hist, 1, -1 do
        if hist[i].word == word then
            prev_count = hist[i].count or 1
            table.remove(hist, i)
        end
    end
    local count = prev_count + 1
    table.insert(hist, 1, {
        word    = word,
        reading = reading or "",
        time    = os.date("%Y-%m-%d %H:%M"),
        count   = count,
    })
    while #hist > HISTORY_MAX do table.remove(hist) end
    local f = io.open(HISTORY_PATH, "w")
    if f then
        f:write(json.encode(hist))
        f:close()
    end
    return count
end

-- LRU cache for dictionary phase-1 results (session-scoped, not persisted)
local _cache      = {}
local _cache_keys = {}
local CACHE_MAX   = 50

-- Page context cache keyed by "cbz_path:page_no" → GPT scene description.
-- Populated in background after first word per page; used for all subsequent lookups.
local _page_ctx_cache  = {}
local _warmed_pages    = {}  -- pages already sent to /warm-page

local function _page_key(scope)
    local ui = scope and scope.ui
    if not ui then return nil end
    local doc = ui.document
    if not doc or not doc.file then return nil end

    -- Prefer MokuroReader's page counter (getCurrentPage() is unreliable on CBZ).
    local pno
    local mr = ui.mokuroreader
    if not mr then
        for _, v in pairs(ui) do
            if type(v) == "table" and type(v.parser) == "table" then mr = v; break end
        end
    end
    -- KOReader tracks current page in ReaderPaging for fixed-layout docs (CBZ, PDF).
    if ui.paging and ui.paging.current_page then
        pno = ui.paging.current_page
    end
    if not pno and ui.view and ui.view.state then
        pno = ui.view.state.page
    end
    if not pno then return nil end
    return doc.file .. ":" .. tostring(pno)
end


-- Returns all OCR text from the current mokuro page as a single string, or "".
-- Data layout (from mokuroreader source):
--   ui.mokuro.mokuro_data.pages[page_no].blocks[i].lines = {"text", ...}
--   ui.mokuro.parser:getPageData(mokuro_data, page_no) handles index variants.
local function _mokuro_page_text(scope)
    local ui = scope and scope.ui
    if not ui then return "" end
    local mr = ui.mokuro
    if not mr or not mr.mokuro_data then return "" end

    local page_no = (ui.paging and ui.paging.current_page)
        or (ui.view and ui.view.state and ui.view.state.page)
        or 1

    local page_data
    if mr.parser and type(mr.parser.getPageData) == "function" then
        local ok, pd = pcall(mr.parser.getPageData, mr.parser, mr.mokuro_data, page_no)
        if ok and pd then page_data = pd end
    end
    if not page_data and mr.mokuro_data.pages then
        page_data = mr.mokuro_data.pages[page_no]
            or mr.mokuro_data.pages[tostring(page_no)]
    end
    if not page_data then return "" end

    local texts = {}
    for _, block in ipairs(page_data.blocks or {}) do
        local parts = {}
        for _, line in ipairs(block.lines or {}) do
            local t = type(line) == "string" and line
                or (type(line) == "table" and (line.text or "")) or ""
            if t ~= "" then parts[#parts+1] = t end
        end
        local bt = (#parts > 0) and table.concat(parts, "")
            or (type(block.text) == "string" and block.text) or ""
        if bt ~= "" then texts[#texts+1] = bt end
    end
    return table.concat(texts, " | ")
end

local function _cache_get(key)
    return _cache[key]
end

local function _cache_set(key, value)
    if _cache[key] then
        for i, k in ipairs(_cache_keys) do
            if k == key then table.remove(_cache_keys, i); break end
        end
    elseif #_cache_keys >= CACHE_MAX then
        local oldest = table.remove(_cache_keys, 1)
        _cache[oldest] = nil
    end
    _cache[key] = value
    _cache_keys[#_cache_keys + 1] = key
end

local function _count_badge(count)
    if not count or count <= 1 then return "" end
    return '  <font color="#aaa"><small>×' .. tostring(count) .. '</small></font>'
end

local _XHTML_HEAD = '<?xml version="1.0" encoding="UTF-8"?>'
    .. '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN"'
    .. ' "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">'
    .. '<html xmlns="http://www.w3.org/1999/xhtml">'
    .. '<head><meta http-equiv="Content-Type" content="text/html; charset=UTF-8"/>'
    .. '<style type="text/css">'
    .. 'body{margin:0;padding:0.5em 0.7em;font-size:1em;line-height:1.7;text-align:left}'
    .. '</style></head><body>'
local _XHTML_TAIL = '</body></html>'

-- ---------------------------------------------------------------------------
-- Grammar reference pages (list-based, no tables, built lazily for i18n)
-- ---------------------------------------------------------------------------
local _REF_HEAD = '<?xml version="1.0" encoding="UTF-8"?>'
    .. '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN"'
    .. ' "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">'
    .. '<html xmlns="http://www.w3.org/1999/xhtml">'
    .. '<head><meta http-equiv="Content-Type" content="text/html; charset=UTF-8"/>'
    .. '<style type="text/css">'
    .. 'body{margin:0;padding:0.3em 0.6em;font-size:0.85em;line-height:1.5;text-align:left}'
    .. 'h2{font-size:1.1em;margin:0.4em 0 0.3em 0;border-bottom:2px solid #777;padding-bottom:0.1em}'
    .. 'h3{font-size:0.95em;margin:0.8em 0 0.2em 0;border-bottom:1px solid #bbb;padding-bottom:0.05em}'
    .. 'p{margin:0.15em 0}'
    .. '</style></head><body>'

-- ── Grammar reference lazy builders (avoid wide tables on narrow screens) ──
local _ref_verbs_cache, _ref_adj_cache, _ref_particles_cache

local function _ref_verbs()
    if _ref_verbs_cache then return _ref_verbs_cache end
    local L = _
    local h = _REF_HEAD
    -- grey helper for romaji
    local function r(t) return '<font color="#888">' .. t .. '</font>' end

    local function vform(jp, romaji, en, use, u_rule, u_changes, u_ex, ru_rule, ru_ex, irr, note)
        h = h .. '<h3>' .. jp .. ' ' .. r(romaji) .. ' — ' .. en .. '</h3>'
        h = h .. '<p style="margin-left:0.5em;color:#444"><i>' .. L("Use") .. ': ' .. use .. '</i></p>'
        h = h .. '<p style="margin-left:0.5em"><b>五段</b> ' .. r('(godan)') .. ': ' .. u_rule .. '</p>'
        if u_changes then h = h .. '<p style="margin-left:1.2em;color:#666">' .. u_changes .. '</p>' end
        h = h .. '<p style="margin-left:1.2em">' .. u_ex .. '</p>'
        h = h .. '<p style="margin-left:0.5em"><b>一段</b> ' .. r('(ichidan)') .. ': ' .. ru_rule .. '</p>'
        h = h .. '<p style="margin-left:1.2em">' .. ru_ex .. '</p>'
        h = h .. '<p style="margin-left:0.5em"><b>' .. L("Irregular") .. ':</b> ' .. irr .. '</p>'
        if note then h = h .. '<p style="margin-left:0.5em;color:#555"><i>' .. note .. '</i></p>' end
        h = h .. '<hr/>'
    end

    h = h .. '<h2>動詞 ' .. r('(dōshi)') .. ' · ' .. L("Verbs") .. '</h2>'

    -- Verb types
    h = h .. '<h3>' .. L("Verb types") .. '</h3>'
    h = h .. '<p><b>五段</b> ' .. r('(godan)') .. ' — ' .. L("ends in: く, ぐ, す, つ, ぬ, ぶ, む, う, or る with a/u/o before") .. '</p>'
    h = h .. '<p style="margin-left:1em;color:#555">書く ' .. r('(kaku)') .. ', 話す ' .. r('(hanasu)') .. ', 飲む ' .. r('(nomu)') .. ', 買う ' .. r('(kau)') .. ', 切る ' .. r('(kiru)') .. '</p>'
    h = h .. '<p><b>一段</b> ' .. r('(ichidan)') .. ' — ' .. L("ends in る with i/e before") .. '</p>'
    h = h .. '<p style="margin-left:1em;color:#555">食べる ' .. r('(taberu)') .. ', 見る ' .. r('(miru)') .. ', 起きる ' .. r('(okiru)') .. ', 教える ' .. r('(oshieru)') .. '</p>'
    h = h .. '<p><b>不規則</b> ' .. r('(fukisoku)') .. ' — ' .. L("only two verbs") .. ': する ' .. r('(suru)') .. ', くる ' .. r('(kuru)') .. '</p>'
    h = h .. '<p style="margin-left:1em;color:#666"><i>⚠ ' .. L("Same sound, different type") .. ': 切る ' .. r('(kiru)') .. ' = 五段 / 着る ' .. r('(kiru)') .. ' = 一段</i></p>'
    h = h .. '<hr/>'

    local function ex(j1, r1, j2, r2) return j1 .. r('('..r1..')') .. '→<b>' .. j2 .. '</b>' .. r('('..r2..')') end

    vform('ない形', '(nai-kei)', L("Negative"),
        L("negate an action: 'do not / does not do X'"),
        L("change final vowel to あ-row + ない"),
        'ku→ka, gu→ga, su→sa, tsu→ta, u→wa, mu→ma, ru→ra',
        ex('書く','kaku','書かない','kakanai') .. '  ' .. ex('買う','kau','買わない','kawanai'),
        L("remove る + ない"),
        ex('食べる','taberu','食べない','tabenai') .. '  ' .. ex('見る','miru','見ない','minai'),
        ex('する','suru','しない','shinai') .. '  ' .. ex('くる','kuru','こない','konai'),
        L("Past negative: ない→なかった") .. ': 書かなかった ' .. r('(kakanakatta)'))

    vform('た形', '(ta-kei)', L("Past tense"),
        L("past actions and events: 'did X / X happened'"),
        L("same pattern as て-form: て→た, で→だ"),
        'ku→ita, gu→ida, su→shita, tsu/u/ru→tta, nu/bu/mu→nda',
        ex('書く','kaku','書いた','kaita') .. '  ' .. ex('飲む','nomu','飲んだ','nonda'),
        L("remove る + た"),
        ex('食べる','taberu','食べた','tabeta') .. '  ' .. ex('見る','miru','見た','mita'),
        ex('する','suru','した','shita') .. '  ' .. ex('くる','kuru','きた','kita'),
        '※ ' .. L("Exception") .. ': 行く ' .. r('(iku)') .. '→行った ' .. r('(itta)'))

    vform('て形', '(te-kei)', L("Te-form"),
        L("connect actions; requests (〜てください); ongoing (〜ている); permission (〜てもいい)"),
        L("ku→ite, gu→ide, su→shite, tsu/u/ru→tte, nu/bu/mu→nde"),
        nil,
        ex('書く','kaku','書いて','kaite') .. '  ' .. ex('買う','kau','買って','katte') .. '  ' .. ex('飲む','nomu','飲んで','nonde'),
        L("remove る + て"),
        ex('食べる','taberu','食べて','tabete') .. '  ' .. ex('見る','miru','見て','mite'),
        ex('する','suru','して','shite') .. '  ' .. ex('くる','kuru','きて','kite'),
        '※ ' .. L("Exception") .. ': 行く ' .. r('(iku)') .. '→行って ' .. r('(itte)'))

    vform('ます形', '(masu-kei)', L("Polite form"),
        L("formal register (teachers, strangers, work). Stem used in: 〜たい, 〜やすい, 〜にくい"),
        L("change to い-row + ます"),
        'ku→ki, gu→gi, su→shi, tsu→chi, mu→mi, ru→ri, u→i',
        ex('書く','kaku','書きます','kakimasu') .. '  ' .. ex('飲む','nomu','飲みます','nomimasu'),
        L("remove る + ます"),
        ex('食べる','taberu','食べます','tabemasu') .. '  ' .. ex('見る','miru','見ます','mimasu'),
        ex('する','suru','します','shimasu') .. '  ' .. ex('くる','kuru','きます','kimasu'),
        nil)

    vform('〜たい', '(~tai)', L("Want to do"),
        L("express desire to do something. Conjugates like an い-adjective"),
        L("ます-stem + たい"),
        nil,
        ex('書く','kaku','書きたい','kakitai') .. '  ' .. ex('飲む','nomu','飲みたい','nomitai'),
        L("remove る + たい"),
        ex('食べる','taberu','食べたい','tabetai') .. '  ' .. ex('見る','miru','見たい','mitai'),
        ex('する','suru','したい','shitai') .. '  ' .. ex('くる','kuru','きたい','kitai'),
        L("Past: たかった  Neg: たくない  Past neg: たくなかった"))

    vform('可能形', '(kanō-kei)', L("Potential"),
        L("can / be able to do X. Object often uses が instead of を"),
        L("change to え-row + る"),
        nil,
        ex('書く','kaku','書ける','kakeru') .. '  ' .. ex('読む','yomu','読める','yomeru') .. '  ' .. ex('買う','kau','買える','kaeru'),
        L("remove る + られる") .. '  ' .. r('(casual: れる — ra-nuki)'),
        ex('食べる','taberu','食べられる','taberareru') .. '  ' .. ex('見る','miru','見られる','mirareru'),
        ex('する','suru','できる','dekiru') .. '  ' .. ex('くる','kuru','こられる','korareru'),
        nil)

    vform('受身形', '(ukemi-kei)', L("Passive"),
        L("(1) passive voice: 'X is done'. (2) nuisance passive (迷惑の受身): something happened to subject"),
        L("negative stem + れる"),
        nil,
        ex('書く','kaku','書かれる','kakareru') .. '  ' .. ex('飲む','nomu','飲まれる','nomareru'),
        L("remove る + られる"),
        ex('食べる','taberu','食べられる','taberareru') .. '  ' .. ex('見る','miru','見られる','mirareru'),
        ex('する','suru','される','sareru') .. '  ' .. ex('くる','kuru','こられる','korareru'),
        L("Example") .. ': 雨に降られた ' .. r('(ame ni furareta)') .. ' = "I got rained on"')

    vform('使役形', '(shieki-kei)', L("Causative"),
        L("make/let someone do X. Which sense depends on context"),
        L("negative stem + せる"),
        nil,
        ex('書く','kaku','書かせる','kakaseru') .. '  ' .. ex('飲む','nomu','飲ませる','nomaseru'),
        L("remove る + させる"),
        ex('食べる','taberu','食べさせる','tabesaseru') .. '  ' .. ex('見る','miru','見させる','misaseru'),
        ex('する','suru','させる','saseru') .. '  ' .. ex('くる','kuru','こさせる','kosaseru'),
        L("Causative-passive (made to do)") .. ': 書かせられる→書かされる ' .. r('(kakasareru)'))

    vform('意向形', '(ikō-kei)', L("Volitional"),
        L("'Let's do X' (invitation) or 'I intend to do X'. Used in 〜ようとする, 〜ようにする"),
        L("change to お-row + う"),
        nil,
        ex('書く','kaku','書こう','kakō') .. '  ' .. ex('飲む','nomu','飲もう','nomō') .. '  ' .. ex('話す','hanasu','話そう','hanasō'),
        L("remove る + よう"),
        ex('食べる','taberu','食べよう','tabeyō') .. '  ' .. ex('見る','miru','見よう','miyō'),
        ex('する','suru','しよう','shiyō') .. '  ' .. ex('くる','kuru','こよう','koyō'),
        nil)

    vform('命令形', '(meirei-kei)', L("Imperative"),
        L("direct commands. Blunt/rude in daily speech. Common in manga, sports, military"),
        L("change to え-row"),
        nil,
        ex('書く','kaku','書け','kake') .. '  ' .. ex('飲む','nomu','飲め','nome') .. '  ' .. ex('話す','hanasu','話せ','hanase'),
        L("remove る + ろ") .. ' ' .. r('(literary: よ)'),
        ex('食べる','taberu','食べろ','tabero') .. '  ' .. ex('見る','miru','見ろ','miro'),
        ex('する','suru','しろ','shiro') .. '  ' .. ex('くる','kuru','こい','koi'),
        L("Polite alternative") .. ': て-' .. L("form") .. ' + ください ' .. r('(kudasai)'))

    h = h .. '<h3>条件形 ' .. r('(jōken-kei)') .. ' — ' .. L("Conditional") .. '</h3>'
    h = h .. '<p style="margin-left:0.5em;color:#444"><i>' .. L("Use") .. ': ' .. L("three forms, each with different nuance") .. '</i></p>'
    h = h .. '<p style="margin-left:0.5em"><b>〜ば</b> — ' .. L("hypothetical: 'If X were to happen, then Y'") .. '</p>'
    h = h .. '<p style="margin-left:1.2em"><b>五段:</b> '  .. L("え-row + ば") .. '  '
        .. ex('書く','kaku','書けば','kakeba') .. '  ' .. ex('飲む','nomu','飲めば','nomeba') .. '</p>'
    h = h .. '<p style="margin-left:1.2em"><b>一段:</b> ' .. L("remove る + れば") .. '  '
        .. ex('食べる','taberu','食べれば','tabereba') .. '  ' .. ex('見る','miru','見れば','mireba') .. '</p>'
    h = h .. '<p style="margin-left:1.2em"><b>' .. L("Irregular") .. ':</b>  '
        .. ex('する','suru','すれば','sureba') .. '  ' .. ex('くる','kuru','くれば','kureba') .. '</p>'
    h = h .. '<p style="margin-left:0.5em"><b>〜たら</b> — ' .. L("concrete/sequential: 'When / after X happens, Y'") .. '</p>'
    h = h .. '<p style="margin-left:1.2em;color:#555">た-' .. L("form") .. ' + ら:  '
        .. '書いたら ' .. r('(kaitara)') .. ',  食べたら ' .. r('(tabetara)') .. ',  したら ' .. r('(shitara)') .. '</p>'
    h = h .. '<p style="margin-left:0.5em"><b>〜と</b> — ' .. L("natural consequence: 'Whenever X, Y always follows' (not for intentions)") .. '</p>'
    h = h .. '<p style="margin-left:1.2em;color:#555">' .. L("dictionary form") .. ' + と:  '
        .. '春になると桜が咲く ' .. r('(haru ni naru to sakura ga saku)') .. '</p>'

    _ref_verbs_cache = h .. _XHTML_TAIL
    return _ref_verbs_cache
end

local function _ref_adj()
    if _ref_adj_cache then return _ref_adj_cache end
    local L = _
    local h = _REF_HEAD
    local function r(t) return '<font color="#888">' .. t .. '</font>' end

    local function row(form, rule, ex, use)
        h = h .. '<p style="margin-left:0.3em"><b>' .. form .. '</b>'
            .. '  <font color="#555">' .. rule .. ' → ' .. ex .. '</font></p>'
        if use then h = h .. '<p style="margin-left:1.2em;color:#444"><i>' .. use .. '</i></p>' end
    end

    h = h .. '<h2>形容詞 ' .. r('(keiyōshi)') .. ' · ' .. L("Adjectives") .. '</h2>'

    h = h .. '<h3>' .. L("Adjective types") .. '</h3>'
    h = h .. '<p><b>い-' .. L("adjectives") .. '</b> — ' .. L("end in い; conjugate by changing い") .. '</p>'
    h = h .. '<p style="margin-left:1em;color:#555">高い ' .. r('(takai)') .. ', 安い ' .. r('(yasui)') .. ', 大きい ' .. r('(ōkii)') .. ', いい ' .. r('(ii)') .. '  →  <b>高い山</b> ' .. r('(takai yama)') .. '</p>'
    h = h .. '<p><b>な-' .. L("adjectives") .. '</b> — ' .. L("add な before noun, だ as predicate") .. '</p>'
    h = h .. '<p style="margin-left:1em;color:#555">静か ' .. r('(shizuka)') .. ', 好き ' .. r('(suki)') .. ', 元気 ' .. r('(genki)') .. '  →  <b>静かな部屋</b> ' .. r('(shizuka na heya)') .. '</p>'
    h = h .. '<p style="margin-left:0.5em;color:#666"><i>⚠ きれい ' .. r('(kirei)') .. ', きらい ' .. r('(kirai)') .. ' ' .. L("look like い-adj but are な-adj") .. '</i></p>'
    h = h .. '<hr/>'

    h = h .. '<h3>い-' .. L("adjectives") .. ' (高い ' .. r('takai') .. ')</h3>'
    row(L("Plain"),           '—',               '高い ' .. r('takai'),         '高い山だ — "it is an expensive mountain"')
    row(L("Negative"),        'い → くない',       '高くない ' .. r('takakunai'), L("not expensive"))
    row(L("Past"),            'い → かった',       '高かった ' .. r('takakatta'), L("was expensive"))
    row(L("Past negative"),   'い → くなかった',   '高くなかった ' .. r('takakunakatta'), L("was not expensive"))
    row(L("Adverb"),          'い → く',          '高く ' .. r('takaku'),        '高く飛ぶ — "fly high"')
    row(L("Te-form"),         'い → くて',         '高くて ' .. r('takakute'),   '高くて買えない — "too expensive to buy"')
    row(L("Conditional -ば"), 'い → ければ',       '高ければ ' .. r('takakereba'), nil)
    row(L("Noun form"),       'い → さ',          '高さ ' .. r('takasa'),        L("height / expensiveness"))
    row(L("Polite"),          'い → いです',       '高いです ' .. r('takai desu'), L("formal register"))
    h = h .. '<p style="margin-left:0.5em;color:#666"><i>⚠ いい/よい ' .. r('(ii/yoi)') .. ' (' .. L("good") .. ') '
        .. L("is irregular") .. ': ' .. L("neg") .. '→よくない ' .. r('(yokunai)') .. ' · ' .. L("past") .. '→よかった ' .. r('(yokatta)') .. ' · '
        .. L("adv") .. '→よく ' .. r('(yoku)') .. ' · ' .. L("cond") .. '→よければ ' .. r('(yokereba)') .. '</i></p>'
    h = h .. '<hr/>'

    h = h .. '<h3>な-' .. L("adjectives") .. ' (静か ' .. r('shizuka') .. ')</h3>'
    row(L("Attributive") .. ' (+ な)',  '+ な',              '静かな ' .. r('shizuka na'),         '静かな場所 — "a quiet place"')
    row(L("Predicative") .. ' (+ だ)',  '+ だ',              '静かだ ' .. r('shizuka da'),         L("it is quiet"))
    row(L("Negative"),                  '+ じゃない',        '静かじゃない ' .. r('shizuka ja nai'), 'ではない ' .. L("is more formal"))
    row(L("Past"),                      '+ だった',          '静かだった ' .. r('shizuka datta'),  L("it was quiet"))
    row(L("Past negative"),             '+ じゃなかった',    '静かじゃなかった ' .. r('shizuka ja nakatta'), nil)
    row(L("Adverb"),                    '+ に',              '静かに ' .. r('shizuka ni'),         '静かに話す — "speak quietly"')
    row(L("Te-form"),                   '+ で',              '静かで ' .. r('shizuka de'),         '静かで快適だ — "quiet and comfortable"')
    row(L("Conditional"),               '+ なら(ば)',        '静かなら ' .. r('shizuka nara'),     L("if it is quiet"))
    row(L("Noun form"),                 '+ さ',              '静かさ ' .. r('shizukasa'),          L("quietness"))
    row(L("Polite"),                    '+ です',            '静かです ' .. r('shizuka desu'),     L("formal register"))
    h = h .. '<hr/>'

    h = h .. '<h3>' .. L("Comparison") .. '</h3>'
    h = h .. '<p><b>' .. L("More (comparative)") .. ':</b>  AはBより＋adj</p>'
    h = h .. '<p style="margin-left:1.2em;color:#555">東京は大阪より大きい ' .. r('(Tōkyō wa Ōsaka yori ōkii)') .. '</p>'
    h = h .. '<p><b>' .. L("Most (superlative)") .. ':</b>  〜の中で一番＋adj</p>'
    h = h .. '<p style="margin-left:1.2em;color:#555">クラスの中で一番高い ' .. r('(kurasu no naka de ichiban takai)') .. '</p>'
    h = h .. '<p><b>' .. L("As … as") .. ':</b>  AはBと同じくらい＋adj</p>'
    h = h .. '<p style="margin-left:1.2em;color:#555">猫は犬と同じくらい可愛い ' .. r('(neko wa inu to onaji kurai kawaii)') .. '</p>'
    h = h .. '<p><b>' .. L("Not as … as") .. ':</b>  AはBほど＋adj+くない</p>'
    h = h .. '<p style="margin-left:1.2em;color:#555">東京は大阪ほど安くない ' .. r('(Tōkyō wa Ōsaka hodo yasukunai)') .. '</p>'
    h = h .. '<hr/>'

    h = h .. '<h3>' .. L("Becoming / making") .. ' (なる/する)</h3>'
    h = h .. '<p><b>い-adj: く + なる</b> — ' .. L("become [adj]") .. '</p>'
    h = h .. '<p style="margin-left:1.2em;color:#555">寒くなる ' .. r('(samuku naru)') .. ' — "become cold"</p>'
    h = h .. '<p><b>な-adj: に + なる</b> — ' .. L("become [adj]") .. '</p>'
    h = h .. '<p style="margin-left:1.2em;color:#555">元気になる ' .. r('(genki ni naru)') .. ' — "become healthy"</p>'
    h = h .. '<p><b>い-adj: く + する</b> — ' .. L("make [adj]") .. '</p>'
    h = h .. '<p style="margin-left:1.2em;color:#555">部屋を暖かくする ' .. r('(heya wo atatakaku suru)') .. '</p>'
    h = h .. '<p><b>な-adj: に + する</b> — ' .. L("make [adj]") .. '</p>'
    h = h .. '<p style="margin-left:1.2em;color:#555">部屋を静かにする ' .. r('(heya wo shizuka ni suru)') .. '</p>'

    _ref_adj_cache = h .. _XHTML_TAIL
    return _ref_adj_cache
end

local function _ref_particles()
    if _ref_particles_cache then return _ref_particles_cache end
    local L = _
    local h = _REF_HEAD
    local function r(t) return '<font color="#888">' .. t .. '</font>' end

    local function particle(p, romaji, func, ex, ex_r)
        h = h .. '<p><b>' .. p .. '</b> ' .. r(romaji) .. '  —  ' .. func .. '</p>'
        h = h .. '<p style="margin-left:1.2em;color:#555">' .. ex .. ' ' .. r(ex_r) .. '</p>'
    end

    local function pattern(pat, meaning, ex, ex_r)
        h = h .. '<p><b>' .. pat .. '</b>  —  ' .. meaning .. '</p>'
        h = h .. '<p style="margin-left:1.2em;color:#555">' .. ex .. ' ' .. r(ex_r) .. '</p>'
    end

    h = h .. '<h2>助詞・文法 ' .. r('(joshi/bunpō)') .. ' · ' .. L("Particles") .. ' &amp; ' .. L("Key Grammar") .. '</h2>'
    h = h .. '<h3>' .. L("Core particles") .. '</h3>'

    particle('は', '(wa)', L("Topic marker — what the sentence is about; contrasts with others"),          '私は学生だ', '(watashi wa gakusei da)')
    particle('が', '(ga)', L("Subject marker — who/what acts; emphasis; subordinate clauses"),            '猫が好きだ・彼が来た', '(neko ga suki da / kare ga kita)')
    particle('を', '(wo)', L("Direct object — receiver of the action"),                                   'りんごを食べる', '(ringo wo taberu)')
    particle('に', '(ni)', L("Direction / destination / time / location / indirect object"),               '学校に行く・3時に起きる', '(gakkō ni iku / sanji ni okiru)')
    particle('で', '(de)', L("Location of action; means/tool; reason (formal)"),                          '図書館で読む・バスで行く', '(toshokan de yomu / basu de iku)')
    particle('へ', '(e)',  L("Direction — softer/more literary than に"),                                  '東京へ行く', '(Tōkyō e iku)')
    particle('の', '(no)', L("Possession; noun modifier ('of'); nominalizer"),                             '私の本・行くのが好き', '(watashi no hon / iku no ga suki)')
    particle('と', '(to)', L("And (exhaustive); accompaniment (with); quotation"),                        '猫と犬・「行く」と言った', '(neko to inu / "iku" to itta)')
    particle('か', '(ka)', L("Question marker; or (between options)"),                                    '行くか？・AかB', '(iku ka?)')
    particle('も', '(mo)', L("Also / too / even; replaces は/が/を"),                                     '私も行く・何もない', '(watashi mo iku / nani mo nai)')
    particle('だけ', '(dake)', L("Only, just, nothing more than"),                                         '一つだけ・これだけ', '(hitotsu dake / kore dake)')
    particle('しか', '(shika)', L("Nothing but / only — always with negative verb"),                       '一つしかない', '(hitotsu shika nai)')
    particle('から', '(kara)', L("From (origin/time); because/since (cause)"),                             '駅から歩く・寒いから行かない', '(eki kara aruku / samui kara ikanai)')
    particle('まで', '(made)', L("Until / up to (time or place)"),                                         '5時まで・駅まで歩く', '(goji made / eki made aruku)')
    particle('より', '(yori)', L("Than (comparison); from (formal/literary)"),                             'AよりBが好き', '(A yori B ga suki)')
    particle('ので', '(node)', L("Because/since — soft, objective. More polite than から"),               '雨なので行かない', '(ame na node ikanai)')
    particle('のに', '(noni)', L("Even though / despite — surprise or disappointment"),                   '頑張ったのに負けた', '(ganbatta noni maketa)')
    h = h .. '<hr/>'

    h = h .. '<h3>' .. L("Key sentence patterns") .. '</h3>'
    pattern('〜ている',          L("ongoing action / resultant state"),              '食べている・結婚している', '(tabete iru / kekkon shite iru)')
    pattern('〜ていた',          L("was doing / had done (at that time)"),           '寝ていた', '(nete ita)')
    pattern('〜てみる',          L("try doing (to see what happens)"),               '食べてみる', '(tabete miru)')
    pattern('〜てしまう',        L("end up doing / done completely (often regret)"), '忘れてしまった', '(wasurete shimatta)')
    pattern('〜てもいい',        L("permission: it's ok to do"),                     '行ってもいい', '(itte mo ii)')
    pattern('〜てはいけない',    L("prohibition: must not do"),                      '入ってはいけない', '(haitte wa ikenai)')
    pattern('〜なければならない', L("obligation: must do"),                           '行かなければならない', '(ikanakereba naranai)')
    pattern('〜なくてもいい',    L("don't have to do"),                              '来なくてもいい', '(konakute mo ii)')
    pattern('〜かもしれない',    L("might / perhaps / possibility"),                 '雨かもしれない', '(ame kamo shirenai)')
    pattern('〜と思う',          L("I think that…"),                                 '行くと思う', '(iku to omou)')
    pattern('〜ようとする',      L("try to do (make attempt)"),                      '逃げようとする', '(nigeyō to suru)')
    pattern('〜ようにする',      L("make effort to / try to habitually"),            '早く寝るようにする', '(hayaku neru yō ni suru)')
    pattern('〜ことができる',    L("can / be able to do"),                           '泳ぐことができる', '(oyogu koto ga dekiru)')
    pattern('〜たことがある',    L("have (ever) done (experience)"),                 '食べたことがある', '(tabeta koto ga aru)')
    pattern('〜ながら',          L("while doing (simultaneous actions)"),            '音楽を聴きながら勉強する', '(ongaku wo kikinagara benkyō suru)')
    pattern('〜ばかり',          L("just did / doing nothing but"),                  '来たばかり・食べてばかりいる', '(kita bakari / tabete bakari iru)')
    pattern('〜はずだ',          L("should be / expected to be"),                    '彼は来るはずだ', '(kare wa kuru hazu da)')
    pattern('〜そうだ',          L("looks like it will / I heard that"),             '雨が降りそうだ', '(ame ga furisō da)')
    pattern('〜らしい',          L("seems like / apparently (evidence-based)"),      '彼は忙しいらしい', '(kare wa isogashii rashii)')
    pattern('〜わけだ',          L("that explains it / that means (logical conclusion)"), 'だからそうなるわけだ', '(dakara sō naru wake da)')

    _ref_particles_cache = h .. _XHTML_TAIL
    return _ref_particles_cache
end

local function _html_esc(s)
    return s:gsub("&", "&amp;"):gsub("<", "&lt;"):gsub(">", "&gt;")
end

local function _build_kanji_html(kanji_list)
    if not kanji_list or #kanji_list == 0 then return "" end
    local lines = {}
    for _, k in ipairs(kanji_list) do
        local readings = {}
        local meanings = {}
        local seen_m = {}
        for _, r in ipairs(k.readings or {}) do
            if r.reading and r.reading ~= "" then
                readings[#readings+1] = r.reading
            end
            for _, g in ipairs(r.glosses or {}) do
                if not seen_m[g] and #meanings < 4 then
                    seen_m[g] = true
                    meanings[#meanings+1] = g
                end
            end
        end
        local reading_str = table.concat(readings, "・")
        local meaning_str = table.concat(meanings, ", ")
        lines[#lines+1] = string.format(
            '<p style="margin:0.1em 0 0.1em 0.2em">'
            .. '<b>%s</b>  <font color="#555">%s</font>'
            .. '  <font color="#888"><small>%s</small></font></p>',
            _html_esc(k.char or ""),
            _html_esc(reading_str),
            _html_esc(meaning_str)
        )
    end
    if #lines == 0 then return "" end
    return '<hr/>\n' .. table.concat(lines, "\n") .. "\n"
end

local function _grammar_lines(text)
    if not text or text == "" then return "" end
    local parts = {}
    local first = true
    for line in (text .. "\n"):gmatch("([^\n]*)\n") do
        line = line:match("^%s*(.-)%s*$")
        if line == "" then
            parts[#parts+1] = '<p style="margin:0.2em 0"> </p>'
        elseif line:match("^[A-ZÁÉÍÓÚÑ%s]+:") then
            local top = first and "0.1em" or "0.6em"
            first = false
            parts[#parts+1] = string.format(
                '<p style="margin:%s 0 0.1em 0"><b>%s</b></p>', top, _html_esc(line))
        elseif line:match("^%- ") or line:match("^• ") then
            local content = line:match("^[%-%•]%s+(.*)")
            content = content or line
            local is_star = content:match("^★") ~= nil
            local head, tail = content:match("^(.-)%s*—%s*(.*)")
            local indent = is_star and "0.5em" or "0.8em"
            local border = is_star and "border-left:4px solid #000; padding-left:0.4em; " or ""
            if head and tail then
                parts[#parts+1] = string.format(
                    '<p style="margin:0.3em 0 0.05em %s; %s"><b>%s</b></p>',
                    indent, border, _html_esc(head))
                parts[#parts+1] = string.format(
                    '<p style="margin:0 0 0.3em %s; %s"><font color="#555">%s</font></p>',
                    indent, border, _html_esc(tail))
            else
                parts[#parts+1] = string.format(
                    '<p style="margin:0.2em 0 0.2em %s; %s"><b>%s</b></p>',
                    indent, border, _html_esc(content))
            end
        else
            parts[#parts+1] = '<p style="margin:0.15em 0">' .. _html_esc(line) .. '</p>'
        end
    end
    return table.concat(parts, "\n")
end

local function buildHistoryHtml(current_word)
    local hist = load_history()
    if #hist == 0 then
        return _XHTML_HEAD .. '<p>' .. _("No history yet.") .. '</p>' .. _XHTML_TAIL
    end
    local lines = {}
    for _, entry in ipairs(hist) do
        local reading = (entry.reading and entry.reading ~= "") and
            (' <font color="gray">(' .. entry.reading .. ')</font>') or ""
        local date_str = entry.time and
            (' <font color="#bbb"><small>' .. entry.time .. '</small></font>') or ""
        local count_str = (entry.count and entry.count > 1) and
            (' <font color="#aaa"><small>×' .. tostring(entry.count) .. '</small></font>') or ""
        if entry.word == current_word then
            lines[#lines+1] = '<p style="margin:0.15em 0; border-left:3px solid #000; padding-left:0.4em"><b>'
                .. entry.word .. '</b>' .. reading .. count_str .. date_str .. '</p>'
        else
            lines[#lines+1] = '<p style="margin:0.15em 0"><b>'
                .. entry.word .. '</b>' .. reading .. count_str .. date_str .. '</p>'
        end
    end
    return _XHTML_HEAD .. table.concat(lines, "\n") .. _XHTML_TAIL
end

-- ---------------------------------------------------------------------------
-- Dictionary definitions and feature settings
-- ---------------------------------------------------------------------------
local DICTS = {
    { key = "jitendex",  label = "Jitendex" },
    { key = "kenkyusha", label = "研究社 (Kenkyusha)" },
    { key = "wisdom",    label = "ウィズダム (Wisdom)" },
    { key = "genius",    label = "ジーニアス (Genius)" },
    { key = "dojg",      label = "文法 (DOJG Grammar)" },
}
local DICT_DEFAULT_ORDER = { "jitendex", "kenkyusha", "wisdom", "genius", "dojg" }
local DICT_VIEWER_NAMES = {
    jitendex  = "Jitendex",
    kenkyusha = "研究社 (Kenkyusha)",
    wisdom    = "ウィズダム (Wisdom)",
    genius    = "ジーニアス (Genius)",
    dojg      = "文法 (DOJG Grammar)",
}

local function _cfg_bool(key, default)
    local v = G_reader_settings:readSetting(key)
    if v == nil then return default end
    return v
end

local function _dict_order()
    return G_reader_settings:readSetting("yomitsu_dict_order") or DICT_DEFAULT_ORDER
end

local function _dict_disabled_set()
    local list = G_reader_settings:readSetting("yomitsu_dict_disabled") or {}
    local s = {}
    for _, k in ipairs(list) do s[k] = true end
    return s
end

-- All Lua traffic goes to port 8002 only — the orchestrator proxies internally.
-- These are overwritten at init() from G_reader_settings if the user has saved values.
local _ORCH_HOST      = "192.168.0.120"  -- stored home server
local _ORCH_PORT      = 8002
local _ORCH_HOST_AWAY = ""               -- stored away server
local _ORCH_PORT_AWAY = 8002
local _ORCH_USE_AWAY  = false
local _ACTIVE_HOST    = _ORCH_HOST       -- currently active server (home or away)
local _ACTIVE_PORT    = _ORCH_PORT
local _DICT_PATH      = "/analyze-dict"

local function _parse_host_port(s)
    s = (s or ""):match("^%s*(.-)%s*$"):gsub("^https?://", ""):gsub("/$", "")
    local host, port = s:match("^(.+):(%d+)$")
    if host and port then return host, tonumber(port) end
    return s ~= "" and s or nil, nil
end

-- Sets the active connection target without touching the stored home/away values.
local function _apply_server(host, port)
    _ACTIVE_HOST = host; _ACTIVE_PORT = port
end
local TIMEOUT_SECS = 20

local original_onLookupWord = ReaderDictionary.onLookupWord
local original_lookup       = ReaderDictionary.lookup

-- Cancel / anti-duplicate state
local _search_id    = 0
local _active_box   = nil
local _last_word    = nil
local _prev_word    = nil
local _last_word_t  = 0
local DEBOUNCE_SECS = 3   -- seconds to ignore the same word after showing a result

-- Frequency tier labels (module-level so buildLoadingHtml and buildAiHtml both use them)
local function _freq_label_jpdb(n)
    if n <= 1500  then return _("very common")
    elseif n <= 5000  then return _("common")
    elseif n <= 10000 then return _("uncommon")
    else return _("rare") end
end
local function _freq_label_bccwj(n)
    if n <= 3000  then return _("very common")
    elseif n <= 8000  then return _("common")
    elseif n <= 15000 then return _("uncommon")
    else return _("rare") end
end

local function hasJapanese(text)
    if not text then return false end
    return text:match("[\227-\233]") ~= nil
end

-- Strip invalid UTF-8 bytes and control chars that break JSON encoding.
-- Validates each byte sequence; keeps only well-formed UTF-8, tabs, newlines.
local function sanitize(text)
    if not text then return "" end
    local out = {}
    local i = 1
    local n = #text
    while i <= n do
        local b = text:byte(i)
        if b < 0x20 then
            -- Control char: keep only tab(9), LF(10), CR(13)
            if b == 9 or b == 10 or b == 13 then out[#out+1] = text:sub(i,i) end
            i = i + 1
        elseif b == 0x7F then
            i = i + 1  -- DEL, skip
        elseif b < 0x80 then
            out[#out+1] = text:sub(i,i)  -- plain ASCII
            i = i + 1
        elseif b >= 0xC2 and b <= 0xDF then
            -- 2-byte sequence
            if i+1 <= n then
                local b2 = text:byte(i+1)
                if b2 >= 0x80 and b2 <= 0xBF then
                    out[#out+1] = text:sub(i,i+1); i = i+2
                else i = i+1 end
            else i = i+1 end
        elseif b >= 0xE0 and b <= 0xEF then
            -- 3-byte sequence
            if i+2 <= n then
                local b2,b3 = text:byte(i+1), text:byte(i+2)
                if b2 >= 0x80 and b2 <= 0xBF and b3 >= 0x80 and b3 <= 0xBF then
                    out[#out+1] = text:sub(i,i+2); i = i+3
                else i = i+1 end
            else i = i+1 end
        elseif b >= 0xF0 and b <= 0xF4 then
            -- 4-byte sequence
            if i+3 <= n then
                local b2,b3,b4 = text:byte(i+1),text:byte(i+2),text:byte(i+3)
                if b2 >= 0x80 and b2 <= 0xBF and b3 >= 0x80 and b3 <= 0xBF
                   and b4 >= 0x80 and b4 <= 0xBF then
                    out[#out+1] = text:sub(i,i+3); i = i+4
                else i = i+1 end
            else i = i+1 end
        else
            i = i+1  -- invalid start byte (0x80-0xC1, 0xF5-0xFF), skip
        end
    end
    return table.concat(out)
end

-- Flatten any structure returned by KOReader text APIs into a plain string.
local function flatten_text(v)
    if type(v) == "string" then return v end
    if type(v) ~= "table"  then return nil end
    if type(v.text) == "string" and #v.text > 0 then return v.text end

    local parts = {}
    local function collect(t)
        for _, item in ipairs(t) do
            if type(item) == "string" then
                parts[#parts+1] = item
            elseif type(item) == "table" then
                local s = item.text or item.t or item.s or item.str or item.value or item.word or ""
                if type(s) == "string" and #s > 0 then parts[#parts+1] = s end
                if item.spans    then collect(item.spans)    end
                if item.segments then collect(item.segments) end
                if item.lines    then collect(item.lines)    end
            end
        end
    end
    collect(v)
    return #parts > 0 and table.concat(parts, "") or nil
end

-- Returns context string and word_offset (0-based char position of word in context).
-- word_offset lets Python find the exact Sudachi token even when the same character
-- appears multiple times on the page (e.g. 日 in both 今日 and 本日).
local function get_sentence_context(scope, word, extra_args)
    -- Sub-lookup from inside DictQuickLookup: no real context available.
    -- Return the word itself so SudachiPy tokenizes only that, not the old sentence.
    if scope.lookupword ~= nil then
        return word, 0
    end

    local doc = scope.ui and scope.ui.document
    local hl  = scope.ui and scope.ui.highlight

    -- Build a context window [word_abs-200 .. word_abs+300] and return the
    -- 0-based offset of word within that window.
    local function make_window(full_text, word_abs_pos_1based)
        local cs = math.max(1, word_abs_pos_1based - 200)
        local ce = math.min(#full_text, word_abs_pos_1based + 300)
        local ctx = full_text:sub(cs, ce)
        return ctx, word_abs_pos_1based - cs   -- 0-based offset
    end

    -- Scan text for best occurrence of word, given optional estimated position.
    local function best_occurrence(s, estimated_pos)
        local best_pos, best_dist = nil, math.huge
        local i = 1
        while true do
            local found = s:find(word, i, true)
            if not found then break end
            local ref = estimated_pos or math.floor(#s / 2)
            local dist = math.abs(found - ref)
            if dist < best_dist then best_dist = dist; best_pos = found end
            i = found + 1
        end
        return best_pos
    end

    -- Collect what we can from extra_args WITHOUT returning early —
    -- we need all available data before choosing the best path.
    local word_box_y = nil   -- screen Y of tapped word (from Geom)
    local xptr0, xptr1 = nil, nil

    -- Diagnostic: log what extra_args actually contains so we can detect
    -- which KOReader data format is in use.
    for i, v in ipairs(extra_args or {}) do
        local vt = type(v)
        if vt == "table" then
            logger.info(string.format(
                "[YOMITSU] extra_args[%d]: table has_x=%s has_pos0=%s has_pboxes=%s len=%s",
                i,
                tostring(type(v.x) == "number"),
                tostring(type(v.pos0) == "string"),
                tostring(type(v.pboxes) == "table"),
                tostring(#v)))
        else
            logger.info(string.format("[YOMITSU] extra_args[%d]: %s", i, vt))
        end
    end

    local function extract_geom_y(t)
        -- Direct Geom: {x=N, y=N, w=N, h=N}
        if type(t.y) == "number" and type(t.x) == "number" then
            return t.y
        end
        -- Array of Geoms: {{x=N,y=N,...}, ...}  (KOReader pboxes / select_boxes)
        if type(t[1]) == "table" and type(t[1].y) == "number" then
            return t[1].y
        end
        -- Named pboxes field: {pboxes = {{x,y,...}, ...}}
        if type(t.pboxes) == "table" and type(t.pboxes[1]) == "table"
           and type(t.pboxes[1].y) == "number" then
            return t.pboxes[1].y
        end
        return nil
    end

    for _, v in ipairs(extra_args or {}) do
        if type(v) == "table" then
            -- Screen position (try all known Geom structures)
            if not word_box_y then
                word_box_y = extract_geom_y(v)
                if word_box_y then
                    logger.info("[YOMITSU] word_box_y=", word_box_y)
                end
            end
            -- Inline text shortcut
            local t = (type(v.text) == "string" and v.text)
                   or (type(v.word) == "string" and v.word) or ""
            if t == _prev_word and t ~= word then t = "" end
            if #t > #word then return t, nil end
            -- mokuroreader: prev_context / next_context as separate fields
            local prev = type(v.prev_context) == "string" and v.prev_context or ""
            local nxt  = type(v.next_context) == "string" and v.next_context or ""
            if prev ~= "" or nxt ~= "" then
                local full = prev .. text .. nxt
                if #full > #text then return full, #prev end
            end
            -- XPointer positions
            if type(v.pos0) == "string" and not xptr0 then
                xptr0 = v.pos0
                xptr1 = type(v.pos1) == "string" and v.pos1 or v.pos0
                logger.info("[YOMITSU] XPointers from extra_args")
            end
        end
    end

    -- Also check scope.selected_word (set by ReaderDictionary before calling us)
    local sw = scope.selected_word
    if type(sw) == "table" then
        if not word_box_y then
            word_box_y = extract_geom_y(sw)
            if word_box_y then logger.info("[YOMITSU] word_box_y from selected_word=", word_box_y) end
        end
        if not xptr0 and type(sw.pos0) == "string" then
            xptr0 = sw.pos0
            xptr1 = type(sw.pos1) == "string" and sw.pos1 or sw.pos0
            logger.info("[YOMITSU] XPointers from selected_word")
        end
    end

    if hl then
        for _, sel in ipairs({hl.selected_text, hl.selected_word}) do
            if type(sel) == "table" then
                -- Standard fields
                local t = (type(sel.text)     == "string" and sel.text)
                       or (type(sel.word)     == "string" and sel.word)
                       or (type(sel.context)  == "string" and sel.context)
                       or (type(sel.sentence) == "string" and sel.sentence) or ""
                -- Skip stale data from the previous lookup (sub-lookup from popup).
                if t == _prev_word and t ~= word then t = "" end
                if #t > #word then return t, nil end
                -- mokuroreader passes prev_context / next_context separately
                local prev = type(sel.prev_context) == "string" and sel.prev_context or ""
                local next = type(sel.next_context) == "string" and sel.next_context or ""
                if prev ~= "" or next ~= "" then
                    local full = prev .. word .. next
                    if #full > #word then return full, #prev end
                end
                if type(sel.pos0) == "string" and not xptr0 then
                    xptr0 = sel.pos0
                    xptr1 = type(sel.pos1) == "string" and sel.pos1 or sel.pos0
                end
            end
        end
        -- mokuroreader injects getSelectedWordContext as a closure with
        -- prev_context/next_context captured — must be called, not read as fields.
        if type(hl.getSelectedWordContext) == "function" then
            local ok, prev, nxt = pcall(hl.getSelectedWordContext, hl, 20)
            if ok then
                prev = prev or ""
                nxt  = nxt  or ""
                if prev ~= "" or nxt ~= "" then
                    local full = prev .. word .. nxt
                    logger.info("[YOMITSU] Contexto de mokuro:", full)
                    return full, #prev
                end
            end
        end
    end

    -- PATH A — XPointer with exact offset (best accuracy).
    -- Strategy: get element text with range [xp0_start → xp1_end], then get
    -- pre-word text with range [xp0_start → xptr0_exact]. Its length is the
    -- exact char offset of the word in the full text.
    if doc and doc.getTextFromXPointers and xptr0 then
        local xp_start = xptr0:gsub("%.%d+$", ".0")
        local xp_end   = xptr1:gsub("%.%d+$", ".9999")

        local ok_full, raw_full = pcall(doc.getTextFromXPointers, doc, xp_start, xp_end)
        if ok_full and raw_full then
            local full = flatten_text(raw_full) or (type(raw_full) == "string" and raw_full) or ""
            if #full > 0 then
                -- Try to compute exact offset using pre-word text
                local word_abs = nil
                local ok_pre, raw_pre = pcall(doc.getTextFromXPointers, doc, xp_start, xptr0)
                if ok_pre and raw_pre then
                    local pre = flatten_text(raw_pre) or (type(raw_pre) == "string" and raw_pre) or ""
                    -- Confirm the word is exactly at this position
                    if full:sub(#pre + 1, #pre + #word) == word then
                        word_abs = #pre + 1
                        logger.info("[YOMITSU] XPointer offset exacto: pre=", #pre, " word_abs=", word_abs)
                    end
                end
                -- If exact method failed, fall back to best-occurrence within element text
                if not word_abs then
                    word_abs = best_occurrence(full, nil)
                end
                if word_abs then
                    local ctx, off = make_window(full, word_abs)
                    logger.info("[YOMITSU] PATH A: full=", #full, " ctx=", #ctx, " offset=", off)
                    return ctx, off
                end
            end
        end
    end

    -- PATH B — getTextFromPositions + screen-Y estimation (fallback).
    if doc and doc.getTextFromPositions then
        local sw = Screen:getWidth()
        local sh = Screen:getHeight()
        local ok, raw = pcall(doc.getTextFromPositions, doc,
            {x = 0, y = 0}, {x = sw, y = sh}, true)
        if ok and raw then
            local s = flatten_text(raw)
            if type(s) == "string" then
                local estimated = (word_box_y and sh > 0) and
                    math.floor((word_box_y / sh) * #s) or nil
                local best_pos = best_occurrence(s, estimated)
                if best_pos then
                    local ctx, off = make_window(s, best_pos)
                    logger.info("[YOMITSU] PATH B: page=", #s, " estimated=", estimated,
                        " best=", best_pos, " offset=", off)
                    return ctx, off
                end
            end
        end
    end

    return word, nil
end

-- Non-blocking HTTP POST. Calls on_done(http_code, body) on success or on_done(nil, nil) on error/cancel.
local function async_post_to(host, port, path, payload, my_id, timeout_secs, on_done)
    local socket_lib = require("socket")
    local tcp = socket_lib.tcp()
    tcp:settimeout(0.5)
    local ok, conn_err = tcp:connect(host, port)
    if not ok then
        logger.warn("[YOMITSU] No se pudo conectar a", host .. ":" .. port, conn_err)
        on_done(nil, nil)
        return
    end
    tcp:settimeout(0)

    local request = string.format(
        "POST %s HTTP/1.0\r\nHost: %s:%d\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s",
        path, host, port, #payload, payload
    )
    local sent     = 0
    local chunks   = {}
    local deadline = os.time() + timeout_secs
    local done     = false

    local function finish(code, body)
        if done then return end
        done = true
        pcall(function() tcp:close() end)
        on_done(code, body)
    end

    local function step()
        if done then return end
        if my_id ~= nil and my_id ~= _search_id then finish(nil, nil); return end
        if os.time() > deadline then finish(nil, nil); return end
        if sent < #request then
            local n, e, m = tcp:send(request:sub(sent + 1))
            if n then sent = sent + n
            elseif e == "timeout" then sent = sent + (m or 0)
            else finish(nil, nil); return end
            UIManager:scheduleIn(0.03, step)
            return
        end
        local data, e, partial = tcp:receive(8192)
        if data then
            chunks[#chunks+1] = data
            UIManager:scheduleIn(0.03, step)
        elseif e == "closed" then
            if partial and #partial > 0 then chunks[#chunks+1] = partial end
            local response = table.concat(chunks)
            local code = tonumber(response:match("^HTTP/%S+ (%d+)"))
            local body  = response:match("\r\n\r\n(.*)")
            finish(code, body)
        elseif partial and #partial > 0 then
            chunks[#chunks+1] = partial
            UIManager:scheduleIn(0.03, step)
        else
            UIManager:scheduleIn(0.03, step)
        end
    end
    UIManager:scheduleIn(0.02, step)
end

-- Minimal HTTP/1.1 chunked-encoding decoder.
-- Extracts all complete chunks from buf; returns (decoded_text, remaining_buf).
local function unchunk(buf)
    local out = {}
    while true do
        local cr = buf:find("\r\n", 1, true)
        if not cr then break end
        local hex = buf:sub(1, cr - 1):match("^%s*([0-9a-fA-F]+)")
        if not hex then break end
        local sz = tonumber(hex, 16)
        if not sz then break end
        if sz == 0 then buf = ""; break end          -- terminal chunk
        local ds = cr + 2
        local de = ds + sz - 1
        if #buf < de + 2 then break end             -- incomplete, wait for more
        out[#out+1] = buf:sub(ds, de)
        buf = buf:sub(de + 3)                        -- skip trailing \r\n
    end
    return table.concat(out), buf
end

-- Extracts \x01value\x01\n metadata header from a streaming response buffer.
-- Returns (value, rest), ("", buf) if no marker, (false, nil) if header incomplete.
local function extract_meta(buf)
    if #buf == 0 or buf:byte(1) ~= 1 then return "", buf end
    local close = buf:find("\1", 2, true)
    if not close then return false, nil end
    local val   = buf:sub(2, close - 1)
    local after = close + 1
    if buf:byte(after) == 10 then after = after + 1 end
    return val, buf:sub(after)
end

-- Non-blocking streaming HTTP POST (HTTP/1.1 + chunked decoding).
-- on_chunk(text) is called for each decoded piece of body data as it arrives.
-- on_done(success) is called when the stream ends or fails.
local function async_stream_post(host, port, path, payload, my_id, timeout_secs, on_chunk, on_done)
    local socket_lib = require("socket")
    local tcp = socket_lib.tcp()
    tcp:settimeout(0.5)
    local ok, conn_err = tcp:connect(host, port)
    if not ok then
        logger.warn("[YOMITSU] No se pudo conectar (stream) a", host .. ":" .. port, conn_err)
        on_done(false)
        return
    end
    tcp:settimeout(0)

    local request = string.format(
        "POST %s HTTP/1.1\r\nHost: %s:%d\r\nContent-Type: application/json\r\n"
        .. "Content-Length: %d\r\nConnection: close\r\n\r\n%s",
        path, host, port, #payload, payload
    )
    local sent       = 0
    local hdr_buf    = ""
    local hdrs_done  = false
    local chunked    = false
    local chunk_buf  = ""
    local http_ok    = false
    local deadline   = os.time() + timeout_secs
    local done       = false

    local function finish(success)
        if done then return end
        done = true
        pcall(function() tcp:close() end)
        on_done(success)
    end

    local function step()
        if done then return end
        if my_id ~= _search_id then finish(false); return end
        if os.time() > deadline  then finish(false); return end

        if sent < #request then
            local n, e, m = tcp:send(request:sub(sent + 1))
            if n then sent = sent + n
            elseif e == "timeout" then sent = sent + (m or 0)
            else finish(false); return end
            UIManager:scheduleIn(0.1, step)
            return
        end

        local data, e, partial = tcp:receive(4096)
        local raw = data or partial

        if raw and #raw > 0 then
            if not hdrs_done then
                hdr_buf = hdr_buf .. raw
                local hend = hdr_buf:find("\r\n\r\n", 1, true)
                if hend then
                    local status = hdr_buf:match("^HTTP/%S+ (%d+)")
                    http_ok  = (status == "200")
                    chunked  = hdr_buf:lower():find("transfer%-encoding:%s*chunked") ~= nil
                    hdrs_done = true
                    local body = hdr_buf:sub(hend + 4)
                    if http_ok and #body > 0 then
                        if chunked then
                            local decoded
                            decoded, chunk_buf = unchunk(body)
                            if #decoded > 0 then on_chunk(decoded) end
                        else
                            on_chunk(body)
                        end
                    end
                end
            elseif http_ok then
                if chunked then
                    local decoded
                    chunk_buf = chunk_buf .. raw
                    decoded, chunk_buf = unchunk(chunk_buf)
                    if #decoded > 0 then on_chunk(decoded) end
                else
                    on_chunk(raw)
                end
            end
        end

        if e == "closed" then
            finish(http_ok)
        else
            UIManager:scheduleIn(0.1, step)
        end
    end
    UIManager:scheduleIn(0.05, step)
end

local function _build_freq_str(frequency)
    local freq_parts = {}
    if frequency and frequency.jpdb then
        freq_parts[#freq_parts+1] = "<b>JPDB</b> #" .. tostring(frequency.jpdb)
            .. " <i>" .. _freq_label_jpdb(frequency.jpdb) .. "</i>"
    end
    if frequency and frequency.bccwj then
        freq_parts[#freq_parts+1] = "<b>BCCWJ</b> #" .. tostring(frequency.bccwj)
            .. " <i>" .. _freq_label_bccwj(frequency.bccwj) .. "</i>"
    end
    return #freq_parts > 0 and
        ('  <font color="gray"><small>' .. table.concat(freq_parts, " · ") .. "</small></font>") or ""
end

local function buildLoadingHtml(word, reading, frequency, count, kanji)
    local reading_str = (reading and reading ~= "") and
        (" <i>(" .. _html_esc(reading) .. ")</i>") or ""
    local body = '<p><b>' .. _html_esc(word) .. '</b>' .. reading_str .. _build_freq_str(frequency) .. _count_badge(count) .. '</p>\n'
        .. _build_kanji_html(kanji)
        .. '<hr/>\n'
        .. '<p style="border-left:3px solid #aaa; padding-left:0.5em; margin:0.4em 0">'
        .. '<font color="gray">' .. _("Translating and analyzing...") .. '</font></p>'
    return _XHTML_HEAD .. body .. _XHTML_TAIL
end

-- Intermediate HTML: shows translation while grammar is still loading.
-- opts.hide_grammar = true → skip the "Generando desglose..." footer
local function buildTranslationHtml(word, reading, ai, original_word, frequency, count, kanji, opts)
    opts = opts or {}
    local reading_str = (reading and reading ~= "") and
        (" <i>(" .. _html_esc(reading) .. ")</i>") or ""
    local freq_str = _build_freq_str(frequency)

    local source = ai.source_sentence or ""
    local translation = (ai.translation_and_nuance or _("No analysis available"))
        :gsub("^%s+", ""):gsub("%s+$", "")
    translation = _html_esc(translation):gsub("\n", "<br/>")

    local source_html = ""
    if source ~= "" then
        local esc = _html_esc(source)
        source_html = esc
        for _, t in ipairs({ word or "", original_word or "" }) do
            if t ~= "" then
                local et = _html_esc(t)
                local i = esc:find(et, 1, true)
                if i then
                    source_html = esc:sub(1, i-1) .. "【" .. et .. "】" .. esc:sub(i + #et)
                    break
                end
            end
        end
    end

    local block = {}
    if source_html ~= "" then
        block[#block+1] =
            '<p style="border-left:3px solid #aaa; padding-left:0.5em; margin:0.3em 0 0.1em 0">'
            .. source_html .. '</p>'
    end
    block[#block+1] =
        '<p style="border-left:3px solid #aaa; padding-left:0.5em; margin:0.1em 0 0.4em 0">'
        .. translation .. '</p>'
    if not opts.hide_grammar then
        block[#block+1] = '<hr/>'
        block[#block+1] =
            '<p style="border-left:2px solid #ccc; padding-left:0.5em; margin:0.3em 0">'
            .. '<font color="#aaa"><i>' .. _("Generating romaji and breakdown...") .. '</i></font></p>'
    end

    local body = '<p><b>' .. _html_esc(word) .. '</b>' .. reading_str .. freq_str .. _count_badge(count) .. '</p>\n'
        .. _build_kanji_html(kanji)
        .. '<hr/>\n'
        .. table.concat(block, "\n") .. "\n"
        .. '<p style="margin-top:0.3em"><font color="gray"><small>'
        .. _html_esc(ai.model_used or _("unknown"))
        .. '</small></font></p>'

    return _XHTML_HEAD .. body .. _XHTML_TAIL
end

-- opts.hide_translation = true → skip Japanese source + Spanish translation block
-- opts.hide_grammar     = true → skip romaji + grammar analysis block
local function buildAiHtml(word, reading, ai, grammar, romaji_sentence, original_word, frequency, count, kanji, opts)
    opts = opts or {}
    local reading_str = (reading and reading ~= "") and
        (" <i>(" .. _html_esc(reading) .. ")</i>") or ""
    local freq_str = _build_freq_str(frequency)
    local romaji = (not opts.hide_grammar) and (romaji_sentence or "") or ""

    local body = '<p><b>' .. _html_esc(word) .. '</b>' .. reading_str .. freq_str .. _count_badge(count) .. '</p>\n'
        .. _build_kanji_html(kanji)
        .. '<hr/>\n'

    -- Translation block
    if not opts.hide_translation then
        local source = ai.source_sentence or ""
        local translation = (ai.translation_and_nuance or _("No analysis available"))
            :gsub("^%s+", ""):gsub("%s+$", "")
        translation = _html_esc(translation):gsub("\n", "<br/>")

        local source_html = ""
        if source ~= "" then
            local esc = _html_esc(source)
            source_html = esc
            for _, t in ipairs({ word or "", original_word or "" }) do
                if t ~= "" then
                    local et = _html_esc(t)
                    local i = esc:find(et, 1, true)
                    if i then
                        source_html = esc:sub(1, i-1) .. "【" .. et .. "】" .. esc:sub(i + #et)
                        break
                    end
                end
            end
        end

        local block = {}
        if source_html ~= "" then
            block[#block+1] =
                '<p style="border-left:3px solid #aaa; padding-left:0.5em; margin:0.3em 0 0.1em 0">'
                .. source_html .. '</p>'
        end
        block[#block+1] =
            '<p style="border-left:3px solid #aaa; padding-left:0.5em; margin:0.1em 0 0.4em 0">'
            .. translation .. '</p>'
        body = body .. table.concat(block, "\n") .. "\n"

        body = body
            .. '<p style="margin-top:0.3em"><font color="gray"><small>'
            .. _html_esc(ai.model_used or _("unknown"))
            .. '</small></font></p>'
    end

    -- Grammar block
    if not opts.hide_grammar then
        if romaji ~= "" then
            body = body .. '<hr/>\n'
                .. '<p style="border-left:2px solid #ccc; padding-left:0.5em; margin:0.3em 0 0.3em 0">'
                .. '<i><font color="#666">' .. _html_esc(romaji) .. '</font></i></p>\n'
        end
        local gram_text = grammar and grammar.analysis or ""
        if gram_text ~= "" then
            local gram_model = (grammar and grammar.model) or ""
            local gram_footer = gram_model ~= "" and
                ('\n<p style="margin-top:0.3em"><font color="gray"><small>'
                .. _html_esc(gram_model) .. '</small></font></p>') or ""
            body = body .. '<hr/>\n' .. _grammar_lines(gram_text) .. gram_footer .. "\n"
        end
    end

    return _XHTML_HEAD .. body .. _XHTML_TAIL
end

local function yomitsuInterceptor(scope, text, ...)
    logger.info("[YOMITSU] Hook capturado:", text)
    local extra_args = {...}

    local doc      = scope.ui and scope.ui.doc
    local doc_lang = (doc and doc:getMeta("language") or "unknown"):lower()
    local is_japanese = doc_lang:match("^ja") or doc_lang:match("^jp") or hasJapanese(text)

    if not is_japanese then
        if original_onLookupWord then
            return original_onLookupWord(scope, text, unpack(extra_args))
        else
            return original_lookup(scope, text, unpack(extra_args))
        end
    end

    -- Anti-duplicado. El timer se reinicia también al MOSTRAR el resultado (abajo),
    -- así un toque en zona vacía justo después de cerrar la ventana se descarta.
    local now = os.time()
    if text == _last_word and (now - _last_word_t) < DEBOUNCE_SECS then
        logger.info("[YOMITSU] Saltando duplicado:", text)
        return true
    end
    _prev_word   = _last_word
    _last_word   = text
    _last_word_t = now

    -- Cada búsqueda lleva un ID. Las closures de async_post_to/async_stream_post comprueban
    -- my_id ~= _search_id antes de cada paso; si el usuario ya inició otra búsqueda,
    -- el paso en vuelo cierra el socket y sale sin mostrar nada.
    _search_id = _search_id + 1
    local my_id = _search_id

    if _active_box then
        UIManager:close(_active_box)
        _active_box = nil
    end

    local closing_by_code = false
    local anim_done   = false
    local _anim_step  = 0
    local _anim_texts = {
        _("Yomitsu: Analyzing."),
        _("Yomitsu: Analyzing.."),
        _("Yomitsu: Analyzing..."),
    }

    local function make_loading_box(txt)
        local box = InfoMessage:new{ text = txt }
        local _orig = box.onCloseWidget
        box.onCloseWidget = function(self)
            if not closing_by_code and my_id == _search_id then
                _search_id = _search_id + 1
            end
            if _active_box == self then _active_box = nil end
            if _orig then return _orig(self) end
        end
        return box
    end

    local function animate_loading()
        if my_id ~= _search_id or anim_done then return end
        _anim_step = (_anim_step % 3) + 1
        closing_by_code = true   -- evitar que onCloseWidget cancele la búsqueda
        if _active_box then UIManager:close(_active_box) end
        closing_by_code = false  -- restaurar para que el usuario pueda cancelar tocando
        local box = make_loading_box(_anim_texts[_anim_step])
        _active_box = box
        UIManager:show(box)
        UIManager:scheduleIn(0.7, animate_loading)
    end

    -- Extraer contexto de forma síncrona (solo consulta DOM, no red).
    local raw_ctx, word_offset = get_sentence_context(scope, text, extra_args)
    local context   = sanitize(raw_ctx or text)
    local safe_word = sanitize(text)
    logger.info("[YOMITSU] Contexto:", context)

    -- ── Shared: build viewer and fire AI streams from parsed dict data ──────────
    local function after_dict(word, reading, frequency, original_word, pos,
                              jitendex, kenkyusha, wisdom, genius, dojg, kanji)
        -- Close loading animation (nil-safe: no-op when called from cache hit path)
        anim_done = true
        closing_by_code = true
        if _active_box then UIManager:close(_active_box); _active_box = nil end
        closing_by_code = false

        -- Read feature flags (read once per lookup for consistency)
        local show_ia    = _cfg_bool("yomitsu_show_ia", true)
        local show_trans = _cfg_bool("yomitsu_show_translation", true)
        local show_gram  = _cfg_bool("yomitsu_show_grammar", true)
        local d_order    = _dict_order()
        local d_disabled = _dict_disabled_set()

        -- Lookup data indexed by key for ordered insertion
        local dict_data = {
            jitendex  = jitendex,
            kenkyusha = kenkyusha,
            wisdom    = wisdom,
            genius    = genius,
            dojg      = dojg,
        }

        local lookup_count = save_to_history(word, reading)

        -- Build result list
        local results = {}

        -- Yomitsu IA tab (position 1, updated in-place by streams)
        if show_ia then
            table.insert(results, {
                dict       = _("Yomitsu AI"),
                word       = word,
                definition = buildLoadingHtml(word, reading, frequency, lookup_count, kanji),
                is_html    = true,
            })
        end

        -- Dictionaries in user-configured order, skipping disabled ones
        local dict_count = 0
        for _, key in ipairs(d_order) do
            if not d_disabled[key] then
                local d = dict_data[key]
                if d and d.found and (d.html_content or "") ~= "" then
                    table.insert(results, {
                        dict       = DICT_VIEWER_NAMES[key] or key,
                        word       = word,
                        definition = d.html_content,
                        is_html    = true,
                    })
                    dict_count = dict_count + 1
                end
            end
        end

        if not show_ia and dict_count == 0 then
            table.insert(results, {
                dict       = _("Dictionary"),
                word       = word,
                definition = "<p>" .. _("No definition found.") .. "</p>",
                is_html    = true,
            })
        end

        table.insert(results, {
            dict       = _("History"),
            word       = word,
            definition = buildHistoryHtml(word),
            is_html    = true,
        })

        table.insert(results, {
            dict       = _("Verbs"),
            word       = word,
            definition = _ref_verbs(),
            is_html    = true,
        })
        table.insert(results, {
            dict       = _("Adjectives"),
            word       = word,
            definition = _ref_adj(),
            is_html    = true,
        })
        table.insert(results, {
            dict       = _("Particles"),
            word       = word,
            definition = _ref_particles(),
            is_html    = true,
        })

        _last_word   = word
        _last_word_t = os.time()

        local sw = Screen:getWidth()
        local sh = Screen:getHeight()
        local viewer = DictQuickLookup:new{
            ui         = scope.ui,
            lookupword = word,
            is_html    = true,
            results    = results,
            width      = sw - 20,
            height     = sh,
        }
        local current_viewer = viewer

        -- Hooking onCloseWidget lets us cancel in-flight phase-2 requests
        -- when the user manually closes the popup (prevents ghost popups).
        local function hook_close(v)
            local orig = v.onCloseWidget
            v.onCloseWidget = function(self)
                if not closing_by_code then
                    _search_id   = _search_id + 1
                    current_viewer = nil
                end
                if orig then return orig(self) end
            end
        end

        hook_close(viewer)
        UIManager:show(viewer)

        -- Cache raw OCR text per page (synchronous — available on first word too).
        local cur_page_key = _page_key(scope)
        if cur_page_key and not _page_ctx_cache[cur_page_key] then
            local ok_ocr, ocr_text = pcall(_mokuro_page_text, scope)
            if ok_ocr and type(ocr_text) == "string" and ocr_text ~= "" then
                _page_ctx_cache[cur_page_key] = ocr_text
                logger.info("[YOMITSU] OCR página cacheado: " .. #ocr_text .. " chars")
            end
        end
        local page_ctx = (cur_page_key and _page_ctx_cache[cur_page_key]) or ""

        -- On first lookup per page, warm the dict cache for all other words.
        -- Use Mokuro OCR text if available, otherwise the sentence context.
        if cur_page_key and not _warmed_pages[cur_page_key] then
            local warm_text = (page_ctx ~= "" and page_ctx) or context
            if warm_text and #warm_text > 1 then
                _warmed_pages[cur_page_key] = true
                async_post_to(_ACTIVE_HOST, _ACTIVE_PORT, "/warm-page",
                    json.encode({ text = warm_text }), nil, 30, function() end)
            end
        end

        -- Shared payload for both streaming requests
        local ok_ai, ai_payload = pcall(json.encode, {
            context_phrase = context,
            target_word    = word,
            original_word  = original_word,
            part_of_speech = pos,
            page_context   = page_ctx,
        })
        if not ok_ai or not ai_payload then return end

        -- In-place update via KOReader's own changeDictionary — no close/reopen.
        -- Silent if user is on a different tab; visible when they navigate back.
        local function update_yomitsu_ia(new_html)
            if my_id ~= _search_id then return end
            if not current_viewer then return end
            results[1].definition = new_html
            if current_viewer.dict_index == 1 then
                pcall(function() current_viewer:changeDictionary(1) end)
            end
        end

        -- ── Streaming state ──────────────────────────────────────────────────────
        -- Streaming HTTP is used so the server never needs to buffer the full
        -- response. Display is updated only twice (translation done, grammar done)
        -- to avoid e-ink full-screen refreshes during generation.
        local trans_meta_done, trans_meta_buf = false, ""
        local trans_buf, trans_source, trans_model = "", "", ""

        local gram_meta_done, gram_meta_buf = false, ""
        local gram_buf, gram_romaji, gram_model = "", "", ""
        local gram_done = false  -- true once on_gram_done has rendered the full analysis

        -- ── Translation stream ────────────────────────────────────────────────────
        local function on_trans_chunk(chunk)
            if not trans_meta_done then
                trans_meta_buf = trans_meta_buf .. chunk
                local meta, rest = extract_meta(trans_meta_buf)
                if meta == false then return end
                if meta ~= "" then
                    local ok_m, obj = pcall(json.decode, meta)
                    if ok_m and obj then
                        trans_source = obj.s or ""
                        trans_model  = obj.m or ""
                    end
                end
                trans_meta_done = true
                trans_meta_buf  = ""
                if rest and #rest > 0 then trans_buf = rest end
            else
                trans_buf = trans_buf .. chunk
            end
            -- No display update during streaming: e-ink full-screen refresh every
            -- 350ms blocks interaction. Display once when stream completes.
        end

        local function on_trans_done(success)
            if my_id ~= _search_id then return end
            if trans_buf == "" then trans_buf = _("No translation.") end
            local ai = {
                translation_and_nuance = trans_buf,
                source_sentence        = trans_source,
                model_used             = trans_model ~= "" and trans_model or "Hy-MT2-7B",
            }
            local opts = { hide_grammar = not show_gram }
            if gram_done or not show_gram then
                local grammar = gram_done
                    and { analysis = gram_buf, model = gram_model ~= "" and gram_model or "gpt-4.1-mini" }
                    or nil
                update_yomitsu_ia(buildAiHtml(word, reading, ai, grammar, gram_romaji, original_word, frequency, lookup_count, kanji, opts))
            else
                update_yomitsu_ia(buildTranslationHtml(word, reading, ai, original_word, frequency, lookup_count, kanji, opts))
            end
            logger.info("[YOMITSU] Traducción completada")
        end

        -- ── Grammar stream ────────────────────────────────────────────────────────
        local function on_gram_chunk(chunk)
            if not gram_meta_done then
                gram_meta_buf = gram_meta_buf .. chunk
                local meta, rest = extract_meta(gram_meta_buf)
                if meta == false then return end
                if meta ~= "" then
                    local ok_m, obj = pcall(json.decode, meta)
                    if ok_m and obj then
                        gram_romaji = obj.romaji or ""
                        gram_model  = obj.model  or ""
                    end
                end
                gram_meta_done = true
                gram_meta_buf  = ""
                if rest and #rest > 0 then gram_buf = rest end
            else
                gram_buf = gram_buf .. chunk
            end
        end

        local function on_gram_done(success)
            if my_id ~= _search_id then return end
            if not success and gram_buf == "" then
                gram_buf = _("Analysis unavailable.")
            end
            -- GPT embeds a ROMAJI section in the body; prefer it over the SudachiPy fallback.
            local rom = gram_buf:match("\nROMAJI:%s*\n(.-)%s*$")
                     or gram_buf:match("\nROMAJI:%s*\n(.+)")
            if rom and rom ~= "" then
                gram_romaji = rom:match("^%s*(.-)%s*$") or rom
                gram_buf    = gram_buf:match("^(.-)%s*\nROMAJI:") or gram_buf
            end
            local ai = {
                translation_and_nuance = show_trans and (trans_buf ~= "" and trans_buf or "—") or nil,
                source_sentence        = show_trans and trans_source or nil,
                model_used             = show_trans and (trans_model ~= "" and trans_model or "Hy-MT2-7B") or nil,
            }
            local grammar = { analysis = gram_buf, model = gram_model ~= "" and gram_model or "gpt-4.1-mini" }
            gram_done = true
            local opts = { hide_translation = not show_trans }
            update_yomitsu_ia(buildAiHtml(word, reading, ai, grammar, gram_romaji, original_word, frequency, lookup_count, kanji, opts))
            logger.info("[YOMITSU] Gramática completada")
        end

        -- Fire streams based on settings ─────────────────────────────────────────
        if show_ia then
            if show_trans then
                async_stream_post(_ACTIVE_HOST, _ACTIVE_PORT, "/analyze-translation-stream",
                    ai_payload, my_id, 30, on_trans_chunk, on_trans_done)
            end
            if show_gram then
                async_stream_post(_ACTIVE_HOST, _ACTIVE_PORT, "/analyze-grammar-stream",
                    ai_payload, my_id, 55, on_gram_chunk, on_gram_done)
            end
        end
    end  -- end after_dict

    -- ── Cache check: skip network if we have dict data for this word ────────────
    local cached = _cache_get(safe_word)
    if cached then
        logger.info("[YOMITSU] Cache hit:", safe_word)
        after_dict(cached.word, cached.reading, cached.frequency, cached.original_word, cached.pos,
            cached.jitendex, cached.kenkyusha, cached.wisdom, cached.genius, cached.dojg, cached.kanji)
        return true
    end

    -- ── Cache miss: show loading animation and do network request ───────────────
    _anim_step = 1
    local init_box = make_loading_box(_anim_texts[1])
    _active_box = init_box
    UIManager:show(init_box)
    UIManager:scheduleIn(0.7, animate_loading)

    -- Phase 1 payload → orchestrator /analyze-dict (proxies to dict service)
    local dict_payload_tbl = {
        raw_text    = context,
        target_word = safe_word,
        word_offset = word_offset,
    }
    local ok_enc, dict_payload = pcall(json.encode, dict_payload_tbl)
    if not ok_enc or not dict_payload then
        logger.warn("[YOMITSU] json.encode falló, reintentando con contexto mínimo")
        dict_payload = json.encode({ context_phrase = safe_word, user_selection = safe_word })
    end

    async_post_to(_ACTIVE_HOST, _ACTIVE_PORT, _DICT_PATH, dict_payload, my_id, 10,
        function(code1, body1)
        if my_id ~= _search_id then return end

        -- Parse phase-1 dict response
        local word, reading, frequency, original_word, pos, kanji
        local jitendex, kenkyusha, wisdom, genius, dojg = {}, {}, {}, {}, {}

        if code1 == 200 and body1 then
            local ok1, d1 = pcall(json.decode, body1)
            if ok1 and d1 then
                local wd = d1.word_data or {}
                local dd = d1.dictionary_data or {}
                word          = wd.normalized_word or safe_word
                reading       = dd.reading or ""
                frequency     = dd.frequency or {}
                original_word = wd.original_word or safe_word
                pos           = wd.part_of_speech or "unknown"
                kanji         = dd.kanji_breakdown or {}
                jitendex  = dd.jitendex  or {}
                kenkyusha = dd.kenkyusha or {}
                wisdom    = dd.wisdom    or {}
                genius    = dd.genius    or {}
                dojg      = dd.grammar   or {}
            end
        end
        word          = word or safe_word
        reading       = reading or ""
        frequency     = frequency or {}
        original_word = original_word or safe_word
        pos           = pos or "unknown"
        kanji         = kanji or {}

        -- Server unreachable (nil code = connection refused/timeout) →
        -- hand off to KOReader's built-in dictionary instead of showing an error popup.
        if not code1 then
            logger.warn("[YOMITSU] Servidor no disponible, usando diccionario por defecto")
            anim_done = true
            closing_by_code = true
            if _active_box then UIManager:close(_active_box); _active_box = nil end
            closing_by_code = false
            if original_onLookupWord then
                original_onLookupWord(scope, text, unpack(extra_args))
            elseif original_lookup then
                original_lookup(scope, text, unpack(extra_args))
            end
            return
        end
        if code1 ~= 200 then
            logger.warn("[YOMITSU] Error diccionario HTTP:", tostring(code1))
        end

        -- Cache the parsed result for instant re-lookup this session
        if code1 == 200 then
            _cache_set(safe_word, {
                word = word, reading = reading, frequency = frequency,
                original_word = original_word, pos = pos, kanji = kanji,
                jitendex = jitendex, kenkyusha = kenkyusha,
                wisdom = wisdom, genius = genius, dojg = dojg,
            })
        end

        after_dict(word, reading, frequency, original_word, pos,
            jitendex, kenkyusha, wisdom, genius, dojg, kanji)
    end)

    return true
end

if ReaderDictionary.onLookupWord then
    ReaderDictionary.onLookupWord = yomitsuInterceptor
else
    ReaderDictionary.lookup = yomitsuInterceptor
end

-- ---------------------------------------------------------------------------
-- KOReader plugin lifecycle & menu
-- ---------------------------------------------------------------------------

function Yomitsu:init()
    -- Load plugin translations. self.path is set by the plugin loader and
    -- points to the plugin directory (e.g. .../plugins/yomitsu.koplugin).
    do
        local lang = (_sys_gt.lang
            or G_reader_settings:readSetting("language")
            or "en"):match("^(%a+)") or "en"
        if lang ~= "en" and self.path then
            local po_path = self.path .. "/l10n/" .. lang .. ".po"
            local f = io.open(po_path, "r")
            if f then
                local last_id
                for line in f:lines() do
                    local id = line:match('^msgid "(.*)"$')
                    if id then
                        last_id = id ~= "" and id:gsub('\\"', '"') or nil
                    else
                        local str = line:match('^msgstr "(.*)"$')
                        if str and last_id then
                            local v = str:gsub('\\"', '"')
                            if v ~= "" then _plugin_i18n[last_id] = v end
                            last_id = nil
                        end
                    end
                end
                f:close()
            end
        end
    end

    -- If config.json exists in the plugin directory and its content has changed
    -- since the last load, its values override G_reader_settings. This lets a
    -- new plugin install push updated server addresses without manual UI entry.
    if self.path then
        local cfg_path = self.path .. "/config.json"
        local f = io.open(cfg_path, "r")
        if f then
            local content = f:read("*a")
            f:close()
            if content ~= (G_reader_settings:readSetting("yomitsu_config_content") or "") then
                local ok, cfg = pcall(json.decode, content)
                if ok and type(cfg) == "table" then
                    if type(cfg.server_host) == "string" and cfg.server_host ~= "" then
                        G_reader_settings:saveSetting("yomitsu_server_host", cfg.server_host)
                    end
                    if type(cfg.server_port) == "number" then
                        G_reader_settings:saveSetting("yomitsu_server_port", cfg.server_port)
                    end
                    if type(cfg.server_host_away) == "string" then
                        G_reader_settings:saveSetting("yomitsu_server_host_away", cfg.server_host_away)
                    end
                    if type(cfg.server_port_away) == "number" then
                        G_reader_settings:saveSetting("yomitsu_server_port_away", cfg.server_port_away)
                    end
                    G_reader_settings:saveSetting("yomitsu_config_content", content)
                end
            end
        end
    end

    local h = G_reader_settings:readSetting("yomitsu_server_host")
    local p = G_reader_settings:readSetting("yomitsu_server_port")
    if h and h ~= "" then _ORCH_HOST = h end
    if p then _ORCH_PORT = p end

    local ha = G_reader_settings:readSetting("yomitsu_server_host_away")
    local pa = G_reader_settings:readSetting("yomitsu_server_port_away")
    if ha and ha ~= "" then _ORCH_HOST_AWAY = ha end
    if pa then _ORCH_PORT_AWAY = pa end

    _ORCH_USE_AWAY = G_reader_settings:readSetting("yomitsu_use_away") or false
    if _ORCH_USE_AWAY and _ORCH_HOST_AWAY ~= "" then
        _apply_server(_ORCH_HOST_AWAY, _ORCH_PORT_AWAY)
    else
        _apply_server(_ORCH_HOST, _ORCH_PORT)
    end

    if self.ui and self.ui.menu then
        self.ui.menu:registerToMainMenu(self)
    end
end


function Yomitsu:_showUrlDialog(title, current_host, current_port, hint, on_save, tmi)
    local dialog
    local current = current_host ~= "" and (current_host .. ":" .. tostring(current_port)) or ""
    dialog = InputDialog:new{
        title      = title,
        input      = current,
        input_hint = hint or "192.168.0.120:8002",
        buttons    = {{
            {
                text = _("Cancel"),
                id   = "close",
                callback = function() UIManager:close(dialog) end,
            },
            {
                text             = _("Save"),
                is_enter_default = true,
                callback = function()
                    local host, port = _parse_host_port(dialog:getInputText())
                    if host then
                        on_save(host, port or 8002)
                        if tmi then tmi:updateItems() end
                    end
                    UIManager:close(dialog)
                end,
            },
        }},
    }
    UIManager:show(dialog)
    dialog:onShowKeyboard()
end

function Yomitsu:_testConnection()
    local http = require("socket.http")
    local url  = "http://" .. _ACTIVE_HOST .. ":" .. tostring(_ACTIVE_PORT) .. "/health"
    local t0   = os.time()
    local body, code = http.request(url)
    local elapsed = os.time() - t0
    local msg
    if body and code == 200 then
        msg = string.format(_("Connection OK (%ds)\n%s:%s"), elapsed, _ACTIVE_HOST, tostring(_ACTIVE_PORT))
    else
        msg = string.format(_("No response (%s)\n%s:%s"), tostring(code or "error"), _ACTIVE_HOST, tostring(_ACTIVE_PORT))
    end
    UIManager:show(InfoMessage:new{ text = msg, timeout = 5 })
end

function Yomitsu:addToMainMenu(menu_items)
    local order = require("ui/elements/reader_menu_order")
    if order.tools and order.tools[1] ~= "yomitsu" then
        table.insert(order.tools, 1, "yomitsu")
    end

    local function toggle(key, default, tmi)
        G_reader_settings:saveSetting(key, not _cfg_bool(key, default))
        if tmi then tmi:updateItems() end
    end

    -- Build dict sub-items dynamically so order/enabled state is always fresh
    local function dict_sub_items()
        local cur_order = _dict_order()
        local dis_set   = _dict_disabled_set()
        local items = {}
        for _, key in ipairs(cur_order) do
            local def_label
            for _, d in ipairs(DICTS) do
                if d.key == key then def_label = d.label; break end
            end
            if not def_label then goto continue end
            local k = key
            local lbl = def_label
            items[#items+1] = {
                text_func = function()
                    local o = _dict_order()
                    for i, kk in ipairs(o) do
                        if kk == k then return tostring(i) .. ". " .. lbl end
                    end
                    return lbl
                end,
                checked_func = function()
                    return not _dict_disabled_set()[k]
                end,
                callback = function(tmi)
                    local list = G_reader_settings:readSetting("yomitsu_dict_disabled") or {}
                    local found = false
                    for i, kk in ipairs(list) do
                        if kk == k then table.remove(list, i); found = true; break end
                    end
                    if not found then list[#list+1] = k end
                    G_reader_settings:saveSetting("yomitsu_dict_disabled", list)
                    if tmi then tmi:updateItems() end
                end,
                hold_callback = function(tmi)
                    local function do_move(delta)
                        local o = _dict_order()
                        for i, kk in ipairs(o) do
                            if kk == k then
                                local ni = i + delta
                                if ni >= 1 and ni <= #o then
                                    o[i], o[ni] = o[ni], o[i]
                                    G_reader_settings:saveSetting("yomitsu_dict_order", o)
                                end
                                break
                            end
                        end
                        UIManager:close(dialog)
                        -- Rebuild and replace item_table in-place so updateItems()
                        -- sees the new order without closing the sub-menu.
                        if tmi then
                            local new_items = dict_sub_items()
                            for i = #tmi.item_table, 1, -1 do
                                tmi.item_table[i] = nil
                            end
                            for i, item in ipairs(new_items) do
                                tmi.item_table[i] = item
                            end
                            tmi:updateItems()
                        end
                    end
                    local dialog
                    dialog = ButtonDialog:new{
                        buttons = {
                            {{ text = _("↑ Move up"),  callback = function() do_move(-1) end }},
                            {{ text = _("↓ Move down"), callback = function() do_move( 1) end }},
                            {{ text = _("Close"),       callback = function() UIManager:close(dialog) end }},
                        },
                    }
                    UIManager:show(dialog)
                end,
            }
            ::continue::
        end
        -- Hint line at the bottom (non-interactive, grayed out)
        if #items > 0 then
            items[#items+1] = {
                text = _("Hold a dictionary entry to reorder"),
                enabled_func = function() return false end,
            }
        end
        return items
    end

    function Yomitsu:_showReference()
        local results = {
            {
                dict       = _("Verbs"),
                word       = _("Grammar reference"),
                definition = _ref_verbs(),
                is_html    = true,
            },
            {
                dict       = _("Adjectives"),
                word       = _("Grammar reference"),
                definition = _ref_adj(),
                is_html    = true,
            },
            {
                dict       = _("Particles"),
                word       = _("Grammar reference"),
                definition = _ref_particles(),
                is_html    = true,
            },
        }
        local viewer = DictQuickLookup:new{
            ui         = self.ui,
            lookupword = _("Grammar reference"),
            is_html    = true,
            results    = results,
            width      = Screen:getWidth() - 20,
            height     = Screen:getHeight(),
        }
        UIManager:show(viewer)
    end

    menu_items.yomitsu = {
        text = "Yomitsu",
        sub_item_table = {
            {
                text_func = function()
                    return _("Server: ") .. _ACTIVE_HOST .. ":" .. tostring(_ACTIVE_PORT)
                end,
                keep_menu_open = true,
                callback = function(tmi)
                    self:_showUrlDialog(
                        _("Home server (host:port)"),
                        _ORCH_HOST, _ORCH_PORT,
                        "192.168.0.120:8002",
                        function(host, port)
                            _ORCH_HOST = host; _ORCH_PORT = port
                            G_reader_settings:saveSetting("yomitsu_server_host", host)
                            G_reader_settings:saveSetting("yomitsu_server_port", port)
                            if not _ORCH_USE_AWAY then _apply_server(host, port) end
                        end, tmi)
                end,
            },
            {
                text_func = function()
                    local h = _ORCH_HOST_AWAY ~= "" and _ORCH_HOST_AWAY or _("not configured")
                    return _("Secondary server: ") .. h .. ":" .. tostring(_ORCH_PORT_AWAY)
                end,
                keep_menu_open = true,
                callback = function(tmi)
                    self:_showUrlDialog(
                        _("Away server (host:port)"),
                        _ORCH_HOST_AWAY, _ORCH_PORT_AWAY,
                        "example.ddns.net:8002",
                        function(host, port)
                            _ORCH_HOST_AWAY = host; _ORCH_PORT_AWAY = port
                            G_reader_settings:saveSetting("yomitsu_server_host_away", host)
                            G_reader_settings:saveSetting("yomitsu_server_port_away", port)
                            if _ORCH_USE_AWAY then _apply_server(host, port) end
                        end, tmi)
                end,
            },
            {
                text_func = function()
                    return _("Use secondary server")
                end,
                checked_func = function() return _ORCH_USE_AWAY end,
                callback = function(tmi)
                    _ORCH_USE_AWAY = not _ORCH_USE_AWAY
                    G_reader_settings:saveSetting("yomitsu_use_away", _ORCH_USE_AWAY)
                    if _ORCH_USE_AWAY and _ORCH_HOST_AWAY ~= "" then
                        _apply_server(_ORCH_HOST_AWAY, _ORCH_PORT_AWAY)
                    else
                        _apply_server(_ORCH_HOST, _ORCH_PORT)
                    end
                    if tmi then tmi:updateItems() end
                end,
            },
            {
                text = _("Test connection"),
                callback = function() self:_testConnection() end,
            },
            {
                text = _("Yomitsu AI"),
                checked_func = function() return _cfg_bool("yomitsu_show_ia", true) end,
                callback = function(tmi) toggle("yomitsu_show_ia", true, tmi) end,
            },
            {
                text = _("  └ Translation"),
                checked_func = function() return _cfg_bool("yomitsu_show_translation", true) end,
                enabled_func = function() return _cfg_bool("yomitsu_show_ia", true) end,
                callback = function(tmi) toggle("yomitsu_show_translation", true, tmi) end,
            },
            {
                text = _("  └ Grammar breakdown"),
                checked_func = function() return _cfg_bool("yomitsu_show_grammar", true) end,
                enabled_func = function() return _cfg_bool("yomitsu_show_ia", true) end,
                callback = function(tmi) toggle("yomitsu_show_grammar", true, tmi) end,
            },
            {
                text = _("Grammar reference"),
                callback = function() self:_showReference() end,
            },
            {
                text = _("Dictionaries"),
                sub_item_table_func = dict_sub_items,
            },
        },
    }
end

return Yomitsu
