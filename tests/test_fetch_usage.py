# Run: /usr/bin/python3 -m unittest discover -s tests   (from ai-usage.widget/)
import importlib.util
import os
import unittest
from datetime import date, datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("fetch_usage", os.path.join(_HERE, "..", "fetch-usage.py"))
fu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fu)

IST = ZoneInfo("Asia/Calcutta")
# 2026-10-29 18:00 IST
NOW = int(datetime(2026, 10, 29, 18, 0, tzinfo=IST).timestamp())
FUTURE = NOW + 3600


class PercentAndRemaining(unittest.TestCase):
    def test_basic(self):
        l = fu.make_limit("Weekly", 8, FUTURE, "t", now=NOW)
        self.assertEqual(l["usedPercent"], 8.0)
        self.assertEqual(l["remainingPercent"], 92.0)
        self.assertEqual(l["pctText"], "8%")
        self.assertEqual(l["remainingText"], "92% left")
        self.assertAlmostEqual(l["bar"], 0.08)
        self.assertFalse(l["overLimit"])

    def test_zero_is_zero(self):
        l = fu.make_limit("x", 0, FUTURE, "t", now=NOW)
        self.assertEqual(l["pctText"], "0%")
        self.assertEqual(l["remainingPercent"], 100.0)

    def test_full(self):
        l = fu.make_limit("x", 100, FUTURE, "t", now=NOW)
        self.assertEqual(l["remainingPercent"], 0.0)
        self.assertFalse(l["overLimit"])
        self.assertEqual(l["bar"], 1.0)

    def test_over_limit_not_capped(self):
        # 8.1M / 3.0M from the old widget is 270%; true value must survive.
        l = fu.make_limit("x", 8.1e6 / 3.0e6 * 100, FUTURE, "t", now=NOW)
        self.assertAlmostEqual(l["usedPercent"], 270.0)
        self.assertEqual(l["pctText"], "270%")
        self.assertTrue(l["overLimit"])
        self.assertEqual(l["remainingPercent"], 0.0)   # max(0, 100 - 270)
        self.assertEqual(l["bar"], 1.0)                # drawing only

    def test_small_values_not_rounded_to_zero(self):
        self.assertEqual(fu.fmt_pct(0.4), "0.4%")
        self.assertEqual(fu.fmt_pct(0.04), "<0.1%")
        self.assertEqual(fu.fmt_pct(2.5), "2.5%")
        self.assertEqual(fu.fmt_pct(12.6), "13%")
        self.assertEqual(fu.fmt_pct(None), "—")


class MissingData(unittest.TestCase):
    def test_missing_percent_is_unavailable(self):
        l = fu.make_limit("x", None, FUTURE, "t", now=NOW)
        self.assertFalse(l["available"])
        self.assertIsNone(l["usedPercent"])
        self.assertEqual(l["bar"], 0.0)
        self.assertEqual(l["pctText"], "—")

    def test_garbage_percent(self):
        self.assertFalse(fu.make_limit("x", "abc", FUTURE, "t", now=NOW)["available"])
        self.assertFalse(fu.make_limit("x", -5, FUTURE, "t", now=NOW)["available"])

    def test_expired_window_is_unavailable(self):
        l = fu.make_limit("x", 40, NOW - 60, "t", now=NOW)
        self.assertFalse(l["available"])

    def test_no_reset_still_available(self):
        l = fu.make_limit("x", 40, None, "t", now=NOW)
        self.assertTrue(l["available"])
        self.assertIsNone(l["resetsAt"])

    def test_codex_empty_payload(self):
        self.assertEqual(fu.parse_codex_rate_limits({}, "t", now=NOW), [])
        self.assertEqual(fu.parse_codex_rate_limits(None, "t", now=NOW), [])
        self.assertEqual(fu.parse_codex_rate_limits({"primary": None, "secondary": None}, "t", now=NOW), [])

    def test_codex_missing_used_percent(self):
        r = fu.parse_codex_rate_limits({"primary": {"windowDurationMins": 300}}, "t", now=NOW)
        self.assertEqual(len(r), 1)
        self.assertFalse(r[0]["available"])

    def test_claude_unrecognised_text(self):
        mode, limits = fu.parse_claude_usage("Something changed", now=NOW)
        self.assertEqual(mode, "unknown")
        self.assertEqual(limits, [])


