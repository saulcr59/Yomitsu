"""
Tests for disambiguation logic in dictionary-service.

Covers:
  - UTF-8 byte offset → Python char index conversion
  - sel_start resolution (with/without char_offset)
  - Sudachi tokenization: correct compound found at given offset
  - Multiple occurrences: offset picks the right one
  - Reading-based disambiguation (今日 → きょう vs こんにち)
  - Edge cases and error paths
"""
import pytest
from sudachipy import dictionary as sudachi_dict
from sudachipy import tokenizer as sudachi_tok


# ---------------------------------------------------------------------------
# Helpers that replicate the logic in main.py without importing main.py
# (to avoid triggering Jitendex/Kenkyusha loading at import time).
# ---------------------------------------------------------------------------

def byte_to_char(phrase: str, byte_off: int) -> int | None:
    """Convert UTF-8 byte offset to Python character index. Returns None if invalid."""
    if byte_off is None:
        return None
    try:
        phrase_bytes = phrase.encode("utf-8")
        if byte_off < 0 or byte_off >= len(phrase_bytes):
            return None
        return len(phrase_bytes[:byte_off].decode("utf-8", errors="ignore"))
    except Exception:
        return None


def resolve_sel_start(phrase: str, selection: str, char_offset: int | None) -> int:
    """
    Replicate the sel_start logic from main.py:
      char_offset is a UTF-8 BYTE offset from Lua.
      Converts to Python char index and verifies selection is there.
      Falls back to phrase.find(selection) if anything fails.
    """
    sel_start = -1
    if char_offset is not None:
        try:
            phrase_bytes = phrase.encode("utf-8")
            if 0 <= char_offset < len(phrase_bytes):
                prefix_chars = len(phrase_bytes[:char_offset].decode("utf-8", errors="ignore"))
                if phrase[prefix_chars:prefix_chars + len(selection)] == selection:
                    sel_start = prefix_chars
                else:
                    for delta in range(-3, 4):
                        i = prefix_chars + delta
                        if i >= 0 and phrase[i:i + len(selection)] == selection:
                            sel_start = i
                            break
        except Exception:
            pass
    if sel_start < 0:
        sel_start = phrase.find(selection)
    return sel_start


def find_best_token(phrase: str, selection: str, sel_start: int,
                    tokenizer_obj, mode):
    """
    Replicate the Sudachi token-finding logic from main.py.
    Returns (surface, normalized, reading) or None.
    """
    tokens = tokenizer_obj.tokenize(phrase, mode)
    sel_end = sel_start + len(selection) if sel_start >= 0 else -1
    best = None
    for tok in tokens:
        if sel_start >= 0:
            tb, te = tok.begin(), tok.end()
            if tb <= sel_start < te or sel_start <= tb < sel_end:
                best = tok
                break
    if best is None:
        for tok in tokens:
            if selection in tok.surface():
                best = tok
                break
    if best is None:
        return None
    return best.surface(), best.normalized_form(), best.reading_form()


# ---------------------------------------------------------------------------
# Session-scoped Sudachi fixture (created once for all tests)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def sudachi():
    obj = sudachi_dict.Dictionary().create()
    mode = sudachi_tok.Tokenizer.SplitMode.C
    return obj, mode


# ===========================================================================
# GROUP 1 — byte_to_char conversion (10 cases)
# ===========================================================================

class TestByteToChar:

    def test_ascii_offset_0(self):
        assert byte_to_char("hello", 0) == 0

    def test_ascii_offset_mid(self):
        # 'e' is at index 1 = byte 1
        assert byte_to_char("hello", 1) == 1

    def test_japanese_single_char_offset_0(self):
        # 日 = E6 97 A5 (3 bytes). Byte 0 → char 0.
        assert byte_to_char("日", 0) == 0

    def test_japanese_two_chars_offset_3(self):
        # "今日" → 今 is bytes 0-2, 日 starts at byte 3 → char 1
        assert byte_to_char("今日", 3) == 1

    def test_japanese_three_chars_offset_6(self):
        # "今日本" → 本 starts at byte 6 → char 2
        assert byte_to_char("今日本", 6) == 2

    def test_mixed_ascii_japanese(self):
        # "a日b" → 日 at byte 1 → char 1; b at byte 4 → char 2
        assert byte_to_char("a日b", 1) == 1
        assert byte_to_char("a日b", 4) == 2

    def test_offset_at_last_char(self):
        # "無事" → 2 chars × 3 bytes = 6 bytes; 事 at byte 3 → char 1
        assert byte_to_char("無事", 3) == 1

    def test_offset_zero_any_string(self):
        # Byte 0 is always char 0
        assert byte_to_char("無事にゲームを獲得し", 0) == 0

    def test_invalid_offset_too_large(self):
        # Byte 100 in a 3-byte string → None
        assert byte_to_char("日", 100) is None

    def test_invalid_negative_offset(self):
        assert byte_to_char("日", -1) is None


