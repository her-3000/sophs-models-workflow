#!/usr/bin/env python3
"""Owner-local evidence probe. No UI, network, sends, recovery execution or unlocks."""
import argparse
import fcntl
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
HEALTH = "collector-health.json"
CHECKPOINTS = "collector-health-checkpoints.json"
CLAIMS = "collector-health-recovery-claims.json"
QUEUE = "collector-pending-leads.json"
LEAD_CLAIMS = "collector-lead-claims.json"
SEEDS = {
    "9000000000000003": ("synthetic-campaign-a", "Synthetic brief A; fee/date/venue unknown", "facebook_comment", "loading_failure"),
    "9000000000000005": ("synthetic-campaign-b", "Synthetic brief B; fee/date/venue unknown", "facebook_comment_or_dm", "user_interrupted_navigation"),
    "9000000000000001": ("synthetic-campaign-c", "Synthetic brief C; fee/date/venue unknown", "facebook_dm", "unreached_after_desktop_interruption"),
}
LIVE_CHECKS = {"machine_connected", "safari_ready", "account_verified", "desktop_stable",
               "live_original_verified", "still_open", "suitable", "current_rules_checked",
               "approved_assets", "preferred_route_verified", "receipts_reconciled", "send_authorised"}
ACTIVITIES = {"maintenance", "preflight", "collecting", "enquiry_sending", "idle", "blocked", "postflight"}


def read(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save(path, data):
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            json.dump(data, out, ensure_ascii=False, indent=2)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def stamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo else None
    except (AttributeError, TypeError, ValueError):
        return None


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:20]


def url(value):
    """Public source identity only: omit query tokens, fragments and private origins."""
    parsed = urlsplit(value or "")
    if parsed.scheme != "https" or parsed.hostname not in {
        "facebook.com", "www.facebook.com", "instagram.com", "www.instagram.com"
    }:
        return None
    return "https://" + parsed.hostname + parsed.path.rstrip("/")


