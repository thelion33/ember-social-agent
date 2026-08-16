"""Publishes a card to Instagram.

Publishing is two calls with a wait in between, and the wait is the part that
bites. Creating a media container returns an id immediately, but the container
is not ready: Instagram is still fetching the image from the public URL. Call
media_publish too early and it fails with error 9007, "Media ID is not
available". So the container's status_code is polled until FINISHED, with
backoff and a ceiling, before publishing is attempted.

Both Meta login flavors are supported and detected from the token itself, so
neither host is hardcoded.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .. import config as cfg

# Instagram's own error for publishing a container that is not ready yet.
ERROR_MEDIA_NOT_AVAILABLE = 9007


class InstagramError(RuntimeError):
    """Publishing failed. Never falls back to anything; fails loudly."""


class ContainerNotReady(InstagramError):
    """The container never reached FINISHED inside the ceiling."""


@dataclass
class PublishResult:
    media_id: str
    container_id: str
    permalink: Optional[str] = None
    polls: int = 0
    waited_seconds: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)


def _request(method: str, url: str, **kwargs) -> Dict[str, Any]:
    import requests

    response = requests.request(method, url, timeout=60, **kwargs)
    try:
        payload = response.json()
    except ValueError:
        raise InstagramError(
            "non-JSON response from {}: HTTP {} {}".format(
                url, response.status_code, response.text[:200]
            )
        )

    error = payload.get("error")
    if error:
        raise InstagramError(
            "{} (code {}, subcode {}): {}".format(
                error.get("type", "error"),
                error.get("code"),
                error.get("error_subcode"),
                error.get("message", ""),
            )
        )
    if response.status_code >= 400:
        raise InstagramError(
            "HTTP {} from {}: {}".format(response.status_code, url, str(payload)[:200])
        )
    return payload


@dataclass
class InstagramPublisher:
    access_token: str
    user_id: str
    base_url: str

    @classmethod
    def from_config(cls, config: Optional[cfg.Config] = None) -> "InstagramPublisher":
        config = config or cfg.get_config()
        ig = config.instagram
        if not ig.is_configured:
            raise InstagramError(
                "INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_USER_ID must both be set"
            )
        return cls(
            access_token=ig.access_token,
            user_id=ig.user_id,
            base_url=ig.base_url,
        )

    def _url(self, path: str) -> str:
        return "{}/{}".format(self.base_url.rstrip("/"), path.lstrip("/"))

    # -- account -------------------------------------------------------

    def whoami(self) -> Dict[str, Any]:
        """Confirms the token and id actually address the intended account."""
        return _request(
            "GET",
            self._url(self.user_id),
            params={
                "fields": "id,username,account_type",
                "access_token": self.access_token,
            },
        )

    # -- publishing ----------------------------------------------------

    def create_container(
        self, image_url: str, caption: str, alt_text: Optional[str] = None
    ) -> str:
        params = {
            "image_url": image_url,
            "caption": caption,
            "access_token": self.access_token,
        }
        if alt_text:
            params["alt_text"] = alt_text
        payload = _request("POST", self._url("{}/media".format(self.user_id)), params=params)
        container_id = payload.get("id")
        if not container_id:
            raise InstagramError("container creation returned no id: {}".format(payload))
        return container_id

    def container_status(self, container_id: str) -> Dict[str, Any]:
        return _request(
            "GET",
            self._url(container_id),
            params={
                "fields": "status_code,status",
                "access_token": self.access_token,
            },
        )

    def wait_for_container(
        self,
        container_id: str,
        ceiling_seconds: float = cfg.CONTAINER_POLL_CEILING_SECONDS,
        initial_delay: float = cfg.CONTAINER_POLL_INITIAL_DELAY,
        backoff: float = cfg.CONTAINER_POLL_BACKOFF,
        sleep=time.sleep,
        now=time.monotonic,
    ) -> Dict[str, Any]:
        """Poll until FINISHED. Anything else is a failure, not a maybe.

        ERROR and EXPIRED are terminal and must not be waited out — publishing
        an errored container just produces a confusing 9007 much later.
        """
        started = now()
        delay = initial_delay
        polls = 0

        while True:
            polls += 1
            status = self.container_status(container_id)
            code = status.get("status_code")

            if code == "FINISHED":
                status["_polls"] = polls
                status["_waited"] = now() - started
                return status
            if code in ("ERROR", "EXPIRED"):
                raise InstagramError(
                    "container {} reported {}: {}".format(
                        container_id, code, status.get("status", "")
                    )
                )

            elapsed = now() - started
            if elapsed + delay > ceiling_seconds:
                raise ContainerNotReady(
                    "container {} still {} after {:.0f}s and {} polls".format(
                        container_id, code or "unknown", elapsed, polls
                    )
                )
            sleep(delay)
            delay *= backoff

    def publish_container(self, container_id: str) -> Dict[str, Any]:
        return _request(
            "POST",
            self._url("{}/media_publish".format(self.user_id)),
            params={
                "creation_id": container_id,
                "access_token": self.access_token,
            },
        )

    def permalink(self, media_id: str) -> Optional[str]:
        try:
            payload = _request(
                "GET",
                self._url(media_id),
                params={"fields": "permalink", "access_token": self.access_token},
            )
        except InstagramError:
            # A missing permalink is cosmetic; the post already exists.
            return None
        return payload.get("permalink")

    def publish(
        self,
        image_url: str,
        caption: str,
        alt_text: Optional[str] = None,
        sleep=time.sleep,
    ) -> PublishResult:
        container_id = self.create_container(image_url, caption, alt_text=alt_text)
        status = self.wait_for_container(container_id, sleep=sleep)
        published = self.publish_container(container_id)

        media_id = published.get("id")
        if not media_id:
            raise InstagramError("publish returned no media id: {}".format(published))

        return PublishResult(
            media_id=media_id,
            container_id=container_id,
            permalink=self.permalink(media_id),
            polls=status.get("_polls", 0),
            waited_seconds=status.get("_waited", 0.0),
            raw=published,
        )