class ResetTimes(unittest.TestCase):
    def test_claude_same_day(self):
        ts = fu.parse_claude_reset("Oct 29 at 10:39pm", "Asia/Calcutta", now=NOW)
        self.assertEqual(datetime.fromtimestamp(ts, IST), datetime(2026, 10, 29, 22, 39, tzinfo=IST))

    def test_claude_other_tz_converts_correctly(self):
        ts = fu.parse_claude_reset("Oct 29 at 9am", "America/New_York", now=NOW)
        # 09:00 EDT == 18:30 IST
        self.assertEqual(datetime.fromtimestamp(ts, IST), datetime(2026, 10, 29, 18, 30, tzinfo=IST))

    def test_claude_year_rollover(self):
        dec = int(datetime(2026, 12, 30, 12, 0, tzinfo=IST).timestamp())
        ts = fu.parse_claude_reset("Jan 2 at 10:29am", "Asia/Calcutta", now=dec)
        self.assertEqual(datetime.fromtimestamp(ts, IST).year, 2027)

    def test_claude_time_only(self):
        ts = fu.parse_claude_reset("10:39pm", "Asia/Calcutta", now=NOW)
        self.assertEqual(datetime.fromtimestamp(ts, IST), datetime(2026, 10, 29, 22, 39, tzinfo=IST))

    def test_claude_bad_inputs(self):
        self.assertIsNone(fu.parse_claude_reset("soon", "Asia/Calcutta", now=NOW))
        self.assertIsNone(fu.parse_claude_reset("Oct 29 at 10pm", "Not/AZone", now=NOW))

    def test_codex_epoch_preserved(self):
        r = fu.parse_codex_rate_limits(
            {"primary": {"usedPercent": 8, "windowDurationMins": 43200, "resetsAt": 1795835731}},
            "t", now=NOW)
        self.assertEqual(r[0]["resetsAt"], 1795835731)
        self.assertEqual(r[0]["label"], "30-day")


class ClaudeUsageText(unittest.TestCase):
    SAMPLE = (
        "You are currently using your subscription to power your Claude Code usage\n\n"
        "Current session: 3% used · resets Oct 29 at 10:39pm (Asia/Calcutta)\n"
        "Current week (all models): 0% used · resets Nov 5 at 10:29am (Asia/Calcutta)\n"
        "Current week (Opus): 112% used · resets Nov 5 at 10:29am (Asia/Calcutta)\n"
    )

    def test_parse(self):
        mode, limits = fu.parse_claude_usage(self.SAMPLE, now=NOW)
        self.assertEqual(mode, "subscription")
        self.assertEqual([l["label"] for l in limits], ["Session (5h)", "Weekly", "Weekly · Opus"])
        self.assertEqual(limits[0]["usedPercent"], 3.0)      # preserved from source
        self.assertEqual(limits[1]["pctText"], "0%")
        self.assertTrue(limits[2]["overLimit"])
        self.assertEqual(limits[2]["remainingPercent"], 0.0)
        self.assertEqual(datetime.fromtimestamp(limits[1]["resetsAt"], IST),
                         datetime(2026, 11, 5, 10, 29, tzinfo=IST))

    def test_api_key_mode(self):
        mode, limits = fu.parse_claude_usage("You are using an API key; usage is billed per token.", now=NOW)
        self.assertEqual(mode, "api_key")
        self.assertEqual(limits, [])


