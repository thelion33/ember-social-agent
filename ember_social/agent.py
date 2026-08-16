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


def check_bfl(config: cfg.Config, network: bool) -> CheckResult:
    """Preferred image provider on policy grounds; optional until configured."""
    bfl = config.bfl
    if not bfl.is_configured:
        return _result(
            "bfl",
            WARN,
            "BFL_API_KEY not set — falling back to Gemini, whose policy "
            "prohibits this content even though its filter permits it",
        )
    detail = "{} {} @ safety_tolerance {}".format(
        _mask(bfl.api_key), bfl.model, bfl.safety_tolerance
    )
    if not network:
        return _result("bfl", OK, "{} (not probed)".format(detail))

    try:
        import requests

        response = requests.get(
            "{}/get_result".format(cfg.BFL_API_HOST),
            headers={"x-key": bfl.api_key},
            params={"id": "credential-probe"},
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        return _result("bfl", FAIL, "request failed: {}".format(exc))

    # A bogus job id is expected to 404; what matters is that the key was not
    # rejected outright.
    if response.status_code in (401, 403):
        return _result("bfl", FAIL, "key rejected (HTTP {})".format(response.status_code))
    return _result("bfl", OK, detail)


def check_gemini(config: cfg.Config, network: bool) -> CheckResult:
    """Scene imagery. Absent, posts fall back to OpenAI silhouettes."""
    gemini = config.gemini
    if not gemini.is_configured:
        return _result(
            "gemini",
            WARN,
            "GEMINI_API_KEY not set — scenes fall back to OpenAI silhouettes",
        )

    models = "{} / {}".format(gemini.instagram_model, gemini.x_model)
    masked = _mask(gemini.api_key)
    if not network:
        return _result("gemini", OK, "{} {} (not probed)".format(masked, models))

    try:
        import requests

        response = requests.get(
            "{}/models".format(cfg.GEMINI_API_HOST),
            headers={"x-goog-api-key": gemini.api_key},
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        return _result("gemini", FAIL, "request failed: {}".format(exc))
    if response.status_code != 200:
        return _result(
            "gemini", FAIL, "HTTP {} — {}".format(response.status_code, _body(response))
        )

    available = {
        model["name"].replace("models/", "")
        for model in response.json().get("models", [])
    }
    missing = [
        name
        for name in (gemini.instagram_model, gemini.x_model)
        if name not in available
    ]
    if missing:
        return _result(
            "gemini", FAIL, "key cannot reach {}".format(", ".join(missing))
        )
    return _result("gemini", OK, "{} {}".format(masked, models))


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
        check_bfl(config, network),
        check_gemini(config, network),
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


def command_preview(args: argparse.Namespace) -> int:
    """Generate a post to a local file. Never touches a social network."""
    from datetime import datetime

    from .journey_spec import anatomy_for
    from .publishers import image_gen

    if args.post_type not in ("journey_anatomy", "overheard"):
        print(
            "Unknown post type {!r}. Available: overheard, journey_anatomy".format(
                args.post_type
            )
        )
        return 2

    cfg.load_dotenv_if_present()

    if args.post_type == "overheard":
        return _preview_overheard(args)

    anatomy = anatomy_for(args.duration)

    if args.offline:
        copy = _offline_copy(anatomy)
    else:
        from .generators import content as content_gen

        print("Generating copy…")
        copy = content_gen.generate_journey_anatomy(anatomy)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = anatomy.duration_label.replace(" ", "")
    out_dir = cfg.ensure_dir(cfg.OUTPUT_DIR)
    image_path = out_dir / "{}-{}-{}.png".format(stamp, args.post_type, slug)
    meta_path = out_dir / "{}-{}-{}.json".format(stamp, args.post_type, slug)

    image_gen.render_journey_anatomy(
        anatomy=anatomy,
        eyebrow=copy.eyebrow,
        headline=copy.headline,
        deck=copy.deck,
        footnote=copy.footnote,
        out_path=image_path,
    )

    import json

    meta = copy.as_dict()
    meta["post_type"] = args.post_type
    meta["duration"] = anatomy.duration_label
    meta["total_cards"] = anatomy.total_cards
    meta_path.write_text(json.dumps(meta, indent=2))

    print("")
    print("  image     {}".format(image_path))
    print("  metadata  {}".format(meta_path))
    print("")
    print("  headline  {}".format(copy.headline))
    print("  deck      {}".format(copy.deck))
    print("  footnote  {}".format(copy.footnote))
    print("")
    print("  instagram ({} chars)".format(len(copy.instagram_caption)))
    for line in copy.instagram_caption.splitlines():
        print("    {}".format(line))
    print("")
    print("  x ({} chars)".format(len(copy.x_caption)))
    for line in copy.x_caption.splitlines():
        print("    {}".format(line))
    print("")
    print("Nothing was published.")
    return 0


def _preview_overheard(args: argparse.Namespace) -> int:
    import json
    from datetime import datetime

    from . import scenes as scene_lib
    from .generators import content as content_gen
    from .publishers import image_gen, scene_gen

    tier = args.tier
    print("Generating scene ({} tier)…".format(tier))
    generated = scene_gen.generate_scene(tier=tier, seed=args.seed)

    for refusal in generated.refusals:
        print("  moderation: {}".format(refusal))
    if generated.was_downgraded:
        print(
            "  tier stepped down from {} to {}".format(
                generated.requested_tier, generated.scene.tier
            )
        )

    scene = generated.scene
    print("Writing the line…")
    copy = content_gen.generate_overheard(
        scene_description=scene.prompt(), tier=scene.tier
    )

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = cfg.ensure_dir(cfg.OUTPUT_DIR)
    meta_path = out_dir / "{}-overheard-{}.json".format(stamp, scene.key)

    # One composite per network: the explicit line cannot appear on the
    # Instagram card, since Meta's policy covers language as well as imagery.
    rendered = {}
    for network in ("instagram", "x"):
        path = out_dir / "{}-overheard-{}-{}.png".format(stamp, scene.key, network)
        image_gen.render_overheard(
            scene_path=generated.path,
            line=copy.line_for(network),
            out_path=path,
            attribution=copy.attribution if args.attribution else None,
        )
        rendered[network] = path

    meta = copy.as_dict()
    meta["post_type"] = "overheard"
    meta["scene"] = scene.as_dict()
    meta["scene_prompt"] = scene.prompt()
    meta["image_model"] = generated.model
    meta["refusals"] = generated.refusals
    meta["images"] = {k: str(v) for k, v in rendered.items()}
    meta_path.write_text(json.dumps(meta, indent=2))

    print("")
    print("  instagram image  {}".format(rendered["instagram"]))
    print("  x image          {}".format(rendered["x"]))
    print("  scene            {}".format(generated.path))
    print("  metadata         {}".format(meta_path))
    print("")
    print("  line (ig) {}".format(copy.line))
    print("  line (x)  {}".format(copy.line_explicit))
    print("  said by   {}".format(copy.attribution))
    print("")
    print("  instagram ({} chars)".format(len(copy.instagram_caption)))
    for line in copy.instagram_caption.splitlines():
        print("    {}".format(line))
    print("")
    print("  x ({} chars)".format(len(copy.x_caption)))
    for line in copy.x_caption.splitlines():
        print("    {}".format(line))
    print("")
    print(
        "  combinations available at this tier: {}".format(
            scene_lib.combination_count(scene.tier)
        )
    )
    print("Nothing was published.")
    return 0


def _offline_copy(anatomy):
    """Deterministic stand-in copy so the renderer can be worked on offline."""
    from .generators.content import PostCopy

    dominant = anatomy.dominant
    return PostCopy(
        eyebrow="Anatomy of a journey",
        headline="A {} session is not five equal blocks".format(anatomy.duration_label),
        deck=(
            "The build is quick. The middle is long. {} alone takes {:.0f} of the "
            "{:.0f} minutes.".format(
                dominant.level.name, dominant.minutes, anatomy.total_minutes
            )
        ),
        footnote="{} cards across five levels, timed from the app's own pacing rules.".format(
            anatomy.total_cards
        ),
        instagram_caption="(offline preview — no caption generated)",
        x_caption="(offline preview — no caption generated)",
        alt_text="Chart of intensity over time across five levels.",
        model="offline",
    )


def command_auto(args: argparse.Namespace) -> int:
    """What the scheduler calls. Runs whatever the calendar says is due."""
    from datetime import datetime

    from .generators import selection
    from .state import ExecutionLog, TokenState

    cfg.load_dotenv_if_present()
    config = cfg.get_config()

    log = ExecutionLog.load()
    if args.smoke:
        due = _smoke_entry(config.timezone, log)
    else:
        due = selection.due_now(log=log, window_hours=args.window)

    print("Ember social agent — auto")
    print("  timezone     {}".format(config.timezone))
    print("  window       last {}h".format(args.window))
    print("  log          {} previous post(s)".format(len(log.entries)))
    print("  publishing   {}".format("ENABLED" if args.publish else "disabled (dry)"))
    print("")

    _report_token_age(TokenState.load())

    if not due:
        print("Nothing due. Exiting cleanly.")
        return 0

    print("{} entr{} due:".format(len(due), "y" if len(due) == 1 else "ies"))
    for entry in due:
        print(
            "  {}  {:<16} {:<9} {}".format(
                entry.scheduled_for.strftime("%Y-%m-%d %H:%M"),
                entry.post_type,
                entry.source,
                entry.key,
            )
        )
    print("")

    if args.dry_run:
        print("Dry run — nothing generated, nothing recorded.")
        return 0

    failures = 0
    for entry in due:
        print("--- {} ---".format(entry.key))
        try:
            result = _run_entry(entry, args, log)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print("  FAILED: {}".format(exc))
            continue
        log.record(
            key=entry.key,
            post_type=entry.post_type,
            networks=result.get("networks", {}),
            scene_key=result.get("scene_key"),
            note=result.get("note"),
        )
        print("  recorded")

    log.prune()
    log.save()
    print("")
    print("Execution log written to {}".format(log.path))

    if failures:
        print("{} entr{} failed.".format(failures, "y" if failures == 1 else "ies"))
        return 1
    return 0


def _smoke_entry(timezone_name: str, log) -> list:
    """A synthetic noop due right now, for exercising the loop on a real runner.

    Keyed to the current hour rather than the current minute, so running the
    workflow twice in the same hour proves dedupe instead of posting twice.
    """
    from datetime import datetime

    from .generators import selection

    now = datetime.now(selection._zone(timezone_name)).replace(
        minute=0, second=0, microsecond=0
    )
    entry = selection.DueEntry(
        key=selection.make_key(now, "noop"),
        post_type="noop",
        scheduled_for=now,
        source="smoke",
    )
    if log.has(entry.key):
        print("Smoke entry {} already ran this hour.".format(entry.key))
        return []
    return [entry]


def command_plan(args: argparse.Namespace) -> int:
    """Show what the calendar will do next, and whether it is about to run out."""
    from datetime import date, datetime, timedelta

    from . import posting_plan
    from .generators import selection
    from .state import ExecutionLog

    cfg.load_dotenv_if_present()
    config = cfg.get_config()
    tz = selection._zone(config.timezone)
    today = datetime.now(tz).date()
    log = ExecutionLog.load()
    posted = log.keys()

    print("Posting plan ({}), next {} days".format(config.timezone, args.days))
    print("")
    for offset in range(args.days):
        on_date = today + timedelta(days=offset)
        entries = selection.candidates_for_date(
            on_date, tz, posting_plan.PLAN, posting_plan.RECURRING
        )
        for entry in entries:
            print(
                "  {}  {:<10} {:<9} {}".format(
                    entry.scheduled_for.strftime("%a %Y-%m-%d %H:%M"),
                    entry.post_type,
                    entry.source,
                    "posted" if entry.key in posted else "",
                )
            )

    print("")
    dated = [item["date"] for item in posting_plan.PLAN]
    if dated:
        last = date.fromisoformat(max(dated))
        remaining = (last - today).days
        print(
            "  dated calendar runs to {} ({} days left)".format(
                last.isoformat(), remaining
            )
        )
        if remaining < 30:
            print(
                "  regenerate soon: python tools/build_posting_plan.py --weeks 26"
            )
    else:
        print("  dated calendar is empty")
    print(
        "  recurring floor: {} rule(s), no end date".format(
            len(posting_plan.RECURRING)
        )
    )
    return 0


def _report_token_age(token_state) -> None:
    days = token_state.days_since_refresh("instagram")
    if days is None:
        print(
            "  token        Instagram token age unknown — refresh and record it "
            "before the {}-day expiry bites".format(cfg.TOKEN_LIFETIME_DAYS)
        )
    elif token_state.needs_refresh("instagram"):
        print(
            "  token        Instagram token last refreshed {:.0f} days ago — due "
            "for refresh at {} days".format(days, cfg.TOKEN_REFRESH_AFTER_DAYS)
        )
    else:
        print("  token        Instagram token refreshed {:.0f} days ago".format(days))
    print("")


def _run_entry(entry, args: argparse.Namespace, log) -> dict:
    """Execute one calendar entry. Returns metadata for the execution log."""
    if entry.post_type == "noop":
        # Exercises scheduling, dedupe, and the state commit without touching
        # a real account or spending a cent.
        print("  noop: no image, no model call, no publish")
        return {"note": "noop smoke test", "networks": {}}

    if entry.post_type == "overheard":
        return _run_overheard(args, log)

    raise ValueError("no handler for post type {!r}".format(entry.post_type))


def _run_overheard(args: argparse.Namespace, log) -> dict:
    from .generators import content as content_gen
    from .publishers import image_gen, scene_gen

    generated = scene_gen.generate_scene(
        tier=args.tier, exclude_keys=sorted(log.scene_keys())
    )
    for refusal in generated.refusals:
        print("  moderation: {}".format(refusal))

    scene = generated.scene
    copy = content_gen.generate_overheard(
        scene_description=scene.prompt(), tier=scene.tier
    )
    print("  line (ig) {}".format(copy.line))
    print("  line (x)  {}".format(copy.line_explicit))

    out_dir = cfg.ensure_dir(cfg.OUTPUT_DIR)
    rendered = {}
    for network in ("instagram", "x"):
        path = out_dir / "{}-{}.png".format(scene.key, network)
        image_gen.render_overheard(
            scene_path=generated.path, line=copy.line_for(network), out_path=path
        )
        rendered[network] = str(path)

    if not args.publish:
        print("  generated but NOT published (pass --publish to go live)")
        return {
            "scene_key": scene.key,
            "note": "dry run",
            "networks": {"generated": rendered},
        }

    raise NotImplementedError(
        "publishing is not wired up yet — Instagram and X publishers arrive "
        "with the credentials"
    )


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
    auto.add_argument(
        "--window",
        type=int,
        default=cfg.CATCHUP_WINDOW_HOURS,
        help="catch-up window in hours (default: {})".format(
            cfg.CATCHUP_WINDOW_HOURS
        ),
    )
    auto.add_argument(
        "--publish",
        action="store_true",
        help="actually publish. Without this, posts are generated but not sent.",
    )
    auto.add_argument(
        "--dry-run",
        action="store_true",
        help="show what is due and stop; generates nothing, records nothing",
    )
    auto.add_argument(
        "--tier",
        default="embrace",
        choices=["embrace", "charged"],
        help="scene explicitness ceiling",
    )
    auto.add_argument(
        "--smoke",
        action="store_true",
        help="ignore the calendar and run one noop, to exercise the loop",
    )
    auto.set_defaults(func=command_auto)

    plan = subparsers.add_parser("plan", help="show upcoming scheduled posts")
    plan.add_argument("--days", type=int, default=14, help="how far ahead to look")
    plan.set_defaults(func=command_plan)

    preview = subparsers.add_parser(
        "preview", help="generate a post to a local file without publishing"
    )
    preview.add_argument("post_type", help="which post type to render")
    preview.add_argument(
        "--duration",
        default="30 min",
        help="session length to describe (default: 30 min)",
    )
    preview.add_argument(
        "--offline",
        action="store_true",
        help="skip the model call and use placeholder copy",
    )
    preview.add_argument(
        "--tier",
        default="embrace",
        choices=["embrace", "charged"],
        help="scene explicitness; embrace is the Instagram ceiling",
    )
    preview.add_argument(
        "--seed", type=int, default=None, help="reproduce a specific scene"
    )
    preview.add_argument(
        "--attribution",
        action="store_true",
        help="print the speaker and time under the line",
    )
    preview.set_defaults(func=command_preview)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