def probe(root, now, report_name=None):
    for required in ("hourly-state.json", "social-collector-state.json", "campaign-receipts.json", "campaign-step-receipts.json"):
        if not (root / required).is_file():
            raise ValueError("required_input_missing:" + required)
    state = read(root / "hourly-state.json", {})
    lease = read(root / "social-collector-state.json", {})
    name = report_name or state.get("report")
    report = {}
    if name:
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or path.suffix != ".json":
            raise ValueError("report_must_be_owner_local_json")
        report = read(path, {})
    # An old report pointer on a running state must never replace the current run.
    running = state.get("active") is True and not report_name
    rid = state.get("run_id") if running else report.get("run_id", state.get("run_id"))
    if not rid:
        raise ValueError("run_identity_missing")
    if report.get("run_id") != rid:
        report = {}
    current = state.get("run_id") == rid
    status = state.get("status", "unknown") if running else report.get("status", state.get("status", "unknown") if current else "unknown")
    stopped = stamp(report.get("stopped_at_bangkok") or (state.get("stopped_at_bangkok") if current else None))
    terminal = not running and (stopped is not None or status in {"deferred", "blocked", "completed", "complete"})
    all_events = read(root / CHECKPOINTS, {})
    events = all_events.get(rid, [])
    earlier = {(p.get("kind"), p.get("identity")) for other, rows in all_events.items() if other != rid
               for row in rows for p in row.get("progress", []) if valid_progress(p)}
    progress = {}
    intervals = 0
    posts = 0
    for event in events:
        for item in event.get("progress", []):
            # record() validates these; validate again because files are inputs.
            if not valid_progress(item) or stamp(item["at"]) > now:
                continue
            identity = (item["kind"], item["identity"])
            if identity in earlier:
                continue
            progress.setdefault(identity, item["at"])
    intervals = sum(k[0] == "verified_interval" for k in progress)
    posts = sum(k[0] == "verified_post" for k in progress)
    receipts = read(root / "campaign-receipts.json", {})
    sends = []
    uncertain = 0
    uncertain_this_run = 0
    for key, item in receipts.items():
        if item.get("status") in {"intent", "submitted_unverified"} or item.get("uncertain_send") is True:
            uncertain += 1
            uncertain_this_run += item.get("run_id") == rid
        if (item.get("run_id") == rid and item.get("status") in {"verified", "verified_enquiry"}
                and item.get("receipt") and item.get("evidence") and stamp(item.get("verified_at"))
                and stamp(item["verified_at"]) <= now):
            sends.append(key)
            progress[("verified_send", key)] = item["verified_at"]
    times = [stamp(t) for t in progress.values() if stamp(t) and stamp(t) <= now]
    last = max(times).isoformat() if times else None
    flags = []
    # Only operational failure sections, not brief text, old diagnoses or digests.
    failure = json.dumps({k: report.get(k) for k in ("errors", "ui_stop", "stop_reason", "actual_errors")}).lower()
    if any(t in failure for t in ("timeout", "timedout", "timed out", "loading", "splash", "blank")):
        flags.append("loading_failure")
    if any(t in failure for t in ("user changed", "userchanged", "user-control", "usercontrol")):
        flags.append("desktop_user_conflict")
    if any(t in failure for t in ("captcha", "login challenge", "security approval", "not allowed", "safety reasons")):
        flags.append("restricted_route")
    flags += [e["blocker"]["category"] for e in events if e.get("blocker")]
    busy = [(s.get("run_id"), s.get("thread_id")) for s in (state, lease) if s.get("active") is True]
    if any(owner != rid for owner, _ in busy) or len(set(busy)) > 1:
        flags.append("desktop_owner_conflict")
    if status == "deferred":
        flags.append("deferred_no_collection")
    elif terminal and not progress:
        flags.append("terminal_progress_unverified")
    start = stamp(state.get("started_at_bangkok")) if current else stamp(report.get("started_at_bangkok"))
    deadline = stamp(state.get("deadline")) if current else stamp(report.get("deadline"))
    if running and deadline and now > deadline:
        flags.append("deadline_exceeded")
    if running and start and (now - (stamp(last) or start)).total_seconds() > 600:
        flags.append("running_without_verified_progress_10min")
    cursor_events = [e for e in events if e.get("cursor") and e.get("page_url")]
    if len(cursor_events) >= 3:
        tail = cursor_events[-3:]
        if len({(e["page_url"], e["cursor"]) for e in tail}) == 1:
            first_at = stamp(tail[0].get("at"))
            if not any(stamp(t) and first_at and stamp(t) >= first_at for t in progress.values()):
                flags.append("repeated_page_checkpoint_loop")
    ready_event = next((e for e in reversed(events) if e.get("readiness")), {})
    readiness = ready_event.get("readiness", {})
    if readiness:
        observed = stamp(ready_event.get("at"))
        readiness = {**readiness, "observed": True, "observed_at": ready_event["at"],
                     "fresh_at_probe": bool(observed and 0 <= (now - observed).total_seconds() <= 300)}
    cursor = digest([(e.get("page_url"), e.get("cursor")) for e in cursor_events[-1:]]) if cursor_events else None
    old = read(root / HEALTH, {})
    history = old.get("recent_runs", [])
    comparable = [h for h in history if h.get("run_id") != rid][-2:]
    if cursor and len(comparable) == 2 and all(h.get("cursor_fingerprint") == cursor and not h.get("verified_work") for h in comparable) and not progress:
        flags.append("repeated_checkpoint_across_runs")
    # A receipt is a send, not proof of source coverage. Legacy visits never become intervals.
    windows = [{k: p[k] for k in ("source_id", "window_start", "window_end", "evidence", "at")}
               for e in events for p in e.get("progress", [])
               if valid_progress(p) and p["kind"] == "verified_interval"]
    coverage = {"verified_intervals": intervals, "verified_posts": posts, "source_windows": windows,
                "all_groups_complete": False, "global_completeness_assessed": False,
                "legacy_report_complete_claim": report.get("complete_coverage", False),
                "legacy_coverage_claim_validated": False}
    recoveries = [e.get("recovery", "none") for e in events if e.get("recovery") not in (None, "none")]
    reload_used = "ordinary_reload" in recoveries or any(t in failure for t in ("one ordinary reload", "one normalreload", "one normal reload", "one refresh", "single permittedreload"))
    routes = sorted({url(e.get("url")) or (e.get("route") if e.get("route") in {"CalMessenger", "SOPHSPageMessenger", "agencyInstagram", "Safari", "facebook", "instagram", "LINE"} else "route:" + digest(e.get("route")))
                     for e in report.get("errors", []) if isinstance(e, dict)})
    if report.get("ui_stop"):
        routes.append("safari_desktop")
    routes += [e["blocker"]["route"] for e in events if e.get("blocker")]
    fingerprint = digest({"categories": sorted(set(flags)), "routes": sorted(set(routes))}) if flags else None
    claims = read(root / CLAIMS, {})
    guards_idle = (state.get("active") is False and lease.get("active") is False
                   and state.get("run_id") == lease.get("run_id") == rid
                   and state.get("thread_id") == lease.get("thread_id") and bool(state.get("thread_id")))
    eligible = (terminal and current and "loading_failure" in flags and guards_idle and not uncertain_this_run
                and not reload_used and rid not in claims
                and not set(flags) & {"restricted_route", "desktop_user_conflict", "desktop_owner_conflict"}
                and report.get("inflight_send") is False)
    snapshot = {"run_id": rid, "cursor_fingerprint": cursor, "verified_work": bool(progress)}
    queued = queue_view(root).get("leads", {})
    queue_counts = {s: sum(l["current_status"] == s for l in queued.values()) for s in {l["current_status"] for l in queued.values()}}
    return {"schema_version": 1, "checked_at": now.isoformat(), "run_id": rid,
            "thread_id": state.get("thread_id") if current else report.get("thread_id"),
            "status": status, "running": running, "terminal": terminal,
            "activity": events[-1].get("activity", "running_unverified") if running and events else ("running_unverified" if running else "blocked" if status.startswith("blocked") else "idle"),
            "last_verified_progress_at": last, "last_progress_time": last, "verified_progress_count": len(progress),
            "coverage": coverage, "verified_sends_this_run": len(sends), "uncertain_or_intent_total": uncertain,
            "uncertain_or_intent_this_run": uncertain_this_run, "guards_verified_inactive": guards_idle,
            "readiness": readiness or {"observed": False}, "recoveries": recoveries,
            "ordinary_reload_already_used": reload_used, "flags": sorted(set(flags)),
            "blocker_fingerprint": fingerprint, "blocker_routes": sorted(set(routes)), "cursor_fingerprint": cursor,
            "recovery_request_eligible": eligible, "recovery_request_only": True,
            "recovery_execution_permitted": False, "owner_idle_verification_still_required": True,
            "recovery_claim_exists": rid in claims,
            "pending_queue": {"total": len(queued), "status_counts": queue_counts, "no_automatic_ready_state": True},
            "recent_runs": (comparable + [snapshot])[-3:]}


