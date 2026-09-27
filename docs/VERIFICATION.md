# Verification record

RED (before production modules):
- `PYTHONPATH=src python3 -m unittest tests.test_contracts -v`
- Result: FAIL during import with `ModuleNotFoundError: No module named 'geoffrey_mission_control.contracts'`.

GREEN:
- `PYTHONPATH=src python3 -m unittest discover -s tests -v`
- Result: PASS, 12 tests.
- `python3 -m compileall -q src tests`
- Result: PASS.
- `git diff --check`
- Result: PASS.
- `PYTHONPATH=src python3 -m geoffrey_mission_control --help`
- Result: PASS; argparse help rendered.
- Synthetic CLI lifecycle with state under TMPDIR: `init`, `scan --now`, `checkpoint --file`, `brief --format json`.
- Result: PASS; initialized state, persisted observation/checkpoint, rendered JSON brief. Scan correctly reported missing configured repository as a visible source error.

Repair A RED (new contract/storage regressions):
- `PYTHONPATH=src python3 -m unittest tests.test_contracts tests.test_store -v`
- Result: FAIL as expected: missing `validate_request`/scan transaction APIs, same-time checkpoint conflict, and symlink-state rejection.

Repair A GREEN:
- `PYTHONPATH=src python3 -m unittest discover -s tests -v`
- Result: PASS, 17 tests.
- `python3 -m compileall -q src tests`
- Result: PASS.
- `git diff --check`
- Result: PASS.

Repair A API appendix (owned contracts/store):
- `load_json(source: bytes|str, *, max_bytes=1048576) -> object`: strict UTF-8 JSON, duplicate-key and finite-number rejection.
- `validate_registry(value: object) -> dict`: strict v1 registry with required fields, bounded IDs/arrays, and normalized defaults already represented in input.
- `validate_checkpoint(value: object, *, ingested_at: str|None=None, clock=None) -> dict`: strict checkpoint, provenance author, UTC timestamps, evidence ordering and five-minute future guard.
- `validate_request(value: object, *, now: str|None=None) -> dict`: strict immutable request fields, bounded lists, and expiry/future/lifetime validation.
- `ensure_state(state_dir: str|Path, source_root: str|Path|None=None) -> Path`: absolute canonical state-root containment and state-root symlink rejection; creates mode 0700.
- `Store.save_checkpoint(checkpoint, ingested_at)`: transactional idempotent checkpoint insert with same-project/same-time conflict rejection.
- `Store.begin_scan()/commit_scan()/rollback_scan()`: OS-exclusive crash-releasing scan lock and atomic SQLite scan transaction.
- `Store.add_observation(..., commit=True)`: append observation; use `commit=False` inside a scan transaction.
- `Store.save_request(request)`: strict request validation and immutable ID replay.
- `Store.save_receipt(request_id, task_id, board, payload)`: requires an existing request and prevents immutable rebinding.

Remaining coordinator-owned or other-module findings remain recorded by the independent review and were not silently expanded into this allowlist.

Repair B RED:
- `PYTHONPATH=src python3 -m unittest tests.test_collectors -v` initially failed during import because the required source-matrix and digest APIs were absent.
- `PYTHONPATH=src python3 -m unittest tests.test_brief -v` initially failed because raw task status, provenance, unavailable-source, stale-boundary, and complete Markdown escaping behavior were absent.

Repair B GREEN:
- `PYTHONPATH=src python3 -m unittest discover -s tests -v`
- Result: PASS, 25 tests.
- `python3 -m compileall -q src tests`
- Result: PASS.
- `git diff --check`
- Result: PASS.

Repair B API appendix (collectors/brief):
- `git_collect(repo_path, runner=subprocess.run, *, now=None) -> dict`: metadata-only argv collection with shell disabled, 15-second timeout, bounded output, strict timestamp/dirty/error handling.
- `hermes_collect(board, task_id, runner=subprocess.run, hermes_bin='hermes', *, now=None) -> dict`: pinned version plus explicit board list/show JSON routes, required-field/type validation, opaque task IDs, duplicate detection, epoch normalization, and sanitized errors.
- `collect_sources(project, *, git_runner=subprocess.run, hermes_runner=hermes_collect, now=None, receipts=None) -> dict`: required-source matrix with git, registered refs, and receipt refs; every required failure remains visible.
- `semantic_digest(value) -> str`: canonical digest excluding checking/heartbeat timestamps while retaining source, error, checkpoint, HEAD, and status changes.
- `build_report(registry, observations=None, checkpoints=None, now=None) -> dict`: one stable project row per registry project, grouped by attention, with raw task statuses, reported/verified provenance, source refs, timestamps, strict stale boundary, conflicts, pauses, decisions, capability, and owner action.
- `render_markdown(report) -> str` / `render_json(report) -> str`: deterministic output; Markdown escapes untrusted pipes, links, HTML, backticks, and control characters.

Known limits: CLI scan integration remains in the downstream CLI scope; this repair does not claim overall G2/G3/v1 acceptance or execute live probes.

Repair C verification:
- RED: added regressions for string/bytes JSON loading and dangling state symlink rejection; both failed for the expected implementation defects.
- GREEN: `PYTHONPATH=src python3 -m unittest discover -s tests -v` -> PASS, 27 tests.
- `python3 -m compileall -q src tests` -> PASS.
- `git diff --check` -> PASS.
- `PYTHONPATH=src python3 -m geoffrey_mission_control --help` -> PASS.
- Integration changes: CLI closes SQLite on every path, uses the source-root safety check independent of cwd, persists complete transactional scans, returns visible source failures, writes bounded packet projections atomically, and performs Hermes receipt identity read-back without claiming idempotency-key verification.
- Remaining gate: no live Hermes probe or coordinator activation was performed; overall G2/G3/v1 acceptance remains coordinator-owned.

