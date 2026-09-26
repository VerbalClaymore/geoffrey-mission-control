import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from geoffrey_mission_control.collectors import (
    git_collect, hermes_collect, hermes_board_collect, collect_sources, semantic_digest,
)


class FakeRunner:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        key = tuple(argv)
        response = self.responses[key]
        if isinstance(response, BaseException):
            raise response
        return SimpleNamespace(returncode=0, stdout=response, stderr="")


class CollectorTests(unittest.TestCase):
    def test_git_collect_rejects_status_failure_instead_of_calling_clean(self):
        runner = FakeRunner({
            ("git", "rev-parse", "HEAD"): "a" * 40,
            ("git", "branch", "--show-current"): "main",
            ("git", "log", "-1", "--format=%cI"): "2026-01-01T00:00:00Z",
            ("git", "status", "--porcelain"): SimpleNamespace(returncode=1),
        })
        # The fake intentionally returns the failed result, not stdout.
        runner.responses[("git", "status", "--porcelain")] = RuntimeError("status failed")
        result = git_collect("/tmp/repo", runner, now="2026-01-02T00:00:00Z")
        self.assertFalse(result["ok"])
        self.assertIn("status", result["error"])

    def test_git_collect_rejects_real_nonzero_completed_process(self):
        def runner(argv, **kwargs):
            if argv == ["git", "rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(argv, 23, "", "fatal: synthetic failure")
            raise AssertionError("collector continued after failed git command")

        result = git_collect("/tmp/repo", runner)
        self.assertEqual(result, {"ok": False, "error": "git: unavailable evidence"})

    def test_git_collect_metadata_and_safety_contract(self):
        runner = FakeRunner({
            ("git", "rev-parse", "HEAD"): "a" * 40,
            ("git", "branch", "--show-current"): "main",
            ("git", "log", "-1", "--format=%cI"): "2026-01-01T00:00:00Z",
            ("git", "status", "--porcelain"): " M file",
        })
        result = git_collect("/tmp/repo", runner, now="2026-01-02T00:00:00Z")
        self.assertTrue(result["ok"])
        self.assertTrue(result["dirty"])
        self.assertEqual(result["head"], "a" * 40)
        self.assertTrue(all(call[1]["shell"] is False for call in runner.calls))
        self.assertTrue(all(call[1]["timeout"] == 15 for call in runner.calls))

    def test_git_timeout_nonzero_and_read_only_invariants(self):
        with tempfile.TemporaryDirectory() as d:
            sentinel = Path(d) / "sentinel"
            sentinel.write_text("unchanged")
            runner = FakeRunner({
                ("git", "rev-parse", "HEAD"): TimeoutError("timed out"),
            })
            result = git_collect(d, runner, now="2026-01-02T00:00:00Z")
            self.assertFalse(result["ok"])
            self.assertIn("unavailable", result["error"])
            self.assertEqual(sentinel.read_text(), "unchanged")
            self.assertEqual([argv for argv, _ in runner.calls], [["git", "rev-parse", "HEAD"]])

        runner = FakeRunner({("git", "rev-parse", "HEAD"): "a" * 40,
                             ("git", "branch", "--show-current"): "main",
                             ("git", "log", "-1", "--format=%cI"): "2026-01-01T00:00:00Z",
                             ("git", "status", "--porcelain"): ""})
        result = git_collect("/tmp/repo;touch hostile", runner)
        self.assertTrue(result["ok"])
        self.assertEqual(runner.calls[0][0], ["git", "rev-parse", "HEAD"])

    def test_git_collect_rejects_malformed_and_future_timestamp(self):
        base = {
            ("git", "rev-parse", "HEAD"): "a" * 40,
            ("git", "branch", "--show-current"): "main",
            ("git", "status", "--porcelain"): "",
        }
        for stamp in ("not-a-time", "2026-01-03T00:00:00Z"):
            runner = FakeRunner({**base, ("git", "log", "-1", "--format=%cI"): stamp})
            result = git_collect("/tmp/repo", runner, now="2026-01-02T00:00:00Z")
            self.assertFalse(result["ok"])

    def test_hermes_requires_version_list_show_and_normalizes_epochs(self):
        version = ("hermes 0.21.5+2453.gd0288be\n",)
        task = {"id": "opaque task/1", "assignee": "geoffrey", "status": "blocked",
                "body": "x", "created_at": 1790447728, "started_at": None,
                "completed_at": None}
        runner = FakeRunner({
            ("hermes", "--version"): version[0],
            ("hermes", "kanban", "--board", "board", "list", "--json"): json.dumps([task]),
            ("hermes", "kanban", "--board", "board", "show", "opaque task/1", "--json"): json.dumps({"task": task}),
        })
        result = hermes_collect("board", "opaque task/1", runner)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["task"]["created_at"], "2026-09-26T18:35:28Z")
        self.assertEqual(result["board"], "board")

    def test_hermes_public_boundary_rejects_invalid_board_and_task_without_calls(self):
        task = {"id": "opaque task/1", "assignee": "geoffrey", "status": "blocked",
                "body": "x", "created_at": 1, "started_at": None, "completed_at": None}
        for board, task_id in (("bad board", "opaque task/1"), ("board", ""),
                               ("board", "x" * 129)):
            runner = FakeRunner({})
            result = hermes_collect(board, task_id, runner)
            self.assertFalse(result["ok"])
            self.assertEqual(runner.calls, [])
        board, task_id = "b" * 64, "x" * 128
        task = {"id": task_id, "assignee": "geoffrey", "status": "blocked",
                "body": "x", "created_at": 1, "started_at": None, "completed_at": None}
        runner = FakeRunner({
            ("hermes", "--version"): "hermes 0.21.5+2453.gd0288be\n",
            ("hermes", "kanban", "--board", board, "list", "--json"): json.dumps([task]),
            ("hermes", "kanban", "--board", board, "show", task_id, "--json"): json.dumps({"task": task}),
        })
        self.assertTrue(hermes_collect(board, task_id, runner)["ok"])

    def test_hermes_rejects_duplicate_ids_wrong_types_and_oversize(self):
        task = {"id": "t", "assignee": "g", "status": "ready", "body": None,
                "created_at": 1, "started_at": None, "completed_at": None}
        for listing in (json.dumps([task, task]), json.dumps([{**task, "created_at": True}]), "{" + "x" * (1024 * 1024) + "}"):
            runner = FakeRunner({
                ("hermes", "--version"): "hermes 0.21.5+2453.gd0288be\n",
                ("hermes", "kanban", "--board", "b", "list", "--json"): listing,
                ("hermes", "kanban", "--board", "b", "show", "t", "--json"): json.dumps({"task": task}),
            })
            self.assertFalse(hermes_collect("b", "t", runner)["ok"])

    def test_hermes_unknown_status_absent_task_and_malformed_envelopes(self):
        task = {"id": "t", "assignee": "g", "status": "ready", "body": None,
                "created_at": 1, "started_at": None, "completed_at": None}
        cases = [
            ([{**task, "status": "mystery"}], {"task": task}),
            ([task], {"task": {**task, "id": "other"}}),
            ([task], []),
            ({"not": "a list"}, {"task": task}),
        ]
        for listing, shown in cases:
            with self.subTest(listing=listing):
                runner = FakeRunner({
                    ("hermes", "--version"): "hermes 0.21.5+2453.gd0288be\n",
                    ("hermes", "kanban", "--board", "b", "list", "--json"): json.dumps(listing),
                    ("hermes", "kanban", "--board", "b", "show", "t", "--json"): json.dumps(shown),
                })
                self.assertFalse(hermes_collect("b", "t", runner)["ok"])

        runner = FakeRunner({
            ("hermes", "--version"): "hermes 0.21.5+2453.gd0288be\n",
            ("hermes", "kanban", "--board", "b", "list", "--json"): json.dumps([task]),
        })
        result = hermes_collect("b", "missing", runner)
        self.assertEqual(result["error"], "hermes: missing task")

    def test_hermes_output_and_future_epoch_limits(self):
        task = {"id": "t", "assignee": "g", "status": "ready", "body": None,
                "created_at": 1, "started_at": None, "completed_at": None}
        runner = FakeRunner({
            ("hermes", "--version"): "hermes 0.21.5+2453.gd0288be\n",
            ("hermes", "kanban", "--board", "b", "list", "--json"): json.dumps([{**task, "created_at": 4102444800}]),
            ("hermes", "kanban", "--board", "b", "show", "t", "--json"): json.dumps({"task": {**task, "created_at": 4102444800}}),
        })
        self.assertEqual(hermes_collect("b", "t", runner, now="2026-01-01T00:00:00Z")["error"], "hermes: future timestamp")

    def test_hermes_collect_accepts_exactly_100_and_rejects_101_before_show(self):
        def task(index):
            return {"id": f"external-{index:03d}", "assignee": "g", "status": "ready",
                    "body": None, "created_at": index + 1, "started_at": None,
                    "completed_at": None, "unknown": {"nested": [index]}}

        version = "hermes 0.21.5+2453.gd0288be\n"
        for count in (100, 101):
            with self.subTest(count=count):
                tasks = [task(i) for i in range(count)]
                runner = FakeRunner({
                    ("hermes", "--version"): version,
                    ("hermes", "kanban", "--board", "b", "list", "--json"): json.dumps(tasks),
                    ("hermes", "kanban", "--board", "b", "show", "external-050", "--json"): json.dumps({"task": tasks[50]}),
                })
                result = hermes_collect("b", "external-050", runner)
                self.assertEqual(result["ok"], count == 100, result)
                if count == 100:
                    self.assertEqual(result["task"]["unknown"], {"nested": [50]})
                    self.assertTrue(any("show" in call[0] for call in runner.calls))
                else:
                    self.assertEqual(result["error"], "hermes: census exceeds 100 entries")
                    self.assertFalse(any("show" in call[0] for call in runner.calls))

    def test_hermes_board_collect_accepts_exactly_100_and_rejects_101_before_show(self):
        def task(index):
            return {"id": f"board-{index:03d}", "assignee": "g", "status": "ready",
                    "body": None, "created_at": index + 1, "started_at": None,
                    "completed_at": None, "extra": {"nested": {"index": index}}}

        version = "hermes 0.21.5+2453.gd0288be\n"
        for count in (100, 101):
            with self.subTest(count=count):
                tasks = [task(i) for i in range(count)]
                responses = {
                    ("hermes", "--version"): version,
                    ("hermes", "kanban", "--board", "b", "list", "--json"): json.dumps(tasks),
                }
                for item in tasks:
                    responses[("hermes", "kanban", "--board", "b", "show", item["id"], "--json")] = json.dumps({"task": item})
                runner = FakeRunner(responses)
                result = hermes_board_collect("b", runner)
                self.assertEqual(result["ok"], count == 100, result)
                if count == 100:
                    self.assertEqual(len(result["tasks"]), 100)
                    self.assertEqual(result["tasks"][50]["extra"], {"nested": {"index": 50}})
                else:
                    self.assertEqual(result["error"], "hermes: census exceeds 100 entries")
                    self.assertFalse(any("show" in call[0] for call in runner.calls))

    def test_sources_require_git_and_refs_and_expose_failure_without_erasing_old(self):
        result = collect_sources({"repo_path": "/tmp/repo", "kanban_refs": [{"board": "b", "task_id": "t"}]},
                                 git_runner=lambda *a, **k: {"ok": True, "head": "h"},
                                 hermes_runner=lambda *a, **k: {"ok": False, "error": "hermes: source error"})
        self.assertFalse(result["ok"])
        self.assertEqual({x["source"] for x in result["errors"]}, {"hermes"})
        self.assertEqual(result["sources"]["git"]["head"], "h")

    def test_digest_ignores_check_time_and_heartbeat_but_tracks_source_changes(self):
        a = {"observed_at": "one", "last_verified": "old", "checked_at": "one", "heartbeat": "two", "git": {"head": "a"}, "errors": []}
        b = {"observed_at": "three", "last_verified": "new", "checked_at": "three", "heartbeat": "four", "git": {"head": "a"}, "errors": []}
        self.assertEqual(semantic_digest(a), semantic_digest(b))
        self.assertNotEqual(semantic_digest(a), semantic_digest({**b, "git": {"head": "b"}}))

    def test_digest_retains_meaningful_activity_and_status_fields(self):
        base = {"activity_at": "2026-09-27T00:00:00Z", "checkpoint_snapshot": [], "sources": {"git": {"head": "a"}}, "errors": []}
        self.assertNotEqual(semantic_digest(base), semantic_digest({**base, "activity_at": "2026-09-27T01:00:00Z"}))
        self.assertNotEqual(semantic_digest(base), semantic_digest({**base, "checkpoint_snapshot": [["cp", "digest", "ingested"]]}))


if __name__ == "__main__":
    unittest.main()
