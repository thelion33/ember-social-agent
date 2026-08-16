"""Card rendering with Pillow.

Everything is drawn at SUPERSAMPLE times the final size and downsampled once at
the end, which is what keeps the curve edges clean without any antialiasing
tricks. The bundled variable font is always used; a system font is never an
acceptable fallback because it would make a Mac render and a runner render
different images.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from .. import brand_kit as bk
from .. import config as cfg
from ..journey_spec import Anatomy, curve_points

SUPERSAMPLE = 2

RGB = Tuple[int, int, int]


class FontUnavailable(RuntimeError):
    """Raised instead of silently substituting a system font."""


def load_font(size: int, weight: int = bk.WEIGHT_REGULAR, opsz: Optional[int] = None):
    if not cfg.FONT_PATH.exists():
        raise FontUnavailable(
            "bundled font missing at {} — refusing to fall back to a system "
            "font".format(cfg.FONT_PATH)
        )
    font = ImageFont.truetype(str(cfg.FONT_PATH), size)
    if opsz is None:
        opsz = bk.OPSZ_DISPLAY if size >= 48 else bk.OPSZ_TEXT
    try:
        font.set_variation_by_axes([float(opsz), float(weight)])
    except Exception as exc:  # noqa: BLE001
        raise FontUnavailable(
            "Pillow could not set variation axes on the bundled font: "
            "{}".format(exc)
        )
    return font


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _lerp_rgb(a: RGB, b: RGB, t: float) -> RGB:
    return (
        int(round(_lerp(a[0], b[0], t))),
        int(round(_lerp(a[1], b[1], t))),
        int(round(_lerp(a[2], b[2], t))),
    )


def _cosine_curve(points: Sequence[Tuple[float, float]], samples: int) -> List[float]:
    """Sample a smooth y(x) through the control points.

    Cosine interpolation rather than a spline: it stays inside the range of its
    control points, so the curve never overshoots above the peak intensity and
    invents a level that does not exist.
    """
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    out: List[float] = []
    for i in range(samples):
        x = i / float(samples - 1)
        if x <= xs[0]:
            out.append(ys[0])
            continue
        if x >= xs[-1]:
            out.append(ys[-1])
            continue
        for j in range(len(xs) - 1):
            if xs[j] <= x <= xs[j + 1]:
                span = xs[j + 1] - xs[j]
                t = 0.0 if span == 0 else (x - xs[j]) / span
                smooth = (1 - math.cos(t * math.pi)) / 2.0
                out.append(_lerp(ys[j], ys[j + 1], smooth))
                break
    return out


def _draw_tracked_text(
    draw: ImageDraw.ImageDraw,
    xy: Tuple[int, int],
    text: str,
    font,
    fill: RGB,
    tracking: float = 0.0,
) -> None:
    x, y = xy
    for char in text:
        draw.text((x, y), char, font=font, fill=fill)
        x += draw.textlength(char, font=font) + tracking


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> List[str]:
    words = text.split()
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = "{} {}".format(current, word).strip()
        if current and draw.textlength(candidate, font=font) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _level_gradient(width: int, height: int, anatomy: Anatomy) -> Image.Image:
    """Horizontal gradient tracking the level colours across elapsed time."""
    gradient = Image.new("RGB", (width, height), bk.BACKGROUND)
    draw = ImageDraw.Draw(gradient)

    total = anatomy.total_seconds or 1
    anchors: List[Tuple[float, RGB]] = []
    elapsed = 0
    for slice_ in anatomy.slices:
        midpoint = (elapsed + slice_.seconds / 2.0) / total
        anchors.append((midpoint, bk.LEVEL_COLORS[slice_.level.key]))
        elapsed += slice_.seconds

    for px in range(width):
        pos = px / float(max(width - 1, 1))
        if pos <= anchors[0][0]:
            color = anchors[0][1]
        elif pos >= anchors[-1][0]:
            color = anchors[-1][1]
        else:
            color = anchors[-1][1]
            for i in range(len(anchors) - 1):
                left, right = anchors[i], anchors[i + 1]
                if left[0] <= pos <= right[0]:
                    span = right[0] - left[0]
                    t = 0.0 if span == 0 else (pos - left[0]) / span
                    color = _lerp_rgb(left[1], right[1], t)
                    break
        draw.line([(px, 0), (px, height)], fill=color)
    return gradient


def _color_at(anatomy: Anatomy, pos: float) -> RGB:
    total = anatomy.total_seconds or 1
    elapsed = 0
    for slice_ in anatomy.slices:
        share = slice_.seconds / total
        if pos <= (elapsed / total) + share:
            return bk.LEVEL_COLORS[slice_.level.key]
        elapsed += slice_.seconds
    return bk.LEVEL_COLORS[anatomy.slices[-1].level.key]


def _layout_level_labels(
    draw: ImageDraw.ImageDraw,
    anatomy: Anatomy,
    chart_left: int,
    chart_width: int,
    scale: int,
    label_font,
    meta_font,
) -> List[dict]:
    """Position each level label, staggering any that would collide.

    Short levels occupy segments narrower than their own name, so on a 10 min
    journey Glow and Spark sit almost on top of each other. Colliding labels
    drop to a second row instead of overlapping.
    """
    total = anatomy.total_seconds or 1
    gap = int(18 * scale)

    labels: List[dict] = []
    elapsed = 0
    for slice_ in anatomy.slices:
        start = chart_left + int(chart_width * elapsed / total)
        elapsed += slice_.seconds
        end = chart_left + int(chart_width * elapsed / total)
        minutes = slice_.minutes
        meta = "{:.0f} min".format(minutes) if minutes >= 1 else "<1 min"
        labels.append(
            {
                "slice": slice_,
                "meta": meta,
                "start": start,
                "end": end,
                "centre": (start + end) // 2,
                "width": max(
                    draw.textlength(slice_.level.name, font=label_font),
                    draw.textlength(meta, font=meta_font),
                ),
            }
        )

    row_right_edge = {0: chart_left - gap, 1: chart_left - gap}
    for label in labels:
        left = label["centre"] - label["width"] / 2
        row = 0 if left >= row_right_edge[0] + gap else 1
        label["row"] = row
        row_right_edge[row] = label["centre"] + label["width"] / 2

    return labels


def render_journey_anatomy(
    anatomy: Anatomy,
    eyebrow: str,
    headline: str,
    deck: str,
    footnote: str,
    out_path: Path,
) -> Path:
    scale = SUPERSAMPLE
    width = bk.CANVAS_WIDTH * scale
    height = bk.CANVAS_HEIGHT * scale
    margin = bk.MARGIN * scale

    image = Image.new("RGB", (width, height), bk.BACKGROUND)
    draw = ImageDraw.Draw(image)

    content_width = width - 2 * margin

    # --- Eyebrow ----------------------------------------------------------
    eyebrow_font = load_font(22 * scale, bk.WEIGHT_SEMIBOLD, opsz=14)
    y = margin
    _draw_tracked_text(
        draw,
        (margin, y),
        eyebrow.upper(),
        eyebrow_font,
        bk.ACCENT,
        tracking=4.0 * scale,
    )
    y += int(58 * scale)

    # --- Headline ---------------------------------------------------------
    headline_font = load_font(72 * scale, bk.WEIGHT_BOLD, opsz=32)
    for line in _wrap(draw, headline, headline_font, content_width):
        draw.text((margin, y), line, font=headline_font, fill=bk.TEXT_PRIMARY)
        y += int(86 * scale)

    # --- Deck -------------------------------------------------------------
    y += int(18 * scale)
    deck_font = load_font(30 * scale, bk.WEIGHT_REGULAR, opsz=18)
    for line in _wrap(draw, deck, deck_font, content_width):
        draw.text((margin, y), line, font=deck_font, fill=bk.TEXT_SECONDARY)
        y += int(44 * scale)

    # --- Chart ------------------------------------------------------------
    chart_left = margin
    chart_right = width - margin
    chart_width = chart_right - chart_left
    label_font = load_font(26 * scale, bk.WEIGHT_SEMIBOLD, opsz=16)
    meta_font = load_font(22 * scale, bk.WEIGHT_REGULAR, opsz=14)
    labels = _layout_level_labels(
        draw, anatomy, chart_left, chart_width, scale, label_font, meta_font
    )

    # Label rows are resolved before the chart is sized: a staggered second row
    # has to take its space from the chart, not from the footnote.
    row_height = int(78 * scale)
    rows_used = max(label["row"] for label in labels) + 1
    footnote_top = height - margin - int(96 * scale)
    chart_bottom = footnote_top - rows_used * row_height - int(30 * scale)
    chart_top = chart_bottom - int(470 * scale)
    chart_height = chart_bottom - chart_top

    axis_font = load_font(19 * scale, bk.WEIGHT_SEMIBOLD, opsz=14)
    _draw_tracked_text(
        draw,
        (chart_left, chart_top - int(46 * scale)),
        "INTENSITY",
        axis_font,
        bk.BORDER,
        tracking=3.0 * scale,
    )
    elapsed_label = "ELAPSED TIME"
    elapsed_width = sum(
        draw.textlength(c, font=axis_font) for c in elapsed_label
    ) + 3.0 * scale * (len(elapsed_label) - 1)
    _draw_tracked_text(
        draw,
        (int(chart_right - elapsed_width), chart_top - int(46 * scale)),
        elapsed_label,
        axis_font,
        bk.BORDER,
        tracking=3.0 * scale,
    )

    samples = chart_width
    ys = _cosine_curve(curve_points(anatomy), samples)

    gradient = _level_gradient(chart_width, chart_height, anatomy)

    # Alpha mask: solid just under the curve, fading toward the baseline so the
    # fill reads as depth rather than a block of colour.
    mask = Image.new("L", (chart_width, chart_height), 0)
    mask_draw = ImageDraw.Draw(mask)
    for px in range(chart_width):
        curve_y = int(round((1.0 - ys[px]) * (chart_height - 1)))
        depth = chart_height - curve_y
        if depth <= 0:
            continue
        steps = 48
        for step in range(steps):
            y0 = curve_y + int(depth * step / steps)
            y1 = curve_y + int(depth * (step + 1) / steps)
            alpha = int(_lerp(190, 18, step / float(steps - 1)))
            mask_draw.line([(px, y0), (px, y1)], fill=alpha)

    image.paste(gradient, (chart_left, chart_top), mask)

    # Baseline.
    draw.line(
        [(chart_left, chart_bottom), (chart_right, chart_bottom)],
        fill=bk.BORDER,
        width=max(1, int(2 * scale)),
    )

    # The curve itself, coloured by the level it is passing through.
    stroke = int(5 * scale)
    for px in range(chart_width):
        pos = px / float(max(chart_width - 1, 1))
        curve_y = chart_top + int(round((1.0 - ys[px]) * (chart_height - 1)))
        draw.line(
            [
                (chart_left + px, curve_y - stroke // 2),
                (chart_left + px, curve_y + stroke // 2),
            ],
            fill=_color_at(anatomy, pos),
        )

    # --- Level labels -----------------------------------------------------
    for label in labels:
        slice_ = label["slice"]
        color = bk.LEVEL_COLORS[slice_.level.key]
        top = chart_bottom + int(30 * scale) + label["row"] * row_height

        if label["start"] > chart_left:
            draw.line(
                [
                    (label["start"], chart_bottom),
                    (label["start"], chart_bottom + int(14 * scale)),
                ],
                fill=bk.BORDER,
                width=max(1, int(2 * scale)),
            )

        name_width = draw.textlength(slice_.level.name, font=label_font)
        draw.text(
            (label["centre"] - name_width / 2, top),
            slice_.level.name,
            font=label_font,
            fill=color,
        )
        meta_width = draw.textlength(label["meta"], font=meta_font)
        draw.text(
            (label["centre"] - meta_width / 2, top + int(36 * scale)),
            label["meta"],
            font=meta_font,
            fill=bk.TEXT_SECONDARY,
        )

    # --- Footnote and wordmark -------------------------------------------
    footnote_font = load_font(24 * scale, bk.WEIGHT_MEDIUM, opsz=14)
    footnote_y = footnote_top
    for line in _wrap(draw, footnote, footnote_font, content_width):
        draw.text((margin, footnote_y), line, font=footnote_font, fill=bk.TEXT_SECONDARY)
        footnote_y += int(36 * scale)

    mark_font = load_font(26 * scale, bk.WEIGHT_BOLD, opsz=16)
    mark = "EMBER"
    mark_width = sum(draw.textlength(c, font=mark_font) for c in mark) + 4.0 * scale * (
        len(mark) - 1
    )
    _draw_tracked_text(
        draw,
        (int(width - margin - mark_width), height - margin - int(26 * scale)),
        mark,
        mark_font,
        bk.ACCENT,
        tracking=4.0 * scale,
    )

    final = image.resize(
        (bk.CANVAS_WIDTH, bk.CANVAS_HEIGHT), Image.Resampling.LANCZOS
    )
    cfg.ensure_dir(out_path.parent)
    final.save(out_path, format="PNG", optimize=True)
    return out_path
