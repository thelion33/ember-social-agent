"""Voice, palette, typography, and the content pillars.

The palette is lifted verbatim from the iOS app's EmberTheme.swift so a card
rendered here and a screen in the app are the same product.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

RGB = Tuple[int, int, int]


def hex_to_rgb(value: str) -> RGB:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


# --- Palette (EmberTheme.swift) -------------------------------------------

BACKGROUND = hex_to_rgb("#0D0D0F")
SURFACE = hex_to_rgb("#17171A")
ELEVATED = hex_to_rgb("#202025")
ACCENT = hex_to_rgb("#C65A2E")
ACCENT_DEEP = hex_to_rgb("#8F2F24")
TEXT_PRIMARY = hex_to_rgb("#F4EFEA")
TEXT_SECONDARY = hex_to_rgb("#A7A19C")
BORDER = hex_to_rgb("#2C2C31")

# Per-level accents, also from EmberTheme.swift.
LEVEL_COLORS: Dict[str, RGB] = {
    "glow": hex_to_rgb("#D4A574"),
    "spark": hex_to_rgb("#C8813A"),
    "heat": hex_to_rgb("#C65A2E"),
    "flame": hex_to_rgb("#A83820"),
    "burn": hex_to_rgb("#8F2F24"),
}

# --- Typography -----------------------------------------------------------

# Weight axis 100-900, optical size axis 14-32 on the bundled Inter.
WEIGHT_REGULAR = 400
WEIGHT_MEDIUM = 500
WEIGHT_SEMIBOLD = 600
WEIGHT_BOLD = 700
WEIGHT_BLACK = 900

OPSZ_DISPLAY = 32
OPSZ_TEXT = 14

# --- Canvas ---------------------------------------------------------------

# Instagram portrait. The tallest frame the feed will render without cropping.
CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1350
MARGIN = 88

# --- Voice ----------------------------------------------------------------

VOICE = """
Ember sounds like a confident adult who has already thought this through.
Direct, specific, unhurried. Short declarative sentences. It states things
plainly instead of hedging, and it never giggles at its own subject matter.

It is not a therapist, not a wellness brand, and not a listicle. It does not
ask permission to talk about sex, and it does not leer either. Assume the
reader is an adult in a long relationship who is tired of being condescended
to.
"""

TONE_RULES = [
    "Lead with the specific claim. No throat-clearing preamble.",
    "Prefer a concrete number, duration, or step over an adjective.",
    "One idea per post. Do not stack three tips into one caption.",
    "Never use more than one emoji, and usually use none.",
    "Never open with a rhetorical question.",
    "Second person is fine. First person plural ('we at Ember') is not.",
    "No hashtag walls. Three at most, lowercase, at the end.",
]

# Phrases that make the account sound like every other page in the category.
BANNED_PHRASES = [
    "spice things up",
    "spice up your",
    "take it to the next level",
    "game changer",
    "game-changer",
    "unlock",
    "unleash",
    "elevate your",
    "dive in",
    "let's be real",
    "in today's world",
    "revolutionary",
    "the secret to",
    "you won't believe",
    "this one trick",
    "date night just got",
    "intimacy hack",
    "level up your love life",
    "sizzle",
    "steamy",
    "get frisky",
    "between the sheets",
    "bedroom bliss",
    "couples goals",
    "relationship goals",
]

# Never render or caption these on Instagram. X captions relax this list; see
# NETWORK_RULES below.
INSTAGRAM_FORBIDDEN_TERMS = [
    "sex",
    "sexual",
    "oral",
    "penetrative",
    "penetration",
    "orgasm",
    "climax",
    "nude",
    "naked",
    "explicit",
    "genitals",
    "foreplay",
    "arousal",
]

NETWORK_RULES = {
    "instagram": {
        "max_chars": 2200,
        "hashtags": 3,
        "allow_links": False,  # Links are dead in IG captions anyway.
        "explicit": False,
        "notes": (
            "Must survive Instagram's adult-content policy. Suggestive is fine; "
            "clinical or explicit terms are not. Imagery stays typographic or "
            "abstract — no bodies."
        ),
    },
    "x": {
        "max_chars": 280,
        "hashtags": 0,
        # A post containing a link costs $0.20 instead of $0.015 under X's
        # pay-per-use pricing, and links suppress reach. Profile link only.
        "allow_links": False,
        "explicit": True,
        "notes": (
            "Adult language is permitted with the account marked for adult "
            "content. Imagery may be suggestive — silhouettes and implied "
            "figures — but never nudity."
        ),
    },
}


@dataclass(frozen=True)
class Pillar:
    key: str
    name: str
    job: str
    description: str
    gate: str
    data_dependent: bool
    networks: List[str] = field(default_factory=lambda: ["instagram", "x"])


PILLARS: List[Pillar] = [
    Pillar(
        key="the_question",
        name="The Question",
        job="reach",
        description=(
            "A real question couples are asking this week, answered in two or "
            "three sentences of genuinely useful advice. No product mention. "
            "Useful to someone who never installs anything."
        ),
        gate=(
            "Requires a harvested thread from the last 48 hours above the "
            "engagement floor that maps to an Ember theme. If the day's harvest "
            "is off-topic or dead, the post skips."
        ),
        data_dependent=True,
    ),
    Pillar(
        key="journey_anatomy",
        name="Journey Anatomy",
        job="proves the tech",
        description=(
            "The real structure of a generated journey — pacing across the five "
            "levels, card counts, and where the time actually goes. Evidence "
            "that a generative system exists behind the app."
        ),
        gate=(
            "None. This pillar is the evergreen floor and runs when every other "
            "pillar has skipped, so the account cannot go dark."
        ),
        data_dependent=False,
    ),
    Pillar(
        key="the_blueprint",
        name="The Blueprint",
        job="planning",
        description=(
            "A concrete, executable plan anchored to something on the calendar: "
            "the coming weekend, a holiday, a season. Someone can follow it "
            "tonight without the app."
        ),
        gate=(
            "Requires a notable date within seven days, or a planning theme "
            "rising in the harvest. Otherwise it skips rather than shipping a "
            "generic date-night post."
        ),
        data_dependent=True,
    ),
    Pillar(
        key="split_the_room",
        name="Split the Room",
        job="replies",
        description=(
            "An either/or drawn from a theme where the source discussion showed "
            "real disagreement."
        ),
        gate=(
            "Requires a harvested thread whose comment-to-upvote ratio clears "
            "the contention floor. Nothing genuinely divisive means no post — a "
            "bland debate prompt earns zero replies and trains the ranker "
            "against the account."
        ),
        data_dependent=True,
    ),
]

PILLARS_BY_KEY = {pillar.key: pillar for pillar in PILLARS}
