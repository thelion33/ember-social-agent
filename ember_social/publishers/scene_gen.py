"""Generates the scene image.

Two providers sit behind one interface. Gemini is primary because it renders
photoreal intimacy that OpenAI refuses outright — the identical afterglow
prompt is blocked at input moderation by one and produced without complaint by
the other. OpenAI remains as a fallback so a Google outage degrades the imagery
rather than killing the post.

A refusal is not an error condition to crash on. Moderation reacts to the
specific combination of setting, wardrobe, and pose rather than to the tier
alone, so the first response to a refusal is a different composition, and only
after several of those is the tier surrendered. What is never acceptable is
publishing without an image or silently sending the charged tier to Instagram.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from .. import config as cfg
from .. import scenes as scene_lib

PROVIDER_GEMINI = "gemini"
PROVIDER_OPENAI = "openai"

OPENAI_MODEL = "gpt-image-2"
OPENAI_SIZE = "1024x1536"

# Moderation is sensitive to the specific combination, not just the tier — some
# compositions are refused while far bolder ones pass. With thousands of
# combinations available, rerolling beats stepping down, so try several before
# losing the tier.
ATTEMPTS_PER_TIER = 3

# Arbitrary large stride: keeps rerolls deterministic under a fixed seed while
# landing far away in the combination space.
_RESEED_STRIDE = 7919


class SceneUnavailable(RuntimeError):
    """No provider produced an image. Fail loudly rather than post without one."""


class ModerationRefusal(RuntimeError):
    """The provider declined this specific prompt."""


@dataclass
class GeneratedScene:
    scene: scene_lib.Scene
    path: Path
    model: str
    provider: str
    requested_tier: str
    refusals: List[str]

    @property
    def was_downgraded(self) -> bool:
        return self.scene.tier != self.requested_tier


def _is_moderation_refusal(exc: Exception) -> bool:
    if isinstance(exc, ModerationRefusal):
        return True
    text = str(exc).lower()
    return (
        "moderation_blocked" in text
        or "safety system" in text
        or "prohibited_content" in text
        or "image_safety" in text
    )


def _refusal_detail(exc: Exception) -> str:
    """The request id is the only part worth keeping; the prose never varies."""
    text = str(exc)
    index = text.find("req_")
    if index != -1:
        return text[index : index + 36]
    return text[:120]


# --- providers ------------------------------------------------------------


def _generate_gemini(prompt: str, model: str) -> bytes:
    import requests

    config = cfg.get_config()
    if not config.gemini.is_configured:
        raise RuntimeError("GEMINI_API_KEY is not set")

    response = requests.post(
        "{}/models/{}:generateContent".format(cfg.GEMINI_API_HOST, model),
        headers={
            "x-goog-api-key": config.gemini.api_key,
            "Content-Type": "application/json",
        },
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {"aspectRatio": cfg.GEMINI_ASPECT_RATIO},
            },
        },
        timeout=180,
    )
    if response.status_code != 200:
        raise RuntimeError(
            "gemini {}: {}".format(
                response.status_code,
                response.json().get("error", {}).get("message", response.text)[:200],
            )
        )

    payload = response.json()
    blocked = payload.get("promptFeedback", {}).get("blockReason")
    if blocked:
        raise ModerationRefusal("gemini blocked the prompt: {}".format(blocked))

    candidates = payload.get("candidates", [])
    if not candidates:
        raise ModerationRefusal("gemini returned no candidate")

    candidate = candidates[0]
    for part in candidate.get("content", {}).get("parts", []):
        inline = part.get("inlineData")
        if inline:
            return base64.b64decode(inline["data"])

    # A finishReason of PROHIBITED_CONTENT or IMAGE_SAFETY arrives here: the
    # request succeeded but the image was withheld.
    raise ModerationRefusal(
        "gemini withheld the image: {}".format(
            candidate.get("finishReason", "unknown")
        )
    )


def _generate_openai(prompt: str, model: str) -> bytes:
    from openai import OpenAI

    result = OpenAI().images.generate(
        model=model, prompt=prompt, size=OPENAI_SIZE, n=1
    )
    payload = result.data[0]
    if getattr(payload, "b64_json", None):
        return base64.b64decode(payload.b64_json)

    import requests

    return requests.get(payload.url, timeout=90).content


def _provider_chain(
    provider: str, tier: str
) -> List[Tuple[str, str, Callable[[str, str], bytes]]]:
    """(provider, model, callable) in the order they should be attempted."""
    config = cfg.get_config()
    gemini = (
        PROVIDER_GEMINI,
        config.gemini.model_for_tier(tier),
        _generate_gemini,
    )
    openai = (PROVIDER_OPENAI, OPENAI_MODEL, _generate_openai)

    if provider == PROVIDER_OPENAI:
        return [openai]
    if not config.gemini.is_configured:
        return [openai]
    return [gemini, openai]


# --- orchestration --------------------------------------------------------


def generate_scene(
    tier: str = scene_lib.TIER_EMBRACE,
    seed: Optional[int] = None,
    exclude_keys: Sequence[str] = (),
    out_dir: Optional[Path] = None,
    provider: str = PROVIDER_GEMINI,
) -> GeneratedScene:
    out_dir = cfg.ensure_dir(out_dir or (cfg.OUTPUT_DIR / "scenes"))

    refusals: List[str] = []
    blocked: List[str] = list(exclude_keys)
    current_tier: Optional[str] = tier

    while current_tier is not None:
        for provider_name, model, generate in _provider_chain(
            provider, current_tier
        ):
            for attempt in range(ATTEMPTS_PER_TIER):
                attempt_seed = (
                    None if seed is None else seed + attempt * _RESEED_STRIDE
                )
                scene = scene_lib.compose(
                    tier=current_tier, seed=attempt_seed, exclude_keys=blocked
                )
                try:
                    data = generate(scene.prompt(), model)
                except Exception as exc:  # noqa: BLE001
                    if not _is_moderation_refusal(exc):
                        # A timeout or a bad key is not a content problem;
                        # move to the next provider rather than burning
                        # attempts on a composition that was never the issue.
                        refusals.append(
                            "{} error: {}".format(provider_name, str(exc)[:150])
                        )
                        break
                    refusals.append(
                        "{}/{} refused {}: {}".format(
                            provider_name,
                            current_tier,
                            scene.key,
                            _refusal_detail(exc),
                        )
                    )
                    blocked.append(scene.key)
                    continue

                path = out_dir / "{}.png".format(scene.key)
                path.write_bytes(data)
                return GeneratedScene(
                    scene=scene,
                    path=path,
                    model=model,
                    provider=provider_name,
                    requested_tier=tier,
                    refusals=refusals,
                )

        current_tier = scene_lib.step_down(current_tier)

    raise SceneUnavailable(
        "{} attempts across every provider and tier were refused:\n{}".format(
            len(refusals), "\n".join(refusals)
        )
    )
