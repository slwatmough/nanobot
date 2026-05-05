# Per-user Workspace Isolation

Status: design settled for filesystem + memory isolation. Open items:
(1) skills scope (deferred, separate discussion); (2) three small
implementation choices in "Open implementation choices" at the end of
the memory section.

## Goal

Allow-listed users currently share one global filesystem workspace
(`~/.nanobot/workspace`). Files written by one user are visible to every
other user, and the same directory also holds agent-internal state
(`SOUL.md`, `AGENT.md`, `memory/`, `sessions/`, etc.) that non-admin users
should not be able to see.

We want a workspace layout where a normal user's tools can only see
(a) their own private subtree and (b) a shared area; agent-internal files
are hidden; and a configured `agent_admins` list of identities retains full
access to everything.

This plan also covers **memory isolation** (SOUL/USER/MEMORY/history split
shared vs. per-user) — see "Memory isolation: design" below. Conversation
sessions, credentials, MCP tool access, and skills remain shared in this
iteration and are tracked as follow-ups.

## Non-goals (this iteration)

- Per-user conversation history / sessions
- Per-user MCP servers, credentials, or tool allowlists
- Per-user skills directory (see "Follow-up discussion: skills" below —
  ROI of per-user skills needs a separate conversation before deciding)
- Per-user cron, dream schedule, provider configuration

**Memory isolation IS in scope** for this iteration — designed in detail
below.

## Resolved decisions

- **Identity unit:** `channel:sender_id`. `sender_id` is stable per human
  across DMs and group chats on the supported channels. Caveat: WhatsApp
  allow-lists sometimes accept phone numbers vs. JIDs interchangeably and
  the bridge's normalisation has been inconsistent in places —
  user-key derivation must canonicalise both forms to the same key
  (see "User-key derivation").
- **Workspace layout:** see "Workspace layout" below. Existing
  user-generated files migrate into `shared/`; agent-internal files
  (`SOUL.md`, `AGENT.md`, `memory/`, `sessions/`, …) stay at the root
  and are admin-only.
- **Shared folder:** yes — a `shared/` directory readable (and writable
  — see "Shared folder semantics") by every authenticated user.
- **Admin override:** `agent_admins` is a top-level config list of
  `channel:sender_id` strings. Listed identities bypass isolation and
  see/write the entire root.
- **Subagent admin scope:** subagents are non-admin by default *even
  when the parent is admin*. The top-level admin agent must explicitly
  request an admin subagent (new `spawn` parameter — see Phase 3).
- **Audit:** admin tool calls that cross into another user's subtree
  (or touch admin-only paths) are written to a dedicated audit log.
  We are also adding a separate TODO to review the existing audit
  surface end-to-end (see "Follow-up TODO: audit review").

## Workspace layout

```
~/.nanobot/workspace/                ← root (admin-visible only)
├── SOUL.md                          ← agent identity, GLOBAL (admin-only edit)
├── AGENT.md                         ← agent config, admin-only
├── PRIVACY_POLICY.md                ← household privacy rules (admin-curated;
│                                       loaded into Dream's promotion classifier)
├── memory/                          ← legacy/seed shared memory — admin-only
│   ├── MEMORY.md                    ← becomes seed for shared memory
│   ├── history.jsonl                ← legacy log (frozen post-migration)
│   └── shared/
│       ├── MEMORY.md                ← household-wide curated facts
│       └── history.jsonl            ← optional shared event log
├── sessions/                        ← per-session JSONL files, admin-only
├── skills/                          ← global skills (visible to all — read-only)
├── shared/                          ← user-files area: visible + writable to all users
└── users/
    ├── telegram__123456789/         ← per-user private subtree
    │   ├── USER.md                  ← agent's notes about THIS user
    │   ├── memory/
    │   │   ├── MEMORY.md            ← per-user durable facts
    │   │   ├── history.jsonl        ← per-user consolidated summaries
    │   │   ├── .cursor
    │   │   └── .dream_cursor
    │   └── ...                      ← user's own files
    └── slack__U0123ABC/
        └── ...
```

What each non-admin user "sees" through their filesystem tools:

- **Default workspace (relative path root):** `users/<their_key>/`.
- **Read access via `extra_allowed_dirs`:** `shared/`, `skills/`, and
  the existing global `media/` dir.
- **Write access:** their own `users/<their_key>/` subtree, plus
  `shared/` (see "Shared folder semantics"). Everything else is
  rejected by `_resolve_path`.