def valid_progress(item):
    return (isinstance(item, dict) and item.get("kind") in {"verified_post", "verified_interval"}
            and item.get("verified") is True and isinstance(item.get("identity"), str) and 0 < len(item["identity"]) <= 256
            and isinstance(item.get("evidence"), str) and 0 < len(item["evidence"]) <= 512 and stamp(item.get("at")) is not None
            and (item["kind"] != "verified_post" or url(item["identity"]) is not None)
            and (item["kind"] != "verified_interval" or (item.get("contiguous") is True
                 and stamp(item.get("window_start")) and stamp(item.get("window_end"))
                 and stamp(item["window_start"]) < stamp(item["window_end"]) and item.get("source_id"))))


def record(root, data):
    state = read(root / "hourly-state.json", {})
    lease = read(root / "social-collector-state.json", {})
    rid = data.get("run_id")
    if not rid or state.get("run_id") != rid or lease.get("run_id") != rid:
        raise ValueError("matching_own_run_and_lease_required")
    if data.get("thread_id") != state.get("thread_id") or data.get("thread_id") != lease.get("thread_id"):
        raise ValueError("matching_owner_thread_required")
    if not stamp(data.get("at")) or data.get("phase") not in {"preflight", "checkpoint", "postflight"}:
        raise ValueError("aware_timestamp_and_phase_required")
    progress = data.get("progress", [])
    if not isinstance(progress, list) or not all(valid_progress(p) for p in progress):
        raise ValueError("verified_progress_evidence_required")
    progress = [{k: p[k] for k in ("kind", "identity", "at", "verified", "evidence", "contiguous",
                                  "source_id", "window_start", "window_end") if k in p} for p in progress]
    ready = data.get("readiness", {})
    allowed = {"machine_connected", "browser", "account_verified", "page_ready", "evidence", "auth_blocked_channels"}
    if set(ready) - allowed or (ready and (ready.get("browser") != "Safari" or not ready.get("evidence"))):
        raise ValueError("explicit_safari_readiness_evidence_required")
    if (any(k in ready and not isinstance(ready[k], bool) for k in ("machine_connected", "account_verified", "page_ready"))
            or (ready and not isinstance(ready.get("evidence"), str))
            or not isinstance(ready.get("auth_blocked_channels", []), list)
            or any(x not in {"CalMessenger", "SOPHSPageMessenger", "agencyInstagram"} for x in ready.get("auth_blocked_channels", []))):
        raise ValueError("typed_readiness_fields_required")
    entry = {k: data[k] for k in ("run_id", "at", "phase")}
    if data.get("activity") not in ACTIVITIES:
        raise ValueError("explicit_activity_required")
    entry["activity"] = data["activity"]
    entry.update(progress=progress, readiness=ready, page_url=url(data.get("page_url")),
                 cursor=data.get("cursor"), recovery=data.get("recovery", "none"))
    if entry["recovery"] not in {"none", "normal_readiness", "ordinary_reload"}:
        raise ValueError("unsupported_recovery_record")
    # Never accept a blanket complete flag from a visited-group count.
    # Scope is per source/window; this probe never certifies all-group completeness.
    blocker = data.get("blocker")
    if blocker:
        if blocker.get("category") not in {"loading_failure", "desktop_user_conflict", "restricted_route", "desktop_owner_conflict"} or not url(blocker.get("route")):
            raise ValueError("public_route_and_blocker_category_required")
        entry["blocker"] = {"category": blocker["category"], "route": url(blocker["route"])}
    entries = read(root / CHECKPOINTS, {})
    entries[rid] = (entries.get(rid, []) + [entry])[-100:]
    save(root / CHECKPOINTS, entries)
    return {"ok": True, "run_id": rid, "phase": entry["phase"]}


