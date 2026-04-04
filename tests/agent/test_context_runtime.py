"""Tests for runtime context block sender identity."""

from nanobot.agent.context import ContextBuilder


class TestRuntimeContextSender:

    def test_sender_name_included(self):
        ctx = ContextBuilder._build_runtime_context(
            channel="whatsapp", chat_id="123@s.whatsapp.net",
            sender_name="Sy",
        )
        assert "Sender: Sy" in ctx

    def test_sender_id_fallback_when_no_name(self):
        ctx = ContextBuilder._build_runtime_context(
            channel="whatsapp", chat_id="123@s.whatsapp.net",
            sender_id="447712345678",
        )
        assert "Sender ID: 447712345678" in ctx
        assert "Sender:" not in ctx.replace("Sender ID:", "")

    def test_sender_name_preferred_over_id(self):
        ctx = ContextBuilder._build_runtime_context(
            channel="whatsapp", chat_id="123@s.whatsapp.net",
            sender_id="447712345678", sender_name="Sy",
        )
        assert "Sender: Sy" in ctx
        assert "Sender ID:" not in ctx

    def test_no_sender_when_both_none(self):
        ctx = ContextBuilder._build_runtime_context(
            channel="whatsapp", chat_id="123@s.whatsapp.net",
        )
        assert "Sender" not in ctx
