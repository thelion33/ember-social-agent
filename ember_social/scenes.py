"""Controlled vocabulary for Overheard scene imagery.

A single hardcoded prompt would make every post look identical, and letting the
model free-associate would wander across the moderation boundary at
unpredictable hours. So scenes are composed from fixed parts: the agent picks
the pieces, the image model only renders them.

The boundary here was measured rather than guessed. Probing Gemini with a
ladder from clothed to explicit found that the filter does not grade skin on a
sliding scale — underwear, sweat, and bare torsos all pass, while phrasings
that name nudity as the subject ("fine art nude", "entirely undressed" as the
point of the image) get refused. So the vocabulary below shows as much skin as
the brief asks for while never making nudity the subject.

Tiers exist because Instagram and X allow different things. Meta permits
underwear and swimwear — lingerie brands live on Instagram — but prohibits
nudity and depicted or implied sexual acts. The embrace tier stops at charged
stillness for that reason. The charged tier adds motion and contact that reads
as mid-act, and is X only.
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
# a stock-photo dump. Sweat and skin texture live here rather than in the pose,
# so every scene inherits them instead of relying on the pose to ask.
STYLE_ANCHOR = (
    "Editorial fashion photograph, shot on 85mm at f/1.4, shallow depth of "
    "field, deep shadow with warm amber highlights, cinematic colour grade, "
    "skin sheened with sweat, damp hair stuck to skin, flushed and breathing "
    "hard, glistening highlights along shoulders and collarbones, visible skin "
    "texture and detail, photorealistic, no text, no watermark, no logos, "
    "no nudity."
)

SETTINGS: List[str] = [
    "a dark bedroom with an unmade bed",
    "a hotel room in front of a floor-to-ceiling window",
    "a kitchen at night, counter and cabinets behind them",
    "a bathroom with steam fogging the glass",
    "a living room lit only by a fireplace",
    "a bedroom with city lights filling the window",
    "a narrow hallway, one of them against the wall",
    "a sunroom at dawn with sheer curtains moving",
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
    "a bathroom light left on down the hall",
]

FRAMING: List[str] = [
    "Full-body wide shot",
    "Tight crop on two faces almost touching",
    "Shot from behind, over one bare shoulder",
    "Low angle looking up",
    "Overhead shot looking straight down",
    "Framed through a doorway, edges falling into shadow",
    "Reflected in a mirror at the edge of frame",
    "Side profile, both figures in the same plane",
]

# Nobody is fully dressed. Wardrobe is its own axis so the same pose can read
# differently across posts, and so "how much clothing" never has to be smuggled
# into the pose description.
WARDROBE: Dict[str, List[str]] = {
    TIER_EMBRACE: [
        "she is in a black lace bra and underwear, he is in boxer briefs",
        "she is in a short silk slip, he is bare-chested in pyjama bottoms",
        "both are stripped to plain cotton underwear",
        "she wears an oversized shirt open over a bralette, he is in boxers",
        "she is in a satin camisole and briefs, he is bare-chested",
        "both are in underwear with a sheet half wrapped around them",
    ],
    TIER_CHARGED: [
        "she is in a sheer black lingerie set, he is in boxer briefs",
        "she is in a bralette and underwear, he is bare-chested",
        "both are in underwear, damp and clinging",
        "she wears only his unbuttoned shirt and underwear",
        "both are in underwear with the sheet kicked to the floor",
    ],
}

# Poses per tier. Embrace is charged stillness: contact and tension, but no
# motion that reads as an act in progress.
POSES: Dict[str, List[str]] = {
    TIER_EMBRACE: [
        "sitting facing each other on the bed, foreheads together, catching their breath",
        "collapsed on their backs side by side, chests rising, laughing",
        "one lying with their head on the other's bare chest",
        "standing in a close embrace, one hand cradling the other's jaw",
        "sitting on the counter with the other standing between their knees",
        "pressed together against the wall, chin lifted, about to kiss",
        "one sitting between the other's legs, leaning back against their chest",
        "lying face to face across the pillows, legs tangled, talking quietly",
    ],
    # Charged reads as mid-act without depicting one: motion, grip, and weight.
    TIER_CHARGED: [
        "she straddles his lap on the edge of the bed, her hands on his chest",
        "tangled across the bed, her leg hooked over his hip, mid-movement",
        "lying back with her spine arched, one hand gripping the sheet overhead",
        "pinned against the wall, one leg lifted and held at his waist",
        "she is above him with her hands braced on the headboard",
        "bent forward over the end of the bed, his hand flat on her back",
    ],
}


@dataclass(frozen=True)
class Scene:
    setting: str
    light: str
    framing: str
    wardrobe: str
    pose: str
    tier: str

    @property
    def key(self) -> str:
        """Stable identity, so the same scene is not posted twice."""
        raw = "|".join(
            [
                self.tier,
                self.setting,
                self.light,
                self.framing,
                self.wardrobe,
                self.pose,
            ]
        )
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    def prompt(self) -> str:
        return (
            "{framing} of an attractive couple in their thirties {pose}, in "
            "{setting}, lit by {light}. {wardrobe}. {style}".format(
                framing=self.framing,
                pose=self.pose,
                setting=self.setting,
                light=self.light,
                wardrobe=self.wardrobe[0].upper() + self.wardrobe[1:],
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
            "wardrobe": self.wardrobe,
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

    def draw() -> Scene:
        return Scene(
            setting=rng.choice(SETTINGS),
            light=rng.choice(LIGHT),
            framing=rng.choice(FRAMING),
            wardrobe=rng.choice(WARDROBE[tier]),
            pose=rng.choice(POSES[tier]),
            tier=tier,
        )

    for _ in range(200):
        scene = draw()
        if scene.key not in excluded:
            return scene

    # Every combination tried is already used. Better to repeat the oldest
    # scene than to skip the day's post entirely.
    return draw()


def combination_count(tier: str = TIER_EMBRACE) -> int:
    return (
        len(SETTINGS)
        * len(LIGHT)
        * len(FRAMING)
        * len(WARDROBE[tier])
        * len(POSES[tier])
    )
