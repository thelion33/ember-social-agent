"""Generates the scene image, stepping down a tier when moderation refuses.

A refusal is not an error condition to crash on — it is an expected outcome at
the charged tier, and the correct response is to render the safer version and
record that it happened. What is never acceptable is publishing without an
image or silently posting the wrong tier to Instagram.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from .. import config as cfg
from .. import scenes as scene_lib

DEFAULT_MODEL = "gpt-image-2"
# Portrait, closest the API offers to Instagram's 4:5 frame. Cropped on render.
DEFAULT_SIZE = "1024x1536"


class SceneUnavailable(RuntimeError):
    """No tier produced an image. Fail loudly rather than post without one."""


@dataclass
class GeneratedScene:
    scene: scene_lib.Scene
    path: Path
    model: str
    requested_tier: str
    refusals: List[str]

    @property
    def was_downgraded(self) -> bool:
        return self.scene.tier != self.requested_tier


def _is_moderation_refusal(exc: Exception) -> bool:
    text = str(exc).lower()
    return "moderation_blocked" in text or "safety system" in text


def generate_scene(
    tier: str = scene_lib.TIER_EMBRACE,
    seed: Optional[int] = None,
    exclude_keys: Sequence[str] = (),
    out_dir: Optional[Path] = None,
    model: str = DEFAULT_MODEL,
    size: str = DEFAULT_SIZE,
) -> GeneratedScene:
    from openai import OpenAI

    client = OpenAI()
    out_dir = cfg.ensure_dir(out_dir or (cfg.OUTPUT_DIR / "scenes"))

    refusals: List[str] = []
    current_tier: Optional[str] = tier

    while current_tier is not None:
        scene = scene_lib.compose(
            tier=current_tier, seed=seed, exclude_keys=exclude_keys
        )
        try:
            result = client.images.generate(
                model=model, prompt=scene.prompt(), size=size, n=1
            )
        except Exception as exc:  # noqa: BLE001
            if not _is_moderation_refusal(exc):
                raise
            refusals.append(
                "{} tier refused: {}".format(current_tier, str(exc)[:200])
            )
            current_tier = scene_lib.step_down(current_tier)
            continue

        payload = result.data[0]
        if getattr(payload, "b64_json", None):
            data = base64.b64decode(payload.b64_json)
        else:
            import requests

            data = requests.get(payload.url, timeout=90).content

        path = out_dir / "{}.png".format(scene.key)
        path.write_bytes(data)
        return GeneratedScene(
            scene=scene,
            path=path,
            model=model,
            requested_tier=tier,
            refusals=refusals,
        )

    raise SceneUnavailable(
        "every tier was refused by moderation:\n{}".format("\n".join(refusals))
    )
