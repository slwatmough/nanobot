"""Tests for AgentLoop's admin override + binding resolution.

Exercises the small parts of AgentLoop that don't require a full provider
to be stood up: ``_is_admin``, ``_resolve_user_binding``,
``_binding_from_message``, and the SpawnTool admin gating.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.workspace_context import (
    WorkspaceBinding,
    bind_workspace,
    reset_workspace,
)
from nanobot.bus.events import InboundMessage


def _make_loop(workspace: Path, *, agent_admins: list[str] | None = None) -> AgentLoop:
    """Build a loop with the smallest viable wiring for binding tests."""
    bus = MagicMock()
    bus.publish_outbound = MagicMock()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = MagicMock()
    provider.generation.max_tokens = 4096
    return AgentLoop(
        bus=bus,
        provider=provider,
        workspace=workspace,
        model="test-model",
        agent_admins=agent_admins or [],
        restrict_to_workspace=True,
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "users").mkdir(parents=True)
    (root / "shared").mkdir()
    (root / "skills").mkdir()
    return root


class TestIsAdmin:
    def test_channel_qualified_admin_matches_only_that_channel(self, workspace: Path) -> None:
        loop = _make_loop(workspace, agent_admins=["telegram:42"])
        assert loop._is_admin("telegram", "42") is True
        assert loop._is_admin("slack", "42") is False

    def test_bare_sender_id_matches_any_channel(self, workspace: Path) -> None:
        loop = _make_loop(workspace, agent_admins=["42"])
        assert loop._is_admin("telegram", "42") is True
        assert loop._is_admin("slack", "42") is True

    def test_no_admins_means_no_one_is_admin(self, workspace: Path) -> None:
        loop = _make_loop(workspace, agent_admins=[])
        assert loop._is_admin("telegram", "42") is False

    def test_whatsapp_normalisation_for_admins(self, workspace: Path) -> None:
        """Admin entries match by canonical sender_id."""
        loop = _make_loop(workspace, agent_admins=["whatsapp:+447111222333"])
        assert loop._is_admin("whatsapp", "447111222333@s.whatsapp.net") is True


class TestResolveUserBinding:
    def test_non_admin_user_gets_per_user_dir(self, workspace: Path) -> None:
        loop = _make_loop(workspace)
        binding = loop._resolve_user_binding("telegram", "alice")
        assert binding.is_admin is False
        assert binding.user_key == "telegram__alice"
        assert binding.default_dir == workspace / "users" / "telegram__alice"
        # First contact lazily creates the dir.
        assert binding.default_dir.is_dir()

    def test_admin_binds_to_root(self, workspace: Path) -> None:
        loop = _make_loop(workspace, agent_admins=["telegram:42"])
        binding = loop._resolve_user_binding("telegram", "42")
        assert binding.is_admin is True
        assert binding.default_dir == workspace

    def test_force_admin_overrides_membership(self, workspace: Path) -> None:
        loop = _make_loop(workspace)  # no admins configured
        binding = loop._resolve_user_binding("system", "subagent", force_admin=True)
        assert binding.is_admin is True
        assert binding.default_dir == workspace


class TestBindingFromMessage:
    def test_user_message_uses_channel_sender(self, workspace: Path) -> None:
        loop = _make_loop(workspace)
        msg = InboundMessage(
            channel="telegram", sender_id="alice", chat_id="c1", content="hi",
        )
        binding = loop._binding_from_message(msg)
        assert binding.user_key == "telegram__alice"
        assert binding.is_admin is False

    def test_subagent_metadata_restores_parent_user(self, workspace: Path) -> None:
        loop = _make_loop(workspace)
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="telegram:c1",
            content="result",
            metadata={
                "subagent_user_key": "telegram__alice",
                "subagent_is_admin": False,
            },
        )
        binding = loop._binding_from_message(msg)
        assert binding.user_key == "telegram__alice"
        assert binding.is_admin is False

    def test_subagent_admin_flag_preserved(self, workspace: Path) -> None:
        loop = _make_loop(workspace, agent_admins=["telegram:42"])
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="telegram:c1",
            content="result",
            metadata={
                "subagent_user_key": "telegram__42",
                "subagent_is_admin": True,
            },
        )
        binding = loop._binding_from_message(msg)
        assert binding.is_admin is True

    def test_cron_metadata_attributes_to_creator(self, workspace: Path) -> None:
        loop = _make_loop(workspace)
        msg = InboundMessage(
            channel="system",
            sender_id="cron",
            chat_id="telegram:c1",
            content="reminder",
            metadata={
                "creator_channel": "telegram",
                "creator_sender_id": "alice",
            },
        )
        binding = loop._binding_from_message(msg)
        assert binding.user_key == "telegram__alice"
        assert binding.is_admin is False

    def test_legacy_system_message_falls_back_to_admin(self, workspace: Path) -> None:
        loop = _make_loop(workspace)
        msg = InboundMessage(
            channel="system",
            sender_id="system",
            chat_id="cli:direct",
            content="legacy",
        )
        binding = loop._binding_from_message(msg)
        assert binding.is_admin is True


class TestSpawnAdminGating:
    """Non-admin parents cannot escalate to admin via spawn(admin=True)."""

    @pytest.mark.asyncio
    async def test_non_admin_parent_admin_request_ignored(
        self, workspace: Path,
    ) -> None:
        import asyncio
        from nanobot.agent.subagent import SubagentManager

        provider = MagicMock()
        provider.get_default_model.return_value = "m"
        bus = MagicMock()
        manager = SubagentManager(provider=provider, workspace=workspace, bus=bus, max_tool_result_chars=1000)

        parent = WorkspaceBinding(
            default_dir=workspace / "users" / "telegram__alice",
            extra_allowed_dirs=[workspace / "shared"],
            is_admin=False,
            user_key="telegram__alice",
            audit_root=workspace,
        )
        parent.default_dir.mkdir(parents=True, exist_ok=True)

        captured: dict = {}
        done = asyncio.Event()

        async def _capture(*args, **kwargs):
            captured.update(kwargs)
            done.set()

        manager._run_subagent = _capture  # type: ignore[assignment]

        token = bind_workspace(parent)
        try:
            await manager.spawn(task="x", admin=True)
            await asyncio.wait_for(done.wait(), timeout=2)
        finally:
            reset_workspace(token)

        assert captured["binding"].is_admin is False
        assert captured["binding"].user_key == "telegram__alice"

    @pytest.mark.asyncio
    async def test_admin_parent_admin_request_honored(
        self, workspace: Path,
    ) -> None:
        import asyncio
        from nanobot.agent.subagent import SubagentManager

        provider = MagicMock()
        provider.get_default_model.return_value = "m"
        bus = MagicMock()
        manager = SubagentManager(provider=provider, workspace=workspace, bus=bus, max_tool_result_chars=1000)

        parent = WorkspaceBinding(
            default_dir=workspace,
            extra_allowed_dirs=[],
            is_admin=True,
            user_key=None,
            audit_root=workspace,
        )

        captured: dict = {}
        done = asyncio.Event()

        async def _capture(*args, **kwargs):
            captured.update(kwargs)
            done.set()

        manager._run_subagent = _capture  # type: ignore[assignment]

        token = bind_workspace(parent)
        try:
            await manager.spawn(task="x", admin=True)
            await asyncio.wait_for(done.wait(), timeout=2)
        finally:
            reset_workspace(token)

        assert captured["binding"].is_admin is True

    @pytest.mark.asyncio
    async def test_admin_parent_no_admin_request_stays_non_admin(
        self, workspace: Path,
    ) -> None:
        """Admin parents default to non-admin subagents (Phase 3 spec)."""
        import asyncio
        from nanobot.agent.subagent import SubagentManager

        provider = MagicMock()
        provider.get_default_model.return_value = "m"
        bus = MagicMock()
        manager = SubagentManager(provider=provider, workspace=workspace, bus=bus, max_tool_result_chars=1000)

        parent = WorkspaceBinding(
            default_dir=workspace,
            extra_allowed_dirs=[],
            is_admin=True,
            user_key=None,
            audit_root=workspace,
        )

        captured: dict = {}
        done = asyncio.Event()

        async def _capture(*args, **kwargs):
            captured.update(kwargs)
            done.set()

        manager._run_subagent = _capture  # type: ignore[assignment]

        token = bind_workspace(parent)
        try:
            await manager.spawn(task="x")  # admin=False (default)
            await asyncio.wait_for(done.wait(), timeout=2)
        finally:
            reset_workspace(token)

        assert captured["binding"].is_admin is False
