local WidgetContainer  = require("ui/widget/container/inputcontainer")
local InfoMessage      = require("ui/widget/infomessage")
local DictQuickLookup  = require("ui/widget/dictquicklookup")
local UIManager        = require("ui/uimanager")
local ReaderDictionary = require("apps/reader/modules/readerdictionary")
local Device           = require("device")
local json             = require("json")
local logger           = require("logger")
local DataStorage      = require("datastorage")
local Screen = Device.screen

local Yomitsu = WidgetContainer:extend{ name = "yomitsu" }

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
    for i = #hist, 1, -1 do
        if hist[i].word == word then table.remove(hist, i) end
    end
    table.insert(hist, 1, {
        word    = word,
        reading = reading or "",
        time    = os.date("%Y-%m-%d %H:%M"),
    })
    while #hist > HISTORY_MAX do table.remove(hist) end
    local f = io.open(HISTORY_PATH, "w")
    if f then
        f:write(json.encode(hist))
        f:close()
    end
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

local function _html_esc(s)
    return s:gsub("&", "&amp;"):gsub("<", "&lt;"):gsub(">", "&gt;")
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
        return _XHTML_HEAD .. '<p>Sin historial todavía.</p>' .. _XHTML_TAIL
    end
    local lines = {}
    for _, entry in ipairs(hist) do
        local reading = (entry.reading and entry.reading ~= "") and
            (' <font color="gray">(' .. entry.reading .. ')</font>') or ""
        local date_str = entry.time and
            (' <font color="#bbb"><small>' .. entry.time .. '</small></font>') or ""
        if entry.word == current_word then
            lines[#lines+1] = '<p style="margin:0.15em 0; border-left:3px solid #000; padding-left:0.4em"><b>'
                .. entry.word .. '</b>' .. reading .. date_str .. '</p>'
        else
            lines[#lines+1] = '<p style="margin:0.15em 0"><b>'
                .. entry.word .. '</b>' .. reading .. date_str .. '</p>'
        end
    end
    return _XHTML_HEAD .. table.concat(lines, "\n") .. _XHTML_TAIL
end

local ORCHESTRATOR_URL = "http://192.168.0.120:8002/analyze"
local TIMEOUT_SECS     = 20

-- Parse URL once at load time so async_post doesn't need to repeat it.
local _ORCH_HOST, _ORCH_PORT, _ORCH_PATH
do
    local h, p, pa = ORCHESTRATOR_URL:match("http://([^:/]+):(%d+)(.*)")
    _ORCH_HOST = h  or "192.168.0.120"
    _ORCH_PORT = tonumber(p) or 8002
    _ORCH_PATH = (pa and pa ~= "") and pa or "/analyze"
end

local original_onLookupWord = ReaderDictionary.onLookupWord
local original_lookup       = ReaderDictionary.lookup

-- Cancel / anti-duplicate state
local _search_id    = 0
local _active_box   = nil
local _last_word    = nil
local _last_word_t  = 0
local DEBOUNCE_SECS = 3   -- seconds to ignore the same word after showing a result

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
            if #t > #word then return t, nil end
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
                local t = (type(sel.text)     == "string" and sel.text)
                       or (type(sel.word)     == "string" and sel.word)
                       or (type(sel.context)  == "string" and sel.context)
                       or (type(sel.sentence) == "string" and sel.sentence) or ""
                if #t > #word then return t, nil end
                if type(sel.pos0) == "string" and not xptr0 then
                    xptr0 = sel.pos0
                    xptr1 = type(sel.pos1) == "string" and sel.pos1 or sel.pos0
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

-- Non-blocking HTTP POST. Uses TCP with settimeout(0) + UIManager:scheduleIn so
-- the event loop keeps running between chunks — the UI stays responsive and taps
-- (e.g. cancel) are processed normally while the request is in flight.
-- Calls on_done(http_code, body_string) on success, on_done(nil, nil) on error/cancel.
local function async_post(payload, my_id, on_done)
    local socket_lib = require("socket")
    local tcp = socket_lib.tcp()

    -- Short blocking timeout only for the initial TCP connect (localhost ≈ instant).
    tcp:settimeout(0.5)
    local ok, conn_err = tcp:connect(_ORCH_HOST, _ORCH_PORT)
    if not ok then
        logger.warn("[YOMITSU] No se pudo conectar al orquestador:", conn_err)
        on_done(nil, nil)
        return
    end
    tcp:settimeout(0)  -- switch to non-blocking for all data I/O

    local request = string.format(
        "POST %s HTTP/1.0\r\nHost: %s:%d\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s",
        _ORCH_PATH, _ORCH_HOST, _ORCH_PORT, #payload, payload
    )

    local sent     = 0
    local chunks   = {}
    local deadline = os.time() + TIMEOUT_SECS
    local done     = false

    local function finish(code, body)
        if done then return end
        done = true
        pcall(function() tcp:close() end)
        on_done(code, body)
    end

    local function step()
        if done then return end
        if my_id ~= _search_id then finish(nil, nil); return end  -- cancelled
        if os.time() > deadline  then finish(nil, nil); return end  -- timeout

        -- Send phase: keep sending until the full request is in the socket buffer.
        if sent < #request then
            local n, e, m = tcp:send(request:sub(sent + 1))
            if n then
                sent = sent + n
            elseif e == "timeout" then
                sent = sent + (m or 0)
            else
                finish(nil, nil); return
            end
            UIManager:scheduleIn(0.03, step)
            return
        end

        -- Receive phase: collect response chunks until server closes the connection.
        local data, e, partial = tcp:receive(8192)
        if data then
            chunks[#chunks+1] = data
            UIManager:scheduleIn(0.03, step)
        elseif e == "closed" then
            -- HTTP/1.0: server closes connection = end of response.
            if partial and #partial > 0 then chunks[#chunks+1] = partial end
            local response = table.concat(chunks)
            local code = tonumber(response:match("^HTTP/%S+ (%d+)"))
            local body  = response:match("\r\n\r\n(.*)")
            finish(code, body)
        elseif partial and #partial > 0 then
            chunks[#chunks+1] = partial
            UIManager:scheduleIn(0.03, step)
        else
            UIManager:scheduleIn(0.03, step)  -- timeout on this chunk, keep waiting
        end
    end

    UIManager:scheduleIn(0.02, step)
end

local function buildAiHtml(word, reading, ai, grammar, romaji_sentence, original_word, frequency)
    local reading_str = (reading and reading ~= "") and
        (" <i>(" .. _html_esc(reading) .. ")</i>") or ""

    local function _freq_label_jpdb(n)
        if n <= 1500  then return "muy común"
        elseif n <= 5000  then return "común"
        elseif n <= 10000 then return "poco común"
        else return "raro" end
    end
    local function _freq_label_bccwj(n)
        if n <= 3000  then return "muy común"
        elseif n <= 8000  then return "común"
        elseif n <= 15000 then return "poco común"
        else return "raro" end
    end
    local freq_parts = {}
    if frequency and frequency.jpdb then
        freq_parts[#freq_parts+1] = "<b>JPDB</b> #" .. tostring(frequency.jpdb)
            .. " <i>" .. _freq_label_jpdb(frequency.jpdb) .. "</i>"
    end
    if frequency and frequency.bccwj then
        freq_parts[#freq_parts+1] = "<b>BCCWJ</b> #" .. tostring(frequency.bccwj)
            .. " <i>" .. _freq_label_bccwj(frequency.bccwj) .. "</i>"
    end
    local freq_str = #freq_parts > 0 and
        ('  <font color="gray"><small>' .. table.concat(freq_parts, " · ") .. "</small></font>") or ""

    local source = ai.source_sentence or ""
    local translation = (ai.translation_and_nuance or "Sin análisis disponible")
        :gsub("^%s+", ""):gsub("%s+$", "")
    translation = _html_esc(translation):gsub("\n", "<br/>")
    local romaji = romaji_sentence or ""

    -- Highlight the target word inside the Japanese sentence (literal search, no patterns)
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

    local sentence_lines = {}
    if source_html ~= "" then
        sentence_lines[#sentence_lines+1] =
            '<p style="border-left:3px solid #aaa; padding-left:0.5em; margin:0.3em 0 0.1em 0">'
            .. source_html .. '</p>'
    end
    if romaji ~= "" then
        sentence_lines[#sentence_lines+1] =
            '<p style="border-left:3px solid #aaa; padding-left:0.5em; margin:0.1em 0 0.1em 0">'
            .. '<i>' .. _html_esc(romaji) .. '</i></p>'
    end
    sentence_lines[#sentence_lines+1] =
        '<p style="border-left:3px solid #aaa; padding-left:0.5em; margin:0.1em 0 0.4em 0">'
        .. translation .. '</p>'

    local body = '<p><b>' .. _html_esc(word) .. '</b>' .. reading_str .. freq_str .. '</p>\n'
        .. '<hr/>\n'
        .. table.concat(sentence_lines, "\n") .. "\n"

    local gram_text = grammar and grammar.analysis or ""
    if gram_text ~= "" then
        local gram_model = (grammar and grammar.model) or ""
        local gram_footer = gram_model ~= "" and
            ('\n<p style="margin-top:0.3em"><font color="gray"><small>'
            .. _html_esc(gram_model) .. '</small></font></p>') or ""
        body = body .. '<hr/>\n' .. _grammar_lines(gram_text) .. gram_footer .. "\n"
    end

    body = body
        .. '<p style="margin-top:0.3em"><font color="gray"><small>'
        .. _html_esc(ai.model_used or "desconocido")
        .. '</small></font></p>'

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
    _last_word   = text
    _last_word_t = now

    -- Cada búsqueda lleva un ID. La closure de async_post comprueba my_id ~= _search_id
    -- antes de cada paso; si el usuario ya inició otra búsqueda (o tocó Cancelar),
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
        "Yomitsu: Analizando.",
        "Yomitsu: Analizando..",
        "Yomitsu: Analizando...",
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

    _anim_step = 1
    local init_box = make_loading_box(_anim_texts[1])
    _active_box = init_box
    UIManager:show(init_box)
    UIManager:scheduleIn(0.7, animate_loading)

    -- Extraer contexto de forma síncrona (solo consulta DOM, no red).
    local raw_ctx, word_offset = get_sentence_context(scope, text, extra_args)
    local context   = sanitize(raw_ctx or text)
    local safe_word = sanitize(text)
    logger.info("[YOMITSU] Contexto:", context)

    local payload_tbl = { raw_text = context, target_word = safe_word }
    if word_offset then
        payload_tbl.word_offset = word_offset
    end
    local ok_enc, payload = pcall(json.encode, payload_tbl)
    if not ok_enc or not payload then
        logger.warn("[YOMITSU] json.encode falló, reintentando con contexto mínimo")
        payload = json.encode({ raw_text = safe_word, target_word = safe_word })
    end

    async_post(payload, my_id, function(code, body)
        if my_id ~= _search_id then return end  -- cancelado mientras esperábamos

        -- Cerrar InfoMessage sin activar la cancelación del onCloseWidget
        anim_done = true
        closing_by_code = true
        if _active_box then
            UIManager:close(_active_box)
            _active_box = nil
        end

        if code == 200 and body then
            logger.info("[YOMITSU] Respuesta 200 OK")

            local res       = json.decode(body)
            local word      = res.word_normalized or text
            local reading   = res.reading or ""
            local ai        = res.ai_contextual_analysis or {}
            local grammar         = res.grammar_analysis or {}
            local romaji_sentence = res.romaji_sentence or ""
            local frequency       = res.frequency or {}
            logger.info("[YOMITSU] grammar.analysis len=",
                tostring(grammar.analysis and #grammar.analysis or 0))
            local dicts     = res.dictionaries or {}
            local jitendex  = dicts.jitendex  or {}
            local kenkyusha = dicts.kenkyusha  or {}
            local wisdom    = dicts.wisdom     or {}
            local genius    = dicts.genius     or {}
            local dojg      = dicts.grammar    or {}

            local results = {}

            table.insert(results, {
                dict       = "Yomitsu IA",
                word       = word,
                definition = buildAiHtml(word, reading, ai, grammar, romaji_sentence, text, frequency),
                is_html    = true,
            })

            if jitendex.found and jitendex.html ~= "" then
                table.insert(results, {
                    dict       = "Jitendex",
                    word       = word,
                    definition = jitendex.html,
                    is_html    = true,
                })
            end

            if kenkyusha.found and kenkyusha.html ~= "" then
                table.insert(results, {
                    dict       = "研究社 (Kenkyusha)",
                    word       = word,
                    definition = kenkyusha.html,
                    is_html    = true,
                })
            end

            if wisdom.found and wisdom.html ~= "" then
                table.insert(results, {
                    dict       = "ウィズダム (Wisdom)",
                    word       = word,
                    definition = wisdom.html,
                    is_html    = true,
                })
            end

            if genius.found and genius.html ~= "" then
                table.insert(results, {
                    dict       = "ジーニアス (Genius)",
                    word       = word,
                    definition = genius.html,
                    is_html    = true,
                })
            end

            if dojg.found and dojg.html ~= "" then
                table.insert(results, {
                    dict       = "文法 (DOJG Grammar)",
                    word       = word,
                    definition = dojg.html,
                    is_html    = true,
                })
            end

            if #results == 1 then
                table.insert(results, {
                    dict       = "Diccionario",
                    word       = word,
                    definition = "<p>No se encontró definición.</p>",
                    is_html    = true,
                })
            end

            -- Guardar en historial y añadir como pestaña final
            save_to_history(word, reading)
            table.insert(results, {
                dict       = "Historial",
                word       = word,
                definition = buildHistoryHtml(word),
                is_html    = true,
            })

            -- Reiniciar el debounce desde ahora: evita que un toque accidental
            -- en espacio vacío justo después de cerrar el popup re-abra la búsqueda.
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
                height     = sh,  -- full screen height; DictQuickLookup will clamp internally
            }
            UIManager:show(viewer)

        else
            logger.warn("[YOMITSU] Error o cancelación. Código HTTP:", tostring(code))
            if code then  -- sólo mostrar error si fue fallo real, no cancelación
                UIManager:show(InfoMessage:new{
                    text    = "Error Yomitsu: código " .. tostring(code),
                    timeout = 3,
                })
            end
        end
    end)

    return true
end

if ReaderDictionary.onLookupWord then
    ReaderDictionary.onLookupWord = yomitsuInterceptor
else
    ReaderDictionary.lookup = yomitsuInterceptor
end

return Yomitsu