def fresh_idle_check(now, value):
    at = stamp(value)
    return bool(at and 0 <= (now - at).total_seconds() <= 60)


def claim(root, now, rid, fingerprint, idle_at, lead_keys=()):
    health = probe(root, now)
    if (not fresh_idle_check(now, idle_at) or health["run_id"] != rid or not fingerprint
            or health["blocker_fingerprint"] != fingerprint or not health["recovery_request_eligible"]):
        return {"ok": False, "reason": "not_safe_or_already_claimed", "recovery_executed": False}
    claims = read(root / CLAIMS, {})
    if rid in claims:
        return {"ok": False, "reason": "run_already_claimed", "recovery_executed": False}
    queue = read(root / QUEUE, {}).get("leads", {})
    if any(k not in queue or queue[k]["current_status"] != "known_unsent" or receipt_state(root, queue[k])[0] != "unsent" for k in lead_keys):
        return {"ok": False, "reason": "lead_not_known_unsent", "recovery_executed": False}
    claims[rid] = {"at": now.isoformat(), "blocker_fingerprint": fingerprint, "lead_keys": sorted(set(lead_keys)),
                   "owner_idle_verified_at": idle_at,
                   "target_thread_id": health["thread_id"], "status": "request_reserved_not_executed"}
    save(root / CLAIMS, claims)
    return {"ok": True, "run_id": rid, "target_thread_id": health["thread_id"],
            "request": "Existing collector only: after fresh ownership/idle checks, normal readiness and at most one unused ordinary reload. Reconcile uncertain sends; no forced unlock or bypass.",
            "recovery_executed": False}


def receipt_state(root, lead):
    """Exact original post OR same canonical campaign; never infer an unsent outcome."""
    matched = []
    for filename in ("campaign-receipts.json", "campaign-step-receipts.json"):
        for key, row in read(root / filename, {}).items():
            sources = [row.get(k, "") for k in ("source_url", "source", "post_url", "receipt")]
            if (key == lead["campaign_key"] or row.get("campaign_key") == lead["campaign_key"]
                    or any(("/" + lead["original_post_id"]) in s for s in sources if isinstance(s, str))):
                matched.append(row)
    uncertain = [r for r in matched if r.get("status") in {"intent", "submitted_unverified", "pending"} or r.get("uncertain_send") is True]
    if uncertain:
        return "uncertain", []
    verified = [r["receipt"] for r in matched if r.get("status") in {"verified", "verified_enquiry"} and r.get("receipt")]
    return ("verified", sorted(set(verified))) if verified else ("unsent", [])


