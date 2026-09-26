"""Deterministic, evidence-honest portfolio brief rendering."""
import json
from datetime import datetime, timezone


def _instant(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _esc(value):
    text = "unknown" if value is None else str(value)
    text = "".join(" " if ord(ch) < 32 or ord(ch) == 127 else ch for ch in text)
    return (text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")
                .replace("`", "\\`").replace("[", "\\[").replace("]", "\\]")
                .replace("(", "\\(").replace(")", "\\)").replace("<", "\\<").replace(">", "\\>"))


def _task_data(observation):
    statuses, refs = [], []
    for source in (observation.get("sources") or {}).get("hermes", []):
        result = source.get("result", source)
        if result.get("ok") and result.get("task"):
            statuses.append(result["task"]["status"])
            refs.append({"board": source.get("board"), "task_id": source.get("task_id")})
    return statuses, refs


def _mapped(statuses):
    if "blocked" in statuses:
        return "blocked"
    if "review" in statuses:
        return "awaiting_user"
    if "running" in statuses:
        return "running"
    if any(s in {"triage", "todo", "ready", "scheduled"} for s in statuses):
        return "ready"
    if statuses and all(s == "archived" for s in statuses):
        return "archived"
    if statuses and all(s == "done" for s in statuses):
        return "reported_done"
    if statuses and all(s in {"done", "archived"} for s in statuses):
        return "reported_done"
    return "unknown"


def _contradictory(checkpoint_state, task_state):
    """Return only conflicts supported by the normalized v1 state contract.

    Unknown is absence of evidence, not a contradiction.  Archived is kept
    distinct from reported_done because it is not proof of completion.
    Active/terminal mismatches and a blocked report versus reported completion
    are the explicit contradictory cases; other differing live states may be
    ordinary observation lag.
    """
    if checkpoint_state == "unknown" or task_state == "unknown":
        return False
    pairs = {
        ("running", "reported_done"), ("running", "archived"),
        ("done", "running"), ("done", "ready"), ("done", "blocked"),
        ("done", "awaiting_user"), ("done", "archived"),
        ("blocked", "reported_done"), ("blocked", "archived"),
    }
    return (checkpoint_state, task_state) in pairs


def build_report(registry, observations=None, checkpoints=None, now=None):
    observations, checkpoints = observations or {}, checkpoints or {}
    current = _instant(now) if now else datetime.now(timezone.utc)
    rows = []
    for project in registry["projects"]:
        pid = project["id"]
        observation = observations.get(pid) or {}
        if isinstance(observation.get("payload"), str):
            try:
                observation = json.loads(observation["payload"])
            except (TypeError, ValueError):
                observation = {}
        history = checkpoints.get(pid) or []
        checkpoint = history[-1] if history else None
        errors = list(observation.get("errors") or [])
        task_statuses, source_refs = _task_data(observation)
        git_source = (observation.get("sources") or {}).get("git") or {}
        if git_source.get("ok"):
            source_refs.append({"source": "git", "head": git_source.get("head"), "branch": git_source.get("branch")})
        task_state = _mapped(task_statuses)
        reported_state = checkpoint.get("state") if checkpoint else "unknown"
        conflict = bool(checkpoint and task_statuses and _contradictory(reported_state, task_state))
        if project["paused"]:
            status, status_origin = "paused", "paused"
        elif errors:
            status, status_origin = "unavailable", "collection_error"
        elif conflict:
            status, status_origin = "conflict", "conflict"
        elif (checkpoint or {}).get("decision"):
            status, status_origin = "awaiting_user", "checkpoint_decision"
        elif task_state != "unknown":
            status, status_origin = task_state, "task"
        elif checkpoint:
            status, status_origin = reported_state, "checkpoint"
        else:
            status, status_origin = "unknown", "unknown"
        activity = observation.get("activity_at") or git_source.get("latest_commit_at") or (checkpoint or {}).get("observed_at")
        stale = True if not activity else (current - _instant(activity)).total_seconds() > project["stale_after_hours"] * 3600
        rows.append({
            "project_id": pid, "name": project["name"], "goal": project["goal"], "milestone": project["milestone"],
            "status": status, "status_origin": status_origin,
            "raw_task_statuses": task_statuses, "task_state": task_state,
            "task_provenance": "hermes_kanban" if task_statuses else "unknown",
            "reported_state": reported_state, "owner_reported_state": reported_state,
            "owner_provenance": (checkpoint or {}).get("provenance") if checkpoint else None,
            "verified_state": task_state if not errors else "unknown", "conflict": conflict,
            "last_checked": observation.get("observed_at"), "last_verified": observation.get("last_verified"),
            "last_meaningful_activity": activity, "stale": stale, "source_refs": source_refs,
            "errors": errors, "capability": "native_kanban_via_geoffrey" if project["owner"]["surface"] == "hermes" and project["owner"].get("profile") else "checkpoint_only",
            "owner_next_action": (checkpoint or {}).get("next_action") or "unknown/request owner checkpoint",
            "next_action": (checkpoint or {}).get("next_action") or "unknown/request owner checkpoint",
            "owner_decision": (checkpoint or {}).get("decision"), "decision": (checkpoint or {}).get("decision"),
        })
    rank = {"paused": 0, "awaiting_user": 1, "blocked": 2, "conflict": 2, "unavailable": 2, "running": 3, "ready": 4, "reported_done": 5, "archived": 5, "unknown": 6}
    rows.sort(key=lambda row: (rank.get(row["status"], 6), row["project_id"]))
    return {"schema_version": 1, "projects": rows}


def render_markdown(report):
    lines = ["# Geoffrey Mission Control Brief", "", "| Project | Status | Origin | Owner state | Task state/raw | Conflict | Checked | Verified | Meaningful activity | Next action | Decision |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    summary_rows = []
    for project in report["projects"]:
        decision = project.get("decision")
        decision_text = decision.get("question") if isinstance(decision, dict) else decision
        values = [project.get("name"), project.get("status"), project.get("status_origin"),
                  project.get("owner_reported_state"),
                  (project.get("task_state"), project.get("raw_task_statuses")),
                  project.get("conflict"), project.get("last_checked"), project.get("last_verified"),
                  project.get("last_meaningful_activity"), project.get("next_action"), decision_text]
        summary_rows.append("| " + " | ".join(_esc(value) for value in values) + " |")
    lines.extend(summary_rows)
    for project in report["projects"]:
        decision = project.get("decision")
        lines.extend([
            "", "Evidence for " + _esc(project.get("project_id")) + ":",
            "- Stale: " + _esc(project.get("stale")),
            "- Source refs: " + _esc(project.get("source_refs")),
            "- Capability: " + _esc(project.get("capability")),
            "- Errors: " + _esc(project.get("errors")),
            "- Owner provenance: " + _esc(project.get("owner_provenance")),
            "- Owner next action: " + _esc(project.get("owner_next_action")),
            "- Task provenance: " + _esc(project.get("task_provenance")),
            "- Recommendation: " + _esc(decision.get("recommendation") if isinstance(decision, dict) else None),
        ])
    return "\n".join(lines) + "\n"


def render_json(report):
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
