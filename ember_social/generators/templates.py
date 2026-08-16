"""LLM prompts, one builder per post type.

Prompts carry the facts as data and ask only for language back. The model is
never asked to invent a number that appears on a card — every figure shown is
computed from journey_spec, so a post cannot promise a structure the app does
not have.
"""

from __future__ import annotations

from typing import Dict, Tuple

from .. import brand_kit as bk
from ..journey_spec import Anatomy


def _voice_block() -> str:
    rules = "\n".join("- {}".format(rule) for rule in bk.TONE_RULES)
    banned = ", ".join('"{}"'.format(p) for p in bk.BANNED_PHRASES)
    return """VOICE
{voice}

RULES
{rules}

BANNED PHRASES — using any of these, in any form, is an automatic rejection:
{banned}
""".format(
        voice=bk.VOICE.strip(), rules=rules, banned=banned
    )


def _network_block() -> str:
    ig = bk.NETWORK_RULES["instagram"]
    x = bk.NETWORK_RULES["x"]
    forbidden = ", ".join(bk.INSTAGRAM_FORBIDDEN_TERMS)
    return """NETWORKS

instagram_caption
- Max {ig_max} characters. At most {ig_tags} lowercase hashtags, on their own
  final line.
- {ig_notes}
- These words must not appear anywhere in it: {forbidden}
- No links. No "link in bio" phrasing.

x_caption
- Max {x_max} characters, hard limit. No hashtags.
- {x_notes}
- No links of any kind. A post containing a link is billed at more than ten
  times the rate of one without, and reaches fewer people.
""".format(
        ig_max=ig["max_chars"],
        ig_tags=ig["hashtags"],
        ig_notes=ig["notes"],
        forbidden=forbidden,
        x_max=x["max_chars"],
        x_notes=x["notes"],
    )


JOURNEY_ANATOMY_SCHEMA = {
    "eyebrow": "<= 34 chars, uppercase-friendly label, no punctuation",
    "headline": "<= 62 chars, the single claim the chart proves",
    "deck": "<= 155 chars, one or two sentences under the headline",
    "footnote": "<= 115 chars, the caveat or the interesting second fact",
    "instagram_caption": "the Instagram caption",
    "x_caption": "the X caption",
    "alt_text": "<= 200 chars, plain description of the chart for screen readers",
}


def journey_anatomy_prompt(anatomy: Anatomy) -> Tuple[str, str]:
    """Returns (system_prompt, user_prompt) for the Journey Anatomy card."""

    rows = []
    for slice_ in anatomy.slices:
        rows.append(
            "- {name} (level {number}): {cards} card(s), about {minutes:.0f} min "
            "total, {share:.0f}% of the session. Typical card runs {typical}s "
            "(guideline range {lo}-{hi}s). {summary}".format(
                name=slice_.level.name,
                number=slice_.level.number,
                cards=slice_.cards,
                minutes=slice_.minutes,
                share=anatomy.share(slice_) * 100,
                typical=slice_.level.timer_typical,
                lo=slice_.level.timer_min,
                hi=slice_.level.timer_max,
                summary=slice_.level.summary,
            )
        )

    dominant = anatomy.dominant

    system = """You write for Ember, an iOS app that generates a guided intimacy journey
for a couple from their own inputs — goals, location, objects, language style,
intensity, session length — rather than handing them a fixed list.

{voice}
{networks}

POST TYPE: Journey Anatomy
Its job is to prove there is a real generative system behind the app by showing
how a journey is actually structured. It accompanies a chart of intensity over
elapsed time, with the five levels labelled along the bottom.

The post must be independently interesting to someone who will never install
anything — treat it as a piece of design writing about pacing, not an ad. Do not
tell anyone to download Ember. Do not describe the app's features. The product
is the implicit answer, never the pitch.

Return ONLY a JSON object with exactly these keys:
{schema}
""".format(
        voice=_voice_block(),
        networks=_network_block(),
        schema="\n".join(
            '  "{}": {}'.format(key, value)
            for key, value in JOURNEY_ANATOMY_SCHEMA.items()
        ),
    )

    user = """These are the real numbers for a {label} Ember journey. Every figure below is
computed from the app's own generation rules. Do not invent, round differently,
or contradict any of them.

{rows}

Totals: {cards} cards across 5 levels, about {minutes:.0f} minutes of guided
time.
The longest stretch by far is {dominant_name}, at {dominant_minutes:.0f} minutes
({dominant_share:.0f}% of the session).

The chart shows intensity rising through the levels, with each level's width
proportional to the time it occupies — so the shape makes it obvious that the
session is not five equal blocks.

Write the card text and the two captions. The most interesting thing here is
that the pacing is deliberately lopsided: the build is quick and the middle is
long. Lead with that idea, in your own words.""".format(
        label=anatomy.duration_label,
        rows="\n".join(rows),
        cards=anatomy.total_cards,
        minutes=anatomy.total_minutes,
        dominant_name=dominant.level.name,
        dominant_minutes=dominant.minutes,
        dominant_share=anatomy.share(dominant) * 100,
    )

    return system, user


PROMPT_BUILDERS: Dict[str, object] = {
    "journey_anatomy": journey_anatomy_prompt,
}