def seed_candidate(post_id):
    campaign, brief, route, failure = SEEDS[post_id]
    key = "sophie|" + campaign + "|female|facebook:" + post_id
    return key, {"original_post_id": post_id, "model": "sophie", "role": "female",
                 "campaign_key": "sophie|" + campaign + "|female", "known_brief": brief,
                 "original_url": "https://www.facebook.com/groups/9000000000000000/posts/" + post_id + "/",
                 "preferred_route": {"known_from_saved_brief": route, "currently_verified": False},
                 "last_failure": failure, "last_attempt_run": "fixture:historical-attempt",
                 "last_attempt_at": None, "last_attempt_time_precision": "not captured / no enquiry transmission",
                 "failure_evidence": "reviews/fixture-history.json",
                 "expiry": {"at": None, "known": False, "live_recheck_required": True},
                 "next_eligible_retry": {"not_before": None, "condition": "next healthy single-owner run after all live checks"},
                 "current_status": "known_unsent", "receipt_references": []}


def seed(root):
    # Maintenance seed is permitted only while both guards are explicitly inactive.
    guards = [read(root / f, {}) for f in ("hourly-state.json", "social-collector-state.json")]
    if any(g.get("active") is not False for g in guards):
        raise ValueError("inactive_guards_required")
    if not (root / "campaign-receipts.json").is_file() or not (root / "campaign-step-receipts.json").is_file():
        raise ValueError("receipt_inputs_required")
    queue = read(root / QUEUE, {"schema_version": 1, "leads": {}})
    for post in SEEDS:
        key, candidate = seed_candidate(post)
        if receipt_state(root, candidate)[0] != "unsent":
            continue
        queue["leads"].setdefault(key, candidate)
    protected = read(root / "campaign-receipts.json", {})
    queue["protected_uncertain_campaigns"] = sorted(k for k, v in protected.items()
        if v.get("status") in {"submitted_unverified", "pending", "intent"} or v.get("uncertain_send") is True)
    save(root / QUEUE, queue)
    return {"ok": True, "queued": len(queue["leads"]), "protected_uncertain": len(queue["protected_uncertain_campaigns"])}


def queue_view(root):
    queue = read(root / QUEUE, {"schema_version": 1, "leads": {}})
    # Derived read-only view reconciles receipts, but never labels anything ready.
    claims = read(root / LEAD_CLAIMS, {})
    for key, lead in queue["leads"].items():
        state, refs = receipt_state(root, lead)
        lead["receipt_reconciliation"] = state
        lead["live_recheck_required"] = True
        if state == "verified":
            lead.update(current_status="verified_sent", receipt_references=refs)
        elif state == "uncertain":
            lead["current_status"] = "quarantined_uncertain"
        elif any(c.get("lead_key") == key and c.get("status") == "outcome_unknown" for c in claims.values()):
            lead["current_status"] = "quarantined_claim_outcome_unknown"
    return queue


