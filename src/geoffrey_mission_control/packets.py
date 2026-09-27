import json, os, tempfile
from pathlib import Path
from .contracts import ContractError, ID_RE, validate_request, timestamp


def make_packet(req, project, observation_id, now):
    # CLI validates the complete request first; retain a small compatibility path
    # for callers constructing a packet from an already-approved request record.
    if 'created_at' not in req:
        req = {**req, 'created_at': now, 'expires_at': now,
               'scope': ['Approved scope'], 'acceptance': ['Approved acceptance'],
               'forbidden': ['Anything outside the packet']}
        from datetime import datetime, timedelta, timezone
        try:
            expiry = datetime.fromisoformat(now.replace('Z', '+00:00')) + timedelta(hours=1)
        except ValueError as exc:
            raise ContractError('request: invalid now') from exc
        req['expires_at'] = expiry.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    req = validate_request(req, now=now)
    if req['project_id'] != project['id'] or req['expected_observation_id'] != observation_id:
        raise ContractError('request: stale observation')
    owner = project['owner']
    if (project['paused'] or project['permission'] == 'observe' or
            owner['surface'] != 'hermes' or owner.get('profile') != req['assignee']):
        raise ContractError('request: not authorized')
    return {
        **req,
        'idempotency_key': f"mc-v1-{req['project_id']}-{req['id']}",
        'completion_contract': 'local-only',
        'packet_marker': f"Mission-Control-Request: {req['project_id']}/{req['id']}",
        'canonical_spec': project.get('canonical_spec'),
        'permission': project['permission'],
        'native_instructions': (
            'Coordinator must submit this packet with kanban_create; do not execute the assignment here. '
            'Read back the returned task and retain the marker receipt.'
        ),
    }


def _safe_packet_dir(root):
    p = Path(root) / 'packets'
    if p.is_symlink():
        raise ContractError('packet: symlink directory is not allowed')
    p.mkdir(mode=0o700, exist_ok=True)
    if p.is_symlink():
        raise ContractError('packet: symlink directory is not allowed')
    os.chmod(p, 0o700)
    return p


def _atomic(path, data, root):
    if path.is_symlink():
        raise ContractError('packet: symlink file is not allowed')
    fd, tmp = tempfile.mkstemp(dir=root, prefix='.tmp.', text=True)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, data.encode('utf-8'))
        os.fsync(fd)
        os.close(fd)
        os.replace(tmp, path)
        dirfd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def write_packet(root, packet):
    p = _safe_packet_dir(root)
    raw = json.dumps(packet, sort_keys=True, indent=2) + '\n'
    marker = packet['packet_marker']
    markdown = (
        '# Mission Control Request\n\n' + marker + '\n\n'
        f"Title: {packet['title']}\n\n"
        f"Assignee: {packet['assignee']}\nBoard: {packet['board']}\n"
        f"Scope:\n" + '\n'.join('- ' + x for x in packet['scope']) + '\n\n'
        'Acceptance:\n' + '\n'.join('- ' + x for x in packet['acceptance']) + '\n\n'
        'Forbidden:\n' + '\n'.join('- ' + x for x in packet['forbidden']) + '\n'
    )
    paths = [p / (packet['id'] + suffix) for suffix in ('.json', '.md')]
    try:
        for path, data in zip(paths, (raw, markdown)):
            _atomic(path, data, p)
    except Exception:
        # Never leave a half-published new projection. Stored SQLite request
        # truth remains available for a later identical replay.
        for path in paths:
            if path.exists() and not path.is_symlink():
                try: path.unlink()
                except OSError: pass
        raise
    return packet
