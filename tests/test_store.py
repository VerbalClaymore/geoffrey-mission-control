import json, os, sqlite3, subprocess, sys, tempfile, textwrap, unittest
from pathlib import Path
from geoffrey_mission_control.store import Store, StoreError, ensure_state
from geoffrey_mission_control.packets import make_packet, write_packet

class StoreTests(unittest.TestCase):
    def setUp(self): self.d=tempfile.TemporaryDirectory(); self.s=Store(self.d.name)
    def tearDown(self): self.s.conn.close(); self.d.cleanup()
    def test_registry_idempotent_and_conflict(self):
        r={"schema_version":1,"projects":[]}; self.s.save_registry(r); self.s.save_registry(r)
        with self.assertRaises(StoreError): self.s.save_registry({"schema_version":1,"projects":[{"id":"x","name":"x","goal":"x","milestone":"x","repo_path":None,"owner":{"surface":"unknown","profile":None,"session_ref":None},"canonical_spec":None,"permission":"observe","paused":False,"stale_after_hours":48,"kanban_refs":[]}]})
    def test_checkpoint_conflicting_replay_rejected(self):
        cp={"schema_version":1,"id":"cp1","project_id":"p","observed_at":"2026-01-01T00:00:00Z","state":"ready","summary":"x","next_action":None,"decision":None,"evidence":[{"kind":"other","ref":"r","at":"2026-01-01T00:00:00Z"}],"provenance":{"surface":"unknown","author":"a"}}
        self.s.save_checkpoint(cp,"2026-01-01T00:00:00Z"); self.s.save_checkpoint(cp,"2026-01-01T00:00:00Z"); cp['summary']='changed'
        with self.assertRaises(StoreError): self.s.save_checkpoint(cp,"2026-01-01T00:00:00Z")

    def _request(self, request_id="req1"):
        return {"schema_version":1,"id":request_id,"project_id":"p",
                "created_at":"2026-01-01T00:00:00Z","expires_at":"2026-01-01T01:00:00Z",
                "expected_observation_id":"obs1","assignee":"worker","board":"board",
                "title":"title","scope":["scope"],"acceptance":["accept"],
                "forbidden":["forbidden"],"approval_ref":"approval"}

    def test_save_request_rejects_unknown_fields_without_mutation(self):
        request = make_packet(self._request(),
                              {"id":"p", "canonical_spec":"docs/spec.md", "permission":"coordinate",
                               "paused":False, "owner":{"surface":"hermes", "profile":"worker"}},
                              "obs1", "2026-01-01T00:30:00Z")
        self.s.save_request(request)
        write_packet(self.s.root, request)
        request["unexpected"] = "must not be silently discarded"
        before_rows = [dict(row) for row in self.s.conn.execute("SELECT * FROM requests")]
        before_packets = {path.name: path.read_bytes() for path in (self.s.root / "packets").iterdir()}
        with self.assertRaises(ValueError):
            self.s.save_request(request)
        after_rows = [dict(row) for row in self.s.conn.execute("SELECT * FROM requests")]
        self.assertEqual(after_rows, before_rows)
        after_packets = {path.name: path.read_bytes() for path in (self.s.root / "packets").iterdir()}
        self.assertEqual(after_packets, before_packets)

    def test_save_request_accepts_canonical_packet_metadata(self):
        request = self._request()
        project = {"id":"p", "canonical_spec":"docs/spec.md", "permission":"coordinate",
                   "paused":False, "owner":{"surface":"hermes", "profile":"worker"}}
        packet = make_packet(request, project, "obs1", "2026-01-01T00:30:00Z")
        saved = self.s.save_request(packet)
        self.assertEqual(saved, packet)
        write_packet(self.s.root, packet)
        self.assertEqual(json.loads(self.s.request("req1")["payload"]), packet)
        self.assertEqual((self.s.root / "packets" / "req1.json").read_bytes(),
                         (json.dumps(packet, sort_keys=True, indent=2) + "\n").encode())

    def test_same_time_checkpoint_conflict_and_out_of_order_history(self):
        def cp(cid, at, summary):
            return {"schema_version":1,"id":cid,"project_id":"p","observed_at":at,"state":"ready","summary":summary,"next_action":None,"decision":None,"evidence":[{"kind":"other","ref":"r","at":at}],"provenance":{"surface":"unknown","author":"a"}}
        self.s.save_checkpoint(cp("old", "2026-01-01T00:00:00Z", "old"), "2026-01-01T01:00:00Z")
        self.s.save_checkpoint(cp("new", "2026-01-02T00:00:00Z", "new"), "2026-01-02T01:00:00Z")
        with self.assertRaises(StoreError):
            self.s.save_checkpoint(cp("other", "2026-01-02T00:00:00Z", "different"), "2026-01-02T01:00:00Z")
        self.assertEqual(self.s.checkpoints("p")[-1]["id"], "new")

    def test_state_symlink_is_rejected(self):
        outside = tempfile.TemporaryDirectory()
        link_parent = tempfile.TemporaryDirectory()
        link = Path(link_parent.name) / "state"
        link.symlink_to(outside.name, target_is_directory=True)
        with self.assertRaises(StoreError):
            ensure_state(link)
        outside.cleanup(); link_parent.cleanup()

    def test_dangling_state_symlink_is_rejected(self):
        parent = tempfile.TemporaryDirectory()
        link = Path(parent.name) / "state"
        link.symlink_to(Path(parent.name) / "missing", target_is_directory=True)
        with self.assertRaises(StoreError):
            ensure_state(link)
        parent.cleanup()

    def test_unsupported_schema_is_rejected_after_restart(self):
        self.s.close()
        connection = sqlite3.connect(Path(self.d.name) / "mission-control.sqlite3")
        connection.execute("PRAGMA user_version=99")
        connection.commit(); connection.close()
        with self.assertRaises(StoreError):
            Store(self.d.name)

    def test_scan_transaction_rolls_back(self):
        self.s.begin_scan()
        self.s.add_observation("one", "p", "2026-01-01T00:00:00Z", {"errors": []}, commit=False)
        self.s.rollback_scan()
        self.assertIsNone(self.s.latest_observation("p"))

    def test_scan_lock_excludes_process_and_releases_after_kill_without_partial_rows(self):
        """A killed scanner cannot publish its partial transaction or strand the lock."""
        self.s.add_observation("prior-a", "a", "2025-12-01T00:00:00Z", {"errors": [], "marker": "prior-a"})
        self.s.add_observation("prior-b", "b", "2025-12-02T00:00:00Z", {"errors": [], "marker": "prior-b"})
        self.s.close()
        before_crash = Store(self.d.name)
        try:
            committed_before = {
                project_id: before_crash.observations(project_id)
                for project_id in ("a", "b")
            }
            committed_count = sum(len(rows) for rows in committed_before.values())
        finally:
            before_crash.close()
        worker = textwrap.dedent("""
            import sys
            from geoffrey_mission_control.store import Store
            store = Store(sys.argv[1])
            store.begin_scan()
            store.add_observation("child-partial-a", "a", "2026-01-01T00:00:00Z", {"errors": []}, commit=False)
            store.add_observation("child-partial-b", "b", "2026-01-01T00:00:00Z", {"errors": []}, commit=False)
            print("READY", flush=True)
            sys.stdin.readline()
        """)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        child = subprocess.Popen(
            [sys.executable, "-c", worker, self.d.name],
            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        try:
            self.assertEqual(child.stdout.readline().strip(), "READY")
            contender = subprocess.run(
                [sys.executable, "-c", textwrap.dedent("""
                    import sys
                    from geoffrey_mission_control.store import Store, StoreError
                    store = Store(sys.argv[1])
                    try:
                        store.begin_scan()
                    except StoreError as exc:
                        print(str(exc))
                        raise SystemExit(0)
                    raise SystemExit("second scanner acquired lock")
                """), self.d.name], env=env, capture_output=True, text=True, timeout=5,
            )
            self.assertEqual(contender.returncode, 0, contender.stderr)
            self.assertEqual(contender.stdout.strip(), "scan: busy")
            child.kill()  # only the test-owned child is terminated
            child.wait(timeout=5)
            self.assertIsNotNone(child.returncode)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            child.stdin.close()
            child.stdout.close()
            child.stderr.close()

        reopened = Store(self.d.name)
        try:
            after_crash = {
                project_id: reopened.observations(project_id)
                for project_id in ("a", "b")
            }
            self.assertEqual(sum(len(rows) for rows in after_crash.values()), committed_count)
            self.assertEqual(after_crash, committed_before)
            self.assertNotIn("child-partial-a", {row["id"] for row in after_crash["a"]})
            self.assertNotIn("child-partial-b", {row["id"] for row in after_crash["b"]})
        finally:
            reopened.close()

        recovery = subprocess.run(
            [sys.executable, "-c", textwrap.dedent("""
                import sys
                from geoffrey_mission_control.store import Store
                store = Store(sys.argv[1])
                store.begin_scan()
                store.add_observation("recovered-a", "a", "2026-01-03T00:00:00Z", {"errors": [], "marker": "recovered-a"}, commit=False)
                store.add_observation("recovered-b", "b", "2026-01-04T00:00:00Z", {"errors": [], "marker": "recovered-b"}, commit=False)
                store.commit_scan()
                print("COMMITTED")
            """), self.d.name], env=env, capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(recovery.returncode, 0, recovery.stderr)
        self.assertEqual(recovery.stdout.strip(), "COMMITTED")
        fresh = Store(self.d.name)
        try:
            after_recovery = {
                project_id: fresh.observations(project_id)
                for project_id in ("a", "b")
            }
            self.assertEqual(sum(len(rows) for rows in after_recovery.values()), committed_count + 2)
            for project_id in ("a", "b"):
                self.assertEqual(after_recovery[project_id][:-1], committed_before[project_id])
            self.assertEqual(after_recovery["a"][-1]["id"], "recovered-a")
            self.assertEqual(after_recovery["b"][-1]["id"], "recovered-b")
        finally:
            fresh.close()

    def test_scan_exception_rolls_back_and_releases_for_fresh_process(self):
        self.s.begin_scan()
        self.s.add_observation("failed", "p", "2026-01-01T00:00:00Z", {"errors": []}, commit=False)
        try:
            raise RuntimeError("synthetic collector failure")
        except RuntimeError:
            self.s.rollback_scan()
        self.s.close()
        fresh = Store(self.d.name)
        try:
            self.assertIsNone(fresh.latest_observation("p"))
            fresh.begin_scan()
            fresh.add_observation("after", "p", "2026-01-01T00:00:01Z", {"errors": []}, commit=False)
            fresh.commit_scan()
        finally:
            fresh.close()
        restarted = Store(self.d.name)
        try:
            latest = restarted.latest_observation("p")
            self.assertIsNotNone(latest)
            self.assertEqual(latest["id"], "after")
        finally:
            restarted.close()

if __name__=='__main__': unittest.main()
