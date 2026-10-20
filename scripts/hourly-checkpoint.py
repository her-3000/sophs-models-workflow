#!/usr/bin/env python3
"""Atomic owner-local casting run guard and campaign receipt journal. No network/UI."""
import argparse, fcntl, hashlib, json, os, re, tempfile, importlib.util
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
_cycle_spec = importlib.util.spec_from_file_location('work_cycle', Path(__file__).with_name('work_cycle.py'))
_cycle = importlib.util.module_from_spec(_cycle_spec)
_cycle_spec.loader.exec_module(_cycle)
_manual_spec = importlib.util.spec_from_file_location('manual_collector_lease', Path(__file__).with_name('manual_collector_lease.py'))
_manual = importlib.util.module_from_spec(_manual_spec)
_manual_spec.loader.exec_module(_manual)

def post_identity(url):
    parsed = urlsplit(url)
    return parsed.netloc.lower().removeprefix("www.") + parsed.path.replace("/permalink/", "/posts/").rstrip("/")

def load(path, fallback):
    return json.loads(path.read_text()) if path.exists() else fallback

def save(path, value):
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(value, f, ensure_ascii=False, indent=2); f.write("\n"); f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)

def valid_digest_readback(proof):
    # Structured human/UI attestation, not automatic screenshot/OCR validation.
    return bool(isinstance(proof, dict)
        and proof.get('recipient_id') == 'fixture-internal-group'
        and proof.get('observed_group_title') == 'Sophie’s castings'
        and proof.get('group_header_visible') is True
        and proof.get('full_digest_visible') is True
        and proof.get('screenshot')
        and proof.get('expected_digest')
        and proof.get('observed_digest') == proof.get('expected_digest')
        and proof.get('digest_sha256') == hashlib.sha256(proof['expected_digest'].encode()).hexdigest())

