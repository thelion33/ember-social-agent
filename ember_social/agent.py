"""CLI entry point.

    python -m ember_social.agent verify
    python -m ember_social.agent auto          # what the scheduler calls
    python -m ember_social.agent preview TYPE  # generate without publishing
"""

from __future__ import annotations

import argparse
import io
import sys
from dataclasses import dataclass
from typing import Callable, List, Optional

from . import config as cfg

OK = "ok"
WARN = "warn"
FAIL = "fail"

_GLYPHS = {OK: "PASS", WARN: "WARN", FAIL: "FAIL"}


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str

    @property
    def is_blocking(self) -> bool:
        return self.status == FAIL


def _result(name: str, status: str, detail: str) -> CheckResult:
    return CheckResult(name=name, status=status, detail=detail)


# --------------------------------------------------------------------------
# Individual checks
# --------------------------------------------------------------------------


def check_runtime() -> CheckResult:
    version = "{}.{}.{}".format(*sys.version_info[:3])
    if sys.version_info < (3, 9):
        return _result("python", FAIL, "{} — 3.9 or newer required".format(version))
    return _result("python", OK, version)


def check_timezone(config: cfg.Config) -> CheckResult:
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(config.timezone)
    except Exception as exc:  # noqa: BLE001 - report any tz failure verbatim
        return _result(
            "timezone", FAIL, "{!r} is not usable: {}".format(config.timezone, exc)
        )
    return _result("timezone", OK, config.timezone)


def check_font() -> CheckResult:
    if not cfg.FONT_PATH.exists():
        return _result("font", FAIL, "missing {}".format(cfg.FONT_PATH))
    if not cfg.FONT_LICENSE_PATH.exists():
        return _result("font", FAIL, "bundled font has no OFL.txt alongside it")
    try:
        from PIL import ImageFont

        ImageFont.truetype(str(cfg.FONT_PATH), 24)
    except Exception as exc:  # noqa: BLE001
        return _result("font", FAIL, "Pillow could not load it: {}".format(exc))
    size_kb = cfg.FONT_PATH.stat().st_size // 1024
    return _result("font", OK, "{} ({} KB, OFL)".format(cfg.FONT_PATH.name, size_kb))


def check_openai(config: cfg.Config, network: bool) -> CheckResult:
    if not config.openai_api_key:
        return _result("openai", FAIL, "OPENAI_API_KEY not set")
    masked = _mask(config.openai_api_key)
    if not network:
        return _result("openai", OK, "{} (not probed)".format(masked))
    try:
        import requests

        response = requests.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": "Bearer {}".format(config.openai_api_key)},
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        return _result("openai", FAIL, "request failed: {}".format(exc))
    if response.status_code != 200:
        return _result(
            "openai", FAIL, "HTTP {} — {}".format(response.status_code, _body(response))
        )
    return _result("openai", OK, "{} authenticated".format(masked))


