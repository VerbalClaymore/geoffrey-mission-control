"""Read-only, bounded evidence collectors for Git and Hermes Kanban."""
import hashlib
import json
import re
import subprocess
import uuid
from datetime import datetime, timedelta, timezone

from .contracts import load_json, timestamp

HERMES_VERSION = "0.21.5+2453.gd0288be"
HERMES_STATUSES = {"triage", "todo", "ready", "running", "blocked", "review", "done", "archived", "scheduled"}
MAX_EXTERNAL_TASKS = 100
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _validate_route(board, task_id=None):
    if type(board) is not str or not _SLUG.fullmatch(board):
        raise ValueError("invalid board")
    if task_id is not None and (type(task_id) is not str or not task_id or len(task_id) > 128):
        raise ValueError("invalid task id")


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _instant(value):
    return datetime.fromisoformat(timestamp(value).replace("Z", "+00:00"))


def _bounded_output(value):
    if isinstance(value, bytes):
        if len(value) > 1024 * 1024:
            raise ValueError("output exceeds 1 MiB")
        return value.decode("utf-8")
    if not isinstance(value, str) or len(value.encode("utf-8")) > 1024 * 1024:
        raise ValueError("output exceeds 1 MiB")
    return value


def _run(runner, argv, *, cwd=None):
    kwargs = {"capture_output": True, "text": True, "timeout": 15, "shell": False}
    if cwd is not None:
        kwargs["cwd"] = cwd
    result = runner(argv, **kwargs)
    stdout = _bounded_output(getattr(result, "stdout", ""))
    stderr = _bounded_output(getattr(result, "stderr", ""))
    if getattr(result, "returncode", 1) != 0:
        raise RuntimeError("subprocess failed")
    return stdout, stderr


def git_collect(repo_path, runner=subprocess.run, *, now=None):
    """Collect only metadata; all failures are explicit and sanitized."""
    try:
        head, _ = _run(runner, ["git", "rev-parse", "HEAD"], cwd=repo_path)
        branch, _ = _run(runner, ["git", "branch", "--show-current"], cwd=repo_path)
        stamp, _ = _run(runner, ["git", "log", "-1", "--format=%cI"], cwd=repo_path)
        status, _ = _run(runner, ["git", "status", "--porcelain"], cwd=repo_path)
        head, branch, stamp = head.strip(), branch.strip(), stamp.strip()
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", head) or not branch:
            raise ValueError("malformed metadata")
        stamp = timestamp(stamp, "git timestamp")
        if now is not None and _instant(stamp) > _instant(now) + timedelta(minutes=5):
            raise ValueError("future timestamp")
        return {"ok": True, "head": head, "branch": branch, "latest_commit_at": stamp, "dirty": bool(status)}
    except Exception as exc:
        category = "future timestamp" if "future timestamp" in str(exc) else ("status unavailable" if "status" in str(exc) else "unavailable evidence")
        return {"ok": False, "error": "git: " + category}


