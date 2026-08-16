import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ember_social.publishers import image_host, instagram  # noqa: E402


def _publisher(base="https://graph.instagram.com"):
    return instagram.InstagramPublisher(
        access_token="IGAAtoken", user_id="17841400000000000", base_url=base
    )


class Routing(unittest.TestCase):
    def test_urls_are_built_without_double_slashes(self):
        pub = _publisher(base="https://graph.instagram.com/")
        self.assertEqual(
            pub._url("/17841400000000000/media"),
            "https://graph.instagram.com/17841400000000000/media",
        )

    def test_facebook_flavor_keeps_its_api_version(self):
        pub = _publisher(base="https://graph.facebook.com/v21.0")
        self.assertIn("/v21.0/", pub._url("me/media"))


class ContainerPolling(unittest.TestCase):
    """Publishing before the container is FINISHED fails with error 9007."""

    def _publisher_with_statuses(self, statuses):
        pub = _publisher()
        pub.container_status = mock.Mock(
            side_effect=[{"status_code": code} for code in statuses]
        )
        return pub

    def test_waits_until_finished(self):
        pub = self._publisher_with_statuses(["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])
        slept = []
        status = pub.wait_for_container("c1", sleep=slept.append)
        self.assertEqual(status["_polls"], 3)
        self.assertEqual(len(slept), 2)

    def test_backoff_grows_rather_than_hammering(self):
        pub = self._publisher_with_statuses(["IN_PROGRESS"] * 3 + ["FINISHED"])
        slept = []
        pub.wait_for_container("c1", initial_delay=1.0, backoff=2.0, sleep=slept.append)
        self.assertEqual(slept, [1.0, 2.0, 4.0])

    def test_an_errored_container_fails_immediately(self):
        """Waiting out an ERROR just produces a confusing 9007 later."""
        pub = self._publisher_with_statuses(["IN_PROGRESS", "ERROR"])
        with self.assertRaises(instagram.InstagramError):
            pub.wait_for_container("c1", sleep=lambda _: None)

    def test_an_expired_container_is_terminal_too(self):
        pub = self._publisher_with_statuses(["EXPIRED"])
        with self.assertRaises(instagram.InstagramError):
            pub.wait_for_container("c1", sleep=lambda _: None)

    def test_the_ceiling_is_honoured_rather_than_hanging_the_job(self):
        pub = _publisher()
        pub.container_status = mock.Mock(return_value={"status_code": "IN_PROGRESS"})
        clock = {"t": 0.0}

        def fake_sleep(seconds):
            clock["t"] += seconds

        with self.assertRaises(instagram.ContainerNotReady):
            pub.wait_for_container(
                "c1",
                ceiling_seconds=90,
                initial_delay=2.0,
                backoff=1.5,
                sleep=fake_sleep,
                now=lambda: clock["t"],
            )
        self.assertLessEqual(clock["t"], 90)

    def test_it_never_publishes_a_container_it_could_not_confirm(self):
        pub = _publisher()
        pub.create_container = mock.Mock(return_value="c1")
        pub.container_status = mock.Mock(return_value={"status_code": "IN_PROGRESS"})
        pub.publish_container = mock.Mock()
        with self.assertRaises(instagram.ContainerNotReady):
            pub.publish("https://example.com/a.png", "caption", sleep=lambda _: None)
        pub.publish_container.assert_not_called()


class ErrorSurfacing(unittest.TestCase):
    def test_a_graph_error_becomes_a_readable_exception(self):
        response = mock.Mock()
        response.status_code = 400
        response.json.return_value = {
            "error": {
                "message": "Media ID is not available",
                "code": 9007,
                "type": "OAuthException",
            }
        }
        with mock.patch("requests.request", return_value=response):
            with self.assertRaises(instagram.InstagramError) as caught:
                instagram._request("POST", "https://graph.instagram.com/x")
        self.assertIn("9007", str(caught.exception))

    def test_an_html_error_page_does_not_raise_a_json_decode_error(self):
        response = mock.Mock()
        response.status_code = 502
        response.json.side_effect = ValueError("no json")
        response.text = "<html>bad gateway</html>"
        with mock.patch("requests.request", return_value=response):
            with self.assertRaises(instagram.InstagramError) as caught:
                instagram._request("GET", "https://graph.instagram.com/x")
        self.assertIn("non-JSON", str(caught.exception))


class Publishing(unittest.TestCase):
    def test_the_happy_path_returns_the_media_id(self):
        pub = _publisher()
        pub.create_container = mock.Mock(return_value="c1")
        pub.container_status = mock.Mock(return_value={"status_code": "FINISHED"})
        pub.publish_container = mock.Mock(return_value={"id": "media-1"})
        pub.permalink = mock.Mock(return_value="https://instagram.com/p/abc")

        result = pub.publish("https://example.com/a.png", "caption")
        self.assertEqual(result.media_id, "media-1")
        self.assertEqual(result.container_id, "c1")

    def test_alt_text_is_passed_through_when_given(self):
        pub = _publisher()
        with mock.patch.object(instagram, "_request", return_value={"id": "c1"}) as req:
            pub.create_container("https://e.com/a.png", "cap", alt_text="a couple")
        self.assertEqual(req.call_args.kwargs["params"]["alt_text"], "a couple")

    def test_alt_text_is_omitted_rather_than_sent_empty(self):
        pub = _publisher()
        with mock.patch.object(instagram, "_request", return_value={"id": "c1"}) as req:
            pub.create_container("https://e.com/a.png", "cap")
        self.assertNotIn("alt_text", req.call_args.kwargs["params"])


class Hosting(unittest.TestCase):
    def test_a_private_repo_is_caught_before_instagram_sees_it(self):
        """Meta's fetcher is unauthenticated; a private repo 404s opaquely."""
        response = mock.Mock()
        response.status_code = 404
        response.content = b""
        with mock.patch("requests.get", return_value=response):
            self.assertFalse(image_host.is_publicly_reachable("https://x/y.png"))

    def test_a_reachable_url_passes(self):
        response = mock.Mock()
        response.status_code = 200
        response.content = b"\x89PNG"
        with mock.patch("requests.get", return_value=response):
            self.assertTrue(image_host.is_publicly_reachable("https://x/y.png"))

    def test_an_empty_body_is_not_considered_reachable(self):
        response = mock.Mock()
        response.status_code = 200
        response.content = b""
        with mock.patch("requests.get", return_value=response):
            self.assertFalse(image_host.is_publicly_reachable("https://x/y.png"))

    def test_missing_hosting_config_explains_the_secret_naming_rule(self):
        from ember_social import config as cfg

        empty = cfg.ImageHostConfig(token=None, repo=None, tag="t")
        config = mock.Mock(image_host=empty)
        with self.assertRaises(image_host.HostingError) as caught:
            image_host.GitHubReleaseBackend.from_config(config)
        self.assertIn("GH_ASSETS_TOKEN", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
