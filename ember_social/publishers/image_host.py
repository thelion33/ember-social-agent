"""Puts a rendered card somewhere Instagram can fetch it.

Instagram never receives image bytes. It is handed a URL and fetches it from
the public internet with its own crawler, which means a local path, a signed
URL, or a private repository all fail — and the private-repo failure is a 404
from Meta's side that reads like a bug in the caller.

GitHub release assets are the default backend because they reuse a token that
already exists for this project. The Backend protocol is the seam: an S3 or R2
implementation only has to return a public URL from bytes.
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

from .. import config as cfg

GITHUB_API = "https://api.github.com"
GITHUB_UPLOADS = "https://uploads.github.com"


class HostingError(RuntimeError):
    """The image could not be made publicly reachable."""


class Backend(Protocol):
    def upload(self, path: Path, name: Optional[str] = None) -> str:
        """Return a public URL for the file at path."""


@dataclass
class GitHubReleaseBackend:
    """Stores assets on a release in a public repo.

    The repository must be public. Instagram's fetcher is unauthenticated, so
    a private repo yields a 404 that surfaces much later as an opaque Instagram
    error rather than as an upload failure.
    """

    token: str
    repo: str
    tag: str

    @classmethod
    def from_config(cls, config: Optional[cfg.Config] = None) -> "GitHubReleaseBackend":
        config = config or cfg.get_config()
        host = config.image_host
        if not host.is_configured:
            raise HostingError(
                "image hosting needs GITHUB_TOKEN and GH_ASSETS_REPO "
                "(stored in Actions as GH_ASSETS_TOKEN, since secret names "
                "may not begin with GITHUB_)"
            )
        return cls(token=host.token, repo=host.repo, tag=host.tag)

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": "Bearer {}".format(self.token),
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _release_id(self) -> int:
        """Find the asset release, creating it the first time."""
        import requests

        response = requests.get(
            "{}/repos/{}/releases/tags/{}".format(GITHUB_API, self.repo, self.tag),
            headers=self._headers,
            timeout=30,
        )
        if response.status_code == 200:
            return response.json()["id"]
        if response.status_code != 404:
            raise HostingError(
                "looking up release {}: HTTP {} {}".format(
                    self.tag, response.status_code, response.text[:200]
                )
            )

        created = requests.post(
            "{}/repos/{}/releases".format(GITHUB_API, self.repo),
            headers=self._headers,
            json={
                "tag_name": self.tag,
                "name": "Post assets",
                "body": "Rendered cards, published so Instagram's fetcher can "
                "reach them. Created automatically.",
            },
            timeout=30,
        )
        if created.status_code not in (200, 201):
            raise HostingError(
                "creating release {}: HTTP {} {}".format(
                    self.tag, created.status_code, created.text[:200]
                )
            )
        return created.json()["id"]

    def _delete_existing(self, release_id: int, name: str) -> None:
        """GitHub rejects a duplicate asset name rather than replacing it."""
        import requests

        response = requests.get(
            "{}/repos/{}/releases/{}/assets".format(GITHUB_API, self.repo, release_id),
            headers=self._headers,
            timeout=30,
        )
        if response.status_code != 200:
            return
        for asset in response.json():
            if asset.get("name") == name:
                requests.delete(
                    "{}/repos/{}/releases/assets/{}".format(
                        GITHUB_API, self.repo, asset["id"]
                    ),
                    headers=self._headers,
                    timeout=30,
                )

    def upload(self, path: Path, name: Optional[str] = None) -> str:
        import requests

        name = name or path.name
        release_id = self._release_id()
        self._delete_existing(release_id, name)

        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        response = requests.post(
            "{}/repos/{}/releases/{}/assets".format(
                GITHUB_UPLOADS, self.repo, release_id
            ),
            headers=dict(self._headers, **{"Content-Type": content_type}),
            params={"name": name},
            data=path.read_bytes(),
            timeout=180,
        )
        if response.status_code not in (200, 201):
            raise HostingError(
                "uploading {}: HTTP {} {}".format(
                    name, response.status_code, response.text[:200]
                )
            )

        url = response.json().get("browser_download_url")
        if not url:
            raise HostingError("upload succeeded but returned no download URL")
        return url


def is_publicly_reachable(url: str) -> bool:
    """Fetch the URL the way Instagram will: no credentials at all.

    Worth doing before handing the URL to Meta, because a private repo fails
    here with a clear 404 instead of surfacing as an inscrutable Instagram
    error several steps later.
    """
    import requests

    try:
        response = requests.get(url, timeout=30, allow_redirects=True)
    except Exception:  # noqa: BLE001
        return False
    return response.status_code == 200 and bool(response.content)


def upload(path: Path, name: Optional[str] = None, backend: Optional[Backend] = None) -> str:
    backend = backend or GitHubReleaseBackend.from_config()
    return backend.upload(path, name=name)