def claim_lead(root, now, key, rid, thread, data):
    queue = queue_view(root)
    if key not in queue["leads"]:
        raise ValueError("lead_not_in_authorised_queue")
    lead = queue["leads"][key]
    post = lead.get("original_post_id")
    if post not in SEEDS or seed_candidate(post)[0] != key or seed_candidate(post)[1]["campaign_key"] != lead.get("campaign_key"):
        raise ValueError("lead_outside_authorised_scope")
    if lead["current_status"] != "known_unsent":
        raise ValueError("lead_not_known_unsent")
    guards = [read(root / f, {}) for f in ("hourly-state.json", "social-collector-state.json")]
    if any(g.get("active") is not True or g.get("run_id") != rid or g.get("thread_id") != thread for g in guards):
        raise ValueError("active_matching_single_owner_required")
    if not fresh_idle_check(now, data.get("live_checked_at")) or set(data.get("checks", {})) != LIVE_CHECKS or not all(v is True for v in data["checks"].values()) or not data.get("evidence"):
        raise ValueError("fresh_complete_live_checks_required")
    expiry = stamp(lead.get("expiry", {}).get("at"))
    if lead.get("expiry", {}).get("known") is True and not expiry:
        raise ValueError("known_expiry_requires_aware_timestamp")
    if expiry and now >= expiry:
        raise ValueError("verified_expiry_passed")
    claims = read(root / LEAD_CLAIMS, {})
    identity = rid + "|" + key
    if identity in claims:
        raise ValueError("lead_already_claimed_this_run")
    token = digest([rid, key, now.isoformat()])
    claims[identity] = {"token": token, "run_id": rid, "lead_key": key, "at": now.isoformat(),
                        "blocker_fingerprint": digest(lead["last_failure"]), "status": "outcome_unknown"}
    # Claim is conservative: a crash may hide a transmission. Never auto-retry it.
    lead.update(current_status="quarantined_claim_outcome_unknown", active_claim=identity,
                last_attempt_run=rid, last_attempt_at=now.isoformat())
    save(root / LEAD_CLAIMS, claims)
    save(root / QUEUE, queue)
    return {"ok": True, "claim_token": token, "run_id": rid, "lead_key": key,
            "send_executed": False, "instruction": "Existing collector only; save canonical intent before any transmission and persist actual outcome. No blind retry."}


def outcome(root, now, key, rid, token, data):
    queue = read(root / QUEUE, {})
    lead = queue.get("leads", {}).get(key)
    claims = read(root / LEAD_CLAIMS, {})
    identity = rid + "|" + key
    claim_row = claims.get(identity, {})
    if not lead or claim_row.get("token") != token or claim_row.get("status") != "outcome_unknown":
        raise ValueError("matching_unresolved_claim_required")
    state, refs = receipt_state(root, lead)
    requested = data.get("outcome")
    if state == "verified":
        lead.update(current_status="verified_sent", receipt_references=refs)
    elif state == "uncertain" or requested == "uncertain" or data.get("transmission_started") is not False:
        lead["current_status"] = "quarantined_uncertain"
    elif not data.get("evidence"):
        raise ValueError("verified_outcome_evidence_required")
    elif requested == "known_unsent":
        lead.update(current_status="known_unsent", last_failure=data.get("failure_code", "known_not_transmitted"))
    elif requested in {"expired", "unsuitable", "cancelled", "essential_input_missing", "permission_missing"}:
        if requested == "expired":
            expiry = stamp(data.get("expiry_at"))
            if not expiry or expiry > now or data.get("live_original_verified") is not True:
                raise ValueError("verified_original_expiry_required")
            lead["expiry"] = {"at": expiry.isoformat(), "known": True, "evidence": data["evidence"]}
        if requested == "cancelled" and data.get("owner_cancelled") is not True:
            raise ValueError("owner_cancellation_required")
        lead["current_status"] = requested
    else:
        raise ValueError("unsupported_outcome")
    lead["last_outcome_evidence"] = data.get("evidence")
    claim_row.update(status="outcome_recorded", recorded_at=now.isoformat(), outcome=lead["current_status"])
    save(root / QUEUE, queue)
    save(root / LEAD_CLAIMS, claims)
    next_keys = [k for k, row in queue_view(root)["leads"].items() if row["current_status"] == "known_unsent" and rid + "|" + k not in claims]
    return {"ok": True, "current_status": lead["current_status"], "send_executed": False,
            "next_to_revalidate": next_keys[0] if next_keys else None, "wait_for_organiser_reply": False}