Admins (per `agent_admins`) bind to the root as their workspace and
have read/write across the entire tree, still subject to
`restrict_to_workspace` (i.e. confined to the nanobot root, not the
host filesystem).

## Shared folder semantics

`shared/` is read+write for every authenticated user by default. This
matches the user's stated intent ("user files go into a shared workspace
area") and keeps the migration trivial: existing user-authored files
just move from the root into `shared/` and remain mutable.

Two follow-on details to decide during implementation (not blocking the
plan):

- **Subdir conventions.** Convention is `shared/<topic>/...`; no
  enforcement. Document in `AGENTS.md`.
- **Concurrent writes.** Two users editing `shared/foo.md` simultaneously
  is no worse than today's situation (single workspace, no isolation).
  No locking added in v1.

## Current state (where workspace flows today)

- `nanobot/config/paths.py:get_workspace_path()` resolves the single global
  workspace path; defaults to `~/.nanobot/workspace`.
- `AgentLoop.__init__` (`nanobot/agent/loop.py:189-249`) stores
  `self.workspace = workspace` and constructs `ContextBuilder`,
  `SessionManager`, `Consolidator`, `Dream`, `SubagentManager`, and the
  `ToolRegistry` against that one path.
- `_register_default_tools` (`loop.py:349-401`) instantiates the filesystem
  tools (`ReadFileTool`, `WriteFileTool`, `EditFileTool`, `ListDirTool`,
  `GlobTool`, `GrepTool`, `NotebookEditTool`) and `ExecTool` once, with the
  workspace path baked in via constructor args. The same tool instances are
  shared by every concurrent user/session.
- `_FsTool._resolve` (`nanobot/agent/tools/filesystem.py:69`) joins relative
  paths against `self._workspace` and (when `restrict_to_workspace=True`)
  enforces that the resolved path lives under `self._allowed_dir` plus
  `get_media_dir()`.
- `_set_tool_context` (`loop.py:425-453`) already mutates per-message
  context for some tools (`message`, `spawn`, `cron`, `my`); filesystem
  tools are not in that list today.
- `FileStateStore` (`nanobot/agent/tools/file_state.py`) is already keyed
  per session via `bind_file_states`/`current_file_states` using a
  `contextvar`. This is the pattern we copy for the active workspace.
- User identity reaches the agent loop on `InboundMessage.sender_id`
  (`nanobot/bus/events.py:13`).

## Design principles

1. **Identity unit = `channel:sender_id`.** Workspace is keyed off the
   sender, not the chat. In a group chat, two users typing in the same
   room each see their own files.
2. **No per-user tool instances.** Filesystem tools stay singletons in
   the `ToolRegistry`. They resolve the active user's workspace at call
   time from a `contextvar`, mirroring how `FileStates` already works.
   This keeps tool registration, MCP wiring, and the schema cache in
   `ToolRegistry` untouched.
3. **Default workspace path = `<root>/users/<safe_user_key>/`** under
   the existing `~/.nanobot/workspace` root. Each per-user workspace has
   its own subtree; nothing escapes it.
4. **Admins bind to the root.** Identities listed in `agent_admins`
   resolve their workspace to `<root>` directly, granting cross-user
   read/write and visibility of agent-internal files.
5. **Shared area is part of every user's allowed dirs.** `shared/` is
   automatically appended to the per-user `extra_allowed_dirs` so
   filesystem tools accept paths under it — no special-casing in tools.
6. **`restrict_to_workspace` is effectively required.** Per-user
   isolation is meaningless without the boundary check. If a deployment
   has `restrict_to_workspace=false`, log a startup warning and force
   it on for non-admin users; admins keep the configured behaviour.
7. **Workspaces are created lazily.** First inbound message from a
   user creates `users/<safe_user_key>/`; do not pre-create empty
   trees for every entry in `allowFrom`.
8. **Subagents inherit user scope by default; admin status is
   opt-in per-spawn.** A non-admin user's subagents are non-admin. An
   admin user's subagents are also non-admin unless the spawn call
   passes `admin=True` (Phase 3.2).

## CLI / single-user mode (former Q1, fuller explanation)