Acceptance harness (this task):
- Added `tests/test_acceptance.py`, an executable unittest harness using only temporary directories, a real temporary git repository, and a disposable fake `hermes` subprocess on `PATH`. It never opens a live Hermes database, project, network, or secret.
- Command: `PYTHONPATH=src python3 -m unittest tests.test_acceptance -v`
- Result: RED, 6 tests: 1 pass, 3 assertion failures, and 1 receipt `KeyError` error (the remaining request-expiry test passes). The receipt error confirms the review's payload-deserialization concern rather than encoding a guessed failure.
- Reproduced defects: failed-source observation still creates a request; failed scan clears prior `last_verified`; checkpoint provenance surface mismatch is accepted; receipt crashes on stored JSON payload instead of performing read-back. The fixture initially exposed a timestamp issue and was corrected to use a clock after the real temporary commit, separating harness failure from product behavior.

Section 8 acceptance coverage matrix:
- Covered by executable tests: real-git init/scan, checkpoint/brief, request/receipt lifecycle, failed-source request exploit, expiry and stale observation rejection, receipt missing/identity contract, provenance-owner mismatch, failed-scan verification preservation, and temporary fake-Hermes subprocess routing.
- Coverage still needing independent coordinator/reviewer evidence: unregistered target-board list/show probing; multiprocess crash/restart stress; fault injection between packet projections; and complete Section 8 criterion-by-criterion green run.
- This appendix records integration evidence only; it does not claim G2/G3 or product acceptance. The red tests are intentionally not skipped or weakened.

Acceptance repair GREEN (this task):
- Production fixes: request preflight now rejects failed/unverified observations and checkpoint-race observations; failed scans retain the prior successful verification timestamp; quiet mode compares semantic observation digests; checkpoint provenance must match the registered owner surface; receipt payloads are decoded from SQLite and immutable receipt replay is read-only; Hermes list/show disagreement and future epochs are rejected; packet publication fsyncs the directory and removes a newly partial projection.
- Acceptance harness correction: the preservation test now makes the registered temporary git source unavailable in the same state, so it tests the actual prior-verification invariant rather than comparing unrelated states.
- `PYTHONPATH=src python3 -m unittest discover -s tests -v` -> PASS, 33 tests.
- `PYTHONPATH=src python3 -m unittest tests.test_acceptance -v` -> PASS, 6 tests, including real temporary git, fake Hermes subprocess, request rejection, receipt read-back, provenance, failed-source preservation, and expiry/staleness paths.
- `python3 -m compileall -q src tests` -> PASS.
- `git diff --check` -> PASS.
- No live Hermes database, project repository, network, credentials, or secret was accessed; no commits or pushes were performed.

Narrow repair regressions (destination-board preflight and equal-clock checkpoint race):
- RED: `PYTHONPATH=src python3 -m unittest tests.test_acceptance.AcceptanceHarness.test_request_queries_destination_board_and_only_relevant_tasks tests.test_acceptance.AcceptanceHarness.test_checkpoint_same_clock_requires_rescan_then_allows_request -v` -> failed before implementation: no destination-board reads occurred and a same-clock checkpoint did not invalidate the prior observation.
- GREEN: `PYTHONPATH=src python3 -m unittest tests.test_acceptance -v` -> PASS, 9 acceptance tests. The fixture logs exact `hermes kanban --board synthetic list/show --json` calls, includes unrelated tasks without blocking, rejects exact marker conflicts and unavailable Hermes, and proves same-clock checkpoint rejection followed by scan-then-request success.
- Requirement mapping: destination-board census and relevant-task dedup -> `test_request_queries_destination_board_and_only_relevant_tasks`, `test_request_rejects_exact_marker_and_unavailable_destination`; checkpoint membership/version/order snapshot and equal-clock rescan gate -> `test_checkpoint_same_clock_requires_rescan_then_allows_request` and the corrected full lifecycle test.
- Explicitly out of scope for this narrow repair: receipt restart/immutable replay expansion, multiprocess scanner crash/restart stress, and packet projection fault injection; these remain reviewer/coordinator follow-up evidence and are not counted as v1 acceptance.

Remaining explicit acceptance limits:
- No live coordinator activation or real native `kanban_create` call was performed (outside this local-only implementation task).
- The CLI request adapter enumerates registered Hermes references for dedup; an unregistered target board cannot be probed without a native dispatch/read route, so no autonomous creation path is claimed.
- Multiprocess crash/restart stress and fault injection between the two packet projections remain coordinator/reviewer follow-up evidence, not silently treated as passed by the 33-test result.

Receipt restart/immutable replay repair:
- RED: `PYTHONPATH=src python3 -m unittest tests.test_acceptance.AcceptanceHarness.test_receipt_immutable_identity_and_false_key_proof -v` -> failed with `KeyError: 'request_id'` after fresh-subprocess init/scan/request/receipt execution. This was the executable proof that the receipt omitted stored request identity/provenance; no tautological assertion was retained.
- GREEN focused: `PYTHONPATH=src python3 -m unittest tests.test_acceptance.AcceptanceHarness.test_receipt_immutable_identity_and_false_key_proof -v` -> PASS, 1 test.
- GREEN acceptance: `PYTHONPATH=src python3 -m unittest tests.test_acceptance -v` -> PASS, 9 tests.
- GREEN static: `python3 -m compileall -q src tests` and `git diff --check` -> PASS.
- Receipt contract mapping: fresh Python subprocesses prove init/scan/request/receipt persistence; stored `request_id`, `project_id`, exact marker, task ID, board, assignee, `task_identity_verified=true`, and `idempotency_key_verified=false` are read from output and SQLite; changed task status and expired request replay preserve the immutable payload; task/board/assignee/marker conflicts, malformed/unavailable Hermes, and missing request fail without receipt/request mutation.
- Request boundary mapping: same-ID identical request replay returns the persisted request packet; same-ID changed payload fails before mutation. Bound receipt replay performs read-only Hermes identity/provenance verification and never rewrites historical status or creates work.
- Scope remains narrow: no scanner crash stress, packet projection fault injection, live Hermes/native creation, real portfolios, secrets, network, commits, or overall v1 acceptance claim.