def defer_lead(root, now, key, rid, thread, data):
    queue = queue_view(root)
    lead = queue.get("leads", {}).get(key)
    guards = [read(root / f, {}) for f in ("hourly-state.json", "social-collector-state.json")]
    if not lead or lead["current_status"] != "known_unsent" or any(g.get("active") is not True or g.get("run_id") != rid or g.get("thread_id") != thread for g in guards):
        raise ValueError("known_unsent_and_active_matching_owner_required")
    if data.get("transmission_started") is not False or not data.get("evidence") or not data.get("failure_code"):
        raise ValueError("known_no_transmission_evidence_required")
    claims = read(root / LEAD_CLAIMS, {})
    identity = rid + "|" + key
    if identity in claims:
        raise ValueError("lead_already_attempted_this_run")
    claims[identity] = {"run_id": rid, "lead_key": key, "at": now.isoformat(), "status": "known_unsent_read_failure"}
    lead.update(last_failure=data["failure_code"], failure_evidence=data["evidence"],
                last_attempt_run=rid, last_attempt_at=now.isoformat())
    disposition = data.get("outcome", "known_unsent")
    if disposition not in {"known_unsent", "expired", "unsuitable", "cancelled", "essential_input_missing", "permission_missing"}:
        raise ValueError("unsupported_no_send_disposition")
    if disposition == "expired":
        expiry = stamp(data.get("expiry_at"))
        if not expiry or expiry > now or data.get("live_original_verified") is not True:
            raise ValueError("verified_original_expiry_required")
        lead["expiry"] = {"at": expiry.isoformat(), "known": True, "evidence": data["evidence"]}
    if disposition == "cancelled" and data.get("owner_cancelled") is not True:
        raise ValueError("owner_cancellation_required")
    lead["current_status"] = disposition
    save(root / LEAD_CLAIMS, claims)
    save(root / QUEUE, queue)
    return {"ok": True, "current_status": disposition, "retry_same_run": False, "send_executed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["probe", "record", "claim-recovery", "seed-queue", "read-queue", "claim-lead", "lead-outcome", "defer-lead"])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--report", help="Existing owner-local report; diagnostic only")
    parser.add_argument("--now", help="Aware ISO timestamp, for deterministic dry runs")
    parser.add_argument("--stdout-only", action="store_true")
    parser.add_argument("--data", help="Collector's compact structured checkpoint JSON")
    parser.add_argument("--run-id")
    parser.add_argument("--fingerprint")
    parser.add_argument("--owner-idle-at", help="Aware time of caller's independently verified owner-thread idle check, within60s")
    parser.add_argument("--lead-key", action="append", default=[])
    parser.add_argument("--thread-id")
    parser.add_argument("--claim-token")
    parser.add_argument("--activity", choices=sorted(ACTIVITIES), help="Maintenance override only while both guards inactive")
    args = parser.parse_args()
    root = args.root.resolve()
    now = stamp(args.now) if args.now else datetime.now(timezone.utc)
    try:
        if now is None or not root.is_dir():
            raise ValueError("existing_root_and_aware_time_required")
        # Same lock as canonical guards: take a consistent snapshot, never modify them.
        with (root / "hourly-state.lock").open("r") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if args.command == "record":
                result = record(root, json.loads(args.data or "{}"))
            elif args.command == "claim-recovery":
                result = claim(root, now, args.run_id, args.fingerprint, args.owner_idle_at, args.lead_key)
            elif args.command == "seed-queue":
                result = seed(root)
            elif args.command == "read-queue":
                result = queue_view(root)
            elif args.command == "claim-lead":
                if len(args.lead_key) != 1:
                    raise ValueError("exactly_one_lead_required")
                result = claim_lead(root, now, args.lead_key[0], args.run_id, args.thread_id, json.loads(args.data or "{}"))
            elif args.command == "lead-outcome":
                if len(args.lead_key) != 1:
                    raise ValueError("exactly_one_lead_required")
                result = outcome(root, now, args.lead_key[0], args.run_id, args.claim_token, json.loads(args.data or "{}"))
            elif args.command == "defer-lead":
                if len(args.lead_key) != 1:
                    raise ValueError("exactly_one_lead_required")
                result = defer_lead(root, now, args.lead_key[0], args.run_id, args.thread_id, json.loads(args.data or "{}"))
            else:
                result = probe(root, now, args.report)
                if args.activity:
                    if args.activity != "maintenance" or any(read(root / f, {}).get("active") is not False for f in ("hourly-state.json", "social-collector-state.json")):
                        raise ValueError("maintenance_override_requires_inactive_guards")
                    result.update(activity="maintenance", activity_reported_at=now.isoformat())
                if not args.stdout_only and not args.report:
                    save(root / HEALTH, result)
            print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "reason": str(error), "recovery_executed": False}))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