When you run `nanobot agent` interactively in a terminal, there is only
one human at the keyboard. The question is: should that human's files
land at `~/.nanobot/workspace/` (today's path) or at
`~/.nanobot/workspace/users/cli__local/`?

- **Option (a) — auto-disable.** If the only enabled channel is `cli`,
  skip the per-user indirection and bind the workspace to the root.
  Pros: zero migration; all existing CLI users keep their files where
  they are. Cons: one more code path; gateway-mode and CLI-mode behave
  differently for the same operator.
- **Option (b) — explicit `multi_user_workspaces` flag.** Operator
  decides via config. Pros: predictable. Cons: most users will never
  set it; we live with whatever the default is anyway.
- **Option (c) — always isolate.** CLI is one user among many, with
  key `cli__local`. Existing files at the root are agent-internal
  (SOUL/AGENT/memory/sessions) and stay there; user-authored files
  migrate into `shared/` per the "Resolved decisions" plan. The CLI
  user gets `users/cli__local/` for their private files. Pros: one
  code path; CLI and gateway behave identically; admins listed in
  `agent_admins` (likely the operator) get the legacy root view back
  via the admin override. Cons: existing CLI workflows that hard-code
  `~/.nanobot/workspace/` for *user* files break unless those files
  are migrated to `shared/`.

**Recommendation: (c).** With the new layout the root contains *only*
agent-internal files; the CLI operator is almost certainly going to be
listed in `agent_admins` anyway, so they retain root visibility. Less
special-casing is the right tradeoff. Confirm before locking in.

## User-key derivation

A new helper `nanobot/utils/user_keys.py`:

```python
def user_workspace_key(channel: str, sender_id: str) -> str:
    # Normalize and sanitize so the key round-trips on every supported FS.
    # Strategy: <channel>__<sanitized_sender>; if sanitization is lossy,
    # append a short stable hash of the canonicalised sender_id.
```

Reuses `safe_filename()` from `nanobot/utils/helpers.py` (already used by
`SessionManager.safe_key`). Goal: stable, collision-free, idempotent.

**WhatsApp normalisation.** Because the bridge has historically accepted
both raw phone numbers and full JIDs interchangeably, the helper must
canonicalise *before* hashing so `+447111222333` and
`447111222333@s.whatsapp.net` collapse to the same key. Concretely:

- Strip leading `+`.
- For sender_ids matching `<digits>@s.whatsapp.net`, drop the
  `@s.whatsapp.net` suffix.
- For group JIDs (`<digits>-<digits>@g.us`) — these aren't sender_ids
  anyway; document that group JIDs must never reach this function.

A small regression test asserts equivalence for the known forms.

Examples after canonicalisation:
- `telegram` + `123456789` → `telegram__123456789`
- `whatsapp` + `+447111222333` → `whatsapp__447111222333`
- `whatsapp` + `447111222333@s.whatsapp.net` → `whatsapp__447111222333`
- `cli` + `local` → `cli__local`

## Implementation plan

### Phase 1 — plumbing: per-user workspace contextvar

1. Add `nanobot/agent/workspace_context.py` exposing:
   - `WorkspaceBinding` dataclass: `(default_dir: Path, extra_allowed_dirs: list[Path], is_admin: bool)`.
   - `current_workspace_binding(default: WorkspaceBinding) -> WorkspaceBinding`.
   - `bind_workspace(b: WorkspaceBinding) -> Token` /
     `reset_workspace(token)`.
   - Mirrors the shape of `bind_file_states` /
     `reset_file_states` (`nanobot/agent/tools/file_state.py`).

2. Update `_FsTool` in `nanobot/agent/tools/filesystem.py`:
   - Add a `_workspace` property that reads
     `current_workspace_binding(self._fallback_binding).default_dir`.
   - Add a `_allowed_dir` property that returns the same when the
     binding indicates restriction is in force; admins return `None`
     (no extra restriction beyond the host filesystem boundary, which
     is still enforced by the loop's `restrict_to_workspace`).
   - Add `_extra_allowed_dirs` property that returns the binding's
     `extra_allowed_dirs` (for non-admins this includes `shared/`,
     `skills/`, and `media/`).
   - Constructors keep accepting explicit `workspace` /
     `allowed_dir` / `extra_allowed_dirs` so `Dream` and the subagent
     code paths (which pass explicit values) continue to work.
   - Apply identical changes to `GlobTool`, `GrepTool` in
     `nanobot/agent/tools/search.py`, and `NotebookEditTool` in
     `nanobot/agent/tools/notebook.py`.

3. Update `ExecTool` in `nanobot/agent/tools/shell.py`:
   - `working_dir` becomes the active user workspace at call time when
     no explicit `working_dir` was provided by the caller.
   - The `restrict_to_workspace` boundary check compares against
     the active user workspace, not the static `self.working_dir`.
   - When the binding is admin, `working_dir` resolves to the root and
     no per-user boundary applies (host-level boundary still does).

4. `MessageTool` (registered with `workspace=self.workspace` at
   `loop.py:396`) — extend `set_context` to take a workspace path so
   it resolves attachments against the per-user dir. Confirm via grep
   that this is the full list of tools holding `workspace`.

### Phase 2 — bind the workspace per turn

1. In `AgentLoop._dispatch` / `_process_message` (`loop.py:740`+ and
   `874`+), derive the user binding from `(msg.channel, msg.sender_id)`
   and bind it before calling `_run_agent_loop`. Pattern parallels the
   existing `bind_file_states(...)` at `loop.py:630`:

   ```python
   binding = self._resolve_user_binding(msg.channel, msg.sender_id)
   ws_token = bind_workspace(binding)
   try:
       ...
   finally:
       reset_workspace(ws_token)
   ```

2. Add `AgentLoop._resolve_user_binding(channel, sender_id) -> WorkspaceBinding`:
   - If `_is_admin(channel, sender_id)`:
     `WorkspaceBinding(default_dir=self.workspace, extra_allowed_dirs=[get_media_dir()], is_admin=True)`.
   - Otherwise:
     `WorkspaceBinding(default_dir=<root>/users/<key>/, extra_allowed_dirs=[<root>/shared, <root>/skills, get_media_dir()], is_admin=False)`,
     creating `users/<key>/` and `shared/` lazily.

3. Special senders:
   - `sender_id == "subagent"` (announce loops at `loop.py:912`) —
     must inherit the *originating* user's binding. Thread the parent's
     binding through `SubagentManager.spawn` and back into the announce
     `InboundMessage.metadata` so the system-message dispatch can
     restore it. The metadata field is the canonical user key plus an
     `is_admin` flag (so an admin-spawned admin subagent comes back as
     admin — see Phase 3.2).
   - `channel == "system"` (cron / heartbeat): cron entries store the
     creator's `sender_id` (today cron context is set via
     `cron.set_context(channel, chat_id, metadata=…, session_key=…)` at
     `loop.py:449`). Add `sender_id` to that context so cron-triggered
     turns bind the correct user binding. Cron jobs created before
     this change have no `sender_id`; treat them as legacy
     admin-equivalent (root binding) and log a one-time warning so
     the operator can re-create them.

### Phase 3 — subagents

Subagents currently receive the loop's `workspace` directly
(`SubagentManager.__init__` → `subagent.py:76`+; tool registration at
`subagent.py:177-181`).

1. **Inherit by default.** `SubagentManager.spawn` reads the active
   `WorkspaceBinding` from the contextvar (or accepts it explicitly)
   and passes it through `_run_subagent` so the subagent's tools
   resolve paths against the same per-user binding.
2. **Admin opt-in.** `spawn` accepts a new `admin: bool = False`
   parameter (also exposed on the `SpawnTool` schema as a tool
   argument). The runtime ignores `admin=True` unless the *parent's*
   binding is admin — non-admin parents cannot escalate via spawn.
   When honored, the subagent runs with a root-bound admin
   `WorkspaceBinding`.
3. **Announce metadata.** The subagent's announce `InboundMessage`
   carries the user key and `is_admin` flag in metadata so step 2.3
   re-binds correctly when the announce re-enters the loop.

### Phase 4 — config + onboarding

1. Add `AgentDefaults.agent_admins: list[str]` (Pydantic
   `Field(default_factory=list)`). Each entry is `"channel:sender_id"`
   (e.g. `"telegram:123456789"`, `"slack:U0123ABC"`); a bare
   sender_id matches on any channel for convenience. Resolution
   helper on `AgentLoop`: `_is_admin(channel, sender_id) -> bool`.
   Empty list = no admins (default).
2. Document the new layout, the `agent_admins` flag, and the `shared/`
   directory in `AGENTS.md` and the relevant `dot_nanobot/` sample.
3. **Migration helper invoked at startup.** If a deployment's root
   workspace contains files but no `users/` or `shared/` subdir:
   - Detect agent-internal files (`SOUL.md`, `USER.md`, `AGENT.md`,
     `memory/`, `sessions/`, `skills/`, `media/`) — these stay at the
     root.
   - Treat *every other* file or directory at the root as
     user-generated and offer to move it into `shared/` (interactive
     prompt for `nanobot onboard`/`nanobot agent`; non-interactive
     gateways print the recommended `mv` commands and exit with a
     loud warning).
   - Idempotent: re-running the migration after `shared/` exists is
     a no-op.
4. **Audit log.** Open `<root>/audit.log` for append on startup. Every
   admin tool call that touches a path *outside* the root (admin
   anywhere) or any tool call from a non-admin that crosses into
   another user's subtree (which should be impossible — but log the
   *attempt* for forensics) writes a structured JSONL record:
   `{"ts": ..., "user_key": ..., "is_admin": ..., "tool": ..., "path": ..., "outcome": ...}`.

