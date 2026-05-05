"""Nightly summary of audit-log activity for admin users.

Reads the JSONL audit log written by ``AgentLoop._audit_admin_access``,
walks forward from a cursor stored alongside it, groups events by
action, and produces a short human-readable summary. The cursor advances
even when there are no new events so a future "high-priority" alert
tier can read from the same file without racing the digest.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

# Cap how many recent representative paths we surface per action group.
# Three is enough to be useful without turning the message into a wall
# of paths when an admin runs a batch operation.
_PATH_PREVIEW = 3
# Hard ceiling on individual lines parsed per digest. Audit logs rotate
# only via operator intervention (see follow-up TODO in the design doc),
# so an unbounded read is theoretically possible.
_MAX_LINES = 10_000


@dataclass(slots=True)
class _DigestStats:
    total: int = 0
    by_action: Counter[str] = None  # type: ignore[assignment]
    sample_paths_by_action: dict[str, list[str]] = None  # type: ignore[assignment]
    by_user: Counter[str] = None  # type: ignore[assignment]
    parse_errors: int = 0
    span_first_ts: str | None = None
    span_last_ts: str | None = None

    def __post_init__(self) -> None:
        if self.by_action is None:
            self.by_action = Counter()
        if self.sample_paths_by_action is None:
            self.sample_paths_by_action = {}
        if self.by_user is None:
            self.by_user = Counter()


class AuditDigest:
    """Render a digest of new audit-log entries since the last run."""

    def __init__(self, audit_path: Path, cursor_path: Path | None = None) -> None:
        self._audit_path = audit_path
        self._cursor_path = cursor_path or audit_path.parent / ".audit_digest_cursor"

    @property
    def audit_path(self) -> Path:
        return self._audit_path

    @property
    def cursor_path(self) -> Path:
        return self._cursor_path

    def _read_cursor(self) -> int:
        try:
            raw = self._cursor_path.read_text(encoding="utf-8").strip()
        except OSError:
            return 0
        try:
            return max(0, int(raw))
        except ValueError:
            return 0

    def _write_cursor(self, offset: int) -> None:
        try:
            self._cursor_path.parent.mkdir(parents=True, exist_ok=True)
            self._cursor_path.write_text(str(offset), encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to write audit-digest cursor: {}", exc)

    def _scan(self, start_offset: int) -> tuple[_DigestStats, int]:
        """Read lines starting at *start_offset* in audit.log and tally them."""
        stats = _DigestStats()
        if not self._audit_path.exists():
            return stats, start_offset

        size = self._audit_path.stat().st_size
        # Audit log was truncated / rotated externally — start over.
        if start_offset > size:
            start_offset = 0

        end_offset = start_offset
        try:
            with self._audit_path.open("r", encoding="utf-8") as fp:
                fp.seek(start_offset)
                for i, raw in enumerate(fp):
                    if i >= _MAX_LINES:
                        break
                    end_offset += len(raw.encode("utf-8"))
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        stats.parse_errors += 1
                        continue
                    action = str(record.get("action") or "unknown")
                    path = str(record.get("path") or "")
                    user_key = str(record.get("user_key") or "-")
                    ts = record.get("ts")

                    stats.total += 1
                    stats.by_action[action] += 1
                    if path:
                        bucket = stats.sample_paths_by_action.setdefault(action, [])
                        if len(bucket) < _PATH_PREVIEW and path not in bucket:
                            bucket.append(path)
                    stats.by_user[user_key] += 1
                    if isinstance(ts, str):
                        if stats.span_first_ts is None:
                            stats.span_first_ts = ts
                        stats.span_last_ts = ts
        except OSError as exc:
            logger.warning("Audit log read failed: {}", exc)

        return stats, end_offset

    def render(self, stats: _DigestStats) -> str:
        """Build the user-facing markdown summary."""
        if stats.total == 0:
            return "🛡️ Audit digest — no admin activity since last digest."

        lines: list[str] = ["🛡️ Audit digest"]
        if stats.span_first_ts and stats.span_last_ts:
            lines.append(f"Window: {stats.span_first_ts} → {stats.span_last_ts}")
        lines.append(f"Total admin actions: {stats.total}")

        if stats.by_action:
            lines.append("")
            lines.append("By action:")
            for action, count in stats.by_action.most_common():
                samples = stats.sample_paths_by_action.get(action) or []
                if samples:
                    sample_str = ", ".join(samples)
                    lines.append(f"  - {action}: {count} ({sample_str})")
                else:
                    lines.append(f"  - {action}: {count}")

        # Keep the user breakdown only when there's more than one actor —
        # for a single admin it just adds noise.
        if len(stats.by_user) > 1:
            lines.append("")
            lines.append("By user:")
            for user, count in stats.by_user.most_common():
                lines.append(f"  - {user}: {count}")

        if stats.parse_errors:
            lines.append("")
            lines.append(f"⚠️ {stats.parse_errors} malformed audit lines skipped")

        return "\n".join(lines)

    def run(self, *, advance_cursor: bool = True) -> tuple[str, _DigestStats]:
        """Compute and return the digest summary.

        When *advance_cursor* is true (default), persist the new offset so
        the next run starts where this one stopped.
        """
        cursor = self._read_cursor()
        stats, end_offset = self._scan(cursor)
        summary = self.render(stats)
        if advance_cursor and end_offset != cursor:
            self._write_cursor(end_offset)
        return summary, stats

    def peek(self) -> tuple[str, _DigestStats]:
        """Compute the digest without advancing the cursor (for testing / ad-hoc)."""
        return self.run(advance_cursor=False)


def digest_payload_for_logging(stats: _DigestStats) -> dict[str, Any]:
    """Compact dict version of the digest, suitable for structured logs."""
    return {
        "total": stats.total,
        "by_action": dict(stats.by_action),
        "by_user": dict(stats.by_user),
        "parse_errors": stats.parse_errors,
        "span": [stats.span_first_ts, stats.span_last_ts],
    }
