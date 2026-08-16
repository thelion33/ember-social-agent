import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from ember_social import brand_kit as bk  # noqa: E402
from ember_social import scenes  # noqa: E402
from ember_social.generators import content  # noqa: E402
from ember_social.publishers import image_gen, scene_gen  # noqa: E402


def _payload(**overrides):
    base = {
        "line": "The dishwasher can wait. Come here.",
        "line_explicit": "The dishwasher can wait. Get on the counter.",
        "attribution": "her, 1:14am",
        "instagram_caption": "Some sentences do not need volume.",
        "x_caption": "Some sentences do not need volume.",
        "alt_text": "A backlit silhouette of a couple embracing in a doorway.",
    }
    base.update(overrides)
    return base


class Tiers(unittest.TestCase):
    def test_instagram_never_exceeds_the_embrace_tier(self):
        self.assertEqual(scenes.max_tier_for("instagram"), scenes.TIER_EMBRACE)

    def test_x_may_use_the_charged_tier(self):
        self.assertEqual(scenes.max_tier_for("x"), scenes.TIER_CHARGED)

    def test_unknown_network_defaults_to_the_safest_tier(self):
        self.assertEqual(scenes.max_tier_for("threads"), scenes.TIER_EMBRACE)

    def test_charged_steps_down_to_embrace_then_stops(self):
        self.assertEqual(scenes.step_down(scenes.TIER_CHARGED), scenes.TIER_EMBRACE)
        self.assertIsNone(scenes.step_down(scenes.TIER_EMBRACE))


class Composition(unittest.TestCase):
    def test_same_seed_gives_the_same_scene(self):
        first = scenes.compose(seed=42)
        second = scenes.compose(seed=42)
        self.assertEqual(first.key, second.key)

    def test_different_seeds_diverge(self):
        keys = {scenes.compose(seed=n).key for n in range(20)}
        self.assertGreater(len(keys), 10)

    def test_already_posted_scenes_are_avoided(self):
        first = scenes.compose(seed=3)
        second = scenes.compose(seed=3, exclude_keys=[first.key])
        self.assertNotEqual(first.key, second.key)

    def test_prompt_carries_the_style_anchor(self):
        prompt = scenes.compose(seed=1).prompt()
        self.assertIn("no text", prompt)
        self.assertIn("no explicit anatomy", prompt)
        self.assertIn("silhouette", prompt.lower())

    def test_enough_combinations_to_avoid_repeats_for_years(self):
        self.assertGreater(scenes.combination_count(scenes.TIER_EMBRACE), 1000)

    def test_unknown_tier_is_rejected(self):
        with self.assertRaises(ValueError):
            scenes.compose(tier="pornographic")


class RefusalHandling(unittest.TestCase):
    def test_moderation_errors_are_recognised(self):
        blocked = Exception(
            "Error code: 400 - safety system ... code: 'moderation_blocked'"
        )
        self.assertTrue(scene_gen._is_moderation_refusal(blocked))

    def test_other_errors_are_not_swallowed(self):
        self.assertFalse(scene_gen._is_moderation_refusal(Exception("timed out")))


class OverheardValidation(unittest.TestCase):
    def test_clean_copy_passes(self):
        self.assertEqual(content.validate_overheard(_payload()), [])

    def test_fabricated_endorsement_is_rejected(self):
        problems = content.validate_overheard(_payload(line="I love this app!"))
        self.assertTrue(any("endorsement" in p for p in problems))

    def test_naming_the_product_in_the_line_is_rejected(self):
        problems = content.validate_overheard(_payload(line="Ember was right."))
        self.assertTrue(any("endorsement" in p for p in problems))

    def test_quoted_line_is_rejected(self):
        problems = content.validate_overheard(_payload(line='"Say that again."'))
        self.assertTrue(any("quotation marks" in p for p in problems))

    def test_overlong_line_is_rejected(self):
        problems = content.validate_overheard(_payload(line="x" * 59))
        self.assertTrue(any("line is 59 characters" in p for p in problems))

    def test_banned_phrase_still_applies(self):
        problems = content.validate_overheard(
            _payload(instagram_caption="A way to spice things up.")
        )
        self.assertTrue(any("spice things up" in p for p in problems))

    def test_identical_lines_are_rejected(self):
        same = "The dishwasher can wait. Come here."
        problems = content.validate_overheard(_payload(line=same, line_explicit=same))
        self.assertTrue(any("identical" in p for p in problems))

    def test_endorsement_in_the_explicit_line_is_also_caught(self):
        problems = content.validate_overheard(
            _payload(line_explicit="Best thing this app ever did.")
        )
        self.assertTrue(any("endorsement" in p for p in problems))

    def test_line_for_routes_each_network_to_its_own_variant(self):
        copy = content.OverheardCopy(
            line="clean",
            line_explicit="explicit",
            attribution="her, 1am",
            instagram_caption="a",
            x_caption="b",
            alt_text="c",
        )
        self.assertEqual(copy.line_for("instagram"), "clean")
        self.assertEqual(copy.line_for("x"), "explicit")
        # Anything unrecognised must get the safe variant, never the explicit one.
        self.assertEqual(copy.line_for("threads"), "clean")

    def test_explicit_term_allowed_on_x_but_not_the_image_line(self):
        self.assertEqual(
            content.validate_overheard(_payload(x_caption="That was the best sex.")),
            [],
        )
        problems = content.validate_overheard(_payload(line="That was the best sex."))
        self.assertTrue(any('"sex"' in p for p in problems))


class Composite(unittest.TestCase):
    def _scene_file(self, directory):
        path = Path(directory) / "scene.png"
        Image.new("RGB", (1024, 1536), (40, 20, 12)).save(path)
        return path

    def test_composite_is_the_instagram_portrait_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = image_gen.render_overheard(
                self._scene_file(tmp),
                "I didn't know you had it in you.",
                Path(tmp) / "card.png",
            )
            with Image.open(out) as img:
                self.assertEqual(img.size, (bk.CANVAS_WIDTH, bk.CANVAS_HEIGHT))

    def test_a_very_long_line_still_renders(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = image_gen.render_overheard(
                self._scene_file(tmp),
                "We are absolutely never telling anyone about what happened "
                "to that chair.",
                Path(tmp) / "long.png",
            )
            self.assertGreater(out.stat().st_size, 5_000)

    def test_composite_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            scene = self._scene_file(tmp)
            first = image_gen.render_overheard(
                scene, "Say that again.", Path(tmp) / "a.png"
            ).read_bytes()
            second = image_gen.render_overheard(
                scene, "Say that again.", Path(tmp) / "b.png"
            ).read_bytes()
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
