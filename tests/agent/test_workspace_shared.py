"""shared/ is read-write for every authenticated user."""

from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.agent.tools.filesystem import ReadFileTool, WriteFileTool
from nanobot.agent.workspace_context import (
    WorkspaceBinding,
    bind_workspace,
    reset_workspace,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "users").mkdir(parents=True)
    (root / "shared").mkdir()
    (root / "skills").mkdir()
    return root


def _binding(workspace: Path, key: str) -> WorkspaceBinding:
    user_dir = workspace / "users" / key
    user_dir.mkdir(parents=True, exist_ok=True)
    return WorkspaceBinding(
        default_dir=user_dir,
        extra_allowed_dirs=[workspace / "shared", workspace / "skills"],
        is_admin=False,
        user_key=key,
        audit_root=workspace,
    )


@pytest.mark.asyncio
async def test_two_users_can_collaborate_in_shared(workspace: Path) -> None:
    write_tool = WriteFileTool(workspace=workspace, allowed_dir=workspace)
    read_tool = ReadFileTool(workspace=workspace, allowed_dir=workspace)

    shared_path = str(workspace / "shared" / "notes.md")

    # Alice writes
    token = bind_workspace(_binding(workspace, "telegram__alice"))
    try:
        result = await write_tool.execute(path=shared_path, content="alice was here")
    finally:
        reset_workspace(token)
    assert "Successfully wrote" in result

    # Bob reads what Alice wrote
    token = bind_workspace(_binding(workspace, "telegram__bob"))
    try:
        result = await read_tool.execute(path=shared_path)
    finally:
        reset_workspace(token)
    assert "alice was here" in result

    # Bob appends (overwrite) — Alice can read the latest
    token = bind_workspace(_binding(workspace, "telegram__bob"))
    try:
        await write_tool.execute(path=shared_path, content="bob too")
    finally:
        reset_workspace(token)
    token = bind_workspace(_binding(workspace, "telegram__alice"))
    try:
        result = await read_tool.execute(path=shared_path)
    finally:
        reset_workspace(token)
    assert "bob too" in result
