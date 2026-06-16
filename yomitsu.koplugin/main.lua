local WidgetContainer = require("ui/widget/container/inputcontainer")
local InfoMessage     = require("ui/widget/infomessage")
local DictQuickLookup = require("ui/widget/dictquicklookup")
local UIManager       = require("ui/uimanager")
local ReaderDictionary = require("apps/reader/modules/readerdictionary")
local json            = require("json")
local logger          = require("logger")

local Yomitsu = WidgetContainer:extend{ name = "yomitsu" }

local ORCHESTRATOR_URL = "http://192.168.50.87:8002/analyze"

local original_onLookupWord = ReaderDictionary.onLookupWord
local original_lookup       = ReaderDictionary.lookup

local function hasJapanese(text)
    if not text then return false end
    return text:match("[%z\1-\127]") == nil or text:match("[\228-\233]") ~= nil
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

    UIManager:scheduleIn(0.05, function()
        logger.info("[YOMITSU] Enviando petición al Orquestador...")

        local payload = json.encode({
            raw_text    = text,
            target_word = text
        })

        local socket = require("socket.http")
        local ltn12  = require("ltn12")
        socket.TIMEOUT = 30

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
