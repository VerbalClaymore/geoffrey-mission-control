# Geoffrey Mission Control — v1 execution authority

Status: implementation contract; fresh-eyes review required before production code.

## 1. Goal and authority
Give Vernon one Geoffrey conversation for an evidence-backed portfolio briefing and bounded, durable routing to existing specialists. Hermes Kanban is the sole execution queue. This document is the sole v1 implementation authority; README, issues, handoffs and agent memory cannot override it. Material changes require a written amendment here plus reviewer acceptance before code. No silent scope expansion.

## 2. Baseline and reuse
New empty public repository; no existing application or tests. Python 3.11+ standard library, unittest, no runtime third-party dependencies. Reuse the installed Hermes CLI and Kanban; do not fork Hermes or build another scheduler/worker engine. GitHub Project/Issues track implementation milestones only, not portfolio runtime state. The local GitHub reuse catalog was consulted (query `agent orchestration`); alternative orchestration frameworks are discovery leads only and are not adopted. Current authoritative Hermes docs: https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban and /docs/user-guide/features/cron . Installed CLI help and read-only JSON output must be checked before adapters are implemented.

## 3. Locked v1 scope
1. A validated local JSON portfolio registry, append-only observations/decisions in SQLite, and deterministic Markdown/JSON brief.
2. Read-only git evidence for explicitly registered absolute repository roots: HEAD SHA, branch, latest commit timestamp, dirty flag. Never inspect file contents, diff bodies, remotes with credentials, environment files or secrets. Git evidence is activity, not proof a milestone is complete or a worker is active.
3. Read-only Hermes Kanban task evidence for explicit board/task references via documented CLI JSON. Explicit profile/board targeting; no direct writes to Hermes databases.
4. External Hermes/Codex/Claude/Cowork owner checkpoint ingestion through a strict JSON handoff contract. No automatic transcript scraping or resumption. Report observation and dispatch capabilities separately.
5. A request packet generator: validates approved scope and produces a durable JSON/Markdown assignment ready for Geoffrey's native kanban_create tool. v1 CLI does NOT execute assignments, start agents or auto-unblock tasks. Native Kanban owns dispatch, dedup and acknowledgment. Record linked task receipts and refresh them read-only. An existing session is never claimed to have resumed merely because a new task exists.
6. A no-agent `scan` command usable from Hermes cron, silent on unchanged semantic state with `--quiet-unchanged`, recording errors visibly without inventing success. The coordinator activates one local hourly read-only monitor after review and a real smoke test; no agent cron loop or remote deployment. Host must be awake. A morning briefing remains on-demand in v1, not a second scheduler.
7. An installable Geoffrey skill explaining the CLI workflow, evidence rules, native Kanban routing and approval boundaries. Source skill lives in this repository; installation only into Geoffrey's profile.

### Non-goals
No custom web UI, MCP server, GitHub synchronization daemon, vector database, cross-editor input injection, direct Codex/Claude resumption, automatic project execution, app submission, deploys, model experiments, or changes to observed projects. No inference that an old commit means a project is stuck. No collection of full conversations. Cross-project task dependencies remain documentary references, not illegal cross-board edges.

## 4. Storage and privacy
Source, tests, synthetic examples and this plan are public. Runtime registry, SQLite, real project paths, session identifiers, observations, decision text, reports and cron output remain outside Git under a caller-specified absolute state directory. Operational default is Geoffrey's private profile `mission-control/` directory, resolved using HERMES_HOME, never hard-coded to the default profile. CLI requires explicit --state-dir if HERMES_HOME is absent; fail closed rather than guess. Create state directories mode 0700 and files 0600 on POSIX. Refuse state inside the source Git repository. Do not claim filesystem permissions are encryption. Do not collect credentials or host inventory; encrypted backups/remote synchronization are deferred. Public fixtures use synthetic paths/identities. Runtime files must never be staged or uploaded.

## 5. Files and commands
Allowed implementation files (no others without plan amendment):
- README.md, AGENTS.md, .gitignore, pyproject.toml
- docs/V1_PLAN.md (amendments and execution log only), docs/OPERATIONS.md, docs/VERIFICATION.md
- src/geoffrey_mission_control/{__init__,__main__,cli,contracts,store,collectors,brief,packets}.py
- tests/{test_contracts,test_store,test_collectors,test_brief,test_packets,test_cli}.py
- examples/{registry,checkpoint,request}.json
- skills/geoffrey-mission-control/SKILL.md
- .github/workflows/test.yml
Forbidden: all other project repositories, Hermes source/config/auth/session databases, other profiles' files, live credentials, GitHub secrets, OS services. Agents may read CLI help; never print auth/config secrets. No commits/pushes by implementation or review workers; coordinator owns publication.

