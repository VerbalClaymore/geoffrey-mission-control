"""Section 8 executable acceptance harness.

These tests use only disposable git repositories, a disposable fake Hermes argv
program, and temporary state.  They intentionally assert the v1 contract rather
than the current implementation; red results are actionable production defects.
"""
import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from geoffrey_mission_control.cli import main
from geoffrey_mission_control.store import Store
from geoffrey_mission_control.packets import make_packet, write_packet

NOW = "2026-09-27T00:00:00Z"
TASK_CREATED_AT = int(datetime.fromisoformat(NOW.replace("Z", "+00:00")).timestamp()) - 60


class AcceptanceHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Fixture"], cwd=self.repo, check=True)
        (self.repo / "README").write_text("synthetic\n")
        subprocess.run(["git", "add", "README"], cwd=self.repo, check=True)
        commit_env = os.environ.copy()
        commit_env["GIT_AUTHOR_DATE"] = "2026-09-26T23:00:00Z"
        commit_env["GIT_COMMITTER_DATE"] = "2026-09-26T23:00:00Z"
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=self.repo, env=commit_env, check=True)
        self.state = self.root / "state"
        self.registry = self.root / "registry.json"
        self.registry.write_text(json.dumps({
            "schema_version": 1, "projects": [{
                "id": "demo", "name": "Demo", "goal": "Test", "milestone": "v1",
                "repo_path": str(self.repo),
                "owner": {"surface": "hermes", "profile": "geoffrey", "session_ref": None},
                "canonical_spec": "docs/V1_PLAN.md", "permission": "coordinate",
                "paused": False, "stale_after_hours": 48, "kanban_refs": [],
            }]
        }))
        self.assertEqual(self.call("init", "--registry", str(self.registry))[0], 0)

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, *args, env=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--state-dir", str(self.state), *args])
        return code, out.getvalue(), err.getvalue()

    def subprocess_call(self, *args):
        return self.subprocess_call_at(*args)

    def subprocess_call_at(self, *args, clock_value=None):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        env["PATH"] = str(self.root) + os.pathsep + env.get("PATH", "")
        command = [sys.executable, "-m", "geoffrey_mission_control"]
        if clock_value is not None:
            # Inject the existing cli.clock seam in the child process; this
            # keeps expiry proof deterministic without adding a public flag.
            command = [sys.executable, "-c", (
                "from geoffrey_mission_control import cli; "
                f"cli.clock=lambda: {clock_value!r}; "
                "raise SystemExit(cli.main())"
            )]
        return subprocess.run(
            command + ["--state-dir", str(self.state), *args],
            env=env, capture_output=True, text=True, check=False,
        )

    def observation_id(self):
        with contextlib.closing(Store(self.state)) as _:
            pass

    def request_file(self, observation_id, request_id="req-one", created=NOW, expires="2026-09-27T01:00:00Z"):
        path = self.root / (request_id + ".json")
        path.write_text(json.dumps({
            "schema_version": 1, "id": request_id, "project_id": "demo",
            "created_at": created, "expires_at": expires,
            "expected_observation_id": observation_id, "assignee": "geoffrey",
            "board": "synthetic", "title": "Synthetic request", "scope": ["No-op"],
            "acceptance": ["Read-only"], "forbidden": ["Execution"],
            "approval_ref": "operator-approved",
        }))
        return path

    def fake_hermes(self, task_id="t_fixture", status="blocked", body=None, board="synthetic", log=None, tasks=None, assignee="geoffrey"):
        exe = self.root / "hermes"
        # Keep the external fixture just behind the controlled acceptance clock;
        # wall-clock timestamps can be after NOW on CI and look future-dated.
        created_at = TASK_CREATED_AT
        payload = {"id": task_id, "assignee": assignee, "status": status,
                   "body": body or "synthetic", "created_at": created_at,
                   "started_at": None, "completed_at": None}
        listed = tasks if tasks is not None else [payload]
        log_line = str(log) if log else ""
        script = "#!/bin/sh\n" + ("printf '%s\\n' \"$*\" >> '" + log_line + "'\n" if log_line else "") + "if [ \"$1\" = \"--version\" ]; then printf '%s\\n' 'hermes " + "0.21.5+2453.gd0288be" + "'; exit 0; fi\n" + "if [ \"$4\" = \"list\" ]; then printf '%s' '" + json.dumps(listed).replace("'", "'\\''") + "'; exit 0; fi\nprintf '%s' '" + json.dumps({"task": payload}).replace("'", "'\\''") + "'\n"
        exe.write_text(script)
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
        return exe

    def test_request_queries_destination_board_and_only_relevant_tasks(self):
        self.assertEqual(self.call("scan", "--now", NOW)[0], 0)
        with contextlib.closing(Store(self.state)) as store: oid = store.latest_observation("demo")["id"]
        log = self.root / "hermes.log"
        unrelated = {"id": "unrelated", "assignee": "geoffrey", "status": "running", "body": "synthetic", "created_at": TASK_CREATED_AT, "started_at": None, "completed_at": None}
        self.fake_hermes(task_id="unrelated", status="running", board="synthetic", log=log, tasks=[unrelated])
        old_path = os.environ.get("PATH", ""); os.environ["PATH"] = str(self.root) + os.pathsep + old_path
        try:
            code, _, err = self.call("request", "--file", str(self.request_file(oid)), "--now", NOW)
        finally: os.environ["PATH"] = old_path
        self.assertEqual(code, 0, err + (log.read_text() if log.exists() else "<no-log>"))
        calls = log.read_text().splitlines()
        self.assertIn("kanban --board synthetic list --json", calls)
        self.assertIn("kanban --board synthetic show unrelated --json", calls)

    def test_receipt_invalid_arguments_fail_before_external_call_or_state_mutation(self):
        log = self.root / "receipt.log"
        self.fake_hermes(log=log)
        before = {p.relative_to(self.state): p.read_bytes() for p in self.state.rglob("*") if p.is_file()}
        old_path = os.environ.get("PATH", ""); os.environ["PATH"] = str(self.root) + os.pathsep + old_path
        try:
            for request_id, task_id, board in (("bad id!", "t_fixture", "synthetic"),
                                               ("req-one", "", "synthetic"),
                                               ("req-one", "x" * 129, "synthetic"),
                                               ("req-one", "t_fixture", "bad board")):
                with self.subTest(request_id=request_id, task_id=task_id, board=board):
                    self.assertNotEqual(self.call("receipt", "--request-id", request_id,
                                                  "--task-id", task_id, "--board", board)[0], 0)
        finally:
            os.environ["PATH"] = old_path
        after = {p.relative_to(self.state): p.read_bytes() for p in self.state.rglob("*") if p.is_file()}
        self.assertEqual(after, before)
        self.assertFalse(log.exists())

    def test_request_rejects_exact_marker_and_unavailable_destination(self):
        self.assertEqual(self.call("scan", "--now", NOW)[0], 0)
        with contextlib.closing(Store(self.state)) as store: oid = store.latest_observation("demo")["id"]
        marker = "Mission-Control-Request: demo/req-one"
        self.fake_hermes(body=marker)
        old_path = os.environ.get("PATH", ""); os.environ["PATH"] = str(self.root) + os.pathsep + old_path
        try:
            self.assertNotEqual(self.call("request", "--file", str(self.request_file(oid)), "--now", NOW)[0], 0)
        finally: os.environ["PATH"] = old_path

        self.fake_hermes(tasks=[], log=None)
        try:
            self.assertNotEqual(self.call("request", "--file", str(self.request_file(oid, request_id="req-two")), "--now", NOW)[0], 0)
        finally: os.environ["PATH"] = old_path

    def test_request_rejects_oversized_destination_without_state_mutation(self):
        self.assertEqual(self.call("scan", "--now", NOW)[0], 0)
        with contextlib.closing(Store(self.state)) as store:
            oid = store.latest_observation("demo")["id"]
        tasks = [{"id": f"external-{index:03d}", "assignee": "geoffrey", "status": "ready",
                  "body": "synthetic", "created_at": TASK_CREATED_AT,
                  "started_at": None, "completed_at": None,
                  "unknown": {"nested": index}} for index in range(101)]
        self.fake_hermes(tasks=tasks)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(self.root) + os.pathsep + old_path
        try:
            before = self.state_snapshot()
            result = self.call("request", "--file", str(self.request_file(oid, request_id="req-oversized")), "--now", NOW)
            self.assertNotEqual(result[0], 0)
            self.assertEqual(self.state_snapshot(), before)
        finally:
            os.environ["PATH"] = old_path

    def test_checkpoint_same_clock_requires_rescan_then_allows_request(self):
        self.assertEqual(self.call("scan", "--now", NOW)[0], 0)
        with contextlib.closing(Store(self.state)) as store: oid = store.latest_observation("demo")["id"]
        checkpoint = self.root / "same-clock.json"
        checkpoint.write_text(json.dumps({"schema_version": 1, "id": "cp-same", "project_id": "demo", "observed_at": NOW, "state": "running", "summary": "owner report", "next_action": "continue", "decision": None, "evidence": [{"kind": "test", "ref": "fixture", "at": NOW}], "provenance": {"surface": "hermes", "author": "fixture"}}))
        self.assertEqual(self.call("checkpoint", "--file", str(checkpoint), "--now", NOW)[0], 0)
        self.assertNotEqual(self.call("request", "--file", str(self.request_file(oid)), "--now", NOW)[0], 0)
        self.assertEqual(self.call("scan", "--now", NOW)[0], 0)
        with contextlib.closing(Store(self.state)) as store: fresh = store.latest_observation("demo")["id"]
        self.fake_hermes(tasks=[])
        old_path = os.environ.get("PATH", ""); os.environ["PATH"] = str(self.root) + os.pathsep + old_path
        try:
            result = self.call("request", "--file", str(self.request_file(fresh)), "--now", NOW)
        finally: os.environ["PATH"] = old_path
        self.assertEqual(result[0], 0, result[2])

    def test_full_lifecycle_and_receipt_readback(self):
        code, out, err = self.call("scan", "--now", NOW)
        self.assertEqual((code, err), (0, ""), out)
        with contextlib.closing(Store(self.state)) as store:
            oid = store.latest_observation("demo")["id"]
        checkpoint = self.root / "checkpoint.json"
        checkpoint.write_text(json.dumps({
            "schema_version": 1, "id": "cp-one", "project_id": "demo",
            "observed_at": NOW, "state": "running", "summary": "owner report",
            "next_action": "continue", "decision": None,
            "evidence": [{"kind": "test", "ref": "fixture", "at": NOW}],
            "provenance": {"surface": "hermes", "author": "fixture"},
        }))
        self.assertEqual(self.call("checkpoint", "--file", str(checkpoint), "--now", NOW)[0], 0)
        self.assertEqual(self.call("brief", "--format", "json", "--now", NOW)[0], 0)
        # Equal-clock checkpoint ingestion still invalidates the prior scan;
        # prove rejection first, then establish the required post-checkpoint scan.
        self.assertNotEqual(self.call("request", "--file", str(self.request_file(oid)), "--now", NOW)[0], 0)
        self.assertEqual(self.call("scan", "--now", NOW)[0], 0)
        with contextlib.closing(Store(self.state)) as store: oid = store.latest_observation("demo")["id"]
        req = self.request_file(oid)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(self.root) + os.pathsep + old_path
        try:
            self.fake_hermes(tasks=[])
            self.assertEqual(self.call("request", "--file", str(req), "--now", NOW)[0], 0)
            marker = "Mission-Control-Request: demo/req-one"
            self.fake_hermes(body=marker)
            code, _, err = self.call("receipt", "--request-id", "req-one", "--task-id", "t_fixture", "--board", "synthetic")
        finally:
            os.environ["PATH"] = old_path
        self.assertEqual(code, 0, err)
        self.assertIn('"task_identity_verified": true', _)

    def test_failed_source_cannot_generate_request(self):
        missing = self.root / "does-not-exist"
        data = json.loads(self.registry.read_text())
        data["projects"][0]["repo_path"] = str(missing)
        self.registry.write_text(json.dumps(data))
        # Registry is immutable; a fresh state makes this a real failed-source run.
        self.state = self.root / "failed-state"
        self.assertEqual(self.call("init", "--registry", str(self.registry))[0], 0)
        code, _, _ = self.call("scan", "--now", NOW)
        self.assertEqual(code, 1)
        with contextlib.closing(Store(self.state)) as store:
            oid = store.latest_observation("demo")["id"]
        self.assertNotEqual(self.call("request", "--file", str(self.request_file(oid)), "--now", NOW)[0], 0)

    def test_failed_source_is_a_complete_observation_not_an_interrupted_scan(self):
        """A source error is committed as data; interruption is the atomic path."""
        multi_state = self.root / "multi-state"
        multi_registry = self.root / "multi-registry.json"
        missing = self.root / "missing-second-repo"
        data = json.loads(self.registry.read_text())
        second = dict(data["projects"][0])
        second["id"] = "second"
        second["name"] = "Second"
        second["repo_path"] = str(missing)
        data["projects"].append(second)
        multi_registry.write_text(json.dumps(data))
        self.assertEqual(main(["--state-dir", str(multi_state), "init", "--registry", str(multi_registry)]), 0)
        self.assertEqual(main(["--state-dir", str(multi_state), "scan", "--now", NOW]), 1)

        store = Store(multi_state)
        try:
            first = store.latest_observation("demo")
            failed = store.latest_observation("second")
            self.assertIsNotNone(first)
            self.assertIsNotNone(failed)
            self.assertEqual(len(store.observations("demo")), 1)
            self.assertEqual(len(store.observations("second")), 1)
            self.assertEqual(json.loads(first["payload"])["errors"], [])
            self.assertTrue(json.loads(failed["payload"])["errors"])
        finally:
            store.close()

    def test_failed_scan_preserves_prior_last_verified_and_quiet_is_semantic(self):
        self.assertEqual(self.call("scan", "--now", NOW, "--quiet-unchanged")[0], 0)
        with contextlib.closing(Store(self.state)) as store:
            first = store.latest_observation("demo")["id"]
            before = json.loads(store.latest_observation("demo")["payload"])["last_verified"]
        # Make the already-registered source unavailable, then scan the same
        # state so the prior verification timestamp must remain observable.
        self.repo.rename(self.root / "gone")
        self.assertEqual(main(["--state-dir", str(self.state), "scan", "--now", NOW]), 1)
        with contextlib.closing(Store(self.state)) as store:
            after = json.loads(store.latest_observation("demo")["payload"])
        self.assertEqual(after["last_verified"], before, "failed scan must preserve prior verification")
        self.assertNotEqual(first, "")

    def test_quiet_unchanged_suppresses_advancing_scan_clock_but_persists_freshness(self):
        first_code, first_out, first_err = self.call("scan", "--now", NOW, "--quiet-unchanged")
        self.assertEqual((first_code, first_err), (0, ""))
        self.assertTrue(first_out)
        with contextlib.closing(Store(self.state)) as store:
            first = store.latest_observation("demo")
            first_payload = json.loads(first["payload"])
        later = "2026-09-27T00:05:00Z"
        code, out, err = self.call("scan", "--now", later, "--quiet-unchanged")
        self.assertEqual((code, out, err), (0, "", ""))
        with contextlib.closing(Store(self.state)) as store:
            second = store.latest_observation("demo")
            second_payload = json.loads(second["payload"])
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(second["observed_at"], later)
        self.assertEqual(second_payload["last_verified"], later)
        self.assertEqual(first_payload["semantic_digest"], second_payload["semantic_digest"])

    def test_quiet_unchanged_keeps_zero_project_scan_silent(self):
        registry = json.loads(self.registry.read_text())
        registry["projects"] = []
        empty_registry = self.root / "empty-registry.json"
        empty_registry.write_text(json.dumps(registry))
        empty_state = self.root / "empty-state"
        self.assertEqual(main(["--state-dir", str(empty_state), "init", "--registry", str(empty_registry)]), 0)
        for now in (NOW, "2026-09-27T00:05:00Z"):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(["--state-dir", str(empty_state), "scan", "--now", now, "--quiet-unchanged"])
            self.assertEqual((code, out.getvalue(), err.getvalue()), (0, "", ""))

    def test_quiet_unchanged_surfaces_semantic_changes_and_repeated_errors(self):
        self.assertEqual(self.call("scan", "--now", NOW, "--quiet-unchanged")[0], 0)
        self.repo.rename(self.root / "gone")
        code, out, err = self.call("scan", "--now", "2026-09-27T00:05:00Z", "--quiet-unchanged")
        self.assertEqual(code, 1)
        self.assertTrue(out)
        self.assertIn("unavailable", out)
        code, out, err = self.call("scan", "--now", "2026-09-27T00:10:00Z", "--quiet-unchanged")
        self.assertEqual(code, 1)
        self.assertTrue(out)
        self.assertIn("unavailable", out)

    def test_quiet_unchanged_reports_each_semantic_source_transition(self):
        base = {
            "ok": True,
            "sources": {
                "git": {"ok": True, "head": "a" * 40, "branch": "main", "latest_commit_at": NOW, "dirty": False},
                "hermes": [{"board": "synthetic", "task_id": "t", "result": {"ok": True, "task": {"id": "t", "assignee": "geoffrey", "status": "blocked"}}}],
            },
            "errors": [],
        }
        variants = [
            base,
            {**base, "sources": {**base["sources"], "git": {**base["sources"]["git"], "head": "b" * 40}}},
            {**base, "sources": {**base["sources"], "git": {**base["sources"]["git"], "dirty": True}}},
            {**base, "sources": {**base["sources"], "hermes": [{**base["sources"]["hermes"][0], "result": {"ok": True, "task": {"id": "t", "assignee": "geoffrey", "status": "running"}}}]}},
            {**base, "errors": [{"source": "git", "error": "git: unavailable evidence"}], "ok": False},
            base,
        ]
        clocks = [f"2026-09-27T00:{minute:02d}:00Z" for minute in range(0, 30, 5)]
        with patch("geoffrey_mission_control.cli.collect_sources", side_effect=variants):
            code, out, err = self.call("scan", "--now", clocks[0], "--quiet-unchanged")
            self.assertEqual((code, err), (0, ""), out)
            self.assertTrue(out)
            checkpoint = self.root / "quiet-checkpoint.json"
            checkpoint.write_text(json.dumps({
                "schema_version": 1, "id": "cp-quiet", "project_id": "demo", "observed_at": clocks[0],
                "state": "running", "summary": "semantic checkpoint", "next_action": None, "decision": None,
                "evidence": [{"kind": "test", "ref": "quiet", "at": clocks[0]}],
                "provenance": {"surface": "hermes", "author": "fixture"},
            }))
            self.assertEqual(self.call("checkpoint", "--file", str(checkpoint), "--now", clocks[0])[0], 0)
            for expected, now in zip(variants[1:], clocks[1:]):
                code, out, err = self.call("scan", "--now", now, "--quiet-unchanged")
                self.assertEqual(code, 1 if expected["errors"] else 0, err)
                self.assertTrue(out, expected)

    def test_checkpoint_provenance_must_match_owner(self):
        cp = self.root / "bad-checkpoint.json"
        cp.write_text(json.dumps({
            "schema_version": 1, "id": "cp-bad", "project_id": "demo", "observed_at": NOW,
            "state": "ready", "summary": "bad", "next_action": None, "decision": None,
            "evidence": [{"kind": "other", "ref": "x", "at": NOW}],
            "provenance": {"surface": "codex", "author": "wrong-owner"},
        }))
        self.assertNotEqual(self.call("checkpoint", "--file", str(cp), "--now", NOW)[0], 0)

    def test_request_rejects_stale_expired_future_and_active_task(self):
        self.assertEqual(self.call("scan", "--now", NOW)[0], 0)
        with contextlib.closing(Store(self.state)) as store: oid = store.latest_observation("demo")["id"]
        self.assertNotEqual(self.call("request", "--file", str(self.request_file(oid, created="2025-12-01T00:00:00Z", expires="2025-12-01T01:00:00Z")), "--now", NOW)[0], 0)
        self.assertNotEqual(self.call("request", "--file", str(self.request_file("wrong")), "--now", NOW)[0], 0)

    def test_request_cli_rejects_permission_pause_and_nonhermes_without_mutation(self):
        old_path = os.environ.get("PATH", "")
        self.fake_hermes(tasks=[])
        os.environ["PATH"] = str(self.root) + os.pathsep + old_path
        try:
            for index, changes in enumerate((
                {"permission": "observe"},
                {"paused": True},
                {"owner": {"surface": "codex", "profile": "geoffrey", "session_ref": None}},
            )):
                with self.subTest(changes=changes):
                    state = self.root / f"auth-state-{index}"
                    registry = json.loads(self.registry.read_text())
                    registry["projects"][0].update(changes)
                    registry_path = self.root / f"auth-registry-{index}.json"
                    registry_path.write_text(json.dumps(registry))
                    self.assertEqual(main(["--state-dir", str(state), "init", "--registry", str(registry_path)]), 0)
                    self.assertEqual(main(["--state-dir", str(state), "scan", "--now", NOW]), 0)
                    with contextlib.closing(Store(state)) as store:
                        oid = store.latest_observation("demo")["id"]
                    request = self.request_file(oid, request_id=f"auth-{index}")
                    before = {p.name: p.read_bytes() for p in state.rglob("*") if p.is_file()}
                    result = main(["--state-dir", str(state), "request", "--file", str(request), "--now", NOW])
                    self.assertNotEqual(result, 0)
                    after = {p.name: p.read_bytes() for p in state.rglob("*") if p.is_file()}
                    self.assertEqual(after, before)
        finally:
            os.environ["PATH"] = old_path

    def test_hostile_user_text_is_data_and_never_shell_executed(self):
        self.assertEqual(self.call("scan", "--now", NOW)[0], 0)
        with contextlib.closing(Store(self.state)) as store:
            oid = store.latest_observation("demo")["id"]
        request = self.request_file(oid, request_id="hostile")
        data = json.loads(request.read_text())
        data["title"] = "$(touch " + str(self.root / "executed") + ") ; `touch " + str(self.root / "backtick") + "`"
        request.write_text(json.dumps(data))
        self.fake_hermes(tasks=[])
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(self.root) + os.pathsep + old_path
        try:
            code, out, err = self.call("request", "--file", str(request), "--now", NOW)
        finally:
            os.environ["PATH"] = old_path
        self.assertEqual(code, 0, err)
        self.assertIn("$(touch", out)
        self.assertFalse((self.root / "executed").exists())
        self.assertFalse((self.root / "backtick").exists())

    def test_request_observation_age_boundary_and_unbound_stale_replay(self):
        """Request freshness is measured at the command clock, not scan time."""
        scan_time = "2026-09-26T23:00:00Z"
        command_time = NOW
        self.assertEqual(self.call("scan", "--now", scan_time)[0], 0)
        with contextlib.closing(Store(self.state)) as store:
            oid = store.latest_observation("demo")["id"]
        self.fake_hermes(tasks=[])
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(self.root) + os.pathsep + old_path
        try:
            exact = self.request_file(oid, request_id="req-boundary", created="2026-09-27T00:00:00Z", expires="2026-09-27T02:00:00Z")
            self.assertEqual(self.call("request", "--file", str(exact), "--now", command_time)[0], 0)

            too_old = self.request_file(oid, request_id="req-too-old", created="2026-09-27T00:00:00Z", expires="2026-09-27T02:00:00Z")
            before = self.state_snapshot()
            self.assertNotEqual(self.call("request", "--file", str(too_old), "--now", "2026-09-27T00:00:01Z")[0], 0)
            self.assertEqual(self.state_snapshot(), before)

            replay = self.root / "req-boundary-replay.json"
            replay.write_text(exact.read_text())
            packet_before = (self.state / "packets" / "req-boundary.json").read_bytes()
            before = self.state_snapshot()
            self.assertNotEqual(self.call("request", "--file", str(replay), "--now", "2026-09-27T00:00:01Z")[0], 0)
            self.assertEqual(self.state_snapshot(), before)
            self.assertEqual((self.state / "packets" / "req-boundary.json").read_bytes(), packet_before)
        finally:
            os.environ["PATH"] = old_path

    def test_receipt_immutable_identity_and_false_key_proof(self):
        """Receipt truth must survive process restart and never overclaim key proof."""
        scan = self.subprocess_call("scan", "--now", NOW)
        self.assertEqual(scan.returncode, 0, scan.stderr + scan.stdout)
        with contextlib.closing(Store(self.state)) as store:
            oid = store.latest_observation("demo")["id"]
        request = self.request_file(oid, expires="2026-09-27T00:01:00Z")
        self.fake_hermes(tasks=[])
        request_result = self.subprocess_call("request", "--file", str(request), "--now", NOW)
        self.assertEqual(request_result.returncode, 0, request_result.stderr + request_result.stdout)
        replay_request = self.subprocess_call("request", "--file", str(request), "--now", NOW)
        self.assertEqual(replay_request.returncode, 0, replay_request.stderr)
        self.assertEqual(json.loads(replay_request.stdout)["id"], "req-one")
        conflicting = self.request_file(oid, expires="2026-09-27T00:01:00Z")
        conflict_data = json.loads(conflicting.read_text())
        conflict_data["title"] = "Conflicting payload"
        conflicting.write_text(json.dumps(conflict_data))
        conflict_before = self.state_snapshot()
        conflict_result = self.subprocess_call("request", "--file", str(conflicting), "--now", NOW)
        self.assertNotEqual(conflict_result.returncode, 0)
        self.assertEqual(self.state_snapshot(), conflict_before)

        marker = "Mission-Control-Request: demo/req-one"
        self.fake_hermes(status="running", body=marker)
        first = self.subprocess_call_at(
            "receipt", "--request-id", "req-one", "--task-id", "t_fixture", "--board", "synthetic",
            clock_value=NOW,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        first_payload = json.loads(first.stdout)
        self.assertIs(first_payload["task_identity_verified"], True)
        self.assertIs(first_payload["idempotency_key_verified"], False)
        self.assertEqual(first_payload["task_id"], "t_fixture")
        self.assertEqual(first_payload["board"], "synthetic")
        self.assertEqual(first_payload["request_id"], "req-one")
        self.assertEqual(first_payload["project_id"], "demo")
        self.assertEqual(first_payload["packet_marker"], marker)

        with contextlib.closing(Store(self.state)) as store:
            stored_before = store.receipt("req-one")["payload"]
        persisted_before_expiry = self.state_snapshot()
        expires_at = json.loads(self.state_snapshot()["requests"][0]["payload"])["expires_at"]

        # A fresh process and changed external task state must not rewrite the
        # immutable receipt or turn a read-only replay into new work.
        self.fake_hermes(status="done", body=marker)
        replay_clock = "2026-09-27T00:02:00Z"
        self.assertGreater(
            datetime.fromisoformat(replay_clock.replace("Z", "+00:00")),
            datetime.fromisoformat(expires_at.replace("Z", "+00:00")),
        )
        replay = self.subprocess_call_at(
            "receipt", "--request-id", "req-one", "--task-id", "t_fixture", "--board", "synthetic",
            clock_value=replay_clock,
        )
        self.assertEqual(replay.returncode, 0, replay.stderr)
        self.assertEqual(replay.stdout.strip(), stored_before)
        with contextlib.closing(Store(self.state)) as store:
            self.assertEqual(store.receipt("req-one")["payload"], stored_before)
        self.assertEqual(self.state_snapshot(), persisted_before_expiry)

        # Identity/provenance mismatches are rejected before a receipt can be
        # persisted, including the assignee and exact marker line.  Every
        # failure is checked against both row and packet projections.
        def rejected_without_mutation(*receipt_args):
            before = self.state_snapshot()
            result = self.subprocess_call("receipt", *receipt_args)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.state_snapshot(), before)

        self.fake_hermes(status="running", body="wrong marker")
        rejected_without_mutation("--request-id", "req-one", "--task-id", "t_fixture", "--board", "synthetic")
        self.fake_hermes(status="running", body=marker, assignee="other")
        rejected_without_mutation("--request-id", "req-one", "--task-id", "t_fixture", "--board", "synthetic")

        # The bound receipt remains a historical fact even after its request
        # expires; a conflicting binding or malformed/unavailable read fails
        # without changing it.
        rejected_without_mutation("--request-id", "req-one", "--task-id", "other", "--board", "synthetic")
        rejected_without_mutation("--request-id", "req-one", "--task-id", "t_fixture", "--board", "other-board")
        self.fake_hermes(tasks={}, body=marker)
        rejected_without_mutation("--request-id", "req-one", "--task-id", "t_fixture", "--board", "synthetic")
        self.fake_hermes(tasks=[], body=marker)
        rejected_without_mutation("--request-id", "req-one", "--task-id", "t_fixture", "--board", "synthetic")
        rejected_without_mutation("--request-id", "missing", "--task-id", "t_fixture", "--board", "synthetic")
        self.assertEqual(self.state_snapshot(), persisted_before_expiry)

    def test_packet_crash_after_json_publication_recovers_without_duplicate_rows(self):
        """A killed request worker leaves SQLite truth and repairs its projection."""
        self.assertEqual(self.subprocess_call("scan", "--now", NOW).returncode, 0)
        with contextlib.closing(Store(self.state)) as store:
            oid = store.latest_observation("demo")["id"]
            old_req = json.loads(self.request_file(oid, request_id="req-old").read_text())
            old_req["expires_at"] = "2026-09-27T01:00:00Z"
            old_packet = make_packet(old_req, json.loads(self.registry.read_text())["projects"][0], oid, NOW)
            store.save_request(old_packet)
            store.save_receipt("req-old", "t-old", "synthetic", {"request_id": "req-old", "historical": True})
        write_packet(self.state, old_packet)
        before = self.state_snapshot()
        request = self.request_file(oid, request_id="req-crash")
        self.fake_hermes(tasks=[])
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        env["PATH"] = str(self.root) + os.pathsep + env.get("PATH", "")
        crash_script = """
import os
from geoffrey_mission_control import cli, packets
original = packets._atomic
calls = 0
def crash_after_json(path, data, root):
    global calls
    original(path, data, root)
    calls += 1
    if calls == 1:
        os._exit(23)
packets._atomic = crash_after_json
raise SystemExit(cli.main())
"""
        command = [sys.executable, "-c", crash_script, "--state-dir", str(self.state),
                   "request", "--file", str(request), "--now", NOW]
        crashed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(crashed.returncode, 23, crashed.stderr + crashed.stdout)
        after_crash = self.state_snapshot()
        self.assertEqual(after_crash["requests"][1], before["requests"][0])
        self.assertEqual(len(after_crash["requests"]), 2)
        self.assertEqual(after_crash["receipts"], before["receipts"])
        self.assertIn("req-crash.json", after_crash["packets"])
        self.assertNotIn("req-crash.md", after_crash["packets"])
        recovered = self.subprocess_call("request", "--file", str(request), "--now", NOW)
        self.assertEqual(recovered.returncode, 0, recovered.stderr + recovered.stdout)
        final = self.state_snapshot()
        self.assertEqual(final["requests"][1], before["requests"][0])
        self.assertEqual(final["receipts"], before["receipts"])
        self.assertEqual(len(final["requests"]), 2)
        self.assertEqual(len(final["receipts"]), 1)
        self.assertIn("req-crash.json", final["packets"])
        self.assertIn("req-crash.md", final["packets"])
        self.assertEqual(final["packets"]["req-old.json"], before["packets"]["req-old.json"])
        self.assertEqual(final["packets"]["req-old.md"], before["packets"]["req-old.md"])
        for name in ("req-old.json", "req-old.md", "req-crash.json", "req-crash.md"):
            self.assertEqual((self.state / "packets" / name).stat().st_mode & 0o777, 0o600)

    def state_snapshot(self):
        with contextlib.closing(Store(self.state)) as store:
            rows = {}
            counts = {}
            for table in ("requests", "receipts"):
                rows[table] = [dict(row) for row in store.conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
                counts[table] = len(rows[table])
        packets = {}
        packet_dir = self.state / "packets"
        if packet_dir.exists():
            packets = {p.name: p.read_bytes() for p in sorted(packet_dir.iterdir())}
        return {**rows, "counts": counts, "packets": packets}


if __name__ == "__main__":
    unittest.main()
