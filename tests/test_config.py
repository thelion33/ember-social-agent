import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ember_social import config as cfg  # noqa: E402


class MetaFlavorDetection(unittest.TestCase):
    def test_igaa_token_routes_to_graph_instagram(self):
        ig = cfg.InstagramConfig(access_token="IGAAabc123", user_id="17841400000000000")
        self.assertEqual(ig.flavor, "instagram_login")
        self.assertEqual(ig.host, cfg.INSTAGRAM_LOGIN_HOST)
        self.assertEqual(ig.base_url, "https://graph.instagram.com")
        self.assertTrue(ig.is_configured)

    def test_other_token_routes_to_graph_facebook_with_version(self):
        ig = cfg.InstagramConfig(access_token="EAAGm0PX4ZCpsBA", user_id="17841400000000000")
        self.assertEqual(ig.flavor, "facebook_login")
        self.assertEqual(ig.host, cfg.FACEBOOK_GRAPH_HOST)
        self.assertEqual(
            ig.base_url,
            "https://graph.facebook.com/{}".format(cfg.GRAPH_API_VERSION),
        )

    def test_missing_token_is_unconfigured(self):
        ig = cfg.InstagramConfig(access_token=None, user_id="17841400000000000")
        self.assertEqual(ig.flavor, "unconfigured")
        self.assertFalse(ig.is_configured)

    def test_token_without_user_id_is_not_configured(self):
        ig = cfg.InstagramConfig(access_token="IGAAabc123", user_id=None)
        self.assertFalse(ig.is_configured)


class XCredentials(unittest.TestCase):
    def test_bearer_token_alone_does_not_count_as_configured(self):
        x = cfg.XConfig(
            api_key=None,
            api_secret=None,
            access_token=None,
            access_token_secret=None,
            bearer_token="AAAAAAAA",
        )
        self.assertFalse(x.is_configured)

    def test_full_oauth1_set_is_configured(self):
        x = cfg.XConfig(
            api_key="k",
            api_secret="s",
            access_token="t",
            access_token_secret="ts",
            bearer_token=None,
        )
        self.assertTrue(x.is_configured)


class EnvironmentReads(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_blank_values_are_treated_as_unset(self):
        os.environ["OPENAI_API_KEY"] = "   "
        config = cfg.Config.from_env()
        self.assertIsNone(config.openai_api_key)

    def test_timezone_and_asset_tag_have_defaults(self):
        os.environ.pop("TIMEZONE", None)
        os.environ.pop("GH_ASSETS_TAG", None)
        config = cfg.Config.from_env()
        self.assertEqual(config.timezone, "America/New_York")
        self.assertEqual(config.image_host.tag, "post-assets")


class ImportIsSideEffectFree(unittest.TestCase):
    """A stray mkdir at import time crashes the runner on an unrelated import."""

    def test_importing_every_module_creates_nothing_on_disk(self):
        repo_root = Path(__file__).resolve().parent.parent
        script = textwrap.dedent(
            """
            import pkgutil, sys
            from pathlib import Path
            root = Path(sys.argv[1])
            before = {p.name for p in root.iterdir()}
            sys.path.insert(0, str(root))
            import ember_social
            for module in pkgutil.walk_packages(
                ember_social.__path__, "ember_social."
            ):
                __import__(module.name)
            after = {p.name for p in root.iterdir()}
            created = sorted(after - before)
            print(",".join(created))
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script, str(repo_root)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        created = [name for name in result.stdout.strip().split(",") if name]
        self.assertEqual(
            created, [], "importing the package created: {}".format(created)
        )


class BundledFont(unittest.TestCase):
    def test_font_and_license_are_committed(self):
        self.assertTrue(cfg.FONT_PATH.exists(), "bundled font is missing")
        self.assertTrue(cfg.FONT_LICENSE_PATH.exists(), "OFL license is missing")


if __name__ == "__main__":
    unittest.main()