CLI uses argparse, `python -m geoffrey_mission_control --state-dir ABS <command>`:
- `init --registry FILE`: validate and copy registry; initialize schema; refuse overwrite except byte-identical idempotent replay.
- `scan [--now RFC3339] [--quiet-unchanged]`: collect and persist observations, render brief to stdout unless unchanged; failures return exit 1 and error summary. Clock injection is for deterministic tests; default UTC wall clock.
- `brief [--format markdown|json] [--now RFC3339]`: current derived report; no external reads.
- `checkpoint --file FILE`: validate entire checkpoint, persist atomically; idempotent exact replay, conflicting same ID rejected.
- `request --file FILE`: generate validated request packet under state/packets; no external side effects.
- `receipt --request-id ID --task-id ID --board SLUG`: link actual Kanban task only after CLI read-back verifies board, returned task ID, assignee, and deterministic idempotency key. A receipt alone never proves completion. Once bound, cannot silently rebind. Update registry is not required to track receipt tasks.

## 6. Data contracts (reject unknown fields and wrong types)
All JSON documents require integer schema_version=1. IDs/slugs match `[a-z0-9][a-z0-9_-]{0,63}`; timestamps are timezone-aware RFC3339 normalized to UTC, accept Z and numeric offsets. No NaN/infinity. Reject duplicate JSON keys. Strict bool vs int. Max input file 1 MiB; strings max 4096 chars except summary (2000), IDs as above. Arrays max 100; registry max 100 projects. Never interpolate fields into shell; subprocess argv, shell=False, timeout 15s, bounded output 1 MiB. Error output uses category and source ID, not raw subprocess output.

Registry root: schema_version, projects[]. Each project: id, name, goal, milestone (nonempty strings); repo_path (absolute string or null); owner {surface: hermes|codex|claude|cowork|unknown, profile: slug|null, session_ref: string|null}; canonical_spec (string|null, descriptive pointer only); permission: observe|coordinate|advance; paused: bool; stale_after_hours: integer 1..8760 (default specified explicitly in file, recommend 48); kanban_refs: [{board: slug, task_id: nonempty string <=128}]. No field grants permission to run arbitrary commands. Same ID or duplicate board/task refs rejected. Registry does not imply directory existence: missing roots become unavailable evidence during scan. v1 dispatch capability is `native_kanban_via_geoffrey` only for hermes owners with a non-null profile; otherwise `checkpoint_only`.

Checkpoint: schema_version, id, project_id, observed_at, state (running|ready|awaiting_user|blocked|paused|done|unknown), summary, next_action (string|null), decision (null or {question, options: 2..4 nonempty strings, recommendation: string matching one option}), evidence: [{kind: git|test|artifact|task|session|other, ref: nonempty string, at: RFC3339}], provenance: {surface matching owner surface, author: nonempty string}. Requires at least one evidence entry; reject future observed_at/evidence.at (more than 5 minutes beyond ingestion time), evidence later than observed_at+5min, unregistered project, surface mismatch. Checkpoints are always OWNER_REPORTED, never VERIFIED tests just because the text says PASS. Ingest older checkpoint into history but never let it replace newer current evidence. For same project+observed_at, conflicting checkpoints are rejected to avoid nondeterministic ordering. `done` remains reported_done until independent task verification exists.

Request: schema_version, id, project_id, created_at, expires_at, expected_observation_id, assignee, board, title, scope, acceptance: nonempty array of nonempty strings, forbidden: nonempty array, approval_ref: nonempty string. Time: created_at <= now+5min; expires_at > now and > created_at, max 24h lifetime. Require latest observation exact match, successful and fresh collection, non-paused project, permission coordinate or advance, Hermes surface, assignee exactly registered owner profile. Observation freshness <= 1h. Refuse any tracked task in triage/todo/ready/running/review/scheduled or any unavailable tracked task (no duplicate work); blocked is not implicitly approved for another worker and also refuses. Packet key = `mc-v1-<project_id>-<request_id>` (check CLI accepted key bounds). User text is data not shell. approval_ref is an auditable operator assertion, not authentication; local single-user trust model. Replay same request returns identical packet, conflicting payload same ID fails. Validation on replay still enforces expiry and current observations. Packets include exact canonical spec pointer, immutable request fields, observation ID, permission, and native Kanban instructions with idempotency_key. `advance` does not enable autonomous dispatch in v1.

Receipt read-back must use documented Hermes JSON and recorded request content; unknown schemas/error/truncation -> reject. Retain receipts and audit log across restart. Direct CLI completion is forbidden. Report Kanban status separately from reported owner state.

## 7. Observation and brief semantics
Store schema version via PRAGMA user_version=1; reject newer/unknown nonzero schemas. SQLite transactions, busy_timeout=5000, foreign_keys ON. Append observations with project ID, observed_at, activity_at (nullable), canonical semantic digest, collector results, and errors; JSON stored in database is validated data, never executable. Transactional per scan: collect first, persist the complete scan atomically; input/DB failure must not partially mutate state. Concurrent scanner lock prevents overlapping scans and returns explicit busy error; release reliably after crash/process exit (OS lock preferred). Reader tolerates old successful results but always surfaces latest failure; failed probe never updates last_verified.