### Phase 5 — tests

New tests under `tests/agent/`:

1. `test_workspace_isolation.py`:
   - Two users send concurrent inbound messages; each writes a file
     with the same name; verify they land in different paths and
     neither can `read_file` the other.
   - Group-chat parity: same `chat_id`, two `sender_id`s — files
     isolated.
   - Subagent inherits parent user's binding (non-admin parent → non-admin subagent).
   - Cron-triggered turn binds to the user who created the cron entry.
2. `test_workspace_path_resolution.py`:
   - `_resolve` joins relative paths against the *active* user
     workspace, not the loop's static workspace.
   - Absolute path attempts that escape the user workspace are
     rejected when `restrict_to_workspace=True`.
   - `shared/` and `skills/` are reachable via absolute path from a
     non-admin user.
   - Agent-internal files (`SOUL.md`, `memory/MEMORY.md`,
     `sessions/foo.jsonl`) are rejected for non-admins.
3. `test_workspace_admin_override.py`:
   - A user listed in `agent_admins` can `read_file` and `write_file`
     under another user's `users/<key>/` subtree.
   - A non-admin attempting the same path receives the standard
     out-of-workspace error.
   - Per-channel admin scoping: admin on telegram, non-admin on slack.
   - Admin-spawned subagent without `admin=True` is non-admin.
   - Admin-spawned subagent with `admin=True` is admin.
   - Non-admin parent cannot escalate via `admin=True`.
