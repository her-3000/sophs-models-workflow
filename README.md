# Soph’s Models casting workflow

## Start here: portable handoff

Codex is the primary daily driver for building, maintaining and troubleshooting
this workflow and helping set up other software. Orca and DeepSeek Harness are
optional supporting interfaces; neither is required for the Codex happy path.
Each agent should read this file explicitly after `AGENTS.md`. The entrypoint
is `scripts/portable_handoff.py`. It performs
file-only checks and journals outcomes; it has no browser, message, model API,
task dispatch or scheduler adapter. Existing coordinator scripts and history
remain available for regression testing, not automatic execution.

Before drafting, load the reusable
[casting communication skill](.agents/skills/sophs-casting-communication/SKILL.md).
It carries source-based Thai/English selection, representative voice, initial
interest versus follow-up, essential questions, truthful facts, materials,
contact-link policy and commitment boundaries, with sanitized draft templates.

Git contains sanitized code, documentation and synthetic tests only. Actual
SOP, verified profile facts, approved card bytes/metadata, contact/template,
eligibility evidence, organiser history, deduplication and receipts belong in
a separately reviewed private export outside this checkout. Never commit them.
Do not copy the earlier 23-file test bundle over this repository: it excluded
the private ledgers/profile/card/template/pay preferences and is not an import.

Local OS remains authoritative until a complete checked minimal-state import
and an explicit owner migration decision. A cloud journal must be reconciled
back into that authority before another executor acts. No automatic two-way
sync or ownership transfer exists. Recurring casting remains paused; a manual
exception does not clear holds or activate recurring work.

### Current safe checkpoint and attempt history

- Baseline source: `bec6755b44d6f2610cfcc8b1404d0894cffc80de`.
- Earlier reviewed local-origin candidate: 23 files; independently passed 100
  synthetic cloud tests. Projected checkpoint is non-runtime evidence only.
- Parent reports one manual Linux Facebook initial enquiry succeeded with an
  approved card after local SOP/asset/dedup consultation. Its 10m21s wall time
  includes waits; no timing breakdown or efficiency claim is established.
- That experiment proves one UI path, not sustained sessions, Orca execution,
  DeepSeek inference, complete migration or production readiness. Actual
  attempt identity/receipt must arrive in the reviewed private export.
- Historical uncertainties remain no-replay holds. Older campaign/hold counts
  are not current truth. This checkout supplies no authoritative current counts.
- The exact verified personal LINE share URL is still missing here. Relevant
  preflight holds until supplied and verified; never derive a URL or substitute
  business LINE. The one test exception does not change normal paid-MV preference.

### Setup, validation and resume

Use installed Python 3.11+ on Unix; no install, credential creation or model
call is needed. First run the offline tests from repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts -p 'test_*.py'
```

The intended Mia private folder is `/workspace/shared/Sophs Models` on a
**different host**. A matching path string in Codex is not shared storage.
Obtain the actual execution-host identity, reviewed export ID and SHA-256 of
`handoff.json` through the authorized handoff. Supply them explicitly; do not
guess host identity from this checkout. Verify permission and filesystem
mapping on the target host, then use the read-only import validator:

```sh
python3 scripts/portable_handoff.py validate-import \
  --private-root "$SOPHS_PRIVATE_ROOT" --expected-host "$SOPHS_EXPECTED_HOST" \
  --manifest-sha256 "$SOPHS_EXPORT_SHA256" --export-id "$SOPHS_EXPORT_ID"
