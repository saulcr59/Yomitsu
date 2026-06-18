local WidgetContainer = require("ui/widget/container/inputcontainer")
local InfoMessage     = require("ui/widget/infomessage")
local DictQuickLookup = require("ui/widget/dictquicklookup")
local UIManager       = require("ui/uimanager")
local ReaderDictionary = require("apps/reader/modules/readerdictionary")
local json            = require("json")
local logger          = require("logger")

local Yomitsu = WidgetContainer:extend{ name = "yomitsu" }

local ORCHESTRATOR_URL = "http://192.168.50.87:8002/analyze"
local TIMEOUT_SECS     = 30

local original_onLookupWord = ReaderDictionary.onLookupWord
local original_lookup       = ReaderDictionary.lookup

-- Fix: 0xE3 (227) covers hiragana+katakana; 0xE4-0xE9 (228-233) covers kanji.
-- Previous code missed hiragana/katakana entirely.
local function hasJapanese(text)
    if not text then return false end
    return text:match("[\227-\233]") ~= nil
end

-- Try to recover a sentence/phrase of context from KOReader's highlight state.
-- Without real context, Sudachi and the LLM both work blind (can't disambiguate
-- homographs like 今日 → きょう vs こんにち).
local function get_sentence_context(scope, word)
    local hl = scope.ui and scope.ui.highlight
    if hl then
        -- When the user drags to select a range, selected_text holds the full selection.
        if hl.selected_text and type(hl.selected_text) == "table" then
            local t = hl.selected_text.text or ""
            if #t > #word then return t end
        end
        -- Some KOReader builds store a sentence context in selected_word.
        if hl.selected_word and type(hl.selected_word) == "table" then
            local ctx = hl.selected_word.context or hl.selected_word.sentence or ""
            if #ctx > #word then return ctx end
        end
    end
    -- Fallback: send just the word. Sudachi will still normalise correctly,
    -- but homograph disambiguation won't be possible without surrounding text.
    return word
end

-- Construye el HTML simple para el análisis IA
local function buildAiHtml(word, reading, ai)
    local reading_str = (reading and reading ~= "") and
        (" <font color='gray'>(" .. reading .. ")</font>") or ""

    local ai_str = (ai.translation_and_nuance or "Sin análisis disponible")
        :gsub("\n", "<br/>")

    return string.format([[
        <p><b>%s</b>%s</p>
        <hr/>
        <p>%s</p>
        <p><font color="gray"><small>Modelo: %s</small></font></p>
    ]],
        word,
        reading_str,
        ai_str,
        ai.model_used or "desconocido"
    )
end

local function yomitsuInterceptor(scope, text, ...)
    logger.info("[YOMITSU] Hook capturado:", text)

    local doc      = scope.ui and scope.ui.doc
    local doc_lang = (doc and doc:getMeta("language") or "unknown"):lower()

    local is_japanese_book = doc_lang:match("^ja") or doc_lang:match("^jp")
    local is_japanese_text = hasJapanese(text)

    if not (is_japanese_book or is_japanese_text) then
        if original_onLookupWord then
            return original_onLookupWord(scope, text, ...)
        else
            return original_lookup(scope, text, ...)
        end
    end

    -- Mensaje de carga
    local info_box = InfoMessage:new{ text = "Yomitsu: Analizando..." }
    UIManager:show(info_box)
    UIManager:forceRePaint()  -- render loading UI before the blocking HTTP call

    UIManager:scheduleIn(0.05, function()
        logger.info("[YOMITSU] Enviando petición al Orquestador...")

        -- Fix: send the surrounding sentence as context so Sudachi/LLM can
        -- disambiguate. target_word is ONLY the tapped word.
        local context = get_sentence_context(scope, text)
        logger.info("[YOMITSU] Contexto:", context)

        local payload = json.encode({
            raw_text    = context,
            target_word = text
        })

        local socket = require("socket.http")
        local ltn12  = require("ltn12")
        socket.TIMEOUT = TIMEOUT_SECS

        local response_body = {}
        local _, code = socket.request{
            url    = ORCHESTRATOR_URL,
            method = "POST",
            headers = {
                ["Content-Type"]   = "application/json",
                ["Content-Length"] = tostring(#payload),
            },
            source = ltn12.source.string(payload),
            sink   = ltn12.sink.table(response_body),
        }

        UIManager:close(info_box)

        if code == 200 then
            logger.info("[YOMITSU] Respuesta 200 OK")

            local res        = json.decode(table.concat(response_body))
            local word       = res.word_normalized or text
            local reading    = res.reading or ""
            local ai         = res.ai_contextual_analysis or {}
            local dicts      = res.dictionaries or {}
            local jitendex   = dicts.jitendex or {}
            local kenkyusha  = dicts.kenkyusha or {}

            -- Construimos las entradas para DictQuickLookup
            -- Cada entrada es un resultado navegable con ◁◁ ▷▷
            local results = {}

            -- 1. Análisis IA — siempre primero
            table.insert(results, {
                dict       = "Yomitsu IA",
                word       = word,
                definition = buildAiHtml(word, reading, ai),
                is_html    = true,
            })

            -- 2. Jitendex — si encontró resultado
            if jitendex.found and jitendex.html ~= "" then
                table.insert(results, {
                    dict       = "Jitendex",
                    word       = word,
                    definition = jitendex.html,
                    is_html    = true,
                })
            end

            -- 3. Kenkyusha — si encontró resultado
            if kenkyusha.found and kenkyusha.html ~= "" then
                table.insert(results, {
                    dict       = "研究社",
                    word       = word,
                    definition = kenkyusha.html,
                    is_html    = true,
                })
            end

            -- Si no hay ningún diccionario, añadir mensaje
            if #results == 1 then
                table.insert(results, {
                    dict       = "Diccionario",
                    word       = word,
                    definition = "<p>No se encontró definición.</p>",
                    is_html    = true,
                })
            end

            local viewer = DictQuickLookup:new{
                ui         = scope.ui,  -- required for navigation actions (◁◁ ▷▷)
                lookupword = word,
                is_html    = true,
                results    = results,
            }
            UIManager:show(viewer)

        else
            UIManager:show(InfoMessage:new{
                text    = "Error Yomitsu: " .. tostring(code or "Servidor offline"),
                timeout = 3,
            })
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
