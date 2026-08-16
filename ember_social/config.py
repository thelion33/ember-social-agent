"""Settings and environment reads.

Import-time work is limited to computing paths. Nothing here creates a
directory, opens a file, or touches the network, so importing this module is
safe on a runner whose filesystem looks nothing like a Mac.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = REPO_ROOT / "assets"
FONT_PATH = ASSETS_DIR / "fonts" / "Inter-Variable.ttf"
FONT_LICENSE_PATH = ASSETS_DIR / "fonts" / "OFL.txt"

# Created on demand via ensure_dir(), never at import.
OUTPUT_DIR = REPO_ROOT / "out"
STATE_DIR = REPO_ROOT / "state"
EXECUTION_LOG_PATH = STATE_DIR / "execution_log.json"
TOKEN_STATE_PATH = STATE_DIR / "token_state.json"

INSTAGRAM_LOGIN_HOST = "graph.instagram.com"
FACEBOOK_GRAPH_HOST = "graph.facebook.com"
GRAPH_API_VERSION = "v21.0"

X_API_HOST = "https://api.x.com"

# Instagram builds media containers asynchronously; publishing before the
# container reports FINISHED fails with error 9007.
CONTAINER_POLL_CEILING_SECONDS = 90
CONTAINER_POLL_INITIAL_DELAY = 2.0
CONTAINER_POLL_BACKOFF = 1.5

# Cron fires late. Anything due within this many hours that has not already
# posted is still considered due.
CATCHUP_WINDOW_HOURS = 3

# Instagram long-lived tokens expire after 60 days, silently. Refresh well
# before that and persist the result outside the ephemeral runner filesystem.
TOKEN_REFRESH_AFTER_DAYS = 30
TOKEN_LIFETIME_DAYS = 60


def _env(name: str) -> Optional[str]:
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def load_dotenv_if_present() -> bool:
    """Load .env into os.environ. Returns True if a file was found.

    Called explicitly by the CLI rather than at import so that tests and CI
    (where values come from the real environment) are unaffected.
    """
    dotenv_path = REPO_ROOT / ".env"
    if not dotenv_path.exists():
        return False
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    load_dotenv(dotenv_path, override=False)
    return True


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(frozen=True)
class InstagramConfig:
    access_token: Optional[str]
    user_id: Optional[str]

    @property
    def flavor(self) -> str:
        """Which Meta login produced this token.

        Instagram Login tokens begin with "IGAA" and speak to
        graph.instagram.com. Anything else is treated as a Facebook Login
        token and routed to graph.facebook.com, where the user id must be the
        IG Business account id rather than the Page id.
        """
        if not self.access_token:
            return "unconfigured"
        if self.access_token.startswith("IGAA"):
            return "instagram_login"
        return "facebook_login"

    @property
    def host(self) -> str:
        if self.flavor == "instagram_login":
            return INSTAGRAM_LOGIN_HOST
        return FACEBOOK_GRAPH_HOST

    @property
    def base_url(self) -> str:
        if self.flavor == "instagram_login":
            # graph.instagram.com is unversioned for the Login flavor.
            return "https://{}".format(INSTAGRAM_LOGIN_HOST)
        return "https://{}/{}".format(FACEBOOK_GRAPH_HOST, GRAPH_API_VERSION)

    @property
    def is_configured(self) -> bool:
        return bool(self.access_token and self.user_id)


@dataclass(frozen=True)
class XConfig:
    api_key: Optional[str]
    api_secret: Optional[str]
    access_token: Optional[str]
    access_token_secret: Optional[str]
    bearer_token: Optional[str]

    @property
    def is_configured(self) -> bool:
        """True only with a full OAuth 1.0a user-context credential set.

        The bearer token alone is app-only: it can read, but it cannot upload
        media or post, so it does not count toward being configured.
        """
        return all(
            [
                self.api_key,
                self.api_secret,
                self.access_token,
                self.access_token_secret,
            ]
        )


@dataclass(frozen=True)
class ImageHostConfig:
    token: Optional[str]
    repo: Optional[str]
    tag: str

    @property
    def is_configured(self) -> bool:
        return bool(self.token and self.repo)


@dataclass(frozen=True)
class Config:
    openai_api_key: Optional[str]
    instagram: InstagramConfig
    x: XConfig
    image_host: ImageHostConfig
    timezone: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            openai_api_key=_env("OPENAI_API_KEY"),
            instagram=InstagramConfig(
                access_token=_env("INSTAGRAM_ACCESS_TOKEN"),
                user_id=_env("INSTAGRAM_USER_ID"),
            ),
            x=XConfig(
                api_key=_env("X_API_KEY"),
                api_secret=_env("X_API_SECRET"),
                access_token=_env("X_ACCESS_TOKEN"),
                access_token_secret=_env("X_ACCESS_TOKEN_SECRET"),
                bearer_token=_env("X_BEARER_TOKEN"),
            ),
            image_host=ImageHostConfig(
                token=_env("GITHUB_TOKEN"),
                repo=_env("GH_ASSETS_REPO"),
                tag=_env("GH_ASSETS_TAG") or "post-assets",
            ),
            timezone=_env("TIMEZONE") or "America/New_York",
        )


def get_config() -> Config:
    return Config.from_env()