Receipt evidence correction:
- The receipt acceptance fixture now injects the existing `cli.clock` seam only inside fresh test subprocesses. It persists the original receipt before expiry, then replays at `2026-09-27T00:02:00Z` and asserts that effective replay time is strictly later than the stored `expires_at`; the fake Hermes task timestamp is generated behind the real clock so it remains valid for both clocks.
- The replay changes external task status to `done` while asserting the immutable receipt payload, request/receipt row counts, stored rows, and packet artifacts are unchanged.
- Conflicting request payload, mismatched task/board/assignee/marker, malformed/unavailable Hermes source, and missing request each snapshot request and receipt rows plus packet bytes immediately before and after the rejected operation; every snapshot is unchanged.
- Focused: `PYTHONPATH=src python3 -m unittest tests.test_acceptance.AcceptanceHarness.test_receipt_immutable_identity_and_false_key_proof -v` -> PASS.
- Acceptance: `PYTHONPATH=src python3 -m unittest tests.test_acceptance -v` -> PASS, 9 tests.
- Full suite: `PYTHONPATH=src python3 -m unittest discover -s tests -v` -> PASS, 36 tests; `python3 -m compileall -q src tests` -> PASS; `git diff --check` -> PASS.
- Coverage classification: evidence-only correction; no production files changed and no overall v1 acceptance claim.

Scanner multiprocess/crash repair evidence:
- Added deterministic separate-process scanner tests in `tests/test_store.py` using subprocess pipes, a READY handshake, bounded 5-second waits, and only the test-owned child PID terminated.
- `test_scan_lock_excludes_process_and_releases_after_kill_without_partial_rows` proves the first process owns the OS `.scan.lock` and SQLite transaction; a fresh contender returns exact `scan: busy`; after SIGKILL/reap, fresh state reads contain neither multi-project `child-partial-a` nor `child-partial-b` row and a new process immediately acquires the lock, commits `recovered`, and a further fresh connection reads only that committed row.
- `test_scan_exception_rolls_back_and_releases_for_fresh_process` proves normal exception rollback removes the uncommitted row, a fresh connection can acquire/commit, and a restarted connection reads the committed `after` row.
- Added `AcceptanceHarness.test_failed_source_is_a_complete_observation_not_an_interrupted_scan`: a two-project scan with one missing repository returns exit 1 but commits exactly one complete observation for each project; the successful project has no errors and the failed project has visible errors. This distinguishes source-failure observation semantics from interrupted-scan atomic rollback.
- Focused repeated run: `for i in $(seq 1 10); do PYTHONPATH=src python3 -m unittest tests.test_store.StoreTests.test_scan_lock_excludes_process_and_releases_after_kill_without_partial_rows || exit 1; done` -> PASS, 10/10.
- Focused acceptance: `PYTHONPATH=src python3 -m unittest tests.test_acceptance.AcceptanceHarness.test_failed_source_is_a_complete_observation_not_an_interrupted_scan -v` -> PASS.
- Full suite: `PYTHONPATH=src python3 -m unittest discover -s tests -v` -> PASS, 39 tests.
- Static checks: `python3 -m compileall -q src tests` -> PASS; `git diff --check` -> PASS.
- Scope: test/verification coverage only; no production scanner defect was exposed, no live Hermes/project/network/secrets, no commits/pushes, and no overall v1 acceptance claim.

Packet interrupted-write and restart recovery proof:
- Added `tests/test_packets.py` fault-injection coverage before JSON publication, between JSON and Markdown publication, and packet-directory symlink escape rejection. Each failure asserts no partial target is exposed, unrelated valid packet bytes remain exact, and a bounded retry produces mode `0600` projections under a mode `0700` packet directory.
- Added `AcceptanceHarness.test_packet_crash_after_json_publication_recovers_without_duplicate_rows`: a test-owned subprocess publishes JSON, then exits with code 23 before Markdown/publication completion. The parent bounds and reaps that child, proves SQLite request persistence plus a partial projection, then uses a fresh process retry to repair both projections while preserving pre-existing request/receipt rows and packet bytes exactly.
- Canonical recovery semantics exercised: SQLite request truth is durable before projection; a crash may leave an incomplete projection, but no successful CLI result is emitted. An identical valid replay regenerates both canonical projections idempotently; no receipt or autonomous task execution is created. Two filesystem renames are not treated as globally atomic.
- Focused: `PYTHONPATH=src python3 -m unittest tests.test_packets tests.test_acceptance.AcceptanceHarness.test_packet_crash_after_json_publication_recovers_without_duplicate_rows -v` -> PASS, 4 tests.
- Packet/acceptance focused: `PYTHONPATH=src python3 -m unittest tests.test_packets tests.test_acceptance -v` -> PASS, 16 tests.
- No production defect was exposed by this lane; no live Hermes/project/network/secrets, commits, pushes, or overall v1 acceptance claim.