def execute(root, command, data):
    data = dict(data)
    if command in ('finish', 'lease-finish') and data.get('line_readback_verified') is True:
        if not valid_digest_readback(data.get('line_readback_proof')):
            data['line_readback_verified'] = False
            data['line_readback_validation'] = 'missing_or_mismatched_full_digest_proof'

    root.mkdir(parents=True, exist_ok=True)
    with (root / "hourly-state.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = root / "hourly-state.json"
        state = load(path, {"active": False})
        lease_path = root / 'social-collector-state.json'
        lease = load(lease_path, {'active': False})
        manual = bool(data.get('manual_authorization_token') or
                      (command == 'lease-finish' and lease.get('manual_exception')))
        if manual:
            if command not in ('lease-begin', 'lease-finish'):
                return {'ok': False, 'reason': 'manual_lease_only_no_hourly_begin'}
            try:
                grants, grant = _manual.validate(root, data, lease, state,
                                                releasing=command == 'lease-finish')
            except (ValueError, KeyError, TypeError, OSError) as error:
                return {'ok': False, 'reason': str(error)}
            clean = {k: v for k, v in data.items() if k != 'manual_authorization_token'}
            if command == 'lease-begin':
                grant.update(status='consumed', consumed_at=datetime.now().astimezone().isoformat())
                # Consumption first is fail-closed on interruption: never replay a token.
                save(root / _manual.FILE, grants)
                save(lease_path, {**clean, 'active': True, 'manual_exception': True})
            else:
                save(lease_path, {**lease, **clean, 'active': False})
                grant.update(status='released', checkpoint=data['checkpoint'])
                save(root / _manual.FILE, grants)
            return {'ok': True, 'command': command, 'manual_exception': True,
                    'normal_cycle_unchanged': True}
        if command in ('begin', 'lease-begin'):
            blocked = _cycle.admission(root, command, data)
            if blocked: return blocked
        if command in ("lease-begin", "lease-finish"):
            lease_path = root / "social-collector-state.json"
            lease = load(lease_path, {"active": False})
            if command == "lease-begin":
                if lease.get("active") or (state.get("active") and state.get("run_id") != data.get("run_id")):
                    return {"ok": False, "reason": "collector_busy", "run_id": lease.get("run_id") if lease.get("active") else state.get("run_id")}
                lease = {**data, "active": True}
            else:
                if lease.get("run_id") != data.get("run_id"):
                    return {"ok": False, "reason": "different_lease"}
                lease.update(data, active=False)
            save(lease_path, lease)
        elif command == "begin":
            lease = load(root / "social-collector-state.json", {"active": False})
            if lease.get("active") and lease.get("run_id") != data.get("run_id"):
                return {"ok": False, "reason": "collector_busy", "run_id": lease.get("run_id")}
            if state.get("active"):
                return {"ok": False, "reason": "active_run", "run_id": state.get("run_id")}
            previous = state
            state = {k: previous[k] for k in ("held_candidates", "consecutive_failures",
                     "consecutive_coverage_incomplete_slots", "consecutive_deferred_slots",
                     "reconciled_historical_enquiry") if k in previous}
            state.update(previous_run=previous.get("run_id"), previous_run_status=previous.get("status"))
            state.update(data, active=True, status="in_progress", verified_new_applications=0,
                         verified_new_enquiries=0, line_readback_verified=False,
                         line_digest_status="not_sent", line_message_id=None, complete_coverage=False)
            # Never carry the previous run's completion time or delivery receipt into this run.
            state.pop("stopped_at_bangkok", None)
            save(path, state)
        elif command == "finish":
            if data.get("run_id") != state.get("run_id"):
                return {"ok": False, "reason": "different_run"}
            state.update(data, active=False); save(path, state)
        elif command == "post":
            # Lightweight operational cache/outbox; ChatGPT Pages owns user-facing lists.
            data = dict(data)
            statuses = {"new", "reviewed", "skipped", "drafted", "pending", "uncertain", "applied_confirmed", "replied", "closed"}
            parsed = urlsplit(data.get("post_url", ""))
            fb = re.fullmatch(r"/groups/([^/]+)/(?:posts|permalink)/(\d+)/?", parsed.path)
            ig = re.fullmatch(r"/(?:p|reel)/([-\w]+)/?", parsed.path)
            if parsed.scheme != "https" or not data.get("source_id") or data.get("status") not in statuses:
                return {"ok": False, "reason": "invalid_post_record"}
            if parsed.netloc.lower() in {"facebook.com", "www.facebook.com", "m.facebook.com", "mbasic.facebook.com"} and fb:
                identity = "facebook:" + fb[2]
                data["post_url"] = "https://www.facebook.com/groups/" + fb[1] + "/posts/" + fb[2] + "/"
            elif parsed.netloc.lower() in {"instagram.com", "www.instagram.com"} and ig:
                identity = "instagram:" + ig[1]
                data["post_url"] = "https://www.instagram.com/p/" + ig[1] + "/"
            else:
                return {"ok": False, "reason": "unsupported_post_url"}
            try:
                observed = datetime.fromisoformat(data["observed_at"])
                posted = datetime.fromisoformat(data["posted_at"]) if data.get("posted_at") else None
                if observed.tzinfo is None or (posted and posted.tzinfo is None): raise ValueError()
            except (KeyError, TypeError, ValueError):
                return {"ok": False, "reason": "aware_timestamps_required"}
            if data["status"] == "skipped" and not data.get("reason"):
                return {"ok": False, "reason": "skip_reason_required"}
            if data["status"] == "applied_confirmed" and not (data.get("application_receipt") and data.get("receipt_verified") is True and data.get("evidence")):
                return {"ok": False, "reason": "verified_application_receipt_required"}
            if data["status"] == "replied" and not (data.get("reply") and data.get("evidence")):
                return {"ok": False, "reason": "reply_evidence_required"}
            journal_path = root / "casting-posts.json"
            journal = load(journal_path, {})
            key = data["source_id"] + "|" + identity
            old = journal.get(key, {})
            if old and observed < datetime.fromisoformat(old["observed_at"]):
                return {"ok": False, "reason": "observation_regression"}
            if old.get("status") in {"uncertain", "pending", "applied_confirmed", "replied", "closed"} and data["status"] in {"new", "reviewed", "skipped", "drafted"}:
                return {"ok": False, "reason": "outcome_regression"}
            if old.get("status") == "uncertain" and data["status"] != "uncertain" and not data.get("reconciliation_evidence"):
                return {"ok": False, "reason": "uncertain_requires_reconciliation"}
            entry = {**old, **data, "post_id": identity, "first_observed_at": old.get("first_observed_at", data["observed_at"])}
            if old.get("posted_at") and not data.get("posted_at"): entry["posted_at"] = old["posted_at"]
            entry["history"] = old.get("history", []) + [data]
            entry["pages_sync_status"] = "pending"
            journal[key] = entry
            save(journal_path, journal)
        elif command == "campaign-step":
            # Separate authorised crosspost replies; canonical campaign protects LINE.
            if data.get("channel") not in ("facebook_comment", "instagram_reply") or not data.get("post_url"):
                return {"ok": False, "reason": "invalid_crosspost_step"}
            data = dict(data)
            data["post_url"] = "https://" + post_identity(data["post_url"])
            canonical = load(root / "campaign-receipts.json", {}).get(data["campaign_key"], {})
            if data["status"] == "intent":
                if canonical.get("status") in ("intent", "submitted_unverified"):
                    return {"ok": False, "reason": "canonical_send_uncertain"}
                original = canonical.get("source_url") or canonical.get("source") or canonical.get("receipt", "")
                if canonical.get("channel") == data["channel"] and original and post_identity(original) == post_identity(data["post_url"]) and canonical.get("status") in ("verified", "verified_enquiry"):
                    return {"ok": False, "reason": "canonical_post_already_sent"}
            ledger_path = root / "campaign-step-receipts.json"
            ledger = load(ledger_path, {})
            key = data["campaign_key"] + "|" + data["channel"] + "|" + data["post_url"]
            existing = ledger.get(key)
            if existing and data["status"] == "intent" and existing["status"] in ("intent", "submitted_unverified", "verified", "verified_enquiry"):
                return {"ok": False, "reason": "duplicate_or_uncertain", "existing": existing["status"]}
            if data["status"] in ("verified", "verified_enquiry") and not data.get("receipt"):
                return {"ok": False, "reason": "receipt_required"}
            ledger[key] = {**(existing or {}), **data}; save(ledger_path, ledger)
        elif command == "campaign":
            ledger_path = root / "campaign-receipts.json"
            ledger = load(ledger_path, {})
            key = data["key"]
            existing = ledger.get(key)
            if existing and data["status"] == "intent" and existing["status"] in ("intent", "submitted_unverified", "verified", "verified_enquiry"):
                return {"ok": False, "reason": "duplicate_or_uncertain", "existing": existing["status"]}
            if data["status"] in ("verified", "verified_enquiry") and not data.get("receipt"):
                return {"ok": False, "reason": "receipt_required"}
            ledger[key] = {**(existing or {}), **data}; save(ledger_path, ledger)
        elif command == "source":
            data = dict(data)
            source_path = root / "hourly-source-checkpoints.json"
            sources = load(source_path, {})
            key = data["source_id"]; existing = sources.get(key, {})
            # Never silently age unresolved coverage out of a moving rolling window.
            if existing.get("status") != "complete" and existing.get("window_start") and data.get("window_start"):
                if datetime.fromisoformat(data["window_start"]) > datetime.fromisoformat(existing["window_start"]):
                    if data["status"] != "complete" or not data.get("backlog_resolved_evidence"):
                        data["window_start"] = existing["window_start"]
            # Optional journal-backed cursor; persist observations before advancing.
            if "reviewed_post_ids" in data:
                journal = load(root / "casting-posts.json", {})
                if any(key + "|" + identity not in journal for identity in data["reviewed_post_ids"]):
                    return {"ok": False, "reason": "post_not_durable"}
                data["last_seen_post_ids"] = list(dict.fromkeys(data["reviewed_post_ids"]))
                data["overlap_hours"] = 24
            if data["status"] == "complete":
                # Boundary must be genuinely inspected and there must be no missing feed/image/date segment.
                if not data.get("boundary_verified") or data.get("gaps", True):
                    return {"ok": False, "reason": "incomplete_boundary"}
                if datetime.fromisoformat(data["oldest_verified_at"]) > datetime.fromisoformat(data["window_start"]):
                    return {"ok": False, "reason": "boundary_not_reached"}
                data["last_complete_window"] = [data["window_start"], data["window_end"]]
            sources[key] = {**existing, **data}; save(source_path, sources)
        else: raise ValueError(command)
        if command in ('begin', 'lease-begin'):
            _cycle.admitted(root, command, data)
        elif command == 'lease-finish':
            _cycle.released(root, data)
        return {"ok": True, "command": command}

if __name__ == "__main__":
    p=argparse.ArgumentParser(); p.add_argument("command", choices=["begin","finish","campaign","campaign-step","source","post","lease-begin","lease-finish"])
    p.add_argument("--data", required=True); p.add_argument("--root", type=Path, default=ROOT)
    a=p.parse_args(); result=execute(a.root,a.command,json.loads(a.data)); print(json.dumps(result)); raise SystemExit(0 if result["ok"] else 2)
