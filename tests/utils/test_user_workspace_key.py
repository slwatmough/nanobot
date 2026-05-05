"""Tests for nanobot.utils.user_keys."""

from __future__ import annotations

import pytest

from nanobot.utils.user_keys import (
    canonical_sender_id,
    parse_admin_entry,
    user_workspace_key,
)


class TestCanonicalSenderId:
    def test_whatsapp_phone_strips_plus(self) -> None:
        assert canonical_sender_id("whatsapp", "+447111222333") == "447111222333"

    def test_whatsapp_jid_strips_user_domain(self) -> None:
        assert (
            canonical_sender_id("whatsapp", "447111222333@s.whatsapp.net")
            == "447111222333"
        )

    def test_whatsapp_phone_and_jid_collapse(self) -> None:
        a = canonical_sender_id("whatsapp", "+447111222333")
        b = canonical_sender_id("whatsapp", "447111222333@s.whatsapp.net")
        assert a == b

    def test_other_channels_unchanged(self) -> None:
        assert canonical_sender_id("telegram", "123456789") == "123456789"
        assert canonical_sender_id("slack", "U0123ABC") == "U0123ABC"

    def test_empty_returns_empty(self) -> None:
        assert canonical_sender_id("whatsapp", "") == ""


class TestUserWorkspaceKey:
    def test_telegram(self) -> None:
        assert user_workspace_key("telegram", "123456789") == "telegram__123456789"

    def test_slack(self) -> None:
        assert user_workspace_key("slack", "U0123ABC") == "slack__U0123ABC"

    def test_cli_local(self) -> None:
        assert user_workspace_key("cli", "local") == "cli__local"

    def test_whatsapp_phone_and_jid_collide(self) -> None:
        """The whole point: WhatsApp's two forms must not split into two dirs."""
        plain = user_workspace_key("whatsapp", "+447111222333")
        jid = user_workspace_key("whatsapp", "447111222333@s.whatsapp.net")
        assert plain == jid == "whatsapp__447111222333"

    def test_unsafe_chars_get_hashed_suffix(self) -> None:
        """Senders with characters lost in sanitisation get a stable hash."""
        key = user_workspace_key("matrix", "@alice:example.com")
        # Sanitised form would lose ':' so a hash is appended for stability.
        assert key.startswith("matrix__")
        # Re-deriving the same identity yields the same key.
        assert key == user_workspace_key("matrix", "@alice:example.com")
        # And the hash differentiates from a sender that only differs in
        # what sanitisation drops.
        other = user_workspace_key("matrix", "@alice/example.com")
        assert key != other

    def test_empty_sender_raises(self) -> None:
        with pytest.raises(ValueError):
            user_workspace_key("telegram", "")

    def test_empty_channel_raises(self) -> None:
        with pytest.raises(ValueError):
            user_workspace_key("", "alice")


class TestParseAdminEntry:
    def test_channel_qualified(self) -> None:
        assert parse_admin_entry("telegram:12345") == ("telegram", "12345")

    def test_bare_sender_id(self) -> None:
        assert parse_admin_entry("12345") == (None, "12345")

    def test_whitespace_stripped(self) -> None:
        assert parse_admin_entry("  slack:U99  ") == ("slack", "U99")

    def test_blank_returns_empty(self) -> None:
        assert parse_admin_entry("") == (None, "")
