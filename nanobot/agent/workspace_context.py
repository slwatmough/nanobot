"""Per-user workspace binding propagated through async tasks.

The agent loop holds one shared set of filesystem/exec/notebook tools. Each
inbound message gets bound to a ``WorkspaceBinding`` that the tools read at
call time, so the same tool instance behaves like a per-user tool without
having to re-register tools for every concurrent user.

Mirrors the shape of ``nanobot.agent.tools.file_state``: an explicit
binding object plus a ``ContextVar`` and bind/reset helpers.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class WorkspaceBinding:
    """Active per-turn workspace scoping for tool calls."""

    default_dir: Path
    extra_allowed_dirs: list[Path] = field(default_factory=list)
    is_admin: bool = False
    user_key: str | None = None
    audit_root: Path | None = None


_current: ContextVar[WorkspaceBinding | None] = ContextVar(
    "nanobot_workspace_binding",
    default=None,
)


def current_workspace_binding(default: WorkspaceBinding) -> WorkspaceBinding:
    """Return the active binding, falling back to *default*."""
    return _current.get() or default


def get_workspace_binding() -> WorkspaceBinding | None:
    """Return the active binding, or None when nothing is bound."""
    return _current.get()


def bind_workspace(binding: WorkspaceBinding) -> Token[WorkspaceBinding | None]:
    """Bind *binding* for the current async task."""
    return _current.set(binding)


def reset_workspace(token: Token[WorkspaceBinding | None]) -> None:
    """Restore the previous binding."""
    _current.reset(token)