4. `test_workspace_shared.py`:
   - Two users both write to `shared/notes.md`; both can read each
     other's content.
5. `test_user_workspace_key.py`:
   - `whatsapp:+447111222333` and
     `whatsapp:447111222333@s.whatsapp.net` produce the same key.
   - Other channels' keys round-trip through sanitisation.

### Phase 6 — observability

- Log every workspace bind at `info` level the first time per session
  (admin/non-admin and the resolved path), `debug` thereafter.
- Surface the active user workspace and admin flag in `MyTool` status
  output.
- Audit log writes (Phase 4.4) are observable in `<root>/audit.log`.

## Risk register

- **Path escape via symlink.** A malicious user with shell-tool access
  could `ln -s ../other_user_dir ./peek` and then `read_file ./peek/...`.
  `_resolve_path` uses `Path.resolve()` which follows symlinks before
  the boundary check, so this is already defended. Verify in tests.
- **Race on first-use directory creation.** Two concurrent inbound
  messages from the same user can race to `mkdir`. Use
  `mkdir(parents=True, exist_ok=True)` (already idempotent).
- **Subagent context loss.** If the parent's contextvar isn't propagated
  into the subagent's `asyncio.Task`, the subagent inherits the wrong
  binding. `asyncio.create_task` copies contextvars by default, so
  subagents spawned during a user's turn inherit correctly. Cron and
  bus-driven re-entry do not — they need explicit threading (handled
  in Phase 2.3 / Phase 3.3).
- **Memory split.** `Consolidator`, `Dream`, and the memory store are
  constructed against the loop's static workspace, *not* the per-user
  workspace. They continue writing to the root `memory/`, `SOUL.md`,
  `USER.md` — which is now admin-only territory and matches the
  decision in §"Resolved decisions". Call this out clearly in the
  changelog so operators understand memory remains cross-user.
- **`get_media_dir()` is global.** `_resolve_path` always allows the
  global media dir as an extra-allowed dir so channels can deliver
  attachments. User A can theoretically read media uploaded by
  user B if they know the filename. Document this; addressing it is
  part of "isolate channel media per user" follow-up.
- **Privilege escalation via misconfigured admin list.** A typo in
  `agent_admins` could grant admin to the wrong identity. Mitigation:
  log the resolved admin identities at startup so operators can
  visually confirm; require an exact match (no wildcards).

## Follow-up discussion: skills (former Q5)

The user asked to chat through the ROI of per-user skills before
deciding. Material to anchor the discussion:

