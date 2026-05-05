"""Stable, collision-resistant per-user workspace keys.

Used by the per-user filesystem isolation feature: every inbound
``(channel, sender_id)`` pair is collapsed to a single key that becomes the
name of a directory under ``<workspace>/users/``.

The key must round-trip on every supported filesystem (no spaces, slashes,
or platform-specific reserved chars), be stable across normalisation
quirks (WhatsApp's mix of raw phone numbers and JIDs), and stay short
enough to be readable in directory listings and audit logs.
"""

from __future__ import annotations

import hashlib
import re

from nanobot.utils.helpers import safe_filename


_DIGITS_RE = re.compile(r"[^0-9]+")
_JID_USER_DOMAIN_RE = re.compile(r"^([0-9]+)@s\.whatsapp\.net$", re.IGNORECASE)
_KEY_OK_RE = re.compile(r"^[A-Za-z0-9_\-.]+$")
_HASH_LEN = 8


def canonical_sender_id(channel: str, sender_id: str) -> str:
    """Normalise a raw sender_id from a channel into a stable form.

    Collapses WhatsApp's interchangeable forms — raw phone numbers
    (``+447111222333``, ``447111222333``) and full JIDs
    (``447111222333@s.whatsapp.net``) — to the same digits-only string so
    they hash identically. Other channels are returned unchanged.
    """
    if not sender_id:
        return ""
    if channel.lower() == "whatsapp":
        sid = sender_id.strip()
        if match := _JID_USER_DOMAIN_RE.match(sid):
            return match.group(1)
        if sid.startswith("+"):
            sid = sid[1:]
        if sid.isdigit():
            return sid
        return sid
    return sender_id.strip()


def user_workspace_key(channel: str, sender_id: str) -> str:
    """Return ``<channel>__<sanitized_sender>`` (with hash if sanitisation lossy).

    The hash is deterministic in the *canonical* sender_id, so equivalent
    forms collapse to the same key.
    """
    if not channel:
        raise ValueError("channel is required")
    if not sender_id:
        raise ValueError("sender_id is required")

    canonical = canonical_sender_id(channel, sender_id)
    sanitized = safe_filename(canonical).strip(".")
    # Replace any characters safe_filename left through but that would
    # confuse path tooling on Windows or break the round-trip check.
    sanitized = re.sub(r"\s+", "_", sanitized)
    base = f"{channel}__{sanitized}"

    if _KEY_OK_RE.match(base) and sanitized == canonical and sanitized:
        return base

    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:_HASH_LEN]
    if not sanitized:
        sanitized = "user"
    return f"{channel}__{sanitized}__{digest}"


def parse_admin_entry(entry: str) -> tuple[str | None, str]:
    """Parse an ``agent_admins`` entry into ``(channel, sender_id)``.

    Bare values ("123456") match on any channel.
    """
    raw = entry.strip()
    if not raw:
        return None, ""
    if ":" in raw:
        channel, sender = raw.split(":", 1)
        channel = channel.strip().lower() or None
        sender = sender.strip()
        return channel, sender
    return None, raw