# ===========================================================================
# GROUP 2 — resolve_sel_start: offset correctly finds the right occurrence
# ===========================================================================

class TestResolveSelStart:

    def test_no_offset_falls_back_to_first(self):
        # Without offset, picks first occurrence
        phrase = "今日は良い今日だ"  # 今日 appears at 0 and 5
        assert resolve_sel_start(phrase, "今日", None) == 0

    def test_offset_picks_first_occurrence(self):
        # byte offset 0 → char 0 → first 今日
        phrase = "今日は良い今日だ"
        byte_off = 0  # byte 0 = char 0 = first 今日
        assert resolve_sel_start(phrase, "今日", byte_off) == 0

    def test_offset_picks_second_occurrence(self):
        # 今日は良い今日だ: second 今日 starts at char 5
        # char 5 in UTF-8 = byte 5*3=15
        phrase = "今日は良い今日だ"
        byte_off = 15  # second 今日
        result = resolve_sel_start(phrase, "今日", byte_off)
        assert result == 5

    def test_offset_at_exact_boundary(self):
        phrase = "本日発売"  # 本=0,日=1,発=2,売=3
        byte_off = 3  # 日 at byte 3 = char 1
        result = resolve_sel_start(phrase, "日", byte_off)
        assert result == 1

    def test_offset_none_multi_occurrence(self):
        # 奴の事と無事 — without offset, 事 finds first occurrence (in 奴の事)
        phrase = "奴の事と無事"
        result = resolve_sel_start(phrase, "事", None)
        # First 事 is at char 2 (奴=0, の=1, 事=2)
        assert result == 2

    def test_offset_points_to_second_koto(self):
        # 奴の事と無事 — with byte offset 12 (無事の事, char 5)
        phrase = "奴の事と無事"
        # 無 is at char 4 (byte 12), 事 in 無事 is at char 5 (byte 15)
        byte_off = 15
        result = resolve_sel_start(phrase, "事", byte_off)
        assert result == 5

    def test_selection_not_in_phrase_returns_minus_one(self):
        result = resolve_sel_start("今日は良い", "月", None)
        assert result == -1

    def test_single_char_context_equals_selection(self):
        # When phrase == selection (bare-minimum context)
        result = resolve_sel_start("日", "日", 0)
        assert result == 0

    def test_delta_correction_encoding_drift(self):
        # Simulate a byte offset that's 1 byte off (mid-sequence for 3-byte char)
        # Should use delta search to find the correct char
        phrase = "無事"
        # 無 at char 0, byte 0. 事 at char 1, byte 3.
        # If Lua sends byte 4 (mid-sequence), delta search finds char 1
        result = resolve_sel_start(phrase, "事", 4)
        assert result == 1

    def test_out_of_bounds_offset_falls_back(self):
        # Byte offset beyond string → falls back to find()
        phrase = "今日"
        result = resolve_sel_start(phrase, "日", 9999)
        # find() returns 1 (second char)
        assert result == 1


# ===========================================================================
# GROUP 3 — Sudachi tokenization with exact offset (integration tests)
# ===========================================================================

