"""Keeps the Instagram token alive.

Long-lived tokens last 60 days and die silently — no warning, no error until a
post fails. Refreshing produces a *new* token string, so the refresh is
worthless unless the new value is persisted somewhere the next run will read.

On a runner that means `gh secret set`. Writing to .env on an ephemeral
filesystem accomplishes exactly nothing, and the only evidence is an account
that stops posting two months later.

The refresh timestamp goes in state/, which is committed. The token itself
never does.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Optional

from .. import config as cfg
from ..state import TokenState

SECRET_NAME = "INSTAGRAM_ACCESS_TOKEN"


class RefreshError(RuntimeError):
    pass


@dataclass
class RefreshOutcome:
    refreshed: bool
    expires_in_days: Optional[float] = None
    persisted_to: Optional[str] = None
    detail: str = ""


def _refresh_instagram_login(token: str) -> dict:
    """graph.instagram.com refreshes a token in place."""
    import requests

    response = requests.get(
        "https://{}/refresh_access_token".format(cfg.INSTAGRAM_LOGIN_HOST),
        params={"grant_type": "ig_refresh_token", "access_token": token},
        timeout=60,
    )
    payload = response.json()
    if "access_token" not in payload:
        raise RefreshError("refresh failed: {}".format(str(payload)[:200]))
    return payload


def _refresh_facebook_login(token: str) -> dict:
    """Facebook long-lived user tokens exchange rather than refresh.

    This needs the app credentials, which the agent deliberately does not
    carry — the app secret is the application's master password and has no
    business in a posting pipeline. So this path reports rather than acts.
    """
    raise RefreshError(
        "Facebook Login tokens must be exchanged using the app id and secret, "
        "which this agent does not hold by design. Re-generate the long-lived "
        "token in the App Dashboard, or switch to an Instagram Login (IGAA) "
        "token, which refreshes without app credentials."
    )


def persist_to_actions(token: str, repo: Optional[str] = None) -> str:
    """Store the new token as a GitHub secret so the next run sees it."""
    repo = repo or (cfg.get_config().image_host.repo)
    if not repo:
        raise RefreshError("no repository configured to persist the token to")

    result = subprocess.run(
        ["gh", "secret", "set", SECRET_NAME, "--repo", repo],
        input=token,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RefreshError(
            "gh secret set failed: {}".format((result.stderr or result.stdout)[:200])
        )
    return "{} secret {}".format(repo, SECRET_NAME)


def refresh_if_due(
    config: Optional[cfg.Config] = None,
    state: Optional[TokenState] = None,
    persist: bool = False,
    force: bool = False,
) -> RefreshOutcome:
    config = config or cfg.get_config()
    state = state or TokenState.load()
    ig = config.instagram

    if not ig.is_configured:
        return RefreshOutcome(False, detail="Instagram is not configured")

    if not force and not state.needs_refresh("instagram"):
        days = state.days_since_refresh("instagram")
        return RefreshOutcome(
            False,
            detail="last refreshed {:.0f} days ago, next due at {}".format(
                days or 0, cfg.TOKEN_REFRESH_AFTER_DAYS
            ),
        )

    if ig.flavor == "facebook_login":
        raise RefreshError(_refresh_facebook_login.__doc__ or "unsupported flavor")

    payload = _refresh_instagram_login(ig.access_token)
    new_token = payload["access_token"]
    expires_days = payload.get("expires_in", 0) / 86400.0

    persisted = None
    if persist:
        persisted = persist_to_actions(new_token)

    state.mark_refreshed("instagram")
    state.save()

    return RefreshOutcome(
        refreshed=True,
        expires_in_days=expires_days,
        persisted_to=persisted,
        detail="new token valid for {:.0f} days".format(expires_days),
    )
