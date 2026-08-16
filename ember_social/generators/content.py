"""Calls the model and returns validated, per-network copy.

Validation is not advisory. Copy that trips a banned phrase or leaks a term
Instagram will not tolerate is sent back to the model once with the specific
violations quoted, and if it fails again the run raises rather than publishing
something off-brand or account-threatening.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .. import brand_kit as bk
from ..journey_spec import Anatomy
from . import templates

DEFAULT_MODEL = "gpt-5.5"

LENGTH_LIMITS = {
    "eyebrow": 34,
    "headline": 62,
    "deck": 155,
    "footnote": 115,
    "alt_text": 200,
    "instagram_caption": bk.NETWORK_RULES["instagram"]["max_chars"],
    "x_caption": bk.NETWORK_RULES["x"]["max_chars"],
}


class ContentRejected(RuntimeError):
    """The model could not produce copy that satisfies the brand rules."""


@dataclass
class PostCopy:
    eyebrow: str
    headline: str
    deck: str
    footnote: str
    instagram_caption: str
    x_caption: str
    alt_text: str
    model: str = ""
    raw: Dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, str]:
        return {
            "eyebrow": self.eyebrow,
            "headline": self.headline,
            "deck": self.deck,
            "footnote": self.footnote,
            "instagram_caption": self.instagram_caption,
            "x_caption": self.x_caption,
            "alt_text": self.alt_text,
            "model": self.model,
        }


def _model_name() -> str:
    return (os.environ.get("OPENAI_MODEL") or "").strip() or DEFAULT_MODEL


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(re.escape(phrase), text, flags=re.IGNORECASE) is not None


def _contains_word(text: str, word: str) -> bool:
    return re.search(r"\b{}\b".format(re.escape(word)), text, flags=re.IGNORECASE) is not None


def validate(payload: Dict[str, str]) -> List[str]:
    """Returns a list of human-readable violations; empty means acceptable."""
    problems: List[str] = []

    for key in templates.JOURNEY_ANATOMY_SCHEMA:
        if not payload.get(key):
            problems.append("missing or empty field: {}".format(key))

    for key, limit in LENGTH_LIMITS.items():
        value = payload.get(key) or ""
        if len(value) > limit:
            problems.append(
                "{} is {} characters, limit is {}".format(key, len(value), limit)
            )

    everything = " ".join(str(v) for v in payload.values())
    for phrase in bk.BANNED_PHRASES:
        if _contains_phrase(everything, phrase):
            problems.append('banned phrase used: "{}"'.format(phrase))

    instagram_surfaces = " ".join(
        str(payload.get(k) or "")
        for k in ("eyebrow", "headline", "deck", "footnote", "instagram_caption", "alt_text")
    )
    for term in bk.INSTAGRAM_FORBIDDEN_TERMS:
        if _contains_word(instagram_surfaces, term):
            problems.append(
                'term "{}" cannot appear on Instagram surfaces'.format(term)
            )

    ig_caption = payload.get("instagram_caption") or ""
    hashtags = re.findall(r"#\w+", ig_caption)
    if len(hashtags) > bk.NETWORK_RULES["instagram"]["hashtags"]:
        problems.append(
            "{} hashtags in the Instagram caption, limit is {}".format(
                len(hashtags), bk.NETWORK_RULES["instagram"]["hashtags"]
            )
        )

    for key in ("instagram_caption", "x_caption"):
        value = payload.get(key) or ""
        if re.search(r"https?://|\bwww\.|link in bio", value, flags=re.IGNORECASE):
            problems.append("{} contains a link or link-bait phrasing".format(key))

    if re.findall(r"#\w+", payload.get("x_caption") or ""):
        problems.append("x_caption must not contain hashtags")

    return problems


def _call_model(system: str, user: str, model: str) -> Dict[str, str]:
    from openai import OpenAI

    client = OpenAI()
    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    content = response.choices[0].message.content or "{}"
    return json.loads(content)


def generate_journey_anatomy(
    anatomy: Anatomy, model: Optional[str] = None
) -> PostCopy:
    model = model or _model_name()
    system, user = templates.journey_anatomy_prompt(anatomy)

    payload = _call_model(system, user, model)
    problems = validate(payload)

    if problems:
        retry_note = (
            "\n\nYour previous response was rejected. Fix every one of these and "
            "return the corrected JSON object:\n{}".format(
                "\n".join("- {}".format(p) for p in problems)
            )
        )
        payload = _call_model(system, user + retry_note, model)
        problems = validate(payload)

    if problems:
        raise ContentRejected(
            "copy still violates the brand rules after one retry:\n{}".format(
                "\n".join("  - {}".format(p) for p in problems)
            )
        )

    return PostCopy(
        eyebrow=payload["eyebrow"],
        headline=payload["headline"],
        deck=payload["deck"],
        footnote=payload["footnote"],
        instagram_caption=payload["instagram_caption"],
        x_caption=payload["x_caption"],
        alt_text=payload["alt_text"],
        model=model,
        raw=payload,
    )