class CodexRateLimits(unittest.TestCase):
    def test_app_server_shape(self):
        rl = {"limitId": "codex", "primary": {"usedPercent": 8, "windowDurationMins": 43200, "resetsAt": NOW + 999},
              "secondary": {"usedPercent": 55.5, "windowDurationMins": 10080, "resetsAt": NOW + 99}}
        r = fu.parse_codex_rate_limits(rl, "app", now=NOW)
        self.assertEqual([x["label"] for x in r], ["30-day", "Weekly"])
        self.assertEqual(r[1]["pctText"], "56%")
        self.assertAlmostEqual(r[1]["remainingPercent"], 44.5)

    def test_session_log_shape(self):
        rl = {"primary": {"used_percent": 8.0, "window_minutes": 300, "resets_at": NOW + 10}, "secondary": None}
        r = fu.parse_codex_rate_limits(rl, "log", as_of=NOW - 30, now=NOW)
        self.assertEqual(r[0]["label"], "5-hour")
        self.assertEqual(r[0]["asOf"], NOW - 30)

    def test_plan_names(self):
        self.assertEqual(fu.plan_name("go"), "Go")
        self.assertEqual(fu.plan_name("plus"), "Plus")
        self.assertIsNone(fu.plan_name(None))


class UnitsAndEstimates(unittest.TestCase):
    def test_fmt_tokens_units(self):
        self.assertEqual(fu.fmt_tokens(0), "0")
        self.assertEqual(fu.fmt_tokens(950), "950")
        self.assertEqual(fu.fmt_tokens(950_000), "950K")
        self.assertEqual(fu.fmt_tokens(8_094_066), "8.1M")
        self.assertEqual(fu.fmt_tokens(15_000_000), "15M")
        self.assertEqual(fu.fmt_tokens(2_500_000_000), "2.5B")

    def test_rolling_windows_are_separate_sums(self):
        today = date(2026, 10, 29)
        daily = [
            {"date": "2026-10-29", "totalTokens": 1_000_000},
            {"date": "2026-10-25", "totalTokens": 200_000},   # within 7d
            {"date": "2026-10-10", "totalTokens": 30_000},    # within 30d only
            {"date": "2026-08-01", "totalTokens": 9},         # outside
            {"date": "garbage", "totalTokens": 5},
        ]
        s = fu.summarize_daily(daily, today=today)
        self.assertEqual(s["totals"], {"today": 1_000_000, "7d": 1_200_000, "30d": 1_230_000})
        self.assertEqual(len(s["series"]), 30)
        self.assertEqual(s["series"][-1]["tokens"], 1_000_000)

    def test_cache_reads_excluded(self):
        s = fu.summarize_daily([{"date": "2026-10-29", "totalTokens": 100, "cacheReadTokens": 60}],
                               count_cache_reads=False, today=date(2026, 10, 29))
        self.assertEqual(s["totals"]["today"], 40)

    def test_codex_models_dict(self):
        s = fu.summarize_daily([{"date": "2026-10-29", "totalTokens": 30,
                                 "models": {"a": {"totalTokens": 10}, "b": {"totalTokens": 20}}}],
                               today=date(2026, 10, 29))
        self.assertEqual([m["name"] for m in s["series"][-1]["models"]], ["b", "a"])

    def test_estimate_has_no_cost_or_limit(self):
        s = fu.summarize_daily([{"date": "2026-10-29", "totalTokens": 1, "costUSD": 9}], today=date(2026, 10, 29))
        self.assertNotIn("cost", s["series"][-1])
        self.assertEqual(set(s["totals"]), {"today", "7d", "30d"})


class Masking(unittest.TestCase):
    def test_mask(self):
        self.assertEqual(fu.mask_email("jane.doe@example.com"), "ja•••@example.com")
        self.assertEqual(fu.mask_email(""), "")
        self.assertEqual(fu.mask_email("nope"), "")


if __name__ == "__main__":
    unittest.main()
