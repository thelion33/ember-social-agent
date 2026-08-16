import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ember_social import journey_spec as js  # noqa: E402
from ember_social.generators import content  # noqa: E402


def _valid_payload(**overrides):
    payload = {
        "eyebrow": "Journey anatomy",
        "headline": "The build is quick because the middle needs room",
        "deck": "Glow and Spark take about five minutes. Flame takes ten on its own.",
        "footnote": "Fifteen cards across five levels, timed by the app's pacing rules.",
        "instagram_caption": "A thirty minute journey is not five equal blocks.",
        "x_caption": "A thirty minute journey is not five equal blocks.",
        "alt_text": "Chart of intensity rising across five labelled levels.",
    }
    payload.update(overrides)
    return payload


class Anatomy(unittest.TestCase):
    def test_card_counts_match_the_app(self):
        self.assertEqual(js.anatomy_for("10 min").total_cards, 5)
        self.assertEqual(js.anatomy_for("30 min").total_cards, 15)
        self.assertEqual(js.anatomy_for("1 hour").total_cards, 25)

    def test_flame_is_the_longest_stretch(self):
        anatomy = js.anatomy_for("30 min")
        self.assertEqual(anatomy.dominant.level.key, "flame")

    def test_shares_sum_to_one(self):
        anatomy = js.anatomy_for("45 min")
        total = sum(anatomy.share(s) for s in anatomy.slices)
        self.assertAlmostEqual(total, 1.0, places=6)

    def test_unknown_duration_is_rejected(self):
        with self.assertRaises(ValueError):
            js.anatomy_for("90 min")

    def test_curve_is_anchored_at_both_ends(self):
        points = js.curve_points(js.anatomy_for("30 min"))
        self.assertEqual(points[0][0], 0.0)
        self.assertEqual(points[-1][0], 1.0)
        self.assertLess(points[0][1], points[1][1], "journey must start from rest")
        self.assertLess(points[-1][1], points[-2][1], "journey must tail into aftercare")

    def test_curve_x_is_monotonic(self):
        xs = [x for x, _ in js.curve_points(js.anatomy_for("20 min"))]
        self.assertEqual(xs, sorted(xs))


class Validation(unittest.TestCase):
    def test_clean_copy_passes(self):
        self.assertEqual(content.validate(_valid_payload()), [])

    def test_banned_phrase_is_caught(self):
        problems = content.validate(
            _valid_payload(deck="A guide to spice things up on a Tuesday.")
        )
        self.assertTrue(any("spice things up" in p for p in problems))

    def test_instagram_forbidden_term_is_caught(self):
        problems = content.validate(
            _valid_payload(instagram_caption="Ten minutes of oral, timed.")
        )
        self.assertTrue(any('"oral"' in p for p in problems))

    def test_forbidden_term_is_allowed_on_x_only(self):
        problems = content.validate(
            _valid_payload(x_caption="Ten minutes of oral, timed.")
        )
        self.assertEqual(problems, [])

    def test_x_caption_length_is_enforced(self):
        problems = content.validate(_valid_payload(x_caption="x" * 281))
        self.assertTrue(any("x_caption is 281 characters" in p for p in problems))

    def test_links_are_rejected(self):
        problems = content.validate(
            _valid_payload(x_caption="Read more at https://ember.app")
        )
        self.assertTrue(any("link" in p for p in problems))

    def test_link_in_bio_is_rejected(self):
        problems = content.validate(
            _valid_payload(instagram_caption="Full breakdown, link in bio.")
        )
        self.assertTrue(any("link" in p for p in problems))

    def test_hashtag_ceiling_is_enforced(self):
        problems = content.validate(
            _valid_payload(instagram_caption="Pacing. #pacing #design #intimacy #couples")
        )
        self.assertTrue(any("hashtags" in p for p in problems))

    def test_x_hashtags_are_rejected_outright(self):
        problems = content.validate(_valid_payload(x_caption="Pacing matters. #design"))
        self.assertTrue(any("x_caption must not contain hashtags" in p for p in problems))

    def test_empty_field_is_caught(self):
        problems = content.validate(_valid_payload(headline=""))
        self.assertTrue(any("headline" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