Scanner committed-history preservation correction:
- Updated only `tests/test_store.py` and this verification record. Before launching the killed child, the crash test now commits distinct `prior-a` and `prior-b` observations for both projects, then snapshots every SQLite row field (including IDs, payloads, digests, timestamps, and count) from a fresh reader.
- The child still adds distinct uncommitted `child-partial-a`/`child-partial-b` rows, signals `READY`, retains the exact `scan: busy` contender assertion, and is the only process killed/reaped. A fresh post-crash reader now asserts the complete pre-crash multi-project snapshot and count are exactly identical and contains neither child row.
- The recovery scanner reacquires the lock and commits distinct `recovered-a`/`recovered-b` rows for both projects. A subsequent fresh reader asserts the two prior rows remain unchanged and both new rows are durable, rather than checking latest observations alone.
- Focused: `PYTHONPATH=src python3 -m unittest tests.test_store.StoreTests.test_scan_lock_excludes_process_and_releases_after_kill_without_partial_rows -v` -> PASS, 1 test.
- Focused repeated run: `for i in $(seq 1 10); do PYTHONPATH=src python3 -m unittest tests.test_store.StoreTests.test_scan_lock_excludes_process_and_releases_after_kill_without_partial_rows || exit 1; done` -> PASS, 10/10.
- Full suite: `PYTHONPATH=src python3 -m unittest discover -s tests -v` -> PASS, 39 tests.
- Static checks: `python3 -m compileall -q src tests` -> PASS; `git diff --check` -> PASS.
- Coverage-only correction: no production files changed, no live Hermes/project/network/secrets, no commits/pushes, and no overall v1 acceptance claim.

Safety repair — state-path validation and observation-age gate:
- RED: `PYTHONPATH=src python3 -m unittest tests.test_cli.CLITests.test_fresh_cli_rejects_state_directory_symlink tests.test_acceptance.AcceptanceHarness.test_request_observation_age_boundary_and_unbound_stale_replay -v` -> FAIL, as expected: the fresh CLI created `outside/mission-control.sqlite3` through a state-root symlink, and an observation older than one hour was accepted.
- GREEN focused: the same command -> PASS, 2 tests. The CLI now preserves the lexical state path until `Store.ensure_state` rejects a symlink and performs canonical source-root containment, and request preflight rejects observation age strictly greater than one hour at the captured command clock while accepting exactly one hour.
- The new acceptance test also proves stale unbound same-ID replay is rejected without changing SQLite rows or packet bytes. Existing bound expired-receipt read-only coverage remains in `test_receipt_immutable_identity_and_false_key_proof`; no receipt authorization behavior was changed.
- Scope limits: only `cli.py`, `tests/test_cli.py`, `tests/test_acceptance.py`, and this verification record changed for this repair. Semantic quiet, briefing precedence, and unknown-field request storage findings remain separate; no overall v1 acceptance claim.

Semantic quiet repair:
- RED: `PYTHONPATH=src python3 -m unittest tests.test_collectors tests.test_acceptance.AcceptanceHarness.test_quiet_unchanged_suppresses_advancing_scan_clock_but_persists_freshness tests.test_acceptance.AcceptanceHarness.test_quiet_unchanged_keeps_zero_project_scan_silent tests.test_acceptance.AcceptanceHarness.test_quiet_unchanged_surfaces_semantic_changes_and_repeated_errors -v` -> FAIL, 3 tests: digest retained `observed_at`/`last_verified`, advancing-clock unchanged scans printed, and zero-project quiet output printed.
- GREEN focused: the same command after repair -> PASS, 11 tests. Additional acceptance coverage runs controlled-clock CLI scans for HEAD, dirty, checkpoint, task-status, new error, recovery, repeated errors, and zero-project quiet behavior.
- Production repair: semantic digests now exclude only observation/checking/heartbeat/verification-clock fields (`observed_at`, derived `last_verified`, and existing volatile clock names); source, activity, checkpoint, task, status, and error fields remain semantic. Empty registries are quiet with `--quiet-unchanged`; populated registries suppress only successful repeated semantic digests while failures always render and exit nonzero.
- GREEN full: `PYTHONPATH=src python3 -m unittest discover -s tests -v` -> PASS, 50 tests; `python3 -m compileall -q src tests` -> PASS; `git diff --check` -> PASS.
- Scope limits: only `src/geoffrey_mission_control/collectors.py`, `src/geoffrey_mission_control/cli.py`, `tests/test_collectors.py`, `tests/test_acceptance.py`, and this verification record changed for this repair. Tests use temporary state, a temporary git repository, and patched/fake synthetic sources only; no live Hermes/project/network/credentials/secrets, commits, or pushes.

Briefing conflict/precedence repair:
- RED: `PYTHONPATH=src python3 -m unittest tests.test_brief -v` -> FAIL, 2 failures: checkpoint decisions were hidden by blocked task status, and blocked versus reported completion was not treated as contradictory.
- GREEN focused: `PYTHONPATH=src python3 -m unittest tests.test_brief -v` -> PASS, 6 tests. Table-driven synthetic cases cover unknown absence, running/ready/blocked, review/awaiting-user mapping, done/running and terminal conflicts, archived non-completion labeling, paused precedence, collection-error precedence, decision precedence, raw task status, owner/task provenance, and Markdown visibility.
- GREEN full: `PYTHONPATH=src python3 -m unittest discover -s tests -v` -> PASS, 52 tests; `python3 -m compileall -q src tests` -> PASS; `git diff --check` -> PASS.
- Production repair: `brief.py` now applies paused > collection error/conflict > checkpoint decision > task state > checkpoint state > unknown, retains conflict evidence when a higher-priority display status wins, and exposes normalized/raw states plus owner/task provenance in JSON and Markdown. Unknown is not contradictory; archived remains `archived` rather than `reported_done`; review maps to `awaiting_user` without asserting a human decision.
- Scope limits: this lane changed only `src/geoffrey_mission_control/brief.py`, `tests/test_brief.py`, and this verification record. Synthetic tests only; no live scans, native writes, config/install, credentials/secrets, commits, pushes, or overall v1 acceptance claim.

