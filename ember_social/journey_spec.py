"""The structure of an Ember journey, ported from the iOS app.

Source of truth is the app, not this file. Values here mirror:
  * JourneyLevel                     — Ember/Models/Models.swift
  * SessionDuration.cardsPerLevel    — Ember/Models/Models.swift
  * the per-level TIMERS block       — Ember/Services/JourneyGenerationService.swift

If the app changes, change these to match rather than approximating. A post
that describes a journey the user's own screen contradicts is worse than no
post.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class Level:
    key: str
    number: int
    name: str
    # Instagram-safe restatement of JourneyLevel.description. Same meaning,
    # without terms that trip the adult-content filter.
    summary: str
    # Guideline card duration in seconds, from the TIMERS block.
    timer_min: int
    timer_max: int
    # Relative intensity, used only for the shape of the pacing curve.
    intensity: float

    @property
    def timer_typical(self) -> int:
        return (self.timer_min + self.timer_max) // 2


LEVELS: List[Level] = [
    Level(
        key="glow",
        number=1,
        name="Glow",
        summary="Eye contact, slow kissing, hands over clothing. Nothing is rushed.",
        timer_min=10,
        timer_max=90,
        intensity=1.0,
    ),
    Level(
        key="spark",
        number=2,
        name="Spark",
        summary="Layers come off, one at a time. Teasing, skin, held pace.",
        timer_min=15,
        timer_max=90,
        intensity=2.0,
    ),
    Level(
        key="heat",
        number=3,
        name="Heat",
        summary="Hands, mouths, toys. Full attention on one partner at a time.",
        timer_min=90,
        timer_max=180,
        intensity=3.2,
    ),
    Level(
        key="flame",
        number=4,
        name="Flame",
        summary="Named positions with guided setup and deliberate shifts of control.",
        timer_min=120,
        timer_max=300,
        intensity=4.6,
    ),
    Level(
        key="burn",
        number=5,
        name="Burn",
        summary="The finish, then aftercare. Water, a blanket, no phones.",
        timer_min=60,
        timer_max=120,
        intensity=3.6,
    ),
]

LEVELS_BY_KEY = {level.key: level for level in LEVELS}

# SessionDuration.cardsPerLevel. Total cards is always cardsPerLevel * 5.
CARDS_PER_LEVEL: Dict[str, int] = {
    "10 min": 1,
    "20 min": 2,
    "30 min": 3,
    "45 min": 4,
    "1 hour": 5,
}

# Everything the generator can vary, for combinatorics claims.
GOAL_COUNT = 14
OBJECT_COUNT = 10  # defaultObjects excluding the "None" sentinel
LOCATION_COUNT = 9
LANGUAGE_STYLES = ("Romantic", "Playful", "Explicit")
INTENSITIES = ("Mild", "Moderate", "Intense")
EXPERIENCE_LEVELS = 5


@dataclass(frozen=True)
class LevelSlice:
    level: Level
    cards: int
    seconds: int

    @property
    def minutes(self) -> float:
        return self.seconds / 60.0


@dataclass(frozen=True)
class Anatomy:
    duration_label: str
    cards_per_level: int
    slices: List[LevelSlice]

    @property
    def total_cards(self) -> int:
        return self.cards_per_level * len(LEVELS)

    @property
    def total_seconds(self) -> int:
        return sum(s.seconds for s in self.slices)

    @property
    def total_minutes(self) -> float:
        return self.total_seconds / 60.0

    def share(self, slice_: LevelSlice) -> float:
        total = self.total_seconds
        return slice_.seconds / total if total else 0.0

    @property
    def dominant(self) -> LevelSlice:
        return max(self.slices, key=lambda s: s.seconds)


def anatomy_for(duration_label: str = "30 min") -> Anatomy:
    if duration_label not in CARDS_PER_LEVEL:
        raise ValueError(
            "unknown session duration {!r}; expected one of {}".format(
                duration_label, sorted(CARDS_PER_LEVEL)
            )
        )
    cards = CARDS_PER_LEVEL[duration_label]
    slices = [
        LevelSlice(level=level, cards=cards, seconds=cards * level.timer_typical)
        for level in LEVELS
    ]
    return Anatomy(
        duration_label=duration_label, cards_per_level=cards, slices=slices
    )


def curve_points(anatomy: Anatomy) -> List[Tuple[float, float]]:
    """Intensity against elapsed time, normalised to the unit square.

    x is cumulative time share so the curve widens where the journey actually
    spends its minutes, rather than treating every level as equally long.

    Anchors are added at both ends: a journey starts from nothing and finishes
    on aftercare, so a curve that begins and ends mid-air would misdescribe it.
    """
    total = anatomy.total_seconds or 1
    points: List[Tuple[float, float]] = []
    elapsed = 0
    peak = max(level.intensity for level in LEVELS)
    for slice_ in anatomy.slices:
        midpoint = elapsed + slice_.seconds / 2.0
        points.append((midpoint / total, slice_.level.intensity / peak))
        elapsed += slice_.seconds

    opening = (0.0, points[0][1] * 0.32)
    closing = (1.0, points[-1][1] * 0.46)
    return [opening] + points + [closing]
