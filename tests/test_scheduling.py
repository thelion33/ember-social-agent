import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ember_social import state  # noqa: E402
from ember_social.generators import selection  # noqa: E402

TZ = "America/New_York"
ZONE = ZoneInfo(TZ)

PLAN = [
    {"date": "2026-08-18", "time": "20:00", "type": "overheard"},
    {"date": "2026-08-20", "time": "21:00", "type": "overheard"},
]
RECURRING = [
    {"weekday": "tue", "time": "20:00", "type": "overheard"},
    {"weekday": "sun", "time": "19:00", "type": "overheard"},
]


def _at(text):
    return datetime.fromisoformat(text).replace(tzinfo=ZONE)


def _due(now, log=None, window=3, plan=PLAN, recurring=RECURRING):
    return selection.due_now(
        now=_at(now),
        timezone_name=TZ,
        window_hours=window,
        log=log,
        plan=plan,
        recurring=recurring,
    )


class CatchUpWindow(unittest.TestCase):
    def test_a_post_due_this_hour_is_found(self):
        due = _due("2026-08-18T20:05")
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0].post_type, "overheard")

    def test_cron_running_two_hours_late_still_catches_it(self):
        """GitHub's scheduler is routinely late; exact-hour matching drops posts."""
        due = _due("2026-08-18T22:30")
        self.assertEqual(len(due), 1)

    def test_beyond_the_window_it_is_abandoned_not_posted_at_random(self):
        due = _due("2026-08-19T04:00")
        self.assertEqual(due, [])

    def test_a_post_not_yet_due_is_not_run_early(self):
        due = _due("2026-08-18T19:30")
        self.assertEqual(due, [])

    def test_window_reaching_back_over_midnight_still_finds_yesterday(self):
        due = _due("2026-08-21T00:30", window=4)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0].scheduled_for.date().isoformat(), "2026-08-20")


class Dedupe(unittest.TestCase):
    def test_key_is_stable_regardless_of_when_the_run_happens(self):
        first = _due("2026-08-18T20:05")[0]
        second = _due("2026-08-18T22:30")[0]
        self.assertEqual(first.key, second.key)

    def test_an_already_posted_entry_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = state.ExecutionLog.load(Path(tmp) / "log.json")
            entry = _due("2026-08-18T20:05")[0]
            log.record(key=entry.key, post_type=entry.post_type)
            self.assertEqual(_due("2026-08-18T21:00", log=log), [])

    def test_a_second_concurrent_run_derives_the_same_key(self):
        run_a = _due("2026-08-18T20:01")[0]
        run_b = _due("2026-08-18T20:02")[0]
        self.assertEqual(run_a.key, run_b.key)


class RecurringFloor(unittest.TestCase):
    def test_recurring_fires_when_the_dated_plan_is_empty(self):
        """An expired calendar must not mean silence."""
        due = _due("2026-08-16T19:30", plan=[])
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0].source, "recurring")

    def test_recurring_does_not_double_up_a_dated_day(self):
        # 2026-08-18 is a Tuesday and appears in both PLAN and RECURRING.
        due = _due("2026-08-18T20:05")
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0].source, "plan")

    def test_recurring_has_no_end_date(self):
        from ember_social import posting_plan

        self.assertTrue(posting_plan.RECURRING)
        for rule in posting_plan.RECURRING:
            self.assertNotIn("date", rule)
            self.assertIn("weekday", rule)

    def test_the_floor_still_fires_years_after_the_plan_expires(self):
        due = _due("2031-08-17T19:30", plan=[], recurring=[
            {"weekday": "sun", "time": "19:00", "type": "overheard"}
        ])
        self.assertEqual(len(due), 1)


class Malformed(unittest.TestCase):
    def test_a_bad_time_is_skipped_rather_than_crashing_the_run(self):
        due = _due(
            "2026-08-18T20:05",
            plan=[{"date": "2026-08-18", "time": "not-a-time", "type": "overheard"}],
        )
        self.assertEqual(due, [])


class ExecutionLogFile(unittest.TestCase):
    def test_round_trips_through_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.json"
            log = state.ExecutionLog.load(path)
            log.record(key="k1", post_type="noop")
            log.save()
            self.assertTrue(state.ExecutionLog.load(path).has("k1"))

    def test_truncated_file_does_not_wedge_the_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.json"
            path.write_text('{"posts": [')
            self.assertEqual(state.ExecutionLog.load(path).entries, [])

    def test_pruning_bounds_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = state.ExecutionLog.load(Path(tmp) / "log.json")
            for n in range(600):
                log.record(key="k{}".format(n), post_type="noop")
            log.prune(keep=500)
            self.assertEqual(len(log.entries), 500)
            self.assertTrue(log.has("k599"))

    def test_scene_keys_are_tracked_so_imagery_never_repeats(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = state.ExecutionLog.load(Path(tmp) / "log.json")
            log.record(key="k1", post_type="overheard", scene_key="abc123")
            self.assertIn("abc123", log.scene_keys())


class CredentialSafety(unittest.TestCase):
    """These files get committed to a public repo on every run."""

    def test_an_openai_key_is_refused(self):
        with self.assertRaises(state.CredentialLeak):
            state.assert_no_credentials(
                {"note": "sk-proj-abcdefghijklmnopqrstuvwxyz012345"}
            )

    def test_a_github_token_is_refused(self):
        with self.assertRaises(state.CredentialLeak):
            state.assert_no_credentials({"t": "ghp_abcdefghijklmnopqrstuvwxyz0123456789"})

    def test_an_instagram_token_is_refused(self):
        with self.assertRaises(state.CredentialLeak):
            state.assert_no_credentials({"t": "IGAAabcdefghijklmnopqrstuvwxyz"})

    def test_saving_a_leaky_entry_raises_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.json"
            log = state.ExecutionLog.load(path)
            log.record(key="k1", post_type="noop", note="token IGAAabcdefghijklmnopqrst")
            with self.assertRaises(state.CredentialLeak):
                log.save()
            self.assertFalse(path.exists())

    def test_ordinary_entries_are_fine(self):
        state.assert_no_credentials(
            {"key": "2026-08-18|20:00|overheard", "scene_key": "77f538410ae7"}
        )


class TokenRefresh(unittest.TestCase):
    def test_unknown_age_counts_as_needing_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            token = state.TokenState.load(Path(tmp) / "token.json")
            self.assertIsNone(token.days_since_refresh())
            self.assertTrue(token.needs_refresh())

    def test_a_fresh_token_does_not_need_refreshing(self):
        with tempfile.TemporaryDirectory() as tmp:
            token = state.TokenState.load(Path(tmp) / "token.json")
            token.mark_refreshed()
            self.assertFalse(token.needs_refresh())
            self.assertLess(token.days_since_refresh(), 1)

    def test_an_old_token_needs_refreshing_well_before_the_60_day_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "token.json"
            token = state.TokenState.load(path)
            stale = datetime.now(ZONE) - timedelta(days=31)
            token.data["instagram_refreshed_at"] = stale.isoformat(timespec="seconds")
            self.assertTrue(token.needs_refresh())
            self.assertLess(token.days_since_refresh(), 60)

    def test_the_token_file_never_holds_a_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "token.json"
            token = state.TokenState.load(path)
            token.mark_refreshed()
            token.save()
            self.assertNotIn("token", path.read_text().lower().replace("_refreshed", ""))


if __name__ == "__main__":
    unittest.main()