Briefing evidence/status-origin correction:
- RED: `PYTHONPATH=src python3 -m unittest tests.test_brief -v` -> FAIL, 4 failures. New regressions reproduced incorrect `status_origin` for paused, collection-error, and conflict winners and missing Markdown evidence labels/values.
- Production repair: status origin is assigned alongside the winning precedence branch (`paused`, `collection_error`, `conflict`, `checkpoint_decision`, `task`, `checkpoint`, or `unknown`); owner provenance and task provenance remain separate. Markdown now includes stale, source refs, capability, errors, owner provenance/action, task provenance, and recommendation, with existing escaping and explicit unknowns retained.
- GREEN focused: `PYTHONPATH=src python3 -m unittest tests.test_brief -v` -> PASS, 9 tests.
- GREEN full: `PYTHONPATH=src python3 -m unittest discover -s tests -v` -> PASS, 55 tests; `python3 -m compileall -q src tests` -> PASS; `git diff --check` -> PASS.
- Actual CLI probe using temporary synthetic state: JSON and Markdown `brief` both returned `unavailable`, `collection_error`, and stale evidence values; no live sources, native writes, credentials, or secrets accessed.
- Scope limits: only `src/geoffrey_mission_control/brief.py`, `tests/test_brief.py`, and this verification record changed; storage strict validation, complete Section 8 matrix, activation, and overall v1 acceptance remain open.

Briefing Markdown table-continuity repair:
- RED: `PYTHONPATH=src python3 -m unittest tests.test_brief.BriefTests.test_multi_project_markdown_keeps_summary_table_contiguous -v` -> FAIL, as expected: the third summary row appeared after the first project's evidence block.
- Production repair: `render_markdown` now emits every summary row contiguously in report order, then emits the matching per-project evidence blocks without changing evidence values, escaping, grouping, or JSON output.
- GREEN focused: `PYTHONPATH=src python3 -m unittest tests.test_brief -v` -> PASS, 10 tests.
- GREEN static: `python3 -m compileall -q src tests` and `git diff --check` -> PASS.
- Actual CLI Markdown probes using disposable state: three-project synthetic registry initialized and `brief --format markdown` rendered all three summary rows contiguously before three matching evidence blocks; one-project and zero-project probes retained the expected single-block and header-only cases.
- Scope limits: only `src/geoffrey_mission_control/brief.py`, `tests/test_brief.py`, and this verification record changed; no live scans, native writes, config/install, credentials/secrets, commits, pushes, or overall v1 acceptance claim.

Storage unknown-field repair:
- RED: `PYTHONPATH=src python3 -m unittest tests.test_store.StoreTests.test_save_request_rejects_unknown_fields_without_mutation tests.test_store.StoreTests.test_save_request_accepts_canonical_packet_metadata -v` -> FAIL, 2 tests. Direct `Store.save_request` silently accepted and discarded an `unexpected` caller field; generated packet metadata was also stripped before storage.
- Production repair: `Store.save_request` now validates the complete owned request boundary, rejects unknown fields before any database write, and explicitly validates/preserves the six canonical packet metadata fields generated by `make_packet`. Strict `validate_request` remains request-only and continues rejecting packet metadata when called directly on an input request.
- GREEN focused: the same command -> PASS, 2 tests. The rejection test snapshots request rows and both packet projection bytes before/after the rejected call; the positive test proves canonical generated metadata survives storage and packet projection.
- GREEN full: `PYTHONPATH=src python3 -m unittest discover -s tests -v` -> PASS, 58 tests; `python3 -m compileall -q src tests` -> PASS; `git diff --check` -> PASS.
- Scope limits: only `src/geoffrey_mission_control/store.py`, `src/geoffrey_mission_control/contracts.py`, `tests/test_store.py`, and this verification record changed for this repair. Synthetic isolated tests only; no live Hermes/project/network/credentials/secrets, commits, pushes, or overall v1 acceptance claim.

Section 8 criterion-by-criterion evidence matrix (current run):

