"""Durable state: what has posted, and when the token was last refreshed.

The runner has no disk between jobs, so both files are committed back to the
repository. That has one absolute consequence: neither may ever contain a
credential. The token file records a timestamp, never a token.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from . import config as cfg

LOG_VERSION = 1

# Anything resembling a secret is refused before it can be written to a file
# that gets committed to a public repository.
_SECRET_SHAPES = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"\bIGAA[A-Za-z0-9]{16,}"),
    re.compile(r"\bEAA[A-Za-z0-9]{16,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}"),
]


class CredentialLeak(RuntimeError):
    """Raised rather than committing something secret-shaped to the repo."""


def assert_no_credentials(payload: Any) -> None:
    blob = json.dumps(payload)
    for pattern in _SECRET_SHAPES:
        match = pattern.search(blob)
        if match:
            raise CredentialLeak(
                "refusing to write state containing something that looks like a "
                "credential ({}…)".format(match.group(0)[:8])
            )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        return json.loads(path.read_text() or "{}")
    except ValueError:
        # A truncated file from an interrupted run must not wedge the agent
        # permanently; losing history is better than never posting again.
        return dict(default)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    assert_no_credentials(payload)
    cfg.ensure_dir(path.parent)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


@dataclass
class ExecutionLog:
    path: Path
    entries: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "ExecutionLog":
        path = path or cfg.EXECUTION_LOG_PATH
        data = _read_json(path, {"version": LOG_VERSION, "posts": []})
        return cls(path=path, entries=list(data.get("posts", [])))

    def save(self) -> None:
        _write_json(
            self.path, {"version": LOG_VERSION, "posts": self.entries}
        )

    # -- dedupe --------------------------------------------------------

    def keys(self) -> Set[str]:
        return {entry["key"] for entry in self.entries if entry.get("key")}

    def has(self, key: str) -> bool:
        return key in self.keys()

    def scene_keys(self) -> Set[str]:
        return {
            entry["scene_key"]
            for entry in self.entries
            if entry.get("scene_key")
        }

    def record(
        self,
        key: str,
        post_type: str,
        networks: Optional[Dict[str, Any]] = None,
        scene_key: Optional[str] = None,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        entry = {
            "key": key,
            "type": post_type,
            "posted_at": _utcnow().isoformat(timespec="seconds"),
            "networks": networks or {},
        }
        if scene_key:
            entry["scene_key"] = scene_key
        if note:
            entry["note"] = note
        self.entries.append(entry)
        return entry

    def prune(self, keep: int = 500) -> None:
        """Keep the log from growing without bound across years of hourly runs."""
        if len(self.entries) > keep:
            self.entries = self.entries[-keep:]


@dataclass
class TokenState:
    path: Path
    data: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "TokenState":
        path = path or cfg.TOKEN_STATE_PATH
        return cls(path=path, data=_read_json(path, {}))

    def save(self) -> None:
        _write_json(self.path, self.data)

    def refreshed_at(self, name: str = "instagram") -> Optional[datetime]:
        raw = self.data.get("{}_refreshed_at".format(name))
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def mark_refreshed(self, name: str = "instagram") -> None:
        self.data["{}_refreshed_at".format(name)] = _utcnow().isoformat(
            timespec="seconds"
        )

    def days_since_refresh(self, name: str = "instagram") -> Optional[float]:
        stamp = self.refreshed_at(name)
        if stamp is None:
            return None
        return (_utcnow() - stamp).total_seconds() / 86400.0

    def needs_refresh(
        self, name: str = "instagram", after_days: int = cfg.TOKEN_REFRESH_AFTER_DAYS
    ) -> bool:
        """Unknown age counts as needing a refresh.

        A token with no recorded refresh is one we cannot reason about, and
        Instagram's expiry is silent — assuming it is fine is how an account
        goes quiet for two months.
        """
        days = self.days_since_refresh(name)
        if days is None:
            return True
        return days >= after_days
