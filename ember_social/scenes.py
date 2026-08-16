"""Controlled vocabulary for Overheard scene imagery.

A single hardcoded prompt would make every post look identical, and letting the
model free-associate would wander across the moderation boundary at
unpredictable hours. So scenes are composed from fixed parts: the agent picks
the pieces, the image model only renders them.

Tiers exist because Instagram and X allow different things. Meta's Adult Nudity
and Sexual Activity policy covers *implied* and digitally-created sexual
activity, so a silhouette of an act violates it even with no skin visible. The
embrace tier is what Instagram can carry; the charged tier is X only.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

TIER_EMBRACE = "embrace"
TIER_CHARGED = "charged"
TIERS = (TIER_CHARGED, TIER_EMBRACE)

# Tier permitted per network. Instagram never receives anything above embrace.
NETWORK_MAX_TIER = {
    "instagram": TIER_EMBRACE,
    "x": TIER_CHARGED,
}

# Held constant across every scene so the grid reads as one account rather than
# a stock-photo dump.
STYLE_ANCHOR = (
    "Backlit silhouette photography, warm rust and amber rim light against deep "
    "charcoal shadow, high contrast, minimal composition, cinematic film grain, "
    "editorial poster art, faces not identifiable, no nudity, no explicit "
    "anatomy, no text, no watermark, no logos."
)

SETTINGS: List[str] = [
    "a bedroom doorway seen from a dark hallway",
    "a hotel room in front of a floor-to-ceiling window",
    "a kitchen at night, counter and cabinets behind them",
    "a bathroom with steam on the glass behind them",
    "a living room lit only by a fireplace",
    "a bedroom with city lights filling the window",
    "a narrow hallway, one of them against the wall",
    "an unmade bed under a low window",
    "a balcony at night above a lit street",
    "a stairwell landing, half in shadow",
]

LIGHT: List[str] = [
    "a single warm amber bedside lamp",
    "sunrise bleeding through thin curtains",
    "cold neon city glow from one side",
    "a cluster of candles low and to the right",
    "firelight flickering from below",
    "moonlight through half-open blinds",
    "light spilling through a door left ajar",
    "a phone screen lighting them from beneath",
]

FRAMING: List[str] = [
    "full-body silhouette, wide shot",
    "tight crop on two profiles almost touching",
    "seen from behind, over one shoulder",
    "low angle looking up",
    "overhead looking straight down",
    "framed through a doorway, edges dark",
    "reflected in a mirror at the edge of frame",
]

# Poses per tier. Embrace is Instagram-safe: contact, tension, no act implied.
POSES: Dict[str, List[str]] = {
    TIER_EMBRACE: [
        "standing in a close embrace, foreheads touching",
        "one hand cradling the other's jaw, about to kiss",
        "arms around a waist, pulled in tight from behind",
        "one lifted slightly off the floor, legs around a waist",
        "pressed against a wall, one forearm braced above the other's head",
        "lying close together under a sheet, one head on the other's chest",
        "sitting on a counter, the other standing between their knees",
        "a hand sliding along a bare shoulder, the other leaning in",
    ],
    # Charged is X-only: more heat, still silhouette and still no anatomy.
    # Deliberately conservative until the boundary is probed; anything the
    # image API refuses gets logged and the tier steps down.
    TIER_CHARGED: [
        "tangled together across an unmade bed, limbs overlapping",
        "one kneeling in front of the other, hands on their hips",
        "one bent forward over the edge of a bed, the other close behind",
        "straddling a lap in a low chair, arched back",
        "pinned against a wall, one leg lifted and held",
    ],
}


@dataclass(frozen=True)
class Scene:
    setting: str
    light: str
    framing: str
    pose: str
    tier: str

    @property
    def key(self) -> str:
        """Stable identity, so the same scene is not posted twice."""
        raw = "|".join([self.tier, self.setting, self.light, self.framing, self.pose])
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    def prompt(self) -> str:
        return (
            "{framing} of a couple {pose}, in {setting}, lit by {light}. "
            "{style}".format(
                framing=self.framing[0].upper() + self.framing[1:],
                pose=self.pose,
                setting=self.setting,
                light=self.light,
                style=STYLE_ANCHOR,
            )
        )

    def as_dict(self) -> Dict[str, str]:
        return {
            "key": self.key,
            "tier": self.tier,
            "setting": self.setting,
            "light": self.light,
            "framing": self.framing,
            "pose": self.pose,
        }


def max_tier_for(network: str) -> str:
    return NETWORK_MAX_TIER.get(network, TIER_EMBRACE)


def step_down(tier: str) -> Optional[str]:
    """The next safer tier, or None when already at the floor."""
    if tier == TIER_CHARGED:
        return TIER_EMBRACE
    return None


def compose(
    tier: str = TIER_EMBRACE,
    seed: Optional[int] = None,
    exclude_keys: Sequence[str] = (),
) -> Scene:
    """Pick a scene, avoiding anything already posted.

    Deterministic when seeded, so a run can be reproduced from its log.
    """
    import random

    if tier not in POSES:
        raise ValueError("unknown tier {!r}".format(tier))

    rng = random.Random(seed)
    excluded = set(exclude_keys)

    for _ in range(200):
        scene = Scene(
            setting=rng.choice(SETTINGS),
            light=rng.choice(LIGHT),
            framing=rng.choice(FRAMING),
            pose=rng.choice(POSES[tier]),
            tier=tier,
        )
        if scene.key not in excluded:
            return scene

    # Every combination tried is already used. Better to repeat the oldest
    # scene than to skip the day's post entirely.
    return Scene(
        setting=rng.choice(SETTINGS),
        light=rng.choice(LIGHT),
        framing=rng.choice(FRAMING),
        pose=rng.choice(POSES[tier]),
        tier=tier,
    )


def combination_count(tier: str = TIER_EMBRACE) -> int:
    return len(SETTINGS) * len(LIGHT) * len(FRAMING) * len(POSES[tier])