| Requirement | Exact executable assertion(s) | Executed result |
|---|---|---|
| Valid registry/checkpoint/request | `test_registry_accepts_minimal_valid_project` asserts normalized project; `test_checkpoint_requires_evidence_and_normalizes_timestamp` asserts UTC normalization; `test_request_contract_enforces_times_and_types` asserts valid request ID | PASS in full 67-test run |
| Duplicate IDs/keys | `test_unknown_fields_duplicate_keys_and_bool_int_are_rejected` asserts duplicate JSON key and bool schema rejection; `test_registry_idempotent_and_conflict` asserts conflicting registry replay; `test_same_time_checkpoint_conflict_and_out_of_order_history` asserts same-time checkpoint conflict | PASS |
| Unknown fields, nested unknown/type boundaries | `test_nested_unknown_fields_and_wrong_types_are_rejected_for_each_document` asserts registry project, checkpoint root/evidence, request root/scope rejection; `test_save_request_rejects_unknown_fields_without_mutation` snapshots rows and packet bytes | PASS |
| Bool/int confusion | `test_unknown_fields_duplicate_keys_and_bool_int_are_rejected` asserts `schema_version=True` rejection; `test_hermes_rejects_duplicate_ids_wrong_types_and_oversize` asserts boolean epoch rejection | PASS |
| Null versus missing fields | `test_missing_null_timezone_and_time_boundaries_are_explicit` asserts missing request fields and null timestamps are rejected | PASS |
| Invalid timezone/future timestamps and boundaries | `test_missing_null_timezone_and_time_boundaries_are_explicit` asserts timezone-less, invalid-offset, exact expiry, and >5-minute future request cases; `test_checkpoint_rejects_future_and_missing_author` asserts checkpoint future rejection; `test_git_collect_rejects_malformed_and_future_timestamp` asserts malformed/future git times; `test_hermes_output_and_future_epoch_limits` asserts future Hermes epoch rejection | PASS |
| Input/file/string/array limits | `test_load_json_accepts_string_and_bytes_and_enforces_limit` asserts byte/string input limit; `test_nested_arrays_and_text_limits_are_enforced` asserts 101-item arrays and 4097-char title rejection; `test_hermes_rejects_duplicate_ids_wrong_types_and_oversize` asserts 1 MiB Hermes output rejection | PASS |
| Missing git root, timeout, nonzero, malformed output | `test_failed_source_cannot_generate_request` asserts missing repo scan/request failure; `test_git_timeout_nonzero_and_read_only_invariants` asserts timeout-like failure; `test_git_collect_rejects_status_failure_instead_of_calling_clean` asserts failed status is visible; `test_git_collect_rejects_malformed_and_future_timestamp` asserts malformed metadata | PASS |
| Dirty git without invented timestamp and read-only invariant | `test_git_collect_metadata_and_safety_contract` asserts dirty flag, exact HEAD, shell=False, timeout=15; `test_git_timeout_nonzero_and_read_only_invariants` snapshots sentinel and exact argv, including hostile repo path | PASS |
| Hermes supported/malformed/unknown status/absent task | `test_hermes_requires_version_list_show_and_normalizes_epochs` asserts pinned version, list/show route, and normalized epoch; `test_hermes_unknown_status_absent_task_and_malformed_envelopes` asserts unknown status, mismatched task, malformed show/list, and missing task errors | PASS |
| Checkpoint replay/conflict/out-of-order/same-time | `test_checkpoint_conflicting_replay_rejected` asserts exact replay then changed summary rejection; `test_same_time_checkpoint_conflict_and_out_of_order_history` asserts newer checkpoint remains and equal-time conflict fails; `test_checkpoint_same_clock_requires_rescan_then_allows_request` asserts checkpoint invalidates pre-checkpoint observation until scan | PASS |
| Store restart/concurrency/schema mismatch | `test_unsupported_schema_is_rejected_after_restart` asserts PRAGMA 99 rejection; `test_scan_lock_excludes_process_and_releases_after_kill_without_partial_rows` asserts busy contender, no child rows after SIGKILL, preserved committed history, and recovery; `test_scan_exception_rolls_back_and_releases_for_fresh_process` asserts rollback/restart | PASS |
| Failed scan atomicity/source-error semantics | `test_scan_transaction_rolls_back` asserts uncommitted row absent; `test_failed_source_is_a_complete_observation_not_an_interrupted_scan` asserts one complete row per project and visible error; `test_failed_scan_preserves_prior_last_verified_and_quiet_is_semantic` asserts prior verification survives | PASS |
| Receipt verification mismatch and immutability | `test_receipt_immutable_identity_and_false_key_proof` asserts exact task/board/assignee/marker, malformed/unavailable/missing failures, immutable replay after status/expiry, row/packet snapshots, and `idempotency_key_verified=False` | PASS |
| Request stale/expiry/permission/paused/non-Hermes | `test_request_rejects_stale_expired_future_and_active_task` asserts expired and wrong observation rejection; `test_request_observation_age_boundary_and_unbound_stale_replay` asserts exact one-hour acceptance, stale rejection, and unchanged snapshots; `test_request_cli_rejects_permission_pause_and_nonhermes_without_mutation` asserts all three CLI rejections and byte-identical state | PASS |
| Request active/unavailable task and destination dedup | `test_request_queries_destination_board_and_only_relevant_tasks` asserts explicit destination list/show calls; `test_request_rejects_exact_marker_and_unavailable_destination` asserts marker and empty/unavailable destination rejection | PASS |
| Request replay/conflict/expired unbound replay | `test_receipt_immutable_identity_and_false_key_proof` asserts identical replay, changed-title conflict without mutation, and bound expired read-only replay; `test_request_observation_age_boundary_and_unbound_stale_replay` asserts stale unbound replay packet/SQLite immutability | PASS |
| No subprocess execution of user text | `test_hostile_user_text_is_data_and_never_shell_executed` asserts shell metacharacters survive packet output while `executed`/`backtick` sentinel files do not exist; collector tests assert `shell=False` | PASS |
| Brief grouping/provenance/conflict/pause/stale boundary | `test_checkpoint_task_compatibility_matrix` asserts contradiction matrix; `test_status_origin_is_the_winning_precedence_branch` asserts each precedence origin; `test_paused_and_decision_conflict_unknowns_and_capability` asserts pause/decision/capability; `test_failure_preserves_old_verified_and_stale_equality_is_fresh` asserts equality freshness; Markdown tests assert evidence/provenance/escaping and contiguous multi-project table | PASS |
| Semantic no-change quiet/new error | `test_quiet_unchanged_suppresses_advancing_scan_clock_but_persists_freshness` asserts empty output, new observation, unchanged digest; `test_quiet_unchanged_keeps_zero_project_scan_silent` asserts both clocks silent; `test_quiet_unchanged_reports_each_semantic_source_transition` and `test_quiet_unchanged_surfaces_semantic_changes_and_repeated_errors` assert source changes/errors remain visible | PASS |
| CLI end-to-end and packet crash/recovery | `test_full_lifecycle_and_receipt_readback` asserts scan→checkpoint→brief→request→receipt; `test_packet_crash_after_json_publication_recovers_without_duplicate_rows` asserts SQLite truth, partial projection, fresh-process repair, preserved rows/bytes, and modes; packet tests assert injected publication failures and symlink escape | PASS |

