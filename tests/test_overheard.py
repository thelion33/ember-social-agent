import base64
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from ember_social import brand_kit as bk  # noqa: E402
from ember_social import config as cfg  # noqa: E402
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
        self.assertIn("no nudity", prompt)
        self.assertIn("photorealistic", prompt)

    def test_every_scene_names_what_they_are_wearing(self):
        """Nobody fully clothed, but nudity is never the subject either."""
        for tier in scenes.TIERS:
            for seed in range(15):
                scene = scenes.compose(tier=tier, seed=seed)
                self.assertTrue(scene.wardrobe)
                self.assertIn(scene.wardrobe, scenes.WARDROBE[tier])

    def test_no_wardrobe_entry_describes_nudity(self):
        """Naming nudity as the subject is what actually trips the filter.

        Measured: prompts describing bare skin passed, while prompts whose
        subject was nudity were refused by both Gemini models.
        """
        banned = ("nude", "naked", "undressed", "topless")
        for tier, options in scenes.WARDROBE.items():
            for option in options:
                for word in banned:
                    self.assertNotIn(word, option.lower(), (tier, option))

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


def _png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (64, 96), (20, 12, 8)).save(buffer, format="PNG")
    return buffer.getvalue()


class ProviderChain(unittest.TestCase):
    """Gemini renders what OpenAI refuses, so it leads; OpenAI is the net."""

    def _chain(self, provider=scene_gen.PROVIDER_AUTO, tier=scenes.TIER_EMBRACE,
               gemini_key="test-key", bfl_key=None):
        env = {}
        if gemini_key:
            env["GEMINI_API_KEY"] = gemini_key
        if bfl_key:
            env["BFL_API_KEY"] = bfl_key
        with mock.patch.dict(os.environ, env, clear=True):
            return [
                (name, model)
                for name, model, _ in scene_gen._provider_chain(provider, tier)
            ]

    def test_gemini_leads_and_openai_backs_it_up(self):
        chain = self._chain()
        self.assertEqual(
            [name for name, _ in chain],
            [scene_gen.PROVIDER_GEMINI, scene_gen.PROVIDER_OPENAI],
        )

    def test_bfl_outranks_gemini_when_configured(self):
        """Ordered by permission granted, not by output quality."""
        chain = self._chain(bfl_key="test-bfl")
        self.assertEqual(
            [name for name, _ in chain],
            [
                scene_gen.PROVIDER_BFL,
                scene_gen.PROVIDER_GEMINI,
                scene_gen.PROVIDER_OPENAI,
            ],
        )

    def test_requesting_one_provider_measures_only_that_provider(self):
        """A probe must not silently fall through to a different backend."""
        chain = self._chain(provider=scene_gen.PROVIDER_BFL, bfl_key="test-bfl")
        self.assertEqual([name for name, _ in chain], [scene_gen.PROVIDER_BFL])

    def test_requesting_an_unconfigured_provider_fails_loudly(self):
        with self.assertRaises(RuntimeError):
            self._chain(provider=scene_gen.PROVIDER_BFL, bfl_key=None)

    def test_instagram_draws_from_the_stricter_model(self):
        """Pro refuses more than Flash, so it draws for the stricter platform."""
        chain = self._chain(tier=scenes.TIER_EMBRACE)
        self.assertEqual(chain[0][1], cfg.DEFAULT_GEMINI_INSTAGRAM_MODEL)

    def test_x_draws_from_the_permissive_model(self):
        chain = self._chain(tier=scenes.TIER_CHARGED)
        self.assertEqual(chain[0][1], cfg.DEFAULT_GEMINI_X_MODEL)

    def test_without_a_key_gemini_is_skipped_entirely(self):
        chain = self._chain(gemini_key=None)
        self.assertEqual([name for name, _ in chain], [scene_gen.PROVIDER_OPENAI])

    def test_openai_can_be_forced(self):
        chain = self._chain(provider=scene_gen.PROVIDER_OPENAI)
        self.assertEqual([name for name, _ in chain], [scene_gen.PROVIDER_OPENAI])

    def test_bfl_moderation_statuses_read_as_refusals(self):
        for status in ("Request Moderated", "Content Moderated"):
            self.assertTrue(
                scene_gen._is_moderation_refusal(
                    scene_gen.ModerationRefusal("bfl {}: ['Sexual Content']".format(status))
                )
            )

    def test_a_withheld_image_reads_as_a_refusal_not_a_crash(self):
        for reason in ("PROHIBITED_CONTENT", "IMAGE_SAFETY"):
            self.assertTrue(
                scene_gen._is_moderation_refusal(
                    scene_gen.ModerationRefusal(
                        "gemini withheld the image: {}".format(reason)
                    )
                )
            )

    def test_a_gemini_outage_falls_through_to_openai(self):
        calls = []

        def failing_gemini(prompt, model):
            calls.append("gemini")
            raise RuntimeError("gemini 503: backend unavailable")

        def working_openai(prompt, model):
            calls.append("openai")
            return _png_bytes()

        chain = [
            (scene_gen.PROVIDER_GEMINI, "m", failing_gemini),
            (scene_gen.PROVIDER_OPENAI, "n", working_openai),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                scene_gen, "_provider_chain", return_value=chain
            ):
                generated = scene_gen.generate_scene(seed=1, out_dir=Path(tmp))
        self.assertEqual(generated.provider, scene_gen.PROVIDER_OPENAI)
        # One gemini attempt, not three: an outage is not a content problem.
        self.assertEqual(calls, ["gemini", "openai"])


