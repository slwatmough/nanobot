"""Tests for the AuditDigest module and AgentLoop.run_audit_digest."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.audit_digest import AuditDigest
from nanobot.agent.loop import AgentLoop


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "audit.log"


def _write_records(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        for r in records:
            fp.write(json.dumps(r) + "\n")


class TestAuditDigest:
    def test_empty_log_renders_no_activity(self, audit_path: Path) -> None:
        digest = AuditDigest(audit_path)
        summary, stats = digest.run()
        assert "no admin activity" in summary
        assert stats.total == 0

    def test_groups_by_action_with_path_samples(self, audit_path: Path) -> None:
        _write_records(audit_path, [
            {"ts": "2026-01-01T00:00:00", "user_key": None, "is_admin": True,
             "action": "write_file", "path": "ADMIN.md"},
            {"ts": "2026-01-01T00:01:00", "user_key": None, "is_admin": True,
             "action": "write_file", "path": "OTHER.md"},
            {"ts": "2026-01-01T00:02:00", "user_key": None, "is_admin": True,
             "action": "exec", "path": "ls /tmp"},
        ])
        digest = AuditDigest(audit_path)
        summary, stats = digest.run()
        assert stats.total == 3
        assert stats.by_action["write_file"] == 2
        assert stats.by_action["exec"] == 1
        assert "write_file: 2" in summary
        assert "ADMIN.md" in summary
        assert "exec: 1" in summary

    def test_advances_cursor_so_subsequent_runs_only_see_new_records(
        self, audit_path: Path,
    ) -> None:
        _write_records(audit_path, [
            {"ts": "t1", "user_key": None, "is_admin": True,
             "action": "read_file", "path": "a"},
        ])
        digest = AuditDigest(audit_path)
        first, _ = digest.run()
        assert "Total admin actions: 1" in first

        # Second run with no new entries → no activity.
        second, stats = digest.run()
        assert stats.total == 0
        assert "no admin activity" in second

        # Add another record → only the new one shows up.
        _write_records(audit_path, [
            {"ts": "t2", "user_key": None, "is_admin": True,
             "action": "exec", "path": "echo hi"},
        ])
        third, stats = digest.run()
        assert stats.total == 1
        assert "exec: 1" in third
        assert "read_file" not in third

    def test_truncated_log_resets_cursor(self, audit_path: Path) -> None:
        _write_records(audit_path, [
            {"ts": "t1", "user_key": None, "is_admin": True,
             "action": "read_file", "path": "a"},
        ])
        digest = AuditDigest(audit_path)
        digest.run()
        # Operator rotates / truncates the log.
        audit_path.write_text("")
        # Cursor was past EOF — peek should not error and should report 0.
        summary, stats = digest.run()
        assert stats.total == 0
        assert "no admin activity" in summary

    def test_malformed_lines_counted_but_not_fatal(self, audit_path: Path) -> None:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(
            'not-json\n'
            + json.dumps({"ts": "t", "user_key": None, "is_admin": True,
                          "action": "exec", "path": "x"}) + "\n"
        )
        digest = AuditDigest(audit_path)
        summary, stats = digest.run()
        assert stats.total == 1
        assert stats.parse_errors == 1
        assert "malformed" in summary

    def test_peek_does_not_advance_cursor(self, audit_path: Path) -> None:
        _write_records(audit_path, [
            {"ts": "t", "user_key": None, "is_admin": True,
             "action": "read_file", "path": "a"},
        ])
        digest = AuditDigest(audit_path)
        digest.peek()
        digest.peek()
        # Even after two peeks, run() should still see the record.
        _, stats = digest.run()
        assert stats.total == 1

    def test_path_sample_dedupes_and_caps(self, audit_path: Path) -> None:
        _write_records(audit_path, [
            {"ts": "t", "user_key": None, "is_admin": True,
             "action": "write_file", "path": f"f{i}.md"}
            for i in range(10)
        ])
        digest = AuditDigest(audit_path)
        summary, stats = digest.run()
        assert stats.total == 10
        # Only first three sample paths surface in the summary.
        sampled = stats.sample_paths_by_action["write_file"]
        assert sampled == ["f0.md", "f1.md", "f2.md"]

    def test_user_breakdown_only_when_multiple_actors(
        self, audit_path: Path,
    ) -> None:
        _write_records(audit_path, [
            {"ts": "t", "user_key": "telegram__alice", "is_admin": True,
             "action": "read_file", "path": "a"},
            {"ts": "t", "user_key": "telegram__alice", "is_admin": True,
             "action": "read_file", "path": "b"},
        ])
        single_user_summary, _ = AuditDigest(audit_path).peek()
        assert "By user" not in single_user_summary

        _write_records(audit_path, [
            {"ts": "t", "user_key": "telegram__bob", "is_admin": True,
             "action": "exec", "path": "ls"},
        ])
        multi_user_summary, _ = AuditDigest(audit_path).peek()
        assert "By user" in multi_user_summary
        assert "telegram__alice" in multi_user_summary
        assert "telegram__bob" in multi_user_summary


class TestAgentLoopRunAuditDigest:
    @pytest.fixture
    def loop(self, tmp_path: Path) -> AgentLoop:
        provider = MagicMock()
        provider.get_default_model.return_value = "stub"
        provider.generation = MagicMock()
        provider.generation.max_tokens = 4096
        bus = MagicMock()
        bus.publish_outbound = AsyncMock()
        return AgentLoop(
            bus=bus,
            provider=provider,
            workspace=tmp_path / "ws",
            agent_admins=["telegram:42", "slack:U99"],
            restrict_to_workspace=True,
        )

    @pytest.mark.asyncio
    async def test_publishes_to_each_admin(self, loop: AgentLoop) -> None:
        # Seed the audit log via the loop's own helper so paths line up.
        binding = loop._resolve_user_binding("system", "operator", force_admin=True)
        loop._audit_admin_access(
            binding, action="write_file", detail={"path": "ADMIN.md"},
        )
        summary = await loop.run_audit_digest()

        assert "Total admin actions: 1" in summary
        assert loop.bus.publish_outbound.await_count == 2  # two admins
        targets = {(c.args[0].channel, c.args[0].chat_id)
                   for c in loop.bus.publish_outbound.await_args_list}
        assert targets == {("telegram", "42"), ("slack", "U99")}
        for call in loop.bus.publish_outbound.await_args_list:
            assert call.args[0].metadata.get("_audit_digest") is True

    @pytest.mark.asyncio
    async def test_skips_bare_admin_entries(self, tmp_path: Path) -> None:
        provider = MagicMock()
        provider.get_default_model.return_value = "stub"
        provider.generation = MagicMock()
        provider.generation.max_tokens = 4096
        bus = MagicMock()
        bus.publish_outbound = AsyncMock()
        loop = AgentLoop(
            bus=bus,
            provider=provider,
            workspace=tmp_path / "ws",
            agent_admins=["12345"],  # bare sender, no channel — undeliverable
            restrict_to_workspace=True,
        )
        binding = loop._resolve_user_binding("system", "operator", force_admin=True)
        loop._audit_admin_access(binding, action="exec", detail={"path": "ls"})

        await loop.run_audit_digest()
        assert bus.publish_outbound.await_count == 0

    @pytest.mark.asyncio
    async def test_no_admins_is_a_noop_but_still_advances(self, tmp_path: Path) -> None:
        provider = MagicMock()
        provider.get_default_model.return_value = "stub"
        provider.generation = MagicMock()
        provider.generation.max_tokens = 4096
        bus = MagicMock()
        bus.publish_outbound = AsyncMock()
        loop = AgentLoop(
            bus=bus,
            provider=provider,
            workspace=tmp_path / "ws",
            agent_admins=[],
            restrict_to_workspace=True,
        )
        # No audit entries → no delivery, no error.
        summary = await loop.run_audit_digest()
        assert "no admin activity" in summary
        assert bus.publish_outbound.await_count == 0