- **Today.** `<workspace>/skills/` is a single shared directory.
  Anyone can see and trigger any skill installed there. Builtin skills
  are global and read-only.
- **Option A — keep skills global.** Simplest. All users share one
  curated skill set. ROI: zero implementation cost, no surprises.
  Downside: no per-user customisation; one user's experimental skill
  is visible (and triggerable) by another user.
- **Option B — global + per-user overlay.** Builtin + global stay as
  today; each user can additionally install skills under
  `users/<key>/skills/` that *only* their turns see. The skill
  resolver merges the two roots, with per-user shadowing global on
  name collisions. ROI: meaningful when users have differing
  workflows; cost is moderate (the skill resolver lives in
  `nanobot/agent/skills.py` and already supports multiple roots).
- **Option C — fully per-user.** No shared skills directory.
  Probably wrong: skills are often deployment-level configuration.

**Open for the next chat:** what fraction of skills today are
per-user vs. shared in your real deployment? That ratio drives
whether B is worth doing soon or can wait.

## Memory isolation: design

Memory today is one shared notebook: `SOUL.md`, `USER.md`,
`memory/MEMORY.md`, `memory/history.jsonl`, all under the workspace
root, all written by every user's session. We split that into a
shared layer and a per-user layer with explicit privacy controls.

### File layout (resolved)

```
<root>/SOUL.md                          ← GLOBAL — agent identity
<root>/PRIVACY_POLICY.md                ← household-curated promotion rules
<root>/memory/                          ← legacy/seed shared memory (admin-only)
    MEMORY.md                           ← seed for shared MEMORY (carried forward)
    history.jsonl                       ← legacy log; FROZEN post-migration
    shared/MEMORY.md                    ← household-wide curated facts (Dream-managed)
    shared/history.jsonl                ← group-chat summaries / promoted entries
<root>/users/<user_key>/USER.md         ← per-user notes about that human
<root>/users/<user_key>/memory/MEMORY.md     ← per-user durable facts
<root>/users/<user_key>/memory/history.jsonl ← per-user consolidated summaries
<root>/users/<user_key>/memory/.cursor
<root>/users/<user_key>/memory/.dream_cursor
```

Reasoning:

- **SOUL.md global** — agent identity is a singleton.
- **USER.md per-user** — exists *to* capture per-human notes; sharing
  it was the leak.
- **MEMORY.md split** — per-user holds private facts (one user's
  schedule, hobbies, feelings); `memory/shared/MEMORY.md` holds
  household-wide facts (broken dishwasher, holiday plans, family
  norms).
- **history.jsonl per-user** — conversation summaries belong with
  the speaker(s).
- **`PRIVACY_POLICY.md`** — household-curated, admin-editable. Loaded
  into Dream's promotion classifier prompt so the policy is data,
  not code. Ships with a sensible default; the agent can help the
  admin refine it over time.

### Consolidator routing

`Consolidator` is invoked inside a user's turn (which now has a bound
`WorkspaceBinding` carrying `user_key` — see Phase 1 of the
filesystem plan above). Two cases:

- **DM session (one speaker in the slice).** The summary is appended
  to that user's `users/<user_key>/memory/history.jsonl`. One
  destination, no fanout.
- **Group-chat session (multiple speakers).** Build the set of
  `sender_id`s the agent considers participants in this channel:
  - For channels that expose membership APIs (Slack, Discord,
    Matrix), use the channel-reported member list — this includes
    silent participants ("lurkers"), who per the resolved
    requirement should also receive memory.
  - For channels without reliable membership APIs (Telegram,
    WhatsApp groups), fall back to "every `sender_id` that has
    spoken in this session at any point" tracked in
    `session.metadata`.
  - Append the *same* summary entry to *each* participant's
    `users/<user_key>/memory/history.jsonl` with a marker
    `{"source": "group", "session_key": "...", "participants": [...]}`
    so per-user Dream knows where it came from.
  - Also append a copy to `<root>/memory/shared/history.jsonl` —
    group-chat content is presumptively household-wide and worth
    a head start on shared promotion.
- **Admin's own turn.** Treated like any other user: their own
  `users/<admin_key>/memory/`. Admin filesystem scope does not
  imply admin memory bucket.

### Dream cadence + flow (resolved)

- **Cadence: every 12h** (06:00 / 18:00 local time, configurable).
  `DreamConfig.interval_h` default changes from 2 → 12.
- **Sequential per-user.** No worker pool needed at this scale; small
  family deployments will complete in single-digit minutes total.
  `concurrency = 1` with the option to raise later.

