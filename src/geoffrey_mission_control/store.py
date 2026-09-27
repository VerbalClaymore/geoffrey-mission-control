import fcntl, hashlib, json, os, sqlite3, tempfile
from pathlib import Path
from .contracts import (load_json, validate_registry, validate_checkpoint, validate_request,
                        ContractError, REQUEST_FIELDS, PACKET_METADATA_FIELDS)

class StoreError(RuntimeError): pass


def _reject_symlink(path):
    path = Path(path)
    if path.is_symlink(): raise StoreError("state: symlink is not allowed")


def ensure_state(state_dir, source_root=None):
    raw = Path(state_dir).expanduser()
    if not raw.is_absolute(): raise StoreError("state: absolute state directory required")
    _reject_symlink(raw)
    resolved = raw.resolve(strict=False)
    if source_root:
        source = Path(source_root).expanduser().resolve(strict=False)
        if resolved == source or source in resolved.parents: raise StoreError("state: cannot be inside source repository")
    raw.mkdir(parents=True, exist_ok=True)
    if raw.is_symlink(): raise StoreError("state: symlink is not allowed")
    os.chmod(raw, 0o700)
    return raw


class Store:
    def __init__(self, state_dir, *, source_root=None):
        self.root = ensure_state(state_dir, source_root)
        self.db = self.root / "mission-control.sqlite3"
        _reject_symlink(self.db)
        self.conn = sqlite3.connect(self.db, timeout=5, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1): raise StoreError("state: unsupported schema")
        if version == 0:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS checkpoints (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, observed_at TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL, ingested_at TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS checkpoint_project_time ON checkpoints(project_id, observed_at);
                CREATE TABLE IF NOT EXISTS observations (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, observed_at TEXT NOT NULL, activity_at TEXT, payload TEXT NOT NULL, digest TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, bound_task_id TEXT, bound_board TEXT);
                CREATE TABLE IF NOT EXISTS receipts (request_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, board TEXT NOT NULL, payload TEXT NOT NULL);
                PRAGMA user_version=1;
            """)
        os.chmod(self.db, 0o600)
        self._lock_file = None

    def close(self):
        self.release_scan()
        self.conn.close()

    def save_registry(self, registry):
        registry = validate_registry(registry)
        path = self.root / "registry.json"; _reject_symlink(path)
        data = json.dumps(registry, sort_keys=True, indent=2) + "\n"
        if path.exists():
            if path.read_text() == data: return registry
            raise StoreError("state: registry overwrite conflict")
        self._atomic_write(path, data)
        return registry

    def registry(self):
        path = self.root / "registry.json"; _reject_symlink(path)
        if not path.exists(): raise StoreError("state: registry missing")
        return validate_registry(load_json(path.read_bytes()))

    def _atomic_write(self, path, data):
        _reject_symlink(path)
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".tmp-", text=True)
        try:
            os.fchmod(fd, 0o600)
            view = memoryview(data.encode("utf-8"))
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd); os.close(fd); os.replace(tmp, path)
            dirfd = os.open(self.root, os.O_RDONLY)
            try: os.fsync(dirfd)
            finally: os.close(dirfd)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)

    def save_checkpoint(self, checkpoint, ingested_at):
        checkpoint = validate_checkpoint(checkpoint, ingested_at=ingested_at)
        raw = json.dumps(checkpoint, sort_keys=True, separators=(",", ":")); digest = hashlib.sha256(raw.encode()).hexdigest()
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            old = self.conn.execute("SELECT digest FROM checkpoints WHERE id=?", (checkpoint["id"],)).fetchone()
            same_time = self.conn.execute("SELECT digest FROM checkpoints WHERE project_id=? AND observed_at=?", (checkpoint["project_id"], checkpoint["observed_at"])).fetchone()
            if (old and old[0] != digest) or (same_time and same_time[0] != digest): raise StoreError("checkpoint: conflicting replay")
            if not old: self.conn.execute("INSERT INTO checkpoints VALUES (?,?,?,?,?,?)", (checkpoint["id"], checkpoint["project_id"], checkpoint["observed_at"], raw, digest, ingested_at))
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return checkpoint

    def checkpoints(self, project_id):
        rows = self.conn.execute("SELECT payload FROM checkpoints WHERE project_id=? ORDER BY observed_at", (project_id,))
        return [json.loads(row[0]) for row in rows]

    def checkpoint_newer_than(self, project_id, observed_at):
        row = self.conn.execute("SELECT 1 FROM checkpoints WHERE project_id=? AND ingested_at>? LIMIT 1", (project_id, observed_at)).fetchone()
        return row is not None

    def checkpoint_snapshot(self, project_id):
        rows = self.conn.execute(
            "SELECT id,digest,ingested_at FROM checkpoints WHERE project_id=? ORDER BY ingested_at,id",
            (project_id,),
        )
        return [[row[0], row[1], row[2]] for row in rows]

    def begin_scan(self):
        if self._lock_file is not None: raise StoreError("scan: busy")
        path = self.root / ".scan.lock"; _reject_symlink(path)
        self._lock_file = open(path, "a+")
        os.chmod(path, 0o600)
        try: fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._lock_file.close(); self._lock_file = None
            raise StoreError("scan: busy")
        self.conn.execute("BEGIN IMMEDIATE")

    def commit_scan(self):
        self.conn.execute("COMMIT"); self.release_scan()

    def rollback_scan(self):
        try: self.conn.execute("ROLLBACK")
        finally: self.release_scan()

    def release_scan(self):
        if self._lock_file is not None:
            try: fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            finally: self._lock_file.close(); self._lock_file = None

    def add_observation(self, oid, project_id, observed_at, payload, *, activity_at=None, commit=True):
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")); digest = hashlib.sha256(raw.encode()).hexdigest()
        self.conn.execute("INSERT INTO observations VALUES (?,?,?,?,?,?)", (oid, project_id, observed_at, activity_at, raw, digest))
        if commit: self.conn.commit()

    def latest_observation(self, project_id):
        row = self.conn.execute("SELECT * FROM observations WHERE project_id=? ORDER BY observed_at DESC, rowid DESC LIMIT 1", (project_id,)).fetchone()
        return dict(row) if row else None

    def observations(self, project_id):
        rows = self.conn.execute("SELECT * FROM observations WHERE project_id=? ORDER BY observed_at, rowid", (project_id,))
        return [dict(row) for row in rows]

    def save_request(self, request):
        if type(request) is not dict:
            raise ContractError("contract: request must be object")
        unknown = set(request) - REQUEST_FIELDS - PACKET_METADATA_FIELDS
        if unknown:
            raise ContractError("contract: unknown fields: " + ",".join(sorted(unknown)))
        base = {k: request[k] for k in REQUEST_FIELDS if k in request}
        validated = validate_request(base)
        metadata = {k: request[k] for k in PACKET_METADATA_FIELDS if k in request}
        if metadata:
            expected_marker = f"Mission-Control-Request: {validated['project_id']}/{validated['id']}"
            if "idempotency_key" in metadata and metadata["idempotency_key"] != f"mc-v1-{validated['project_id']}-{validated['id']}":
                raise ContractError("request: invalid idempotency_key")
            if "completion_contract" in metadata and metadata["completion_contract"] != "local-only":
                raise ContractError("request: invalid completion_contract")
            if "packet_marker" in metadata and metadata["packet_marker"] != expected_marker:
                raise ContractError("request: invalid packet_marker")
            if "canonical_spec" in metadata and metadata["canonical_spec"] is not None and type(metadata["canonical_spec"]) is not str:
                raise ContractError("request: invalid canonical_spec")
            if "permission" in metadata and metadata["permission"] not in {"observe", "coordinate", "advance"}:
                raise ContractError("request: invalid permission")
            if "native_instructions" in metadata and type(metadata["native_instructions"]) is not str:
                raise ContractError("request: invalid native_instructions")
        request = {**validated, **metadata}
        raw = json.dumps(request, sort_keys=True, separators=(",", ":"))
        old = self.conn.execute("SELECT payload FROM requests WHERE id=?", (request["id"],)).fetchone()
        if old and old[0] != raw: raise StoreError("request: conflicting replay")
        if not old: self.conn.execute("INSERT INTO requests(id,project_id,payload,created_at) VALUES(?,?,?,?)", (request["id"], request["project_id"], raw, request["created_at"]))
        return request

    def request(self, request_id):
        row = self.conn.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        return dict(row) if row else None

    def receipt(self, request_id):
        row = self.conn.execute("SELECT * FROM receipts WHERE request_id=?", (request_id,)).fetchone()
        return dict(row) if row else None

    def save_receipt(self, request_id, task_id, board, payload):
        encoded = json.dumps(payload, sort_keys=True)
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            if self.request(request_id) is None: raise StoreError("receipt: request does not exist")
            old = self.conn.execute("SELECT task_id,board,payload FROM receipts WHERE request_id=?", (request_id,)).fetchone()
            if old and (old["task_id"] != task_id or old["board"] != board or old["payload"] != encoded):
                raise StoreError("receipt: already bound")
            if not old: self.conn.execute("INSERT INTO receipts VALUES(?,?,?,?)", (request_id, task_id, board, encoded))
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return payload

    def receipts(self, project_id):
        rows = self.conn.execute(
            "SELECT r.request_id,r.task_id,r.board,r.payload FROM receipts r JOIN requests q ON q.id=r.request_id WHERE q.project_id=?",
            (project_id,),
        )
        return [{"request_id": row[0], "task_id": row[1], "board": row[2], **json.loads(row[3])} for row in rows]
