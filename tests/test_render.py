import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from ember_social import brand_kit as bk  # noqa: E402
from ember_social import journey_spec as js  # noqa: E402
from ember_social.publishers import image_gen  # noqa: E402


def _render(duration, directory):
    return image_gen.render_journey_anatomy(
        anatomy=js.anatomy_for(duration),
        eyebrow="Journey anatomy",
        headline="The build is quick because the middle needs room",
        deck="Glow and Spark take five minutes. Flame takes ten on its own.",
        footnote="Fifteen cards across five levels.",
        out_path=Path(directory) / "card.png",
    )


class Rendering(unittest.TestCase):
    def test_card_is_the_instagram_portrait_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _render("30 min", tmp)
            with Image.open(path) as img:
                self.assertEqual(img.size, (bk.CANVAS_WIDTH, bk.CANVAS_HEIGHT))

    def test_every_duration_renders(self):
        with tempfile.TemporaryDirectory() as tmp:
            for duration in js.CARDS_PER_LEVEL:
                path = _render(duration, tmp)
                self.assertGreater(path.stat().st_size, 10_000)

    def test_render_is_deterministic(self):
        """Same inputs must give byte-identical output on any machine."""
        with tempfile.TemporaryDirectory() as tmp:
            first = _render("30 min", tmp).read_bytes()
            second = _render("30 min", tmp).read_bytes()
            self.assertEqual(first, second)

    def test_bundled_font_loads_at_every_weight(self):
        for weight in (bk.WEIGHT_REGULAR, bk.WEIGHT_SEMIBOLD, bk.WEIGHT_BOLD):
            self.assertIsNotNone(image_gen.load_font(32, weight))

    def test_missing_font_raises_instead_of_substituting(self):
        original = image_gen.cfg.FONT_PATH
        image_gen.cfg.FONT_PATH = Path("/nonexistent/Nope.ttf")
        try:
            with self.assertRaises(image_gen.FontUnavailable):
                image_gen.load_font(32)
        finally:
            image_gen.cfg.FONT_PATH = original


class LabelLayout(unittest.TestCase):
    def test_short_levels_are_staggered_not_overlapped(self):
        from PIL import ImageDraw

        canvas = Image.new("RGB", (100, 100))
        draw = ImageDraw.Draw(canvas)
        labels = image_gen._layout_level_labels(
            draw,
            js.anatomy_for("30 min"),
            chart_left=0,
            chart_width=904,
            scale=1,
            label_font=image_gen.load_font(26, bk.WEIGHT_SEMIBOLD),
            meta_font=image_gen.load_font(22, bk.WEIGHT_REGULAR),
        )
        by_row = {}
        for label in labels:
            by_row.setdefault(label["row"], []).append(label)

        for row_labels in by_row.values():
            ordered = sorted(row_labels, key=lambda item: item["centre"])
            for left, right in zip(ordered, ordered[1:]):
                left_edge = right["centre"] - right["width"] / 2
                right_edge = left["centre"] + left["width"] / 2
                self.assertGreaterEqual(
                    left_edge,
                    right_edge,
                    "{} overlaps {}".format(
                        right["slice"].level.name, left["slice"].level.name
                    ),
                )


if __name__ == "__main__":
    unittest.main()
