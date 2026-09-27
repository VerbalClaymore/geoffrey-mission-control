import json
import unittest
from datetime import datetime, timezone

from geoffrey_mission_control.contracts import ContractError, load_json, validate_registry, validate_checkpoint, validate_request


class ContractsTests(unittest.TestCase):
    def test_load_json_accepts_string_and_bytes_and_enforces_limit(self):
        self.assertEqual(load_json('{"x": 1}')["x"], 1)
        self.assertEqual(load_json(b'{"x": 1}')["x"], 1)
        with self.assertRaises(ContractError):
            load_json("{}", max_bytes=1)
        with self.assertRaises(ContractError):
            load_json(b"{}", max_bytes=1)
    def test_registry_accepts_minimal_valid_project(self):
        registry = {
            "schema_version": 1,
            "projects": [{
                "id": "demo", "name": "Demo", "goal": "Ship", "milestone": "v1",
                "repo_path": None,
                "owner": {"surface": "unknown", "profile": None, "session_ref": None},
                "canonical_spec": None, "permission": "observe", "paused": False,
                "stale_after_hours": 48, "kanban_refs": [],
            }],
        }
        self.assertEqual(validate_registry(registry)["projects"][0]["id"], "demo")

    def test_registry_boundary_limits_and_nullable_profile(self):
        project = {
            "id": "demo", "name": "Demo", "goal": "Ship", "milestone": "v1",
            "repo_path": "/" + "a" * 4095,
            "owner": {"surface": "hermes", "profile": "worker_1", "session_ref": None},
            "canonical_spec": None, "permission": "observe", "paused": False,
            "stale_after_hours": 48, "kanban_refs": [],
        }
        registry = {"schema_version": 1, "projects": [project]}
        self.assertEqual(validate_registry(registry)["projects"][0]["repo_path"], project["repo_path"])
        project["repo_path"] += "a"
        with self.assertRaises(ContractError): validate_registry(registry)
        project["repo_path"] = None
        project["owner"]["profile"] = "not a profile!"
        with self.assertRaises(ContractError): validate_registry(registry)
        project["owner"]["profile"] = None
        self.assertIsNone(validate_registry(registry)["projects"][0]["owner"]["profile"])

    def test_unknown_fields_duplicate_keys_and_bool_int_are_rejected(self):
        with self.assertRaises(ContractError):
            validate_registry({"schema_version": 1, "projects": [], "extra": 1})
        with self.assertRaises(ContractError):
            load_json('{"schema_version":1,"schema_version":1}')
        with self.assertRaises(ContractError):
            validate_registry({"schema_version": True, "projects": []})

    def test_checkpoint_requires_evidence_and_normalizes_timestamp(self):
        checkpoint = {
            "schema_version": 1, "id": "cp1", "project_id": "demo",
            "observed_at": "2026-01-01T12:00:00-05:00", "state": "ready",
            "summary": "ready", "next_action": None, "decision": None,
            "evidence": [{"kind": "other", "ref": "note", "at": "2026-01-01T17:00:00Z"}],
            "provenance": {"surface": "unknown", "author": "owner"},
        }
        self.assertEqual(validate_checkpoint(checkpoint)["observed_at"], "2026-01-01T17:00:00Z")
        checkpoint["evidence"] = []
        with self.assertRaises(ContractError):
            validate_checkpoint(checkpoint)

    def test_checkpoint_rejects_future_and_missing_author(self):
        checkpoint = {
            "schema_version": 1, "id": "cp1", "project_id": "demo",
            "observed_at": "2026-01-01T12:10:00Z", "state": "ready",
            "summary": "ready", "next_action": None, "decision": None,
            "evidence": [{"kind": "other", "ref": "note", "at": "2026-01-01T12:10:00Z"}],
            "provenance": {"surface": "unknown"},
        }
        with self.assertRaises(ContractError):
            validate_checkpoint(checkpoint, ingested_at="2026-01-01T12:00:00Z")

    def test_request_contract_enforces_times_and_types(self):
        req = {
            "schema_version": 1, "id": "req1", "project_id": "demo",
            "created_at": "2026-01-01T11:00:00Z", "expires_at": "2026-01-01T12:00:00Z",
            "expected_observation_id": "abc", "assignee": "owner", "board": "main",
            "title": "Do it", "scope": ["one"], "acceptance": ["done"],
            "forbidden": ["deploy"], "approval_ref": "approval",
        }
        self.assertEqual(validate_request(req, now="2026-01-01T11:30:00Z")["id"], "req1")
        req["scope"] = "one"
        with self.assertRaises(ContractError):
            validate_request(req, now="2026-01-01T11:30:00Z")

    def test_nested_unknown_fields_and_wrong_types_are_rejected_for_each_document(self):
        registry = {"schema_version": 1, "projects": []}
        with self.assertRaises(ContractError):
            validate_registry({**registry, "projects": [{"id": "p", "unexpected": 1}]})
        checkpoint = {
            "schema_version": 1, "id": "cp1", "project_id": "p",
            "observed_at": "2026-01-01T00:00:00Z", "state": "ready", "summary": "s",
            "next_action": None, "decision": None,
            "evidence": [{"kind": "other", "ref": "r", "at": "2026-01-01T00:00:00Z"}],
            "provenance": {"surface": "unknown", "author": "a"},
        }
        with self.assertRaises(ContractError):
            validate_checkpoint({**checkpoint, "unexpected": 1})
        with self.assertRaises(ContractError):
            validate_checkpoint({**checkpoint, "evidence": [{**checkpoint["evidence"][0], "unexpected": 1}]})
        request = {
            "schema_version": 1, "id": "r1", "project_id": "p",
            "created_at": "2026-01-01T00:00:00Z", "expires_at": "2026-01-01T01:00:00Z",
            "expected_observation_id": "o", "assignee": "a", "board": "b", "title": "t",
            "scope": ["s"], "acceptance": ["a"], "forbidden": ["f"], "approval_ref": "x",
        }
        with self.assertRaises(ContractError):
            validate_request({**request, "unexpected": 1})
        with self.assertRaises(ContractError):
            validate_request({**request, "scope": [1]})

    def test_missing_null_timezone_and_time_boundaries_are_explicit(self):
        base = {
            "schema_version": 1, "id": "r1", "project_id": "p",
            "created_at": "2026-01-01T00:00:00Z", "expires_at": "2026-01-01T01:00:00Z",
            "expected_observation_id": "o", "assignee": "a", "board": "b", "title": "t",
            "scope": ["s"], "acceptance": ["a"], "forbidden": ["f"], "approval_ref": "x",
        }
        for missing in ("created_at", "expires_at", "scope"):
            value = dict(base); value.pop(missing)
            with self.subTest(missing=missing), self.assertRaises(ContractError): validate_request(value)
        for value in ({**base, "created_at": None}, {**base, "expires_at": None},
                      {**base, "created_at": "2026-01-01T00:00:00"},
                      {**base, "created_at": "2026-01-01T00:00:00+25:00"}):
            with self.assertRaises(ContractError): validate_request(value)
        self.assertEqual(validate_request(base, now="2026-01-01T00:00:00Z")["id"], "r1")
        with self.assertRaises(ContractError):
            validate_request({**base, "expires_at": "2026-01-01T00:00:00Z"}, now="2026-01-01T00:00:00Z")
        with self.assertRaises(ContractError):
            validate_request({**base, "created_at": "2026-01-01T00:06:00Z"}, now="2026-01-01T00:00:00Z")

    def test_nested_arrays_and_text_limits_are_enforced(self):
        base = {
            "schema_version": 1, "id": "r1", "project_id": "p",
            "created_at": "2026-01-01T00:00:00Z", "expires_at": "2026-01-01T01:00:00Z",
            "expected_observation_id": "o", "assignee": "a", "board": "b", "title": "t",
            "scope": ["s"], "acceptance": ["a"], "forbidden": ["f"], "approval_ref": "x",
        }
        for field in ("scope", "acceptance", "forbidden"):
            with self.subTest(field=field), self.assertRaises(ContractError):
                validate_request({**base, field: ["x"] * 101})

        with self.assertRaises(ContractError): validate_request({**base, "title": "x" * 4097})


if __name__ == "__main__":
    unittest.main()