class TestSudachiDisambiguation:

    def test_mujini_offset_at_mu(self, sudachi):
        """Tapping 無 in 無事 → compound 無事 (mujini sense)."""
        obj, mode = sudachi
        phrase = "無事にゲームを獲得した"
        sel = "無"
        # 無 at char 0, byte 0
        sel_start = resolve_sel_start(phrase, sel, 0)
        result = find_best_token(phrase, sel, sel_start, obj, mode)
        assert result is not None
        assert result[1] == "無事"  # normalized form

    def test_mujini_offset_at_ji(self, sudachi):
        """Tapping 事 in 無事 → compound 無事, NOT standalone koto."""
        obj, mode = sudachi
        phrase = "無事にゲームを獲得した"
        sel = "事"
        # 事 at char 1 = byte 3
        sel_start = resolve_sel_start(phrase, sel, 3)
        result = find_best_token(phrase, sel, sel_start, obj, mode)
        assert result is not None
        assert result[1] == "無事", f"Expected 無事 but got {result[1]}"

    def test_koto_standalone_in_yatsuno_koto(self, sudachi):
        """事 in 奴の事 → standalone 事 (koto), not a compound."""
        obj, mode = sudachi
        phrase = "奴の事と無事"
        sel = "事"
        # 事 in 奴の事 is at char 2 = byte 6
        sel_start = resolve_sel_start(phrase, sel, 6)
        result = find_best_token(phrase, sel, sel_start, obj, mode)
        assert result is not None
        assert result[1] in ("事", "こと"), f"Expected standalone 事 but got {result[1]}"

    def test_honjitsu_offset_at_hon(self, sudachi):
        """Tapping 本 in 本日 → compound 本日."""
        obj, mode = sudachi
        phrase = "本日発売の新作ゲーム"
        sel = "本"
        sel_start = resolve_sel_start(phrase, sel, 0)
        result = find_best_token(phrase, sel, sel_start, obj, mode)
        assert result is not None
        assert result[1] == "本日"

    def test_honjitsu_offset_at_nichi(self, sudachi):
        """Tapping 日 in 本日 → compound 本日, NOT 日 standalone."""
        obj, mode = sudachi
        phrase = "本日発売の新作ゲーム"
        sel = "日"
        # 日 in 本日 is at char 1 = byte 3
        sel_start = resolve_sel_start(phrase, sel, 3)
        result = find_best_token(phrase, sel, sel_start, obj, mode)
        assert result is not None
        assert result[1] == "本日", f"Expected 本日 but got {result[1]}"

    def test_kyou_in_konnichi_phrase_offset_at_kyou(self, sudachi):
        """Page has 今日 and 本日; tapping 日 in 今日 → 今日."""
        obj, mode = sudachi
        # 今日 at start, 本日 at char 3
        phrase = "今日と本日は違う"
        sel = "日"
        # 日 in 今日 is at char 1 = byte 3
        sel_start = resolve_sel_start(phrase, sel, 3)
        result = find_best_token(phrase, sel, sel_start, obj, mode)
        assert result is not None
        assert result[1] == "今日", f"Expected 今日 but got {result[1]}"

    def test_honjitsu_on_page_with_kyou_offset_selects_honjitsu(self, sudachi):
        """Critical case: page has 今日 early and 本日 later. Tap 日 in 本日."""
        obj, mode = sudachi
        # Simulates: page context window already built around 本日
        phrase = "本日発売の新作ゲーム"
        sel = "日"
        # 日 in 本日 is at char 1 = byte 3
        sel_start = resolve_sel_start(phrase, sel, 3)
        result = find_best_token(phrase, sel, sel_start, obj, mode)
        assert result is not None
        assert result[1] == "本日", f"Expected 本日 but got {result[1]}"

    def test_phrase_equals_selection_single_char(self, sudachi):
        """Bare-minimum context: phrase == selection (1 char)."""
        obj, mode = sudachi
        phrase = "日"
        sel = "日"
        sel_start = resolve_sel_start(phrase, sel, None)
        result = find_best_token(phrase, sel, sel_start, obj, mode)
        assert result is not None
        assert sel in result[0]  # surface contains selection

    def test_no_offset_page_center_heuristic(self, sudachi):
        """Without offset, first occurrence is used (may be wrong — documents the fallback behavior)."""
        obj, mode = sudachi
        phrase = "今日と無事に帰った今日"
        sel = "今日"
        # No offset → falls back to find() → char 0 (first 今日)
        sel_start = resolve_sel_start(phrase, sel, None)
        assert sel_start == 0

    def test_complex_compound_tabeiru(self, sudachi):
        """食べ物: offset at 食 → compound 食べ物."""
        obj, mode = sudachi
        phrase = "美味しい食べ物が食べたい"
        sel = "食"
        # 食べ物 starts at char 4 = byte 12
        sel_start = resolve_sel_start(phrase, sel, 12)
        result = find_best_token(phrase, sel, sel_start, obj, mode)
        assert result is not None
        assert "食" in result[1]  # normalized contains 食