Current execution evidence:
- `PYTHONPATH=src python3 -m unittest discover -s tests -v` -> PASS, 67 tests.
- `PYTHONPATH=src python3 -m unittest tests.test_contracts tests.test_collectors tests.test_store tests.test_acceptance -v` -> PASS, 49 tests.
- `python3 -m compileall -q src tests` -> PASS.
- `git diff --check` -> PASS.
- `PYTHONPATH=src python3 -m geoffrey_mission_control --help` -> PASS; argparse help rendered.
- Only `tests/test_contracts.py`, `tests/test_collectors.py`, `tests/test_store.py`, `tests/test_acceptance.py`, and this verification record changed. No production edits, live scans, native writes, credentials, network, config/install, commits, or pushes.
- This matrix proves local offline test evidence only; G4-G7 and overall v1 acceptance remain coordinator-owned and pending.

Boundary repair — registry and receipt argument validation:
- RED: `PYTHONPATH=src python3 -m unittest tests.test_contracts.ContractsTests.test_registry_boundary_limits_and_nullable_profile tests.test_collectors.CollectorTests.test_hermes_public_boundary_rejects_invalid_board_and_task_without_calls tests.test_acceptance.AcceptanceHarness.test_receipt_invalid_arguments_fail_before_external_call_or_state_mutation -v` -> FAIL, 2 defects reproduced: repository paths over 4096 were accepted and Hermes adapter calls began before route validation. The CLI invalid-receipt regression was already green because the missing request failed before external invocation; production validation was still added at the CLI boundary.
- GREEN focused: the same command -> PASS, 3 tests. Registry now accepts absolute/null `repo_path` only through 4096 characters and nullable canonical profile slugs; the public Hermes adapters reject malformed board slugs, empty task IDs, and task IDs over 128 before any runner call while preserving opaque task text verbatim. Receipt validates request ID, board slug, and opaque task ID before opening state or invoking Hermes; invalid cases leave state bytes unchanged and create no external log.
- GREEN: `PYTHONPATH=src python3 -m unittest discover -s tests -v` -> PASS, 70 tests; `PYTHONPATH=src python3 -m unittest tests.test_contracts tests.test_collectors tests.test_store tests.test_acceptance -v` -> PASS, 52 tests; `python3 -m compileall -q src tests` -> PASS; `git diff --check` -> PASS; `PYTHONPATH=src python3 -m geoffrey_mission_control --help` -> PASS. Exact 4096-character repository and 128-character opaque task boundaries are accepted; over-limit values are rejected.
- Scope: only `src/geoffrey_mission_control/contracts.py`, `src/geoffrey_mission_control/cli.py`, `src/geoffrey_mission_control/collectors.py`, `tests/test_contracts.py`, `tests/test_collectors.py`, `tests/test_acceptance.py`, and this verification record. Synthetic/offline only; no live Hermes/project/network/secrets/config/install/commits/pushes. No overall v1 acceptance claim; plan allowlist and remaining collector coverage gaps stay open.

Accepted amendment follow-up (parent `t_cf8d0d5e`):
- Parent fresh-eyes review accepted the narrow contract amendment: the existing `tests/test_acceptance.py` allowlist entry and the Section 6A external root census bound are approved; this records acceptance after review, not retroactive approval.
- RED: `PYTHONPATH=src python3 -m unittest tests.test_collectors.CollectorTests.test_hermes_collect_accepts_exactly_100_and_rejects_101_before_show tests.test_collectors.CollectorTests.test_hermes_board_collect_accepts_exactly_100_and_rejects_101_before_show -v` -> FAIL for both 101-entry cases before the collector repair; both incorrectly returned successful censuses and proceeded to show traversal. The new nonzero `CompletedProcess` Git test was already green and was retained as genuine coverage, not represented as a fabricated RED.
- GREEN focused: the same collector command plus `test_git_collect_rejects_real_nonzero_completed_process` -> PASS, 3 tests. Both collectors now reject 101 entries as `hermes: census exceeds 100 entries` before any show traversal, accept exact 100 entries, preserve opaque IDs and nested unknown external fields, and never truncate a census.
- GREEN full: `PYTHONPATH=src python3 -m unittest discover -s tests -v` -> PASS, 74 tests; `python3 -m compileall -q src tests` -> PASS; `git diff --check` -> PASS; `PYTHONPATH=src python3 -m geoffrey_mission_control --help` -> PASS.
- Exact Section 8 mapping updates: input/array limits now include the two exact-100/101 census tests; missing git/nonzero coverage includes `test_git_collect_rejects_real_nonzero_completed_process`; Hermes supported/malformed coverage includes both census-bound tests; request destination dedup includes `test_request_queries_destination_board_and_only_relevant_tasks`, and oversized destination refusal plus DB/packet immutability is proven by `test_request_rejects_oversized_destination_without_state_mutation` before any show traversal. No production DB/packet mutation path was introduced or changed by this collector-only repair.
- Scope: only `src/geoffrey_mission_control/collectors.py`, `tests/test_collectors.py`, `tests/test_acceptance.py`, and this verification record changed for this amendment. Synthetic/offline only; no live Hermes/project/network/secrets/config/install/commits/pushes. This is not a G1-G3 or whole-v1 acceptance claim; G4-G7 remain coordinator-owned.