def check_instagram(config: cfg.Config, network: bool) -> CheckResult:
    ig = config.instagram
    if not ig.access_token:
        return _result("instagram", FAIL, "INSTAGRAM_ACCESS_TOKEN not set")
    if not ig.user_id:
        return _result("instagram", FAIL, "INSTAGRAM_USER_ID not set")

    flavor_label = {
        "instagram_login": "Instagram Login (token starts with IGAA)",
        "facebook_login": "Facebook Login (user id must be the IG Business "
        "account id, not the Page id)",
    }[ig.flavor]
    routing = "{} -> {}".format(flavor_label, ig.base_url)

    if not network:
        return _result("instagram", OK, "{} (not probed)".format(routing))

    try:
        import requests

        if ig.flavor == "instagram_login":
            url = "{}/me".format(ig.base_url)
        else:
            url = "{}/{}".format(ig.base_url, ig.user_id)
        response = requests.get(
            url,
            params={
                "fields": "id,username",
                "access_token": ig.access_token,
            },
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        return _result("instagram", FAIL, "{} — request failed: {}".format(routing, exc))

    if response.status_code != 200:
        return _result(
            "instagram",
            FAIL,
            "{} — HTTP {}: {}".format(routing, response.status_code, _body(response)),
        )

    payload = response.json()
    handle = payload.get("username")
    resolved_id = payload.get("id")
    if resolved_id and ig.user_id and str(resolved_id) != str(ig.user_id):
        return _result(
            "instagram",
            WARN,
            "{} — token resolves to id {} but INSTAGRAM_USER_ID is {}".format(
                routing, resolved_id, ig.user_id
            ),
        )
    return _result(
        "instagram",
        OK,
        "{} — @{}".format(routing, handle or "handle unavailable"),
    )


def check_x(config: cfg.Config, network: bool) -> CheckResult:
    x = config.x
    if not x.is_configured:
        return _result(
            "x",
            WARN,
            "not configured — X publishing disabled, Instagram still runs",
        )
    if not network:
        return _result("x", OK, "OAuth 1.0a credentials present (not probed)")

    try:
        from requests_oauthlib import OAuth1Session
    except ImportError:
        return _result("x", FAIL, "requests-oauthlib not installed")

    session = OAuth1Session(
        client_key=x.api_key,
        client_secret=x.api_secret,
        resource_owner_key=x.access_token,
        resource_owner_secret=x.access_token_secret,
    )

    try:
        identity = session.get(
            "{}/2/users/me".format(cfg.X_API_HOST), timeout=20
        )
    except Exception as exc:  # noqa: BLE001
        return _result("x", FAIL, "request failed: {}".format(exc))

    if identity.status_code != 200:
        return _result(
            "x",
            FAIL,
            "identity HTTP {}: {}".format(identity.status_code, _body(identity)),
        )
    handle = identity.json().get("data", {}).get("username", "unknown")

    media_status, media_detail = _probe_x_media_upload(session)
    return _result("x", media_status, "@{} — {}".format(handle, media_detail))


def _probe_x_media_upload(session) -> "tuple":
    """Actually attempt a media upload.

    X's v1.1 upload endpoint was retired in March 2025 and the v2 replacement
    documents OAuth 2.0 with a media.write scope, while still listing OAuth 1.0a
    as an accepted scheme. Rather than guess which applies to this account, we
    upload a 1x1 PNG and report the answer. Media that is never attached to a
    post simply expires.
    """
    try:
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (1, 1), (0, 0, 0)).save(buffer, format="PNG")
        buffer.seek(0)
    except Exception as exc:  # noqa: BLE001
        return WARN, "could not build probe image: {}".format(exc)

    try:
        response = session.post(
            "{}/2/media/upload".format(cfg.X_API_HOST),
            files={"media": ("probe.png", buffer.getvalue(), "image/png")},
            data={"media_category": "tweet_image"},
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        return WARN, "media upload probe failed: {}".format(exc)

    if response.status_code in (200, 201):
        return OK, "media upload works over OAuth 1.0a"
    if response.status_code in (401, 403):
        return (
            WARN,
            "media upload rejected with HTTP {} — this account likely needs "
            "OAuth 2.0 with the media.write scope. Text-only posting may still "
            "work. Response: {}".format(response.status_code, _body(response)),
        )
    return (
        WARN,
        "media upload probe returned HTTP {}: {}".format(
            response.status_code, _body(response)
        ),
    )


def check_image_host(config: cfg.Config, network: bool) -> CheckResult:
    host = config.image_host
    if not host.token:
        return _result(
            "image host",
            FAIL,
            "GITHUB_TOKEN not set (stored in Actions as GH_ASSETS_TOKEN — "
            "secret names cannot begin with GITHUB_)",
        )
    if not host.repo:
        return _result("image host", FAIL, "GH_ASSETS_REPO not set")

    summary = "{} @ tag {}".format(host.repo, host.tag)
    if not network:
        return _result("image host", OK, "{} (not probed)".format(summary))

    try:
        import requests

        response = requests.get(
            "https://api.github.com/repos/{}".format(host.repo),
            headers={
                "Authorization": "Bearer {}".format(host.token),
                "Accept": "application/vnd.github+json",
            },
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        return _result("image host", FAIL, "request failed: {}".format(exc))

    if response.status_code == 404:
        return _result(
            "image host", FAIL, "{} not found, or the token cannot see it".format(summary)
        )
    if response.status_code != 200:
        return _result(
            "image host",
            FAIL,
            "HTTP {}: {}".format(response.status_code, _body(response)),
        )

    repo = response.json()
    if repo.get("private"):
        return _result(
            "image host",
            FAIL,
            "{} is PRIVATE — Instagram fetches image URLs unauthenticated and "
            "will 404".format(summary),
        )
    return _result("image host", OK, "{} (public)".format(summary))


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _mask(secret: str) -> str:
    if len(secret) <= 8:
        return "*" * len(secret)
    return "{}…{}".format(secret[:4], secret[-4:])


def _body(response, limit: int = 180) -> str:
    text = (response.text or "").strip().replace("\n", " ")
    return text[:limit]


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def command_verify(args: argparse.Namespace) -> int:
    found_dotenv = cfg.load_dotenv_if_present()
    config = cfg.get_config()
    network = not args.no_network

    print("Ember social agent — credential and environment check")
    print("  source: {}".format(".env + environment" if found_dotenv else "environment only"))
    print("  probes: {}".format("live" if network else "skipped (--no-network)"))
    print("")

    results: List[CheckResult] = [
        check_runtime(),
        check_timezone(config),
        check_font(),
        check_openai(config, network),
        check_instagram(config, network),
        check_x(config, network),
        check_image_host(config, network),
    ]

    width = max(len(r.name) for r in results)
    for result in results:
        print(
            "  [{}] {}  {}".format(
                _GLYPHS[result.status], result.name.ljust(width), result.detail
            )
        )

    print("")
    blocking = [r for r in results if r.is_blocking]
    warnings = [r for r in results if r.status == WARN]
    if blocking:
        print(
            "{} blocking problem(s), {} warning(s). Not ready to run.".format(
                len(blocking), len(warnings)
            )
        )
        return 1
    print("All required checks passed ({} warning(s)).".format(len(warnings)))
    return 0


def _not_yet(step: str) -> Callable[[argparse.Namespace], int]:
    def run(args: argparse.Namespace) -> int:
        print("Not implemented yet — arrives in build step {}.".format(step))
        return 2

    return run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ember-social", description="Autonomous social posting agent for Ember."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify = subparsers.add_parser(
        "verify", help="check every credential and print what is configured"
    )
    verify.add_argument(
        "--no-network",
        action="store_true",
        help="only check that values are present; make no API calls",
    )
    verify.set_defaults(func=command_verify)

    auto = subparsers.add_parser("auto", help="run whatever the calendar says is due")
    auto.set_defaults(func=_not_yet("5"))

    preview = subparsers.add_parser(
        "preview", help="generate a post to a local file without publishing"
    )
    preview.add_argument("post_type", help="which post type to render")
    preview.set_defaults(func=_not_yet("2"))

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
