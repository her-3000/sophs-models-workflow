# Soph's Models casting workflow

This repository is the file-based casting coordinator for Soph's Models: sanitized Python tools, docs, a communication skill, and offline regression tests. It is a source backup and local test harness. It does not send messages, browse, or run a schedule by itself.

Read `AGENTS.md` first, then this file. Before drafting any casting caption or reply, read `.agents/skills/sophs-casting-communication/SKILL.md`. A passing test, a prepared caption, or this README does not permit browsing, sending, resuming a schedule, or clearing a hold. Missing private data means hold.

GitHub repository: [her-3000/sophs-models-workflow](https://github.com/her-3000/sophs-models-workflow). Default branch: `main`. GitHub description, checked 2026-11-04: "Sanitized casting coordination workflow and offline regression tests".

## Who it is for

The project brief names the owner as Cal, GitHub account `her-3000`, and the collaborator as Sophie, GitHub account `sst11-99`.

Checked against GitHub on 2026-11-04:

- `her-3000` is the admin on this repository. That account's public name field is `her`. The two newest commits in this history are authored by Callum Runnerstrum. The oldest commit is authored as Workflow Export. No file names a person Cal. `CalMessenger` in `scripts/collector-health.py` is a route label. The short name Cal is from the brief.
- The GitHub user `sst11-99` exists. The collaborator list returned for this repository contained only `her-3000`. Sophie as a collaborator on this repo is unverified. Script labels such as `complete-sophie` and the fixture thread id `fixture-casting-thread` are local workflow names, not that GitHub account.

Soph's Models' public website is a different repository, [her-3000/modeling-company](https://github.com/her-3000/modeling-company). GitHub describes it as "Minimal modeling company application prototype" and records its homepage as https://sophsmodel.vercel.app/. A request to that URL on 2026-11-04 returned HTTP 200 from Vercel. This workflow tree does not mention that repository or URL. The "part of Soph's Models" relationship is the project brief plus those checks.

Coding agents that maintain this checkout are the other audience. `AGENTS.md` names Codex as the main daily driver and Orca and DeepSeek Harness as optional. This session did not check whether those tools are installed.

## Repository status

GitHub reported this repository as public (`visibility` PUBLIC, `isPrivate` false) on 2026-11-04. It is about to become private. That change was not applied in this checkout. After it happens, cloning will require access. Keep treating the tree as code, docs, and synthetic tests only. Private SOP, profile facts, card bytes, contact templates, ledgers, receipts, and credentials stay outside git. `.gitignore` already ignores `.env*`, private directories, state JSON, receipts, authorizations, and image/video files.

## What is in this checkout

Tracked paths (`git ls-files`):

| Path | Role |
| --- | --- |
| `AGENTS.md` | Short agent entry. Points at this README, `scripts/portable_handoff.py`, and the casting skill. |
| `README.md` | This briefing. |
| `WORKFLOW.md` | Deidentified operating principles and evidence boundaries. It says it is not a copy of private rules. |
| `EXPORT-MANIFEST.json` | Historical code-only baseline. `scope_kind` is `historical_baseline`. It records 12 script hashes from commit `bec6755b44d6f2610cfcc8b1404d0894cffc80de`. It is not a deployment allowlist and it omits the portable handoff, schema, skill, and root docs. Current published files are `git ls-tree -r --name-only HEAD`. |
| `config/automation.example.toml` | Synthetic schedule fixture. Its own comment says reading the file installs or runs nothing. `collector-slot.py` can parse this shape of RRULE when `next-slot` is invoked. Nothing in the repo registers cron or a cloud scheduler from it. `status = "ACTIVE"` is fixture text. |
| `schemas/private-handoff-v1.json` | JSON Schema draft 2020-12 for a complete private handoff (`kind` `COMPLETE_PRIVATE_HANDOFF`, `schema_version` 1). Semantic checks live in `scripts/portable_handoff.py`. The schema file has no operational records. |
| `.agents/skills/sophs-casting-communication/SKILL.md` | Rules for Thai/English casting drafts: initial interest, follow-up, and missing-info questions. Grants no send permission. |
| `.agents/skills/sophs-casting-communication/agents/openai.yaml` | Skill display metadata for an OpenAI-style agent host. |
| `.agents/skills/sophs-casting-communication/references/templates.json` | Sanitized draft shapes. Placeholders such as `{{PRIVATE_PERSONAL_LINE_URL}}` stay unresolved in git. |
| `.gitignore` | Excludes operational state, env files, media, and private paths. |
| `scripts/*.py` | Coordinator CLIs and unittest modules. Details below. |

`scripts/portable_handoff.py` is the entrypoint an agent should use for import checks and outcome journaling. It is file-only. Its docstring says it never operates a UI or sends. Commands: `validate-import`, `preflight`, `final-check`, `record-result`. Required flags: `--private-root`, `--expected-host`, `--manifest-sha256`, `--export-id`. Optional: `--manifest-file` (default `handoff.json`), `--candidate`, `--contact-revision`, `--outcome`. `--expected-host` must equal `os.uname().nodename` before any private read. The private root must be an absolute, non-symlink directory, must not be `/`, and must not contain or sit inside this checkout.

Other coordinator scripts, from their docstrings and `argparse` choices:

| Script | What it does | Commands |
| --- | --- | --- |
| `scripts/hourly-checkpoint.py` | Atomic owner-local run guard and campaign receipt journal. No network or UI. Loads `work_cycle.py` and `manual_collector_lease.py` beside itself. | `begin`, `finish`, `campaign`, `campaign-step`, `source`, `post`, `lease-begin`, `lease-finish` |
| `scripts/work_cycle.py` | File-only phase handoff between local Sophie and Upwork labels. Uses the desktop lock. Never sends. | `init`, `view`, `complete-sophie`, `complete-upwork`, `refresh-window`, `claim-dispatch`, `dispatch-result` |
| `scripts/manual_collector_lease.py` | One-use manual lease checks. No CLI. `hourly-checkpoint.py` imports it. | (library) |
| `scripts/lead-triage.py` | File-only lead budgets and dispositions. No UI, network, or sending. | `start`, `classify`, `check`, `review-wait`, `review-result`, `action-start`, `dispose`, `view` |
| `scripts/collector-health.py` | Owner-local evidence probe. No UI, network, sends, recovery execution, or unlocks. | `probe`, `record`, `claim-recovery`, `seed-queue`, `read-queue`, `claim-lead`, `lead-outcome`, `defer-lead` |
| `scripts/collector-slot.py` | Atomic slot admission and a one-attempt digest journal. No network, UI, or sleep. | `acquire`, `report-intent`, `finish`, `next-slot` |
| `scripts/notification_guard.py` | File-only notification admission and receipts. Docstring: never contacts LINE. | `view`, `quota-claim`, `quota-result`, `prepare`, `result` |

Tests: `scripts/test_collector_health.py`, `scripts/test_lead_triage.py`, `scripts/test_manual_collector_lease.py`, `scripts/test_notification_guard.py`, `scripts/test_portable_handoff.py`, `scripts/test_work_cycle.py`. They use temporary directories and synthetic records.

Several CLIs default `--root` to the repository directory (`Path(__file__).resolve().parents[1]`). Invoking a writer with that default can create JSON state in the checkout. Use a temporary directory when you are only inspecting behavior. `notification_guard.py` uses the same default.

## Tech stack

- Language: Python. `collector-slot.py` and `work_cycle.py` import `tomllib`, which is in the standard library from Python 3.11 onward. This session ran the tests with Python 3.12.3. Python 3.11 itself was not executed here.
- Libraries: standard library only. Imports seen in `scripts/` are `argparse`, `contextlib`, `datetime`, `fcntl`, `hashlib`, `hmac`, `importlib`, `json`, `os`, `pathlib`, `re`, `tempfile`, `tomllib`, `unittest`, `urllib.parse`, `uuid`, and `zoneinfo`. No `requirements.txt`, `pyproject.toml`, `package.json`, or lockfile is in the tree.
- OS: Unix-like. The tools use `fcntl` file locks. Windows was not tested.
- Data: JSON files under a chosen root, plus the TOML schedule fixture. `work_cycle.py`, `collector-slot.py`, and `notification_guard.py` use timezone `Asia/Bangkok`.
- Schema check: `portable_handoff.py` implements its own `shape()` walk of `schemas/private-handoff-v1.json`. The `jsonschema` package is not imported.
- Interfaces named in code, with no client library in the tree: Facebook and Instagram HTTPS identity parsing, a LINE contact kind in the handoff schema, local Sophie/Upwork phase labels, and notification text that must end with a `- Mia` suffix in `notification_guard.py`. Those names are not connected accounts.

## Install

There is no install step in the repo. Do not pip install anything for the tests. Confirm the interpreter:

```sh
python3 --version
```

Verified here: `Python 3.12.3`. Need 3.11 or newer because of `tomllib`. Script files are mode `644`, so call them with `python3` even where a `#!/usr/bin/env python3` shebang is present.

## Run and test

Run commands from the repository root.

Verified on 2026-11-04 in this environment (97 tests, OK), both of these:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts -p 'test_*.py'
python3 -m unittest discover -s scripts -p 'test_*.py'
```

`python3 -m unittest discover -s scripts` puts `scripts/` on the import path. That is why tests can import `portable_handoff`, `work_cycle`, and `notification_guard`. This session did not re-run discovery from another working directory.

Safe CLI inspection, also verified (each `--help` exited 0):

```sh
python3 scripts/portable_handoff.py --help
python3 scripts/hourly-checkpoint.py --help
python3 scripts/work_cycle.py --help
python3 scripts/collector-health.py --help
python3 scripts/lead-triage.py --help
python3 scripts/collector-slot.py --help
python3 scripts/notification_guard.py --help
```

`python3 scripts/portable_handoff.py` with no arguments exited 2 and named the required flags. A `validate-import` call with a host string that does not match this machine returned `{"ok": false, "status": "hold", "hold_code": "host_not_verified", "execution_permitted": false}` and exited 2. That check used no private export.

The regression test in `scripts/test_portable_handoff.py` locks this documented example. It was not executed here. It needs a verified host, a reviewed private export, and sibling directories that are not in this checkout. Whether a host still has those directories is unverified.

```sh
python3 scripts/portable_handoff.py validate-import \
  --private-root '/workspace/shared/Sophs Models/PrivateState' \
  --manifest-file operational-projection-2026-10-25.json \
  --expected-host "$SOPHS_EXPECTED_HOST" \
  --manifest-sha256 "$SOPHS_EXPORT_SHA256" \
  --export-id "$SOPHS_EXPORT_ID"
```

Use `PrivateState` as the private root. The code rejects a private root that contains this checkout. Supply the real host nodename, the SHA-256 of the selected private JSON file, and the export id. Do not guess them from this repo. The shell names above are only placeholders in the example. Python never reads them.

`validate-import` on a `sophs-private-operational-projection` v1 document is read-only. It returns hold, does not write a ledger, and sets `execution_permitted` to false. If `approved_card.bytes_included` is not true, the blocker is `approved_asset_requires_separate_local_byte_verification`. That blocker is not a claim that the card is missing on a named host. The projection does not carry card bytes; a separate local byte check is still required. This checkout does not include those bytes.

The future complete file is `handoff.json`, defined by `schemas/private-handoff-v1.json`. The schema requires `paused` true, `authority` `local_os`, and a verified personal LINE contact. The projection adapter also requires `all_schedules_paused` true and `resume_or_send_authorization` false. Individual CLIs do not share one global pause flag. Do not treat a successful unittest run as permission to call them against live state.

## Backend and environment

This repository has no Supabase usage. There is no Supabase project URL, project ref, table, database function, or edge function in the tree, and no Supabase client or config directory.

There is no other hosted backend. No database URL, HTTP client, or `os.environ` / `getenv` usage appears in the Python. Tests need no environment variables.

`.gitignore` ignores files matching `.env*`. No env file is committed. None is required to run the tests.

Names an operator may set in the shell for the documented `validate-import` example, values not stored here:

- `SOPHS_EXPECTED_HOST`
- `SOPHS_EXPORT_SHA256`
- `SOPHS_EXPORT_ID`

The script receives those values only as `--expected-host`, `--manifest-sha256`, and `--export-id`.

## How it deploys and runs

Searched the git tree for a deploy or runtime manifest. Not found: `vercel.json`, `Dockerfile`, `Procfile`, `fly.toml`, `render.yaml`, `netlify.toml`, a `supabase/` directory, `.github/` workflows, `package.json`, `pyproject.toml`, and `requirements.txt`. The GitHub repo `homepageUrl` is empty. Nothing in this checkout deploys to Vercel or any other host. The Vercel response above belongs to the separate modeling-company site, not to this workflow.

What the code shows instead: local Python CLIs on a Unix-like machine, writing JSON under a directory the caller chooses, taking `fcntl` locks. `EXPORT-MANIFEST.json` calls itself a historical sanitized baseline, not a live deployment. The phrase "actually deployed daily BYHOUR schedule" in `collector-slot.py` is the parser refusing any RRULE other than a daily `BYHOUR` list. It does not mean this repo is deployed.

Unverified: whether any desktop, cron, or agent outside this checkout currently invokes these scripts. This session did not start a scheduler, a browser, or a deploy.
