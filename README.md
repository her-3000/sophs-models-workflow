# Soph’s Models casting workflow

Private, sanitized snapshot of the working file-based casting coordinator and regression tests. This repository is a source backup and test harness, not a running casting service. It contains no credentials, connected account IDs, model materials, private conversations, client records, operational receipts, or custom-rule contents.

Requires Python 3.11+ on a Unix-like system (`fcntl` is used). No third-party dependencies, network access, live account or browser is needed for tests:

```sh
python3 -m unittest discover -s scripts -p 'test_*.py'
```

The tests use temporary directories and synthetic records. All actor, recipient and long numeric source identifiers are fixtures. Historical recovery seed campaigns have been replaced with fictional examples. The relative `config/automation.example.toml` is a test fixture; it does not install, change or trigger a schedule.

## Components

- `hourly-checkpoint.py`: single-owner admission, checkpoints and explicit own-lease release.
- `work_cycle.py`: atomic phase handoff, deadline checks and next scheduled reservation. A completed notification alone cannot authorize handoff.
- `manual_collector_lease.py`: a finite one-use manual exception, requiring separately recorded human authorization and preserving normal hourly/cycle records.
- `lead-triage.py`: bounded screening, separate review wait and action budget, with known-unsent/verified/uncertain dispositions.
- `collector-health.py`: file-only evidence probing, source-gap accounting and recovery eligibility. Synthetic historical seeds are test examples, not leads.
- `collector-slot.py`: file-only slot admission and one-attempt digest journal; included for recovery coverage, not permission to activate a paused platform.
- `notification_guard.py`: daily read-only quota admission, native fallback intent and deduplicated delivery evidence. It never calls a message API or operates LINE.

## Runtime boundary

A separately authorized local executor supplies real machine/account context, current private rule decisions and verified recipients. Live configuration and all operational state remain outside this repository. Nothing here grants permission for outreach, submissions, account changes or paid actions. Do not point synthetic IDs at real recipients or replay test seeds against live state.

Tests cover ownership conflicts, unknown guards, uncertain-send protection, terminal evidence, interrupted recovery, separate screening/review/action clocks, quota checks and duplicate delivery. Passing tests establish local behavior; they do not prove platform access, complete source coverage, delivery or human reading.

See [WORKFLOW.md](WORKFLOW.md) for operation and [EXPORT-MANIFEST.json](EXPORT-MANIFEST.json) for the explicit code allowlist.