def _task(value):
    if type(value) is not dict:
        raise ValueError("task is not an object")
    required = {"id", "assignee", "status", "body", "created_at", "started_at", "completed_at"}
    if not required <= set(value):
        raise ValueError("required field missing")
    if type(value["id"]) is not str or not value["id"] or len(value["id"]) > 128:
        raise ValueError("invalid task id")
    if type(value["assignee"]) is not str or not _SLUG.fullmatch(value["assignee"]):
        raise ValueError("invalid assignee")
    if value["status"] not in HERMES_STATUSES or (value["body"] is not None and type(value["body"]) is not str):
        raise ValueError("invalid task field")
    for key in ("created_at", "started_at", "completed_at"):
        v = value[key]
        if v is not None and (type(v) is not int or v < 0):
            raise ValueError("invalid epoch")
    out = dict(value)
    for key in ("created_at", "started_at", "completed_at"):
        if out[key] is not None:
            out[key] = datetime.fromtimestamp(out[key], timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return out


def hermes_collect(board, task_id, runner=subprocess.run, hermes_bin="hermes", *, now=None):
    """Read and validate the exact pinned Hermes list/show routes."""
    try:
        _validate_route(board, task_id)
        version, _ = _run(runner, [hermes_bin, "--version"])
        first = version.splitlines()[0] if version.splitlines() else ""
        if HERMES_VERSION not in first:
            return {"ok": False, "error": "hermes: unsupported_version"}
        listing, _ = _run(runner, [hermes_bin, "kanban", "--board", board, "list", "--json"])
        values = load_json(listing.encode("utf-8"))
        if type(values) is not list:
            raise ValueError("list is not an array")
        if len(values) > MAX_EXTERNAL_TASKS:
            raise ValueError("census exceeds 100 entries")
        tasks = [_task(v) for v in values]
        if len({v["id"] for v in tasks}) != len(tasks):
            raise ValueError("duplicate task id")
        if not any(v["id"] == task_id for v in tasks):
            return {"ok": False, "error": "hermes: missing task"}
        shown, _ = _run(runner, [hermes_bin, "kanban", "--board", board, "show", task_id, "--json"])
        envelope = load_json(shown.encode("utf-8"))
        if type(envelope) is not dict or type(envelope.get("task")) is not dict:
            raise ValueError("show schema")
        task = _task(envelope["task"])
        if task["id"] != task_id:
            return {"ok": False, "error": "hermes: mismatched task"}
        listed = next(v for v in tasks if v["id"] == task_id)
        # List and show are two independent reads; never combine an identity
        # from one with a status/body from the other when they disagree.
        comparable = ("id", "assignee", "status", "body", "created_at", "started_at", "completed_at")
        if any(listed.get(key) != task.get(key) for key in comparable):
            return {"ok": False, "error": "hermes: list/show disagreement"}
        if now is not None:
            limit = _instant(now) + timedelta(minutes=5)
            for key in ("created_at", "started_at", "completed_at"):
                if task.get(key) is not None and _instant(task[key]) > limit:
                    return {"ok": False, "error": "hermes: future timestamp"}
        return {"ok": True, "board": board, "task": task, "tasks": tasks, "version": HERMES_VERSION}
    except Exception as exc:
        text = str(exc)
        if "output exceeds" in text:
            error = "hermes: output exceeds 1 MiB"
        elif "census exceeds" in text:
            error = "hermes: census exceeds 100 entries"
        elif "unsupported_version" in text:
            error = text
        else:
            error = "hermes: malformed response"
        return {"ok": False, "error": error}


def hermes_board_collect(board, runner=subprocess.run, hermes_bin="hermes", *, now=None):
    """Read a complete board census, including every listed task's show route."""
    try:
        _validate_route(board)
        version, _ = _run(runner, [hermes_bin, "--version"])
        first = version.splitlines()[0] if version.splitlines() else ""
        if HERMES_VERSION not in first:
            return {"ok": False, "error": "hermes: unsupported_version"}
        listing, _ = _run(runner, [hermes_bin, "kanban", "--board", board, "list", "--json"])
        values = load_json(listing.encode("utf-8"))
        if type(values) is not list:
            raise ValueError("list is not an array")
        if len(values) > MAX_EXTERNAL_TASKS:
            raise ValueError("census exceeds 100 entries")
        tasks = [_task(v) for v in values]
        if len({v["id"] for v in tasks}) != len(tasks):
            raise ValueError("duplicate task id")
        shown_tasks = []
        for listed in tasks:
            shown, _ = _run(runner, [hermes_bin, "kanban", "--board", board, "show", listed["id"], "--json"])
            envelope = load_json(shown.encode("utf-8"))
            if type(envelope) is not dict or type(envelope.get("task")) is not dict:
                raise ValueError("show schema")
            task = _task(envelope["task"])
            if task["id"] != listed["id"]:
                raise ValueError("mismatched task")
            comparable = ("id", "assignee", "status", "body", "created_at", "started_at", "completed_at")
            if any(listed.get(key) != task.get(key) for key in comparable):
                raise ValueError("list/show disagreement")
            if now is not None:
                limit = _instant(now) + timedelta(minutes=5)
                if any(task.get(key) is not None and _instant(task[key]) > limit for key in ("created_at", "started_at", "completed_at")):
                    raise ValueError("future timestamp")
            shown_tasks.append(task)
        return {"ok": True, "board": board, "tasks": shown_tasks, "version": HERMES_VERSION}
    except Exception as exc:
        text = str(exc)
        if "output exceeds" in text:
            error = "hermes: output exceeds 1 MiB"
        elif "census exceeds" in text:
            error = "hermes: census exceeds 100 entries"
        elif "future timestamp" in text:
            error = "hermes: future timestamp"
        elif "list/show disagreement" in text:
            error = "hermes: list/show disagreement"
        else:
            error = "hermes: malformed response"
        return {"ok": False, "error": error}


def collect_sources(project, *, git_runner=subprocess.run, hermes_runner=hermes_collect, now=None, receipts=None):
    sources, errors = {}, []
    if project.get("repo_path"):
        if git_runner is subprocess.run:
            result = git_collect(project["repo_path"], git_runner, now=now)
        else:
            result = git_runner(project["repo_path"], now=now)
        sources["git"] = result
        if not result["ok"]:
            errors.append({"source": "git", "error": result["error"]})
    refs = list(project.get("kanban_refs") or [])
    for receipt in receipts or []:
        ref = {"board": receipt.get("board"), "task_id": receipt.get("task_id")}
        if ref["board"] and ref["task_id"] and ref not in refs:
            refs.append(ref)
    if refs:
        task_results = []
        for ref in refs:
            try:
                result = hermes_runner(ref["board"], ref["task_id"], now=now)
            except TypeError:
                # Preserve the small injectable runner API used by callers
                # while the production adapter receives the shared clock.
                result = hermes_runner(ref["board"], ref["task_id"])
            task_results.append({"board": ref["board"], "task_id": ref["task_id"], "result": result})
            if not result.get("ok"):
                errors.append({"source": "hermes", "ref": ref["board"] + "/" + ref["task_id"], "error": result.get("error", "hermes: source error")})
        sources["hermes"] = task_results
    required = bool(project.get("repo_path") or refs)
    return {"ok": required and not errors if required else False, "sources": sources, "errors": errors}


def semantic_digest(value):
    # These fields describe when we looked, rather than what we observed.
    # `last_verified` is derived from the current attempt and therefore must
    # not turn an otherwise identical scan into a notification.
    ignored = {"observed_at", "last_verified", "checked_at", "last_checked", "heartbeat", "heartbeat_at", "ingested_at"}
    def clean(item):
        if isinstance(item, dict):
            return {k: clean(v) for k, v in sorted(item.items()) if k not in ignored}
        if isinstance(item, list):
            return [clean(v) for v in item]
        return item
    raw = json.dumps(clean(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_observation_id():
    return uuid.uuid4().hex
