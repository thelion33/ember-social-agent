# Ember social agent

Autonomous posting agent for [Ember](https://github.com/thelion33/emberapp). Runs
on a GitHub Actions schedule, generates its own images and captions, and
publishes to Instagram and X with no machine of mine switched on.

Headless by design: no simulator, no app screenshots, no browser automation
anywhere in the scheduled path.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # then fill it in
.venv/bin/python -m ember_social.agent verify
```

`.env` is gitignored and was excluded in this repository's first commit, before
any credential existed.

## Commands

| Command | What it does |
| --- | --- |
| `verify` | Checks every credential, probes each API, prints what is configured. Exits non-zero if anything required is missing. |
| `verify --no-network` | Presence checks only, no API calls. |
| `auto` | What the scheduler calls. Posts whatever the calendar says is due. |
| `preview TYPE` | Generates a post to a local file without publishing. |

## Credentials

Every value is read from `.env` locally and from repository secrets in CI. See
`.env.example` for the annotated list.

**Instagram tokens come in two flavors and the right one is detected at
runtime, not hardcoded:**

| Token shape | Host | User id must be |
| --- | --- | --- |
| Starts with `IGAA` | `graph.instagram.com` | the Instagram-scoped user id |
| Anything else | `graph.facebook.com` | the IG **Business account** id, not the Page id |

`verify` prints which flavor it detected and which base URL it will use.

Facebook Page posting is a **separate** integration (Page access token,
`/feed` endpoint) and is not built. Instagram publishing does not cover it.

### Secret naming in Actions

GitHub rejects secret names beginning with `GITHUB_`. The image-hosting token
is therefore stored as the secret **`GH_ASSETS_TOKEN`** and mapped to the
`GITHUB_TOKEN` environment variable in the workflow's `env:` block.

## Image hosting

Instagram will not read a local file; it fetches the image from a public URL.
Rendered cards are uploaded to a GitHub release asset on `GH_ASSETS_REPO`,
which **must be a public repository** — Instagram's fetcher is unauthenticated
and a private repo's asset URL returns 404. `verify` fails loudly if the
configured repo is private.

S3/R2 is supported as an alternate backend behind the same interface.

## Python versions

The code targets Python 3.9 so it runs on macOS system Python unchanged, and CI
pins 3.12. Nothing uses syntax newer than 3.9.

## Fonts

`assets/fonts/Inter-Variable.ttf` is committed to the repository along with its
`OFL.txt`, and is always preferred over any system font. A card rendered on a
Mac and on `ubuntu-latest` are therefore pixel-identical. Never reach for a
system font as a fallback.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Standard library `unittest`, no test-runner dependency.