```

Unset/wrong arguments, wrong host, missing files, hash mismatch, stale evidence,
partial coverage, unknown statuses or missing receipt context produce a hold.
The validator never creates an empty ledger or imports a projected checkpoint.
It does not move files: approved transfer and complete export reconciliation
must precede it. Source digests are reviewed export attestations, not proof
that this host has inspected the original OS records.

The currently supplied `sophs-private-operational-projection` v1 has a dedicated
read-only adapter. Pass its actual relative filename using `--manifest-file`.
The adapter checks the pinned bytes, embedded schema digest, record sections,
counts, paused authority and protection inventory; it returns HOLD with minimal
diagnostics and no candidate clearance. It preserves the original projection
without normalization or ledger writes. Partial coverage and a missing verified
personal LINE URL are explicit blockers. It does not assume card metadata means
card bytes are present on this host.

The future **complete** private format v1 is defined by `schemas/private-handoff-v1.json` and the
entrypoint's semantic checks. `handoff.json` binds approved SOP/profile/asset/
template files by relative paths, revisions and hashes; profile JSON contains
`verified: true` and nonempty `facts`. The template is approved caption text
containing the verified personal LINE hyperlink exactly once, and its metadata
includes `contact_revision`.
Private template metadata also includes `communication`: `skill_revision: 1`,
`language` (`th`/`en`), `brief_language`, `recipient_language`, `message_kind`
(`initial_interest`/`follow_up`/`missing_info`), `language_evidence`, the current
`sop_sha256`, `agency_identity_verified: true` and `claims_reviewed: true`.
Mixed/conflicting/unknown language needs `contextual_language` plus evidence;
there is no automatic Thai or English fallback. Outgoing copy permits only the
verified personal LINE hyperlink and no unresolved private placeholders.
Complete source inventory, reconciled counts,
campaigns, crosspost steps, holds, attempts and an idle checkpoint are required.
Raw chats, credentials, cookies and sessions are excluded. If the source export
uses another format, write and test an explicit versioned normalization step;
never silently discard fields, downgrade unknowns to unsent or clear holds.

For an authorized candidate, privately create `candidate.json` with `key`,
`campaign_key`, canonical `source_url`, crosspost/campaign `aliases`,
`source_evidence`, aware `checked_at`, verified booleans `open`, `eligible`,
`route_verified`, `paid`, and a concrete `category` (for example `music_video`). These are evidence-backed
attestations, not truth inferred by code. Before any UI, use the same arguments
above with `preflight --candidate candidate.json --contact-revision REVISION`.
This saves the prepared caption and verified asset hash in private
`handoff-state.json`; read it privately. Prepare/read the caption **before**
attaching the card. Compare its factual claims to the approved profile and
original brief; hashes establish byte identity, not factual truth. Reuse only the existing authorized browser tab after
independently verifying the browser's account, source, recipient and route.

Immediately before the final separately authorized external write, re-read the
current contact/template revision and approved export fingerprint. Run
`final-check` with the same candidate and current contact revision. It rechecks
all inputs and durably reserves one `intent` before any transmission. A stale
prepared packet or changed revision requires fresh preparation. No command
grants external permission. This file-only tool cannot atomically lock a UI
click: the sole browser executor must serialize the revision check and click,
and abort if anything changes or the reserved action is delayed.

Immediately after the attempt, privately create `outcome.json` with returned
`attempt_id`, `status` (`completed`, `known_unsent`, or `uncertain`), and
`evidence`. Completed requires a verified receipt; known-unsent requires
`transmission_started: false`. Invoke `record-result --outcome outcome.json`
with the same private-root/host/export arguments. One atomic fsynced private
journal stores outcome, dedup keys and checkpoint together. It does not mark
booking or human reading. If persistence fails, stop; retain the evidence and
reconcile the pending intent before any retry.

On resume, read this README, the private journal and current authoritative
export. `preflight` refuses pending/uncertain writes; never delete/reset the
journal. `record-result` can reconcile an uncertainty only with additional
`reconciliation_evidence`, including after export expiry or asset change. It
preserves prior outcomes and never clears an imported hold. A verified
known-unsent retry needs new candidate `retry_review_evidence`; an updated
complete authoritative export must also preserve the decision. Execution and
recurring flags stay paused throughout.

### Harness-specific entry and source coverage

Codex alone can use the Python preflight. Optional harnesses use the same
instruction layer, not universal runtime configuration. No
permissions, credentials, browser login or private filesystem mounts transfer
through a Git clone or a saved agent conversation. Keep existing permissions;
do not enable Full Access to make this work.

| Host | Entry and prerequisite | Source reviewed |
| --- | --- | --- |
| Codex CLI 0.163.0 | Open this repository as working directory; verify applicable instruction overrides, read root AGENTS.md and this README explicitly, then run file-only validation. Authentication and permissions are separate host checks. | [Pinned README](https://github.com/openai/codex/blob/rust-v0.163.0/README.md); parent source review also checked official [instruction](https://learn.chatgpt.com/docs/agent-configuration/agents-md), [skills](https://learn.chatgpt.com/docs/build-skills), [auth](https://learn.chatgpt.com/docs/auth), and [permission](https://learn.chatgpt.com/docs/agent-approvals-security) docs. |
| DeepSeek Harness | Use an already authorized configured profile rooted at this checkout. Verify its instruction plugin is enabled, then explicitly read AGENTS.md/README. Do not auto-create/reset profiles or assume browser state resumes. | [Root README](https://github.com/deepseek-ai/deepseek-harness/blob/22ddee3792402c3e70ec5f86a63c3e3e895a2f60/README.md), [CLI README](https://github.com/deepseek-ai/deepseek-harness/blob/22ddee3792402c3e70ec5f86a63c3e3e895a2f60/apps/cli/README.md), [instruction plugin](https://github.com/deepseek-ai/deepseek-harness/blob/22ddee3792402c3e70ec5f86a63c3e3e895a2f60/packages/context/agent-instructions/README.md). Source package version 0.2.1-rc.3 is not proof of installed binary build. |
| Orca 1.4.239 | Orca hosts an agent in a worktree; that agent reads instructions. Before launching/creating a worktree, audit repository and configured setup hooks because they can execute automatically. Verify selected agent, host and private-root mapping separately. | [Pinned README](https://github.com/stablyai/orca/blob/47f77cf7018ebc0d046b82c6dde31cf704909162/README.md) and [hooks/memory](https://github.com/stablyai/orca/blob/47f77cf7018ebc0d046b82c6dde31cf704909162/docs/site/content/docs/agents/hooks-memory.mdx). |

Source review covered those READMEs and instruction/hook behavior; it did not
install or execute Orca/DSH. Parent additionally reviewed DSH skill-filesystem
and browser-use docs at the same commit: providers require activation; session
logs do not restore browser login/profile state. Both agents support root
instructions but loading differs; no new skill/config layer is needed here.
A parent fresh-harness read-only test remains required before any portability
claim. The saved Codex environment still includes other repositories; task
scope is restricted to this repository, not configuration-level isolation.

Parent's separate authorized read-only installed-metadata check found Codex
0.163.0, DSH 0.2.1-rc.3 build `be72761ee1a6736ebcdf4f3739363b083ae53c98`,
and Orca 1.4.239 build `31fe3c74818b63dcc8c28043f00fecd33d97f14d`.
Those DSH/Orca builds differ from the reviewed source pins above; do not equate
them. Installed DSH docs corroborate root instruction/skill loading, but
effective behavior was not executed. Orca's installed `codex resume` mapping
reopens a Codex-native session; it does not convert conversations across
harnesses. Existing host hooks include HTTP integration; target-project hooks
still require inspection before launch. No harness, model or browser was
launched by those checks; machine-local sessions/auth remain separate.

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
