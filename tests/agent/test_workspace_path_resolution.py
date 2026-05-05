"""Tests for per-user filesystem path resolution.

Verifies that the shared FS tool instances honor the active
WorkspaceBinding contextvar at call time:
  - relative paths resolve against the bound user dir, not the loop's
    static workspace
  - absolute paths outside the user dir are rejected for non-admins
  - shared/ and skills/ are reachable for non-admins
  - admin paths (memory/, sessions/) are rejected for non-admins
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.agent.tools.filesystem import (
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)
from nanobot.agent.workspace_context import (
    WorkspaceBinding,
    bind_workspace,
    reset_workspace,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A fully-formed nanobot workspace with users/, shared/, etc."""
    root = tmp_path / "workspace"
    (root / "users").mkdir(parents=True)
    (root / "shared").mkdir()
    (root / "skills").mkdir()
    (root / "memory").mkdir()
    (root / "sessions").mkdir()
    (root / "SOUL.md").write_text("agent identity")
    return root


def _user_binding(workspace: Path, user_key: str) -> WorkspaceBinding:
    user_dir = workspace / "users" / user_key
    user_dir.mkdir(parents=True, exist_ok=True)
    return WorkspaceBinding(
        default_dir=user_dir,
        extra_allowed_dirs=[workspace / "shared", workspace / "skills"],
        is_admin=False,
        user_key=user_key,
        audit_root=workspace,
    )


def _admin_binding(workspace: Path) -> WorkspaceBinding:
    return WorkspaceBinding(
        default_dir=workspace,
        extra_allowed_dirs=[],
        is_admin=True,
        user_key=None,
        audit_root=workspace,
    )


class TestRelativePathsRouteToUserDir:
    @pytest.mark.asyncio
    async def test_write_relative_path_lands_in_user_dir(self, workspace: Path) -> None:
        # Tool is constructed with the loop's static workspace, but writes
        # under the bound user's per-user dir.
        tool = WriteFileTool(workspace=workspace, allowed_dir=workspace)
        binding = _user_binding(workspace, "telegram__alice")
        token = bind_workspace(binding)
        try:
            await tool.execute(path="notes.md", content="hello")
        finally:
            reset_workspace(token)

        assert (workspace / "users" / "telegram__alice" / "notes.md").read_text() == "hello"
        assert not (workspace / "notes.md").exists()

    @pytest.mark.asyncio
    async def test_two_users_isolated_for_same_relative_path(
        self, workspace: Path,
    ) -> None:
        tool = WriteFileTool(workspace=workspace, allowed_dir=workspace)

        for user_key, content in (("telegram__alice", "A"), ("telegram__bob", "B")):
            token = bind_workspace(_user_binding(workspace, user_key))
            try:
                await tool.execute(path="notes.md", content=content)
            finally:
                reset_workspace(token)

        alice_path = workspace / "users" / "telegram__alice" / "notes.md"
        bob_path = workspace / "users" / "telegram__bob" / "notes.md"
        assert alice_path.read_text() == "A"
        assert bob_path.read_text() == "B"


