"""Tests for utility functions."""
from __future__ import annotations
import pytest
from app.utils import (
    parse_link, sanitize, fmt_size, fmt_dur,
    pbar, is_invite, invite_hash, normalize_chat_id,
)
from app.errors import InvalidLinkError


class TestParseLink:
    def test_public(self):
        c, m, t = parse_link("https://t.me/example/123")
        assert c == "example" and m == 123 and t is None

    def test_private(self):
        c, m, t = parse_link("https://t.me/c/1234567890/42")
        assert c == "-1001234567890" and m == 42 and t is None

    def test_private_topic(self):
        c, m, t = parse_link("https://t.me/c/1234567890/5/99")
        assert c == "-1001234567890" and m == 99 and t == 5

    def test_public_topic(self):
        c, m, t = parse_link("https://t.me/example/5/99")
        assert c == "example" and m == 99 and t == 5

    def test_invalid(self):
        with pytest.raises(InvalidLinkError):
            parse_link("not_a_link")

    def test_empty(self):
        with pytest.raises(InvalidLinkError):
            parse_link("")

    def test_whitespace(self):
        c, m, _ = parse_link("  https://t.me/example/10  ")
        assert c == "example" and m == 10


class TestNormalize:
    def test_positive(self):
        cands = normalize_chat_id("1234567890")
        assert 1234567890 in cands
        assert -1001234567890 in cands

    def test_negative_with_100(self):
        cands = normalize_chat_id("-1001234567890")
        assert -1001234567890 in cands
        assert 1234567890 in cands

    def test_negative_without_100(self):
        cands = normalize_chat_id("-1004417323799")
        has_long = any(
            len(str(abs(c))) > len("1004417323799")
            for c in cands if isinstance(c, int)
        )
        assert has_long

    def test_username(self):
        cands = normalize_chat_id("mygroup")
        assert "mygroup" in cands


class TestInvite:
    def test_plus(self):
        assert is_invite("https://t.me/+ABC123") is True
        assert invite_hash("https://t.me/+ABC123") == "ABC123"

    def test_joinchat(self):
        assert is_invite("https://t.me/joinchat/ABC123") is True

    def test_not_invite(self):
        assert is_invite("https://t.me/example/123") is False


class TestSanitize:
    def test_normal(self):
        assert sanitize("video.mp4") == "video.mp4"

    def test_empty(self):
        assert sanitize("") == "file"

    def test_long(self):
        assert len(sanitize("a" * 300 + ".mp4")) <= 200


class TestFmt:
    def test_size_bytes(self):   assert "B"  in fmt_size(500)
    def test_size_kb(self):      assert "KB" in fmt_size(2048)
    def test_size_mb(self):      assert "MB" in fmt_size(5 * 1024 * 1024)
    def test_size_neg(self):     assert fmt_size(-1) == "unknown"
    def test_dur_sec(self):      assert fmt_dur(45) == "45s"
    def test_dur_min(self):      assert fmt_dur(125) == "2m 05s"
    def test_dur_hour(self):     assert fmt_dur(3661) == "1h 01m 01s"


class TestPbar:
    def test_zero(self):
        b = pbar(0, 100)
        assert "\u2591" in b and "\u2588" not in b

    def test_full(self):
        b = pbar(100, 100)
        assert "\u2591" not in b

    def test_half(self):
        b = pbar(50, 100, 10)
        assert b.count("\u2588") == 5 and b.count("\u2591") == 5
