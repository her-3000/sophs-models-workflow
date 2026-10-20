"""Deterministic file-only checks in temporary directories; never touch real receipts."""
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).with_name("collector-health.py")
spec = importlib.util.spec_from_file_location("collector_health", SCRIPT)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
NOW = datetime.fromisoformat("2026-10-20T00:00:00+07:00")


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "hourly-state.lock").touch()
        self.guards("test:run", False)
        self.put("campaign-receipts.json", {"old-uncertain-" + str(i): {
            "run_id": "old", "status": "submitted_unverified", "source_url": "https://www.facebook.com/groups/1/posts/" + str(100 + i)
        } for i in range(8)})
        self.put("campaign-step-receipts.json", {})

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, value):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value))

    def guards(self, rid, active):
        state = {"run_id": rid, "thread_id": "collector", "active": active,
                 "status": "in_progress" if active else "blocked", "report": "report.json",
                 "started_at_bangkok": (NOW - timedelta(hours=1)).isoformat(),
                 "deadline": (NOW - timedelta(minutes=10)).isoformat()}
        self.put("hourly-state.json", state)
        self.put("social-collector-state.json", state)

    def queue(self):
        h.seed(self.root)
        return list(h.queue_view(self.root)["leads"])

    def checks(self):
        return {"live_checked_at": NOW.isoformat(), "checks": {k: True for k in h.LIVE_CHECKS}, "evidence": "reviews/live-check.json"}

    def record(self, minute=0, progress=None, cursor="same"):
        h.record(self.root, {"run_id": "test:run", "thread_id": "collector", "at": (NOW - timedelta(minutes=minute)).isoformat(),
            "phase": "checkpoint", "activity": "collecting", "page_url": "https://www.facebook.com/groups/1/?secret=omit",
            "cursor": cursor, "progress": progress or []})

    def test_synthetic_success_blocked_deferred_reports(self):
        for case, sends, flag in [("success", 2, "desktop_user_conflict"),
                                   ("blocked", 0, "loading_failure"),
                                   ("deferred", 0, "deferred_no_collection")]:
            with self.subTest(case=case):
                rid = "fixture:" + case
                verified_at = (NOW - timedelta(minutes=10)).isoformat()
                receipts = {"synthetic-send-" + str(i): {
                    "run_id": rid, "status": "verified_enquiry", "receipt": "fixture-comment",
                    "evidence": "fixture.png", "verified_at": verified_at
                } for i in range(sends)}
                self.put("campaign-receipts.json", receipts)
                report = {"run_id": rid, "status": "deferred" if case == "deferred" else "blocked",
                    "inflight_send": False, "stopped_at_bangkok": NOW.isoformat(),
                    "errors": [{"error": "user changed desktop" if case == "success" else "loading failure" if case == "blocked" else "deferred"}]}
                self.guards(rid, False)
                self.put("report.json", report)
                result = h.probe(self.root, NOW)
                self.assertTrue(result["terminal"])
                self.assertFalse(result["running"])
                self.assertEqual(result["verified_sends_this_run"], sends)
                self.assertIn(flag, result["flags"])
                self.assertFalse(result["coverage"]["all_groups_complete"])
                if sends:
                    self.assertEqual(h.stamp(result["last_verified_progress_at"]), h.stamp(verified_at))
                    self.assertNotEqual(result["last_verified_progress_at"], report["stopped_at_bangkok"])
                else:
                    self.assertIsNone(result["last_verified_progress_at"])

    def test_stuck_same_page_has_no_start_or_mtime_progress(self):
        self.guards("test:run", True)
        for minute in (30, 20, 10):
            self.record(minute)
        result = h.probe(self.root, NOW)
        self.assertIn("repeated_page_checkpoint_loop", result["flags"])
        self.assertIn("running_without_verified_progress_10min", result["flags"])
        self.assertIn("deadline_exceeded", result["flags"])
        self.assertIsNone(result["last_verified_progress_at"])
        self.assertFalse(result["terminal"])
        self.assertFalse(result["recovery_request_eligible"])

    def test_one_verified_interval_is_not_all_groups(self):
        self.guards("test:run", True)
        interval = {"kind": "verified_interval", "identity": "synthetic-source-window", "verified": True,
                    "source_id": "synthetic-source", "window_start": (NOW - timedelta(hours=2)).isoformat(),
                    "window_end": (NOW - timedelta(hours=1)).isoformat(), "contiguous": True,
                    "at": NOW.isoformat(), "evidence": "reviews/interval.json"}
        self.record(progress=[interval])
        result = h.probe(self.root, NOW)
        self.assertEqual(result["coverage"]["verified_intervals"], 1)
        self.assertEqual(result["coverage"]["source_windows"][0]["source_id"], "synthetic-source")
        self.assertFalse(result["coverage"]["all_groups_complete"])

    def test_same_post_does_not_advance_time_or_count_again(self):
        self.guards("test:run", True)
        post = {"kind": "verified_post", "identity": "https://www.facebook.com/groups/1/posts/999/", "verified": True,
                "at": (NOW - timedelta(minutes=5)).isoformat(), "evidence": "reviews/post.json"}
        self.record(progress=[post])
        post["at"] = NOW.isoformat()
        self.record(progress=[post])
        result = h.probe(self.root, NOW)
        self.assertEqual(result["verified_progress_count"], 1)
        self.assertEqual(h.stamp(result["last_verified_progress_at"]), NOW - timedelta(minutes=5))
        self.guards("next:run", True)
        h.record(self.root, {"run_id": "next:run", "thread_id": "collector", "at": NOW.isoformat(), "activity": "collecting", "phase": "checkpoint", "progress": [post]})
        self.assertEqual(h.probe(self.root, NOW)["verified_progress_count"], 0)

    def test_readiness_is_explicit_and_can_be_stale(self):
        self.guards("test:run", True)
        h.record(self.root, {"run_id": "test:run", "thread_id": "collector", "phase": "preflight", "activity": "preflight",
            "at": (NOW - timedelta(hours=1)).isoformat(), "readiness": {"machine_connected": True, "browser": "Safari", "account_verified": True, "page_ready": True, "evidence": "reviews/preflight.json"}})
        result = h.probe(self.root, NOW)
        self.assertFalse(result["readiness"]["fresh_at_probe"])
        self.assertIsNone(result["last_verified_progress_at"])

    def failed_report(self, route="https://www.facebook.com/messages/?token=secret"):
        self.put("report.json", {"run_id": "test:run", "status": "blocked", "inflight_send": False,
            "stopped_at_bangkok": (NOW - timedelta(minutes=1)).isoformat(), "errors": [{"url": route, "route": "CalMessenger", "steps": ["navigation timed out loading"]}]})

    def test_recovery_claim_once_fresh_idle_required_and_no_global_uncertain_freeze(self):
        self.failed_report()
        result = h.probe(self.root, NOW)
        self.assertEqual(result["uncertain_or_intent_total"], 8)
        self.assertEqual(result["uncertain_or_intent_this_run"], 0)
        self.assertTrue(result["recovery_request_eligible"])
        self.assertNotIn("secret", json.dumps(result))
        self.assertFalse(h.claim(self.root, NOW, "test:run", result["blocker_fingerprint"], None)["ok"])
        first = h.claim(self.root, NOW, "test:run", result["blocker_fingerprint"], NOW.isoformat())
        self.assertTrue(first["ok"])
        self.assertFalse(first["recovery_executed"])
        self.assertFalse(h.claim(self.root, NOW, "test:run", result["blocker_fingerprint"], NOW.isoformat())["ok"])

    def test_fingerprint_retains_public_route_identity(self):
        self.failed_report()
        a = h.probe(self.root, NOW)["blocker_fingerprint"]
        self.failed_report("https://www.facebook.com/groups/1/posts/9/?token=secret")
        self.assertNotEqual(a, h.probe(self.root, NOW)["blocker_fingerprint"])

    def test_busy_owner_and_affected_uncertain_deny_recovery(self):
        self.failed_report()
        self.put("social-collector-state.json", {"active": True, "run_id": "other", "thread_id": "video"})
        result = h.probe(self.root, NOW)
        self.assertFalse(result["recovery_request_eligible"])
        self.assertIn("desktop_owner_conflict", result["flags"])
        self.guards("test:run", False)
        receipts = h.read(self.root / "campaign-receipts.json", {})
        receipts["affected"] = {"run_id": "test:run", "status": "submitted_unverified"}
        self.put("campaign-receipts.json", receipts)
        self.assertFalse(h.probe(self.root, NOW)["recovery_request_eligible"])

    def test_three_seeds_only_verified_sends_excluded_and_eight_protected(self):
        receipts = h.read(self.root / "campaign-receipts.json", {})
        for post in ("9000000000000004", "9000000000000002"):
            receipts["sent-" + post] = {"status": "verified_enquiry", "source_url": "https://www.facebook.com/groups/9000000000000000/posts/" + post + "/", "receipt": "https://www.facebook.com/messages/t/1/"}
        self.put("campaign-receipts.json", receipts)
        keys = self.queue()
        self.assertEqual(len(keys), 3)
        queue = h.queue_view(self.root)
        self.assertEqual(len(queue["protected_uncertain_campaigns"]), 8)
        self.assertEqual({v["original_post_id"] for v in queue["leads"].values()}, set(h.SEEDS))
        self.assertTrue(all(v["expiry"]["at"] is None and v["live_recheck_required"] for v in queue["leads"].values()))
        for post in ("9000000000000004", "9000000000000002"):
            with self.assertRaises(KeyError):
                h.seed_candidate(post)

    def test_busy_and_missing_live_checks_cannot_claim_lead(self):
        key = self.queue()[0]
        self.guards("other", True)
        with self.assertRaises(ValueError):
            h.claim_lead(self.root, NOW, key, "test:run", "collector", self.checks())
        self.guards("test:run", True)
        incomplete = self.checks()
        incomplete["checks"]["current_rules_checked"] = False
        with self.assertRaises(ValueError):
            h.claim_lead(self.root, NOW, key, "test:run", "collector", incomplete)

    def test_known_unsent_failure_can_retry_next_healthy_run_only(self):
        key = self.queue()[0]
        self.guards("test:run", True)
        claim = h.claim_lead(self.root, NOW, key, "test:run", "collector", self.checks())
        h.outcome(self.root, NOW, key, "test:run", claim["claim_token"], {"outcome": "known_unsent", "transmission_started": False, "evidence": "reviews/not-sent.json"})
        with self.assertRaises(ValueError):
            h.claim_lead(self.root, NOW, key, "test:run", "collector", self.checks())
        self.guards("next:run", True)
        self.assertTrue(h.claim_lead(self.root, NOW, key, "next:run", "collector", self.checks())["ok"])

    def test_claim_crash_and_uncertain_send_quarantined(self):
        key = self.queue()[0]
        self.guards("test:run", True)
        claim = h.claim_lead(self.root, NOW, key, "test:run", "collector", self.checks())
        # Simulate a crash between atomic claim and queue-file writes.
        queue = h.read(self.root / h.QUEUE, {})
        queue["leads"][key]["current_status"] = "known_unsent"
        self.put(h.QUEUE, queue)
        self.assertEqual(h.queue_view(self.root)["leads"][key]["current_status"], "quarantined_claim_outcome_unknown")
        h.outcome(self.root, NOW, key, "test:run", claim["claim_token"], {"outcome": "uncertain", "transmission_started": True, "evidence": "reviews/uncertain.json"})
        self.assertEqual(h.queue_view(self.root)["leads"][key]["current_status"], "quarantined_uncertain")
        self.guards("next:run", True)
        with self.assertRaises(ValueError):
            h.claim_lead(self.root, NOW, key, "next:run", "collector", self.checks())

    def test_affected_canonical_uncertain_cannot_promote(self):
        key = self.queue()[0]
        lead = h.queue_view(self.root)["leads"][key]
        receipts = h.read(self.root / "campaign-receipts.json", {})
        receipts[lead["campaign_key"]] = {"status": "submitted_unverified", "source_url": lead["original_url"]}
        self.put("campaign-receipts.json", receipts)
        self.assertEqual(h.queue_view(self.root)["leads"][key]["current_status"], "quarantined_uncertain")
        self.guards("test:run", True)
        with self.assertRaises(ValueError):
            h.claim_lead(self.root, NOW, key, "test:run", "collector", self.checks())

    def test_verified_receipt_terminal_and_next_lead_without_reply_wait(self):
        keys = self.queue()
        key = keys[0]
        self.guards("test:run", True)
        claim = h.claim_lead(self.root, NOW, key, "test:run", "collector", self.checks())
        lead = h.queue_view(self.root)["leads"][key]
        receipts = h.read(self.root / "campaign-receipts.json", {})
        receipts[lead["campaign_key"]] = {"status": "verified_enquiry", "receipt": lead["original_url"] + "?comment_id=123", "source_url": lead["original_url"], "evidence": "reviews/sent.png"}
        self.put("campaign-receipts.json", receipts)
        result = h.outcome(self.root, NOW, key, "test:run", claim["claim_token"], {"outcome": "verified_sent", "transmission_started": True})
        self.assertEqual(result["current_status"], "verified_sent")
        self.assertEqual(result["next_to_revalidate"], keys[1])
        self.assertFalse(result["wait_for_organiser_reply"])
        self.guards("next:run", True)
        with self.assertRaises(ValueError):
            h.claim_lead(self.root, NOW, key, "next:run", "collector", self.checks())

    def test_expired_and_owner_cancelled_stop(self):
        keys = self.queue()
        self.guards("test:run", True)
        h.defer_lead(self.root, NOW, keys[0], "test:run", "collector", {"outcome": "expired", "expiry_at": (NOW - timedelta(hours=1)).isoformat(), "live_original_verified": True, "transmission_started": False, "evidence": "reviews/deadline.png", "failure_code": "verified_expired"})
        h.defer_lead(self.root, NOW, keys[1], "test:run", "collector", {"outcome": "cancelled", "owner_cancelled": True, "transmission_started": False, "evidence": "reviews/owner-cancel.json", "failure_code": "owner_cancelled"})
        self.assertEqual(h.queue_view(self.root)["leads"][keys[0]]["current_status"], "expired")
        self.assertEqual(h.queue_view(self.root)["leads"][keys[1]]["current_status"], "cancelled")
        self.guards("next:run", True)
        for key in keys[:2]:
            with self.assertRaises(ValueError):
                h.claim_lead(self.root, NOW, key, "next:run", "collector", self.checks())

    def test_missing_guards_or_receipt_input_fail_closed(self):
        (self.root / "campaign-receipts.json").unlink()
        with self.assertRaises(ValueError):
            h.probe(self.root, NOW)
        with self.assertRaises(ValueError):
            h.seed(self.root)

    def test_url_sanitisation_drops_userinfo_port_and_auth_query(self):
        self.assertEqual(h.url("https://user:secret@www.facebook.com:443/groups/1/posts/2/?token=private#x"),
                         "https://www.facebook.com/groups/1/posts/2")
        self.assertIsNone(h.url("https://untrusted.example/private"))

    def test_unknown_expiry_is_not_a_clock_based_ready_transition(self):
        key = self.queue()[0]
        later = NOW + timedelta(days=3)
        self.guards("test:run", True)
        checks = self.checks()
        checks["live_checked_at"] = later.isoformat()
        checks["checks"]["live_original_verified"] = False
        self.assertEqual(h.queue_view(self.root)["leads"][key]["current_status"], "known_unsent")
        with self.assertRaises(ValueError):
            h.claim_lead(self.root, later, key, "test:run", "collector", checks)


if __name__ == "__main__":
    unittest.main(verbosity=2)
