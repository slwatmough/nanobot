# AGENTS.md

This file provides guidance to AI coding agents working with this repository.

## What is nanobot

nanobot is a lightweight personal AI assistant framework (Python 3.11+). It provides a pluggable agent runtime with multi-channel support (Telegram, Discord, Slack, WeChat, etc.) and multi-provider LLM backends (Anthropic, OpenAI, DeepSeek, etc.). Published on PyPI as `nanobot-ai`.

## Common Commands

```bash
# Install from source with dev dependencies
pip install -e ".[dev]"

# Run tests
uv run pytest tests/
pytest tests/                    # if already in venv
pytest tests/agent/              # run a specific test directory
pytest tests/agent/test_loop.py  # run a single test file
pytest -k "test_name"            # run a single test by name

# Lint and format
ruff check nanobot/
ruff format nanobot/

# CLI entry point
nanobot onboard   # interactive setup
nanobot agent     # chat mode
nanobot gateway   # multi-channel server
nanobot status    # system status
```

CI runs `uv sync --all-extras` then `uv run pytest tests/` on Python 3.11, 3.12, 3.13.

## Architecture

### Core Flow

```
Channels (Telegram, Discord, …) → MessageBus.inbound → AgentLoop → AgentRunner → LLMProvider
                                                          ↓
                                  MessageBus.outbound ← response ← ToolRegistry (tools)
```

### Key Modules

- **`nanobot/agent/loop.py`** — Main event loop. Reads from MessageBus, dispatches to runner, manages session lifecycle.
- **`nanobot/agent/runner.py`** — Shared LLM execution loop with tool call handling.
- **`nanobot/agent/memory.py`** — Two-stage memory: `Consolidator` compresses old messages into `history.jsonl`; `Dream` refines long-term files (SOUL.md, USER.md, MEMORY.md) from history on a cron schedule.
- **`nanobot/agent/context.py`** — Context window building and token management.
- **`nanobot/providers/base.py`** — Abstract `LLMProvider` interface. Each provider in `providers/` implements this.
- **`nanobot/channels/base.py`** — Abstract `Channel` interface. Each channel in `channels/` implements this.
- **`nanobot/bus/queue.py`** — Async MessageBus decouples channels from agent loop.
- **`nanobot/config/schema.py`** — Pydantic config schema. Config lives at `~/.nanobot/config.json`.
- **`nanobot/agent/tools/`** — Tool implementations (filesystem, shell, web, cron, MCP, etc.) with a central `registry.py`.
- **`nanobot/cli/commands.py`** — Typer CLI app, entry point for all commands.
- **`nanobot/nanobot.py`** — High-level `Nanobot` class, SDK facade.
- **`nanobot/utils/gitstore.py`** — Git-backed versioning for memory files (via dulwich).
- **`bridge/`** — TypeScript/Node.js WhatsApp bridge using Baileys (separate from Python codebase).

### Design Patterns

- **Async-first**: All I/O uses asyncio. Tests use `asyncio_mode = "auto"`.
- **Pluggable providers and channels**: Add new ones by implementing the base class.
- **MessageBus decoupling**: Channels never call the agent directly.
- **Two-stage memory**: Consolidation (context pressure → history.jsonl) + Dream (history → refined SOUL/USER/MEMORY.md).
- **Per-user workspace isolation**: When `agents.defaults.agent_admins` is configured, each non-admin user's filesystem tools resolve relative paths against `<workspace>/users/<channel>__<sender>/` and reject absolute paths outside their subtree. `<workspace>/shared/` is read+write to every user; `<workspace>/skills/` is read-only. Admins listed in `agent_admins` bypass the per-user fence and see the whole tree. The active binding is propagated via a `ContextVar` (`nanobot/agent/workspace_context.py`) so the same tool instances serve all users without re-registration.

## Code Style

- **Line length**: 100 chars (ruff), E501 ignored
- **Linting**: `ruff check` with rules E, F, I, N, W
- **Formatting**: `ruff format`
- Prefer small, focused changes over broad rewrites
- Prefer readable code over clever code

## Branching

- `main` — stable releases (bug fixes, docs)
- `nightly` — experimental features, refactoring
- Features cherry-picked from nightly → main (~weekly)
