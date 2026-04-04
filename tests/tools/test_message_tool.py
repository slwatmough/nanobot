import pytest

from nanobot.agent.tools.message import MessageTool


@pytest.mark.asyncio
async def test_message_tool_returns_error_when_no_target_context() -> None:
    tool = MessageTool()
    result = await tool.execute(content="test")
    assert result == "Error: No target channel/chat specified"


# ---------------------------------------------------------------------------
# Media path validation
# ---------------------------------------------------------------------------


class TestMediaValidation:

    @pytest.fixture()
    def workspace(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        return ws

    @pytest.fixture()
    def file_in_workspace(self, workspace):
        f = workspace / "report.md"
        f.write_text("hello")
        return f

    def test_path_inside_workspace_allowed(self, workspace, file_in_workspace):
        tool = MessageTool(workspace=workspace, allowed_dir=workspace)
        resolved = tool._validate_media([str(file_in_workspace)])
        assert resolved == [str(file_in_workspace.resolve())]

    def test_path_outside_workspace_rejected(self, workspace, tmp_path):
        outside = tmp_path / "secret.txt"
        outside.write_text("secret")
        tool = MessageTool(workspace=workspace, allowed_dir=workspace)
        with pytest.raises(PermissionError, match="outside allowed directory"):
            tool._validate_media([str(outside)])

    def test_media_dir_always_allowed(self, workspace, tmp_path, monkeypatch):
        """Files in the media directory (received attachments) are always allowed."""
        media_dir = tmp_path / "media"
        media_dir.mkdir()
        media_file = media_dir / "photo.jpg"
        media_file.write_text("img")

        monkeypatch.setattr(
            "nanobot.agent.tools.message.get_media_dir",
            lambda: media_dir,
        )
        tool = MessageTool(workspace=workspace, allowed_dir=workspace)
        resolved = tool._validate_media([str(media_file)])
        assert resolved == [str(media_file.resolve())]

    def test_relative_path_resolved_against_workspace(self, workspace, file_in_workspace):
        tool = MessageTool(workspace=workspace, allowed_dir=workspace)
        resolved = tool._validate_media(["report.md"])
        assert resolved == [str(file_in_workspace.resolve())]

    def test_nonexistent_file_rejected(self, workspace):
        tool = MessageTool(workspace=workspace, allowed_dir=workspace)
        with pytest.raises(FileNotFoundError, match="Media file not found"):
            tool._validate_media([str(workspace / "nope.txt")])

    def test_no_restriction_when_allowed_dir_none(self, tmp_path):
        """Without allowed_dir, any existing file is accepted."""
        f = tmp_path / "anywhere.txt"
        f.write_text("ok")
        tool = MessageTool(workspace=tmp_path, allowed_dir=None)
        resolved = tool._validate_media([str(f)])
        assert resolved == [str(f.resolve())]

    @pytest.mark.asyncio
    async def test_execute_returns_error_for_outside_path(self, workspace, tmp_path):
        outside = tmp_path / "secret.txt"
        outside.write_text("secret")

        async def noop(msg):
            pass

        tool = MessageTool(
            send_callback=noop,
            default_channel="test",
            default_chat_id="123",
            workspace=workspace,
            allowed_dir=workspace,
        )
        result = await tool.execute(content="here", media=[str(outside)])
        assert result.startswith("Error:")
        assert "outside allowed directory" in result
