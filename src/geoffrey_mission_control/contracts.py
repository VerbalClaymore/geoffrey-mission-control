import json, math, os, re
from datetime import datetime, timezone, timedelta

class ContractError(ValueError): pass
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
RFC3339_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$")
SURFACES = {"hermes", "codex", "claude", "cowork", "unknown"}
STATES = {"running", "ready", "awaiting_user", "blocked", "paused", "done", "unknown"}
REQUEST_FIELDS = {"schema_version","id","project_id","created_at","expires_at","expected_observation_id","assignee","board","title","scope","acceptance","forbidden","approval_ref"}
PACKET_METADATA_FIELDS = {"idempotency_key","completion_contract","packet_marker","canonical_spec","permission","native_instructions"}


def _no_dupes(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ContractError("contract: duplicate JSON key: " + key)
        out[key] = value
    return out


def load_json(source, *, max_bytes=1024 * 1024):
    if isinstance(source, (bytes, bytearray)):
        if len(source) > max_bytes: raise ContractError("contract: input exceeds 1 MiB")
        try: source = source.decode("utf-8")
        except UnicodeDecodeError as exc: raise ContractError("contract: invalid UTF-8") from exc
    elif isinstance(source, str):
        if len(source.encode("utf-8")) > max_bytes:
            raise ContractError("contract: input exceeds 1 MiB")
    else:
        raise ContractError("contract: JSON source must be bytes or string")
    try:
        return json.loads(source, object_pairs_hook=_no_dupes,
                          parse_constant=lambda _: (_ for _ in ()).throw(ContractError("contract: non-finite number")))
    except ContractError: raise
    except (ValueError, UnicodeError) as exc: raise ContractError("contract: invalid JSON") from exc


def _obj(value, name):
    if type(value) is not dict: raise ContractError(f"contract: {name} must be object")
    return value


def _str(value, name, nonempty=True, maxlen=4096):
    if type(value) is not str or (nonempty and not value) or len(value) > maxlen:
        raise ContractError(f"contract: invalid {name}")
    return value


def _bool(value, name):
    if type(value) is not bool: raise ContractError(f"contract: invalid {name}")
    return value


def _keys(value, allowed, required=()):
    extra = set(value) - set(allowed)
    if extra: raise ContractError("contract: unknown fields: " + ",".join(sorted(extra)))
    missing = set(required) - set(value)
    if missing: raise ContractError("contract: missing fields: " + ",".join(sorted(missing)))


def timestamp(value, name="timestamp"):
    _str(value, name)
    if not RFC3339_RE.fullmatch(value): raise ContractError(f"contract: invalid {name}")
    try:
        dt = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        dt = dt.astimezone(timezone.utc)
    except ValueError as exc: raise ContractError(f"contract: invalid {name}") from exc
    if dt.microsecond:
        return dt.isoformat(timespec="microseconds").replace("+00:00", "Z").rstrip("0").rstrip(".")
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _instant(value, name):
    return datetime.fromisoformat(timestamp(value, name).replace("Z", "+00:00"))


def _list(value, name, *, maxlen=100):
    if type(value) is not list or len(value) > maxlen: raise ContractError(f"contract: invalid {name}")
    return value


def validate_registry(value):
    d = _obj(value, "registry"); _keys(d, {"schema_version", "projects"}, ("schema_version", "projects"))
    if type(d["schema_version"]) is not int or d["schema_version"] != 1: raise ContractError("contract: schema_version must be 1")
    projects = _list(d["projects"], "projects")
    result, ids, refs = [], set(), set()
    for p in projects:
        p = _obj(p, "project"); _keys(p, {"id","name","goal","milestone","repo_path","owner","canonical_spec","permission","paused","stale_after_hours","kanban_refs"}, ("id","name","goal","milestone","repo_path","owner","canonical_spec","permission","paused","stale_after_hours","kanban_refs"))
        pid = _str(p["id"], "project id")
        if not ID_RE.fullmatch(pid) or pid in ids: raise ContractError("contract: invalid or duplicate project id")
        ids.add(pid)
        for key in ("name", "goal", "milestone"): _str(p[key], key)
        if p["repo_path"] is not None and (type(p["repo_path"]) is not str or len(p["repo_path"]) > 4096 or not os.path.isabs(p["repo_path"])): raise ContractError("contract: repo_path must be absolute or null")
        owner = _obj(p["owner"], "owner"); _keys(owner, {"surface","profile","session_ref"}, ("surface","profile","session_ref"))
        if owner["surface"] not in SURFACES: raise ContractError("contract: invalid owner surface")
        if owner["profile"] is not None and not ID_RE.fullmatch(_str(owner["profile"], "profile")):
            raise ContractError("contract: invalid profile")
        if owner["session_ref"] is not None: _str(owner["session_ref"], "session_ref")
        if p["canonical_spec"] is not None: _str(p["canonical_spec"], "canonical_spec")
        if p["permission"] not in {"observe", "coordinate", "advance"}: raise ContractError("contract: invalid permission")
        _bool(p["paused"], "paused")
        if type(p["stale_after_hours"]) is not int or not 1 <= p["stale_after_hours"] <= 8760: raise ContractError("contract: invalid stale_after_hours")
        refs_out = []
        for ref in _list(p["kanban_refs"], "kanban_refs"):
            ref = _obj(ref, "kanban ref"); _keys(ref, {"board","task_id"}, ("board","task_id"))
            board = _str(ref["board"], "board"); task = _str(ref["task_id"], "task_id", maxlen=128)
            if not ID_RE.fullmatch(board) or (board, task) in refs: raise ContractError("contract: invalid or duplicate task reference")
            refs.add((board, task)); refs_out.append({"board": board, "task_id": task})
        result.append({**p, "owner": dict(owner), "kanban_refs": refs_out})
    return {"schema_version": 1, "projects": result}


def validate_checkpoint(value, *, ingested_at=None, clock=None):
    d = _obj(value, "checkpoint"); _keys(d, {"schema_version","id","project_id","observed_at","state","summary","next_action","decision","evidence","provenance"}, {"schema_version","id","project_id","observed_at","state","summary","next_action","decision","evidence","provenance"})
    if type(d["schema_version"]) is not int or d["schema_version"] != 1: raise ContractError("contract: schema_version must be 1")
    for key in ("id", "project_id"):
        if not ID_RE.fullmatch(_str(d[key], key)): raise ContractError("contract: invalid id")
    observed = timestamp(d["observed_at"], "observed_at")
    if d["state"] not in STATES: raise ContractError("contract: invalid state")
    _str(d["summary"], "summary", maxlen=2000)
    if d["next_action"] is not None: _str(d["next_action"], "next_action")
    decision = d["decision"]
    if decision is not None:
        decision = _obj(decision, "decision"); _keys(decision, {"question","options","recommendation"}, {"question","options","recommendation"})
        _str(decision["question"], "question"); opts = decision["options"]
        if type(opts) is not list or not 2 <= len(opts) <= 4 or any(type(x) is not str or not x or len(x) > 4096 for x in opts): raise ContractError("contract: invalid decision options")
        if decision["recommendation"] not in opts: raise ContractError("contract: invalid recommendation")
    evidence = _list(d["evidence"], "evidence")
    if not evidence: raise ContractError("contract: evidence required")
    evidence_out = []
    for item in evidence:
        item = _obj(item, "evidence"); _keys(item, {"kind","ref","at"}, {"kind","ref","at"})
        if item["kind"] not in {"git","test","artifact","task","session","other"}: raise ContractError("contract: invalid evidence kind")
        evidence_out.append({"kind": item["kind"], "ref": _str(item["ref"], "evidence ref"), "at": timestamp(item["at"], "evidence at")})
    provenance = _obj(d["provenance"], "provenance"); _keys(provenance, {"surface","author"}, {"surface","author"})
    if provenance["surface"] not in SURFACES: raise ContractError("contract: invalid provenance surface")
    _str(provenance["author"], "provenance author")
    if ingested_at is not None:
        limit = _instant(ingested_at, "ingested_at") + timedelta(minutes=5)
        if _instant(observed, "observed_at") > limit: raise ContractError("checkpoint: observed_at is in the future")
        for item in evidence_out:
            at = _instant(item["at"], "evidence at")
            if at > limit or at > _instant(observed, "observed_at") + timedelta(minutes=5): raise ContractError("checkpoint: evidence time is invalid")
    return {**d, "observed_at": observed, "evidence": evidence_out, "decision": decision, "provenance": dict(provenance)}


def validate_request(value, *, now=None):
    d = _obj(value, "request"); fields = REQUEST_FIELDS
    _keys(d, fields, fields)
    if type(d["schema_version"]) is not int or d["schema_version"] != 1: raise ContractError("request: schema_version must be 1")
    for key in ("id", "project_id", "assignee", "board"):
        if not ID_RE.fullmatch(_str(d[key], key)): raise ContractError("request: invalid " + key)
    _str(d["expected_observation_id"], "expected_observation_id", maxlen=128)
    for key in ("title", "approval_ref"): _str(d[key], key)
    for key in ("scope", "acceptance", "forbidden"):
        vals = _list(d[key], key)
        if not vals or any(type(x) is not str or not x or len(x) > 4096 for x in vals): raise ContractError("request: invalid " + key)
    created, expires = _instant(d["created_at"], "created_at"), _instant(d["expires_at"], "expires_at")
    if expires <= created or expires - created > timedelta(hours=24): raise ContractError("request: invalid lifetime")
    if now is not None:
        current = _instant(now, "now")
        if created > current + timedelta(minutes=5) or expires <= current: raise ContractError("request: expired or future")
    return {**d, "created_at": timestamp(d["created_at"], "created_at"), "expires_at": timestamp(d["expires_at"], "expires_at")}


def validate_receipt_arguments(request_id, task_id, board):
    """Validate CLI-owned receipt identifiers before opening state or invoking Hermes."""
    if not ID_RE.fullmatch(_str(request_id, "request_id")):
        raise ContractError("receipt: invalid request_id")
    if type(task_id) is not str or not task_id or len(task_id) > 128:
        raise ContractError("receipt: invalid task_id")
    if not ID_RE.fullmatch(_str(board, "board")):
        raise ContractError("receipt: invalid board")
    return request_id, task_id, board