last_checked = most recent attempted collection; last_verified = most recent successful required-source collection; last_meaningful_activity = latest git commit timestamp or owner checkpoint observed_at with evidence, NOT scan time or Kanban heartbeat/update timestamp. Task status changes are displayed separately; do not use every task updated_at as meaningful work. Dirty flag is a signal without a fabricated activity time. Future git times beyond 5min are invalid evidence. No configured sources => unknown, not healthy success. Git missing + valid checkpoint can be partial, but cannot qualify as successful request preflight. Checkpoint-only projects display reported state, not verified runtime activity.

stale = meaningful activity absent or age strictly greater than stale_after_hours; equality is fresh. Paused remains intentionally paused, not an alert to restart. Evidence-age and collector-health are separate. Contradictory current checkpoint and task states render conflict, never silently prefer rosy status. A task done without real acceptance evidence is `Kanban reports done`, not independently tested here.

Report each registered project exactly once in stable ID order, grouped by primary attention: awaiting_user, blocked/conflict/unavailable, paused, running, ready, reported_done, unknown. Include status origin, last checked/verified/meaningful activity, stale, source refs, owner next action, decision/recommendation, and capability. Priority precedence: paused flag, collection errors/conflict, checkpoint decision, task states, checkpoint state, unknown. No next-action invention: absent owner guidance prints unknown/request owner checkpoint. A recent checkpoint cannot hide a failed source probe. JSON report includes all fields; Markdown safely escapes pipes/newlines/backticks/links as data. Notifications use semantic digest excluding scan timestamps; new error/recovery/status/head/dirty/checkpoint changes count; mere heartbeat/update-time does not. Quiet unchanged scans still persist check timestamps; errors always visible and nonzero.

## 8. Test-first matrix and execution order
Before production code, write tests and record meaningful RED command/failing tests in docs/VERIFICATION.md. Use temp directories and fake argv runners, never live agents or user repos in tests.

Required cases: valid registry/checkpoint/request; duplicate IDs/keys; unknown fields; bool/int confusion; null vs missing fields; invalid timezone and future timestamps; size/output limits; missing git root; git timeout/nonzero/malformed output; dirty repo without timestamp invention; read-only git invariants; Kanban supported and malformed schemas, unknown state, absent task; checkpoints replay/conflict/out-of-order; same-time conflict; store restart/concurrency/schema mismatch; failed scan atomicity; receipt verification mismatch; request stale observation/expiry/permission/paused/non-Hermes/active-or-unavailable-task rejection; request replay conflict and expired replay; no subprocess execution of user text; no state in source repo; privacy file modes; briefing grouping/provenance/conflict/pause; stale equality boundary; semantic no-change quiet and new error visible; CLI end-to-end init -> scan -> checkpoint -> brief -> request with temporary real git repo and fake Hermes runner.

Phases: P0 contract review -> P1 all implementation/tests -> P2 independent fresh-eyes diff/contract review (fix and re-review) -> P3 coordinator reruns tests, real read-only portfolio census/scan and native Kanban canary -> P4 publish code and verified evidence, install skill and hourly monitor after live scan. Existing projects are observe-only on onboarding. Do not claim inferred project/agent associations confirmed. Register named projects with unknown/null fields until evidence resolves identity. Human project goals from this conversation are allowed; exact owners/paths require discovery. Publish only synthetic verification, never real portfolio output.

Commands: `PYTHONPATH=src python3 -m unittest discover -s tests -v`; `python3 -m compileall -q src tests`; `git diff --check`; CLI --help; end-to-end synthetic smoke; coordinator real read-only smoke outside Git. CI runs unittest on Python 3.11 and 3.13. No minimum test count substitutes for matrix coverage.

## 9. Binary exit gates
G1 canonical plan reviewed without unresolved contract decisions.
G2 complete test matrix and all unit/integration tests pass, compile and diff checks pass.
G3 independent review PASS, no open high/medium correctness or safety defects.
G4 private live register covers each user-named lane (unknown mappings explicit), real scan and restart brief exercised without modifying target repos.
G5 one real native Kanban canary receipt/lifecycle verified; no claim that external editor continuation works.
G6 public remote source/plan read back and exact published revision CI green.
G7 profile-scoped skill installed; one scheduled local read-only monitor manually exercised and scheduler execution verified. If scheduler unavailable, explicitly incomplete and block only that activation lane; do not misrepresent cron registration as delivery.
GitHub Project authorization is separately tracked; repository completion does not satisfy Project creation.

## 10. Stop conditions and handoff
Stop on unknown Hermes JSON contracts, forbidden-file pressure, auth requirement, live mutation, or required missing design decision; document exact blocker. Fix contract with reviewer agreement, not improvisation. Worker handoff includes changed paths, RED evidence, GREEN commands/results, known limits, no-secret assertions, and task IDs. Implementation is local-only; coordinator owns GitHub publication and runtime installation. No claim of completion until every applicable gate has verified evidence; external blockers remain labeled.

## Execution log
- P0: Empty repository and GitHub access inspected; Hermes named profiles and empty board inventory verified. Luna routing available via openai-codex/gpt-5.6-luna. GitHub Projects needs additional OAuth project scope. Repository and mission-control Hermes board created; no monitored repositories modified.