Deterministic observation-age acceptance repair:
- RED reproduced with `PYTHONPATH=src python3 -m unittest tests.test_acceptance.AcceptanceHarness.test_request_observation_age_boundary_and_unbound_stale_replay -v` -> FAIL at the scan assertion (exit 1). The real temporary Git commit used the wall clock, while the test injected `2026-09-26T23:00:00Z`; `git_collect` correctly rejected the commit as future evidence relative to that scan clock.
- Repair: `tests/test_acceptance.py` now sets `GIT_AUTHOR_DATE` and `GIT_COMMITTER_DATE` to the injected scan instant only for the synthetic fixture commit. No collector future-evidence guard was weakened and no production code changed.
- GREEN focused: the same focused command -> PASS, 1 test. It accepts an observation exactly one hour old, rejects an observation older than one hour, and rejects an unbound stale same-ID replay while preserving SQLite rows and packet bytes.
- GREEN full: `PYTHONPATH=src python3 -m unittest discover -s tests -v` -> PASS, 74 tests; `PYTHONPATH=src python3 -m unittest tests.test_contracts tests.test_collectors tests.test_store tests.test_acceptance -v` -> PASS, 56 tests; `python3 -m compileall -q src tests` -> PASS; `git diff --check` -> PASS; `PYTHONPATH=src python3 -m geoffrey_mission_control --help` -> PASS.
- Scope: only `tests/test_acceptance.py` and this verification record changed. Synthetic/offline only; no live Hermes/project/network/secrets/config/install/commits/pushes. No overall v1 acceptance claim; G4-G7 remain coordinator-owned.

Hermes compatibility repair:
- Live read-only checks: `hermes --version` -> `Hermes Agent v0.21.5+2858.gb7d0620 (2026.9.24) · upstream b7d0620d`; representative `hermes kanban --board mission-control show t_0dbfee05 --json` returned the reviewed envelope with task fields `id`, `assignee`, `status`, `body`, `created_at`, `started_at`, and `completed_at`, plus `latest_summary`, `parents`, `children`, `comments`, `events`, and `runs`. The required status vocabulary is represented by the accepted contract; the returned task status was `running`.
- The required live list command was attempted exactly as `hermes kanban --board mission-control list --json`, but this worker context returned exit 1 with `kanban: delegate_task child contexts cannot mutate Kanban tasks or boards` before JSON output. No schema mismatch is claimed from that unavailable probe; no live state was changed.
- Production repair: both collectors now share an explicit exact-token allowlist for `v0.21.5+2453.gd0288be` and `v0.21.5+2858.gb7d0620`, inspect only the first version-output line, and fail closed for adjacent/suffixed/unreviewed tokens or versions appearing only on later lines. The prior reviewed pin remains for compatibility; the installed pin is added based on the live version check.
- RED: not available before this worker's implementation because the compatibility defect was identified from the current source/live version comparison; no fabricated pre-change failure is recorded.
- GREEN focused: `PYTHONPATH=src python3 -m unittest tests.test_collectors.CollectorTests.test_both_hermes_collectors_allow_only_reviewed_exact_versions tests.test_collectors.CollectorTests.test_hermes_requires_version_list_show_and_normalizes_epochs -v` -> PASS, 2 tests.
- GREEN full: `PYTHONPATH=src python3 -m unittest discover -s tests -v` -> PASS, 75 tests; `python3 -m compileall -q src tests` -> PASS; `git diff --check` -> PASS; `PYTHONPATH=src python3 -m geoffrey_mission_control --help` -> PASS.
- Scope: only `src/geoffrey_mission_control/collectors.py`, `tests/test_collectors.py`, `docs/V1_PLAN.md`, `docs/OPERATIONS.md`, and this verification record changed. No private registry/canary state, project repository, scheduler, skills, profile config, credentials, global Hermes installation, commits, or pushes were modified. G5 cannot be called safely retried from this worker because native canary lifecycle verification remains coordinator-owned and the live list probe was blocked by the worker-context guard.

Deterministic fake-Hermes epoch repair:
- RED: with the acceptance fixture's wall-clock task epochs and a controlled wall time after `NOW`, `PYTHONPATH=src python3 -m unittest tests.test_acceptance.AcceptanceHarness.test_receipt_immutable_identity_and_false_key_proof tests.test_acceptance.AcceptanceHarness.test_request_queries_destination_board_and_only_relevant_tasks -v` -> FAIL, 2 tests. Both failures were `hermes: future timestamp`/destination unavailable because `int(time.time()) - 60` was later than the injected `2026-09-27T00:00:00Z` command clock.
- Repair: `tests/test_acceptance.py` now derives one `TASK_CREATED_AT` epoch from the fixed `NOW` minus 60 seconds and uses it for all fake Hermes task/list fixtures. Production future-epoch rejection and tests that intentionally exercise future timestamps are unchanged; no production code changed.
- GREEN focused: the same two-test command -> PASS, 2 tests.
- GREEN full: `PYTHONPATH=src python3 -m unittest discover -s tests -v` -> PASS, 75 tests; `PYTHONPATH=src python3 -m unittest tests.test_contracts tests.test_collectors tests.test_store tests.test_acceptance -v` -> PASS, 57 tests; `python3 -m compileall -q src tests` -> PASS; `git diff --check` -> PASS; `PYTHONPATH=src python3 -m geoffrey_mission_control --help` -> PASS.
- Scope: only `tests/test_acceptance.py` and this verification record changed. Synthetic/offline only; no live Hermes/project/network/secrets/config/install/commits/pushes. No overall v1 acceptance claim; G4-G7 remain coordinator-owned.