class RerollOnRefusal(unittest.TestCase):
    """A refusal at the lowest tier must not end the run.

    Moderation reacts to the specific composition, so trying a different one is
    far more productive than giving up — and at the lowest tier there is no
    tier left to step down to.
    """

    def _client(self, refuse_first):
        calls = {"n": 0, "prompts": []}

        class FakeImages:
            def generate(self, **kwargs):
                calls["n"] += 1
                calls["prompts"].append(kwargs["prompt"])
                if calls["n"] <= refuse_first:
                    raise RuntimeError(
                        "Error code: 400 - rejected by the safety system req_abc123"
                    )
                payload = mock.Mock()
                payload.b64_json = base64.b64encode(_png_bytes()).decode()
                result = mock.Mock()
                result.data = [payload]
                return result

        client = mock.Mock()
        client.images = FakeImages()
        return client, calls

    def _generate(self, client, tier, tmp):
        with mock.patch("openai.OpenAI", return_value=client):
            return scene_gen.generate_scene(tier=tier, seed=7, out_dir=Path(tmp))

    def test_a_refused_composition_is_retried_with_a_different_one(self):
        client, calls = self._client(refuse_first=1)
        with tempfile.TemporaryDirectory() as tmp:
            generated = self._generate(client, scenes.TIER_EMBRACE, tmp)
        self.assertEqual(calls["n"], 2)
        self.assertNotEqual(calls["prompts"][0], calls["prompts"][1])
        self.assertEqual(len(generated.refusals), 1)
        # Still the requested tier: rerolling must not cost explicitness.
        self.assertEqual(generated.scene.tier, scenes.TIER_EMBRACE)

    def test_rerolls_are_bounded_before_stepping_down(self):
        client, calls = self._client(refuse_first=scene_gen.ATTEMPTS_PER_TIER)
        with tempfile.TemporaryDirectory() as tmp:
            generated = self._generate(client, scenes.TIER_CHARGED, tmp)
        self.assertEqual(calls["n"], scene_gen.ATTEMPTS_PER_TIER + 1)
        self.assertTrue(generated.was_downgraded)

    def test_the_same_composition_is_never_retried(self):
        client, calls = self._client(refuse_first=2)
        with tempfile.TemporaryDirectory() as tmp:
            self._generate(client, scenes.TIER_EMBRACE, tmp)
        self.assertEqual(len(set(calls["prompts"])), len(calls["prompts"]))

    def test_exhausting_everything_still_fails_loudly(self):
        client, _ = self._client(refuse_first=99)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(scene_gen.SceneUnavailable):
                self._generate(client, scenes.TIER_CHARGED, tmp)

    def test_a_non_moderation_error_is_not_retried(self):
        """A timeout or an auth failure must surface, not burn three attempts."""
        client = mock.Mock()
        client.images.generate.side_effect = RuntimeError("connection timed out")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError) as caught:
                self._generate(client, scenes.TIER_EMBRACE, tmp)
        self.assertIn("timed out", str(caught.exception))
        self.assertEqual(client.images.generate.call_count, 1)


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