Each cycle:

1. **Per-user Dream pass** (one at a time, in `users/` dir order):
   - Read `users/<key>/memory/history.jsonl` since `.dream_cursor`.
   - Phase 1 + Phase 2 exactly as today, scoped to that user's
     files (`USER.md`, `users/<key>/memory/MEMORY.md`).
   - Tool registry for the AgentRunner is bound to that user's
     `WorkspaceBinding` so `read_file`/`edit_file` respect
     isolation.

2. **Shared promotion pass** (runs *after* all per-user passes
   complete in the cycle):
   - For each user whose MEMORY.md changed this cycle, compute the
     git diff (added / modified lines).
   - Build one classifier prompt per user: include `PRIVACY_POLICY.md`
     verbatim, the diff, and a list of explicit privacy markers
     observed in this user's recent history.
   - Ask the classifier: which lines should be *copied* into
     `<root>/memory/shared/MEMORY.md`?
   - Default-reject anything ambiguous.
   - For lines that pass, copy (don't move) into shared MEMORY.md.
     The line stays in the user's MEMORY.md too.
   - After all users are classified, run a small "shared dedupe"
     pass on `<root>/memory/shared/MEMORY.md` to merge equivalent
     lines.

3. Group-chat entries from `<root>/memory/shared/history.jsonl` are
   processed as a final per-deployment pass against
   `<root>/memory/shared/MEMORY.md` directly (skipping the
   per-user-then-promotion route, since group content is
   presumptively shared by definition).

### Privacy policy (`PRIVACY_POLICY.md`)

Admin-curated markdown, loaded at the start of the shared promotion
pass. Default content shipped with nanobot:

```markdown
# Household Privacy Policy

Default: shareable when the fact is household-wide AND not personal
to one individual AND not flagged confidential.

## Always private (never promote to shared MEMORY)

- Personal feelings, mental state, health information, body, money worries
- Relationship issues or concerns about another household member
- Work-specific information for one individual
- Anything one user said about another user (said behind their back)
- Anything flagged with privacy markers in conversation
  ("don't share this", "between us", "private", "confidential",
  surprise gifts, surprise plans)

## Shareable (eligible for promotion)

- Household state and logistics (broken appliances, suppliers,
  passwords kept in shared places, household routines)
- Shared events and calendar (holidays, birthdays, school terms,
  recurring family events)
- Shared decisions and norms expressed openly ("we don't eat meat
  on weekdays" said as a household norm, not one person's diet)
- Family relationships and roles (already known to all)

## When in doubt: leave it private.

The household admin can edit this file at any time; the agent will
help reason about new categories as they come up.
```

The policy is editable interactively by the admin (it's just a file
the admin can read/write). The agent can be asked to suggest
revisions based on observed near-miss cases (e.g. "this fact was
nearly promoted but I was unsure — should the policy clarify?").
That suggestion flow is a stretch goal, not v1.

### Explicit privacy markers

Two layers:

1. **In-conversation markers.** During Consolidator's summary step,
   detect phrases like "don't share this," "between us," "private,"
   "confidential," "surprise" — append `{"private": true}` to the
   resulting `history.jsonl` entry. Per-user Dream still uses the
   entry to update *that user's* MEMORY.md, but the shared promotion
   pass treats lines derived from a private entry as
   never-eligible.
2. **Session-level toggles.** The user can say "everything in this
   conversation is private" — set `session.metadata["private"] = true`
   so all summaries from that session inherit the flag.

Detection is keyword-based for v1 (cheap, false-positive-safe). A
small LLM-assisted detector can replace it later if needed.

### Existing memory → seed shared memory (resolved migration)

User decision: existing `<root>/SOUL.md`, `<root>/USER.md`, and
`<root>/memory/MEMORY.md`/`history.jsonl` are treated as **shared
seed content** for the new layout.

- `SOUL.md` stays put (it was already global).
- `USER.md` is renamed to `<root>/memory/shared/USER.md.legacy` and
  its content is copied verbatim as the seed of every new
  per-user `USER.md` on first contact, then trimmed by that user's
  first Dream cycle. (Or: collapse it into `shared/MEMORY.md` —
  see open implementation choice below.)
- `<root>/memory/MEMORY.md` becomes the seed of
  `<root>/memory/shared/MEMORY.md` (literal move).
- `<root>/memory/history.jsonl` is *frozen* — renamed to
  `history.legacy.jsonl`. New consolidations never write to it;
  Dream reads it once on first cycle (treating it as group-chat
  presumptive-shared content) then ignores it.

This avoids the impossible task of attributing legacy entries to
users.

### `AgentLoop` wiring

- `MemoryStore` constructor accepts `(workspace_root, user_key=None)`.
  `user_key=None` resolves to the shared/legacy paths under
  `<root>/memory/...`; `user_key="..."` resolves to
  `<root>/users/<key>/memory/...` plus `<root>/users/<key>/USER.md`.
- `AgentLoop` keeps a registry: `self._memory_stores: dict[str | None,
  MemoryStore]` keyed by user key (with `None` for the shared store).
  Constructed lazily.
- `Consolidator.archive(messages, *, user_key)` accepts the user
  key at call time (replacing today's "store at construction"
  pattern). For group sessions it's called once per participant.
- `Dream.run()` becomes `Dream.run_cycle()` which iterates the
  registry, then runs the shared-promotion pass.
- The cron entry that schedules Dream lives in
  `nanobot/heartbeat/` today; it stays put, just changes cadence
  and calls `run_cycle`.

### Memory implementation phases

Folded into the overall implementation phasing. Specifically:

- **Phase 7 — `MemoryStore` parameterisation.** Add `user_key`
  argument; verify all existing callers still work with `user_key=None`.
- **Phase 8 — Consolidator routing.** Per-user destination,
  group-chat fanout to channel members.
- **Phase 9 — Per-user Dream loop.** Iterate users in the registry,
  one at a time.
- **Phase 10 — Shared promotion pass.** New classifier LLM call,
  reads `PRIVACY_POLICY.md`, writes `<root>/memory/shared/MEMORY.md`.
- **Phase 11 — Privacy markers.** Keyword detection in Consolidator;
  metadata flag in session and history entry; classifier honours
  the flag.
- **Phase 12 — Cadence change + migration.** Default
  `interval_h: 12`; one-time shared-seed migration on startup.

### Resolved memory questions

(All answered in earlier conversation; recorded here for the audit
trail.)

- **Shared MEMORY.md kept** (not collapsed into SOUL.md).
- **Group chats fan out to all channel participants** (including
  silent ones / lurkers — they can scroll to view the convo, so
  it's not private).
- **Dream cadence: 12h, sequential per-user.**
- **Shared promotion approach: A** — private by default, LLM
  promotes from MEMORY.md diffs, never sees raw `history.jsonl`.
- **Privacy framing: family/household, not technical** — encoded
  in `PRIVACY_POLICY.md` so the admin owns the rules.
- **Explicit privacy markers respected** at both message-level and
  session-level.
- **Legacy memory files become shared seed** — no per-user
  attribution of past content.
- **Admin's own turn → admin's own per-user memory.**

### Open implementation choices (small)

- **USER.md migration:** seed every new per-user `USER.md` from the
  legacy one, *or* dump legacy `USER.md` into `shared/MEMORY.md` and
  start each per-user `USER.md` empty? The first preserves more
  context but cross-pollinates assumptions; the second is cleaner.
  Recommendation: dump-into-shared, start each per-user `USER.md`
  empty. Confirm.
- **Privacy-marker detection scope:** keyword-only for v1
  (recommendation), or include a small LLM check at consolidation
  time?
- **Shared dedupe cadence:** every Dream cycle (12h) is probably
  overkill; once a day is enough. Run shared dedupe only at the
  06:00 cycle.

## Follow-up TODO: audit review

The user flagged that the existing audit surface may be insufficient.
Tracked as a separate piece of work to be planned independently of this
isolation change. Concrete questions for that review:

- What events does nanobot currently log to durable storage (vs.
  `loguru` console only)?
- Is there a structured (JSONL/CSV) audit channel today, or only
  human-readable logs?
- For each tool that crosses a trust boundary
  (`exec`, `read_file`/`write_file`/`edit_file`, `web_fetch`,
  `web_search`, MCP calls, `cron`, `spawn`), is the call recorded
  with: who (user_key), when, what (tool + args), outcome
  (success/error)?
- Retention: who rotates `audit.log`? Is it bounded?
- Visibility: can an admin agent inspect the audit log in-band, or
  only out-of-band?
- Tamper-evidence: append-only? Hash-chained? (Probably overkill, but
  worth deciding now rather than retrofitting.)

A separate plan in `agentplans/` will scope the audit work after this
isolation change lands; the per-user audit log added in Phase 4.4 is
a starting point but not the full picture.