# ===========================================================================
# GROUP 4 — reading-based disambiguation (きょう vs こんにち for 今日)
# ===========================================================================

def kata_to_hira(text: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ン" else c for c in text)


class TestReadingDisambiguation:

    def test_kyou_reading_from_sudachi(self, sudachi):
        """今日 in 今日は良い天気 → Sudachi reads きょう."""
        obj, mode = sudachi
        phrase = "今日は良い天気だ"
        sel = "今日"
        sel_start = resolve_sel_start(phrase, sel, 0)
        result = find_best_token(phrase, sel, sel_start, obj, mode)
        assert result is not None
        hira = kata_to_hira(result[2])
        assert hira == "きょう", f"Expected きょう but got {hira}"

    def test_kyou_reading_sudachi_always_returns_kyou(self, sudachi):
        """今日 in any sentence → Sudachi SplitMode.C always reads キョウ.
        The こんにち sense is selected via Jitendex reading filter when user
        explicitly looks up こんにち, not via Sudachi context.
        This test documents the known Sudachi behavior."""
        obj, mode = sudachi
        for phrase in [
            "今日の社会では様々な問題がある",
            "今日において課題が多い",
            "今日まで問題が続いている",
        ]:
            sel = "今日"
            sel_start = resolve_sel_start(phrase, sel, 0)
            result = find_best_token(phrase, sel, sel_start, obj, mode)
            assert result is not None
            hira = kata_to_hira(result[2])
            # Sudachi always gives きょう regardless of "nowadays" context
            assert hira == "きょう", (
                f"Sudachi behavior changed for {phrase!r}: got {hira}"
            )

    def test_honjitsu_reading(self, sudachi):
        """本日 → Sudachi reads ほんじつ."""
        obj, mode = sudachi
        phrase = "本日はお日柄もよく"
        sel = "本日"
        sel_start = resolve_sel_start(phrase, sel, 0)
        result = find_best_token(phrase, sel, sel_start, obj, mode)
        assert result is not None
        hira = kata_to_hira(result[2])
        assert hira == "ほんじつ", f"Expected ほんじつ but got {hira}"

    def test_mujini_reading(self, sudachi):
        """無事 → Sudachi reads ぶじ."""
        obj, mode = sudachi
        phrase = "無事に帰ってきた"
        sel = "無事"
        sel_start = resolve_sel_start(phrase, sel, 0)
        result = find_best_token(phrase, sel, sel_start, obj, mode)
        assert result is not None
        hira = kata_to_hira(result[2])
        assert hira == "ぶじ", f"Expected ぶじ but got {hira}"

    def test_koto_reading_standalone(self, sudachi):
        """事 in 奴の事 → Sudachi reads こと."""
        obj, mode = sudachi
        phrase = "奴の事を考えた"
        sel = "事"
        sel_start = resolve_sel_start(phrase, sel, 6)  # char 2 = byte 6
        result = find_best_token(phrase, sel, sel_start, obj, mode)
        assert result is not None
        hira = kata_to_hira(result[2])
        assert hira == "こと", f"Expected こと but got {hira}"


# ===========================================================================
# GROUP 5 — full scenario matching the actual failing log
# ===========================================================================

class TestRealLogScenario:
    """
    Replicates the exact failing case from the logs:
    Page text has 奴の事 (char ~85) and 無事 (char ~157).
    User taps 事 in 無事 — should get ぶじ, not こと.
    """

    PAGE_CONTEXT = (
        "した。\n　本日発売のとある人気ネットゲーム、その初回限定版を"
        "手に入れるため、珍しく早起きして行列に並んだのだ。\n　世間では"
        "俺みたいな奴の事を引き篭もりだのネトゲ廃人だのと呼んでいるらしいが。"
        "\n　無事にゲームを獲得し、後は家に帰ってゲーム三昧だと、上機嫌で"
        "帰宅しようとしていた、そんな時だった。\n　携帯をいじりながら俺の前を"
        "歩いていた女の子"
    )

    def _find_char_pos(self, phrase: str, target: str, occurrence: int = 0) -> int:
        """Find the nth occurrence of target in phrase."""
        pos = -1
        for _ in range(occurrence + 1):
            pos = phrase.find(target, pos + 1)
            if pos == -1:
                return -1
        return pos

    def test_ji_in_yatsu_no_koto_char_position(self):
        """Verify char position of 事 in 奴の事."""
        pos = self._find_char_pos(self.PAGE_CONTEXT, "奴の事")
        assert pos >= 0
        koto_ji_pos = pos + 2  # 奴=0, の=1, 事=2 within match
        assert self.PAGE_CONTEXT[koto_ji_pos] == "事"

    def test_ji_in_muji_char_position(self):
        """Verify char position of 事 in 無事."""
        pos = self._find_char_pos(self.PAGE_CONTEXT, "無事")
        assert pos >= 0
        muji_ji_pos = pos + 1  # 無=0, 事=1
        assert self.PAGE_CONTEXT[muji_ji_pos] == "事"

    def test_wrong_offset_gives_koto(self, sudachi):
        """Without correct offset, 事 near start → こと (wrong result)."""
        obj, mode = sudachi
        phrase = self.PAGE_CONTEXT
        sel = "事"
        # Wrong: no offset → find() → first occurrence (奴の事)
        sel_start = resolve_sel_start(phrase, sel, None)
        assert phrase[sel_start] == "事"
        result = find_best_token(phrase, sel, sel_start, obj, mode)
        assert result is not None
        hira = kata_to_hira(result[2])
        # Documents the WRONG behavior: first 事 in 奴の事 → こと
        assert hira == "こと", f"Expected こと (wrong path) but got {hira}"

    def test_correct_offset_gives_buji(self, sudachi):
        """With correct byte offset pointing at 事 in 無事 → ぶじ (correct)."""
        obj, mode = sudachi
        phrase = self.PAGE_CONTEXT
        sel = "事"
        # 無事 occurrence; find char position of 事 in 無事
        muji_pos = phrase.find("無事")
        muji_ji_char = muji_pos + 1  # 事 is 1 char after 無
        muji_ji_byte = len(phrase[:muji_ji_char].encode("utf-8"))

        sel_start = resolve_sel_start(phrase, sel, muji_ji_byte)
        assert sel_start == muji_ji_char, (
            f"Expected sel_start={muji_ji_char} but got {sel_start}")
        result = find_best_token(phrase, sel, sel_start, obj, mode)
        assert result is not None
        hira = kata_to_hira(result[2])
        assert hira == "ぶじ", f"Expected ぶじ but got {hira}"

    def test_correct_offset_gives_honjitsu(self, sudachi):
        """With correct offset for 日 in 本日 → ほんじつ."""
        obj, mode = sudachi
        phrase = self.PAGE_CONTEXT
        sel = "日"
        honjitsu_pos = phrase.find("本日")
        nichi_char = honjitsu_pos + 1
        nichi_byte = len(phrase[:nichi_char].encode("utf-8"))

        sel_start = resolve_sel_start(phrase, sel, nichi_byte)
        assert sel_start == nichi_char
        result = find_best_token(phrase, sel, sel_start, obj, mode)
        assert result is not None
        assert result[1] == "本日", f"Expected 本日 but got {result[1]}"

    def test_correct_offset_mu_gives_buji(self, sudachi):
        """Tapping 無 → 無事 (ぶじ) regardless of other occurrences."""
        obj, mode = sudachi
        phrase = self.PAGE_CONTEXT
        sel = "無"
        mu_char = phrase.find("無")
        mu_byte = len(phrase[:mu_char].encode("utf-8"))

        sel_start = resolve_sel_start(phrase, sel, mu_byte)
        result = find_best_token(phrase, sel, sel_start, obj, mode)
        assert result is not None
        assert result[1] == "無事"