class TestNonAdminBoundary:
    @pytest.mark.asyncio
    async def test_absolute_path_into_other_user_rejected(
        self, workspace: Path,
    ) -> None:
        bob_dir = workspace / "users" / "telegram__bob"
        bob_dir.mkdir(parents=True, exist_ok=True)
        (bob_dir / "secret.md").write_text("bob's secret")

        tool = ReadFileTool(workspace=workspace, allowed_dir=workspace)
        token = bind_workspace(_user_binding(workspace, "telegram__alice"))
        try:
            result = await tool.execute(path=str(bob_dir / "secret.md"))
        finally:
            reset_workspace(token)
        assert "outside allowed directory" in result.lower() or "error" in result.lower()
        # Confirm we never returned the file content.
        assert "bob's secret" not in result

    @pytest.mark.asyncio
    async def test_absolute_path_into_admin_only_path_rejected(
        self, workspace: Path,
    ) -> None:
        (workspace / "memory" / "MEMORY.md").write_text("admin only")
        tool = ReadFileTool(workspace=workspace, allowed_dir=workspace)
        token = bind_workspace(_user_binding(workspace, "telegram__alice"))
        try:
            result = await tool.execute(path=str(workspace / "memory" / "MEMORY.md"))
        finally:
            reset_workspace(token)
        assert "admin only" not in result

    @pytest.mark.asyncio
    async def test_shared_dir_is_reachable_via_absolute_path(
        self, workspace: Path,
    ) -> None:
        (workspace / "shared" / "groceries.md").write_text("eggs")
        tool = ReadFileTool(workspace=workspace, allowed_dir=workspace)
        token = bind_workspace(_user_binding(workspace, "telegram__alice"))
        try:
            result = await tool.execute(path=str(workspace / "shared" / "groceries.md"))
        finally:
            reset_workspace(token)
        assert "eggs" in result

    @pytest.mark.asyncio
    async def test_skills_dir_is_reachable_via_absolute_path(
        self, workspace: Path,
    ) -> None:
        (workspace / "skills" / "manifest.md").write_text("skill body")
        tool = ReadFileTool(workspace=workspace, allowed_dir=workspace)
        token = bind_workspace(_user_binding(workspace, "telegram__alice"))
        try:
            result = await tool.execute(path=str(workspace / "skills" / "manifest.md"))
        finally:
            reset_workspace(token)
        assert "skill body" in result

    @pytest.mark.asyncio
    async def test_isolation_holds_when_static_restrict_is_off(
        self, workspace: Path,
    ) -> None:
        """Per-user fence still applies even with restrict_to_workspace=False."""
        (workspace / "users" / "telegram__bob").mkdir(parents=True, exist_ok=True)
        (workspace / "users" / "telegram__bob" / "secret.md").write_text("nope")

        # Construct tool WITHOUT allowed_dir (i.e. restrict_to_workspace=False)
        tool = ReadFileTool(workspace=workspace, allowed_dir=None)
        token = bind_workspace(_user_binding(workspace, "telegram__alice"))
        try:
            result = await tool.execute(
                path=str(workspace / "users" / "telegram__bob" / "secret.md"),
            )
        finally:
            reset_workspace(token)
        assert "nope" not in result


class TestAdminBypass:
    @pytest.mark.asyncio
    async def test_admin_can_read_other_user_files(self, workspace: Path) -> None:
        bob_dir = workspace / "users" / "telegram__bob"
        bob_dir.mkdir(parents=True, exist_ok=True)
        (bob_dir / "diary.md").write_text("private thoughts")

        tool = ReadFileTool(workspace=workspace, allowed_dir=workspace)
        token = bind_workspace(_admin_binding(workspace))
        try:
            result = await tool.execute(path=str(bob_dir / "diary.md"))
        finally:
            reset_workspace(token)
        assert "private thoughts" in result

    @pytest.mark.asyncio
    async def test_admin_relative_path_resolves_at_workspace_root(
        self, workspace: Path,
    ) -> None:
        tool = WriteFileTool(workspace=workspace, allowed_dir=workspace)
        token = bind_workspace(_admin_binding(workspace))
        try:
            await tool.execute(path="ADMIN_NOTE.md", content="root level")
        finally:
            reset_workspace(token)

        assert (workspace / "ADMIN_NOTE.md").read_text() == "root level"


class TestListDir:
    @pytest.mark.asyncio
    async def test_list_dir_relative_uses_user_dir(self, workspace: Path) -> None:
        tool = ListDirTool(workspace=workspace, allowed_dir=workspace)
        binding = _user_binding(workspace, "telegram__alice")
        (binding.default_dir / "scratch").mkdir()
        token = bind_workspace(binding)
        try:
            result = await tool.execute(path=".")
        finally:
            reset_workspace(token)
        assert "scratch" in result


class TestEdit:
    @pytest.mark.asyncio
    async def test_edit_routes_relative_to_user_dir(self, workspace: Path) -> None:
        write_tool = WriteFileTool(workspace=workspace, allowed_dir=workspace)
        edit_tool = EditFileTool(workspace=workspace, allowed_dir=workspace)
        read_tool = ReadFileTool(workspace=workspace, allowed_dir=workspace)
        token = bind_workspace(_user_binding(workspace, "telegram__alice"))
        try:
            await write_tool.execute(path="todo.md", content="original\n")
            # Read first so the read-before-edit guard is satisfied.
            await read_tool.execute(path="todo.md")
            result = await edit_tool.execute(
                path="todo.md", old_text="original", new_text="updated",
            )
        finally:
            reset_workspace(token)

        assert "Successfully edited" in result
        assert (
            workspace / "users" / "telegram__alice" / "todo.md"
        ).read_text().rstrip("\n") == "updated"
