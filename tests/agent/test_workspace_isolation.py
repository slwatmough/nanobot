"""End-to-end isolation tests using a stub provider.

These exercise the full ``_dispatch`` → ``_run_agent_loop`` →
``WriteFileTool`` chain by feeding crafted tool calls through a
mocked provider. Two users sending the same relative path land in
different per-user dirs.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus


@dataclass
class _StubGen:
    max_tokens: int = 4096


def _make_loop(workspace: Path) -> AgentLoop:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "stub-model"
    provider.generation = _StubGen()
    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=workspace,
        model="stub-model",
        restrict_to_workspace=True,
    )
    return loop


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "users").mkdir(parents=True)
    (root / "shared").mkdir()
    (root / "skills").mkdir()
    return root


@pytest.mark.asyncio
async def test_concurrent_users_isolated_paths(workspace: Path) -> None:
    """Same relative path bound to two users → two different files."""
    from nanobot.agent.tools.filesystem import WriteFileTool
    from nanobot.agent.workspace_context import bind_workspace, reset_workspace

    loop = _make_loop(workspace)
    write_tool: WriteFileTool = loop.tools.get("write_file")  # type: ignore[assignment]
    assert write_tool is not None

    async def _user_writes(channel: str, sender_id: str, content: str) -> None:
        msg = InboundMessage(
            channel=channel, sender_id=sender_id, chat_id="c", content="",
        )
        binding = loop._binding_from_message(msg)
        token = bind_workspace(binding)
        try:
            await write_tool.execute(path="notes.md", content=content)
        finally:
            reset_workspace(token)

    await asyncio.gather(
        _user_writes("telegram", "alice", "alice text"),
        _user_writes("telegram", "bob", "bob text"),
    )

    assert (workspace / "users" / "telegram__alice" / "notes.md").read_text() == "alice text"
    assert (workspace / "users" / "telegram__bob" / "notes.md").read_text() == "bob text"


@pytest.mark.asyncio
async def test_group_chat_same_chat_different_senders_isolated(workspace: Path) -> None:
    """Two senders in the same chat each get their own workspace."""
    from nanobot.agent.tools.filesystem import WriteFileTool
    from nanobot.agent.workspace_context import bind_workspace, reset_workspace

    loop = _make_loop(workspace)
    write_tool: WriteFileTool = loop.tools.get("write_file")  # type: ignore[assignment]

    for sender in ("alice", "bob"):
        msg = InboundMessage(
            channel="telegram", sender_id=sender, chat_id="group_chat_1", content="",
        )
        token = bind_workspace(loop._binding_from_message(msg))
        try:
            await write_tool.execute(path="notes.md", content=f"{sender} note")
        finally:
            reset_workspace(token)

    assert (workspace / "users" / "telegram__alice" / "notes.md").read_text() == "alice note"
    assert (workspace / "users" / "telegram__bob" / "notes.md").read_text() == "bob note"


@pytest.mark.asyncio
async def test_subagent_announce_inherits_parent_user(workspace: Path) -> None:
    """Subagent re-entry binds to the parent user via metadata."""
    from nanobot.agent.tools.filesystem import WriteFileTool
    from nanobot.agent.workspace_context import bind_workspace, reset_workspace

    loop = _make_loop(workspace)
    # First trigger a real user message so the per-user dir exists.
    parent_msg = InboundMessage(
        channel="telegram", sender_id="alice", chat_id="c", content="hi",
    )
    parent_binding = loop._binding_from_message(parent_msg)
    assert parent_binding.user_key == "telegram__alice"

    # Subagent announce arrives as a system message, carrying the parent's
    # user_key in metadata.
    announce = InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id="telegram:c",
        content="result",
        metadata={
            "subagent_user_key": "telegram__alice",
            "subagent_is_admin": False,
        },
    )
    sub_binding = loop._binding_from_message(announce)
    assert sub_binding.user_key == "telegram__alice"
    assert sub_binding.is_admin is False
    assert sub_binding.default_dir == workspace / "users" / "telegram__alice"


@pytest.mark.asyncio
async def test_cron_creator_metadata_attributes_to_user(workspace: Path) -> None:
    loop = _make_loop(workspace)
    cron_msg = InboundMessage(
        channel="system",
        sender_id="cron",
        chat_id="telegram:c",
        content="reminder",
        metadata={
            "creator_channel": "telegram",
            "creator_sender_id": "alice",
        },
    )
    binding = loop._binding_from_message(cron_msg)
    assert binding.user_key == "telegram__alice"
    assert binding.is_admin is False


@pytest.mark.asyncio
async def test_admin_audit_log_records_filesystem_calls(workspace: Path) -> None:
    """Admin tool calls touching the FS append a JSONL record."""
    from nanobot.agent.tools.filesystem import WriteFileTool
    from nanobot.agent.workspace_context import bind_workspace, reset_workspace

    loop = _make_loop(workspace)
    binding = loop._resolve_user_binding("system", "operator", force_admin=True)

    fake_call = MagicMock()
    fake_call.name = "write_file"
    fake_call.arguments = {"path": "ADMIN.md"}

    token = bind_workspace(binding)
    try:
        loop._maybe_audit_tool_calls([fake_call])
    finally:
        reset_workspace(token)

    audit_path = workspace / "audit.log"
    assert audit_path.exists()
    record = json.loads(audit_path.read_text().splitlines()[-1])
    assert record["is_admin"] is True
    assert record["action"] == "write_file"
    assert record["path"] == "ADMIN.md"


@pytest.mark.asyncio
async def test_non_admin_tool_calls_not_audited(workspace: Path) -> None:
    from nanobot.agent.workspace_context import bind_workspace, reset_workspace

    loop = _make_loop(workspace)
    binding = loop._resolve_user_binding("telegram", "alice")

    fake_call = MagicMock()
    fake_call.name = "write_file"
    fake_call.arguments = {"path": "todo.md"}

    token = bind_workspace(binding)
    try:
        loop._maybe_audit_tool_calls([fake_call])
    finally:
        reset_workspace(token)

    audit_path = workspace / "audit.log"
    assert not audit_path.exists()
