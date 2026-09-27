import argparse, json, os, sys
from datetime import datetime, timezone
from pathlib import Path
from .contracts import load_json, validate_checkpoint, validate_request, validate_receipt_arguments, ContractError
from .store import Store, StoreError
from .collectors import collect_sources, semantic_digest, new_observation_id, hermes_collect, hermes_board_collect
from .brief import build_report, render_json, render_markdown
from .packets import make_packet, write_packet


def clock():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _source_root():
    return Path(__file__).resolve().parents[2]


def state_path(args):
    value = args.state_dir or (os.environ.get('HERMES_HOME') and str(Path(os.environ['HERMES_HOME']) / 'mission-control'))
    if not value:
        raise StoreError('state: --state-dir required when HERMES_HOME is absent')
    target = Path(value).expanduser()
    if not target.is_absolute():
        raise StoreError('state: absolute state directory required')
    # Keep the lexical path intact until Store.ensure_state can reject a
    # symlink and canonicalize existing ancestors plus missing descendants.
    # Resolving here would turn a state-root symlink into its target first.
    return target


def _load_file(path):
    return load_json(Path(path).read_bytes())


def _projects(registry):
    return {p['id']: p for p in registry['projects']}


def main(argv=None):
    ap = argparse.ArgumentParser(prog='geoffrey-mission-control')
    ap.add_argument('--state-dir')
    sub = ap.add_subparsers(dest='command', required=True)
    p = sub.add_parser('init'); p.add_argument('--registry', required=True)
    p = sub.add_parser('checkpoint'); p.add_argument('--file', required=True); p.add_argument('--now')
    p = sub.add_parser('scan'); p.add_argument('--now'); p.add_argument('--quiet-unchanged', action='store_true')
    p = sub.add_parser('brief'); p.add_argument('--format', choices=['markdown', 'json'], default='markdown'); p.add_argument('--now')
    p = sub.add_parser('request'); p.add_argument('--file', required=True); p.add_argument('--now')
    p = sub.add_parser('receipt'); p.add_argument('--request-id', required=True); p.add_argument('--task-id', required=True); p.add_argument('--board', required=True)
    args = ap.parse_args(argv)
    st = None
    try:
        if args.command == 'receipt':
            validate_receipt_arguments(args.request_id, args.task_id, args.board)
        st = Store(state_path(args), source_root=_source_root())
        if args.command == 'init':
            st.save_registry(_load_file(args.registry)); print('initialized'); return 0
        registry = st.registry(); projects = _projects(registry)
        if args.command == 'checkpoint':
            ingested = args.now or clock()
            cp = _load_file(args.file)
            cp = validate_checkpoint(cp, ingested_at=ingested)
            if cp['project_id'] not in projects: raise ContractError('checkpoint: unregistered project')
            if cp['provenance']['surface'] != projects[cp['project_id']]['owner']['surface']:
                raise ContractError('checkpoint: provenance surface does not match owner')
            st.save_checkpoint(cp, ingested); print('checkpoint accepted'); return 0
        if args.command == 'scan':
            ingested = args.now or clock()
            st.begin_scan()
            try:
                observations = {}
                failures = False
                for project in registry['projects']:
                    result = collect_sources(project, now=ingested, receipts=st.receipts(project['id']) if hasattr(st, 'receipts') else [])
                    previous = st.latest_observation(project['id'])
                    previous_payload = json.loads(previous['payload']) if previous else {}
                    payload = {
                        'observed_at': ingested, 'activity_at': None,
                        'last_verified': ingested if result['ok'] else previous_payload.get('last_verified'),
                        'sources': result['sources'], 'errors': result['errors'],
                        'checkpoint_snapshot': st.checkpoint_snapshot(project['id']),
                    }
                    git = result['sources'].get('git', {})
                    if git.get('ok'): payload['activity_at'] = git.get('latest_commit_at')
                    payload['semantic_digest'] = semantic_digest(payload)
                    st.add_observation(new_observation_id(), project['id'], ingested, payload, activity_at=payload['activity_at'], commit=False)
                    observations[project['id']] = payload
                    failures = failures or bool(result['errors'])
                st.commit_scan()
            except Exception:
                st.rollback_scan(); raise
            report = build_report(registry, observations, {p['id']: st.checkpoints(p['id']) for p in registry['projects']}, ingested)
            output = render_markdown(report)
            prior_same = []
            for project in registry['projects']:
                rows = st.observations(project['id'])
                prior_same.append(
                    len(rows) > 1
                    and json.loads(rows[-2]['payload']).get('semantic_digest')
                    == json.loads(rows[-1]['payload']).get('semantic_digest')
                )
            # An empty registry has no semantic state to announce.  For a
            # populated registry, every project must have a prior observation
            # with the same semantic digest; timestamps are intentionally not
            # part of that digest, while failures remain noisy below.
            unchanged = not registry['projects'] or (bool(prior_same) and all(prior_same))
            if not (args.quiet_unchanged and not failures and unchanged): print(output, end='')
            return 1 if failures else 0
        if args.command == 'brief':
            now = args.now or clock(); observations = {}
            for project in registry['projects']:
                row = st.latest_observation(project['id'])
                observations[project['id']] = json.loads(row['payload']) if row else {}
            report = build_report(registry, observations, {p['id']: st.checkpoints(p['id']) for p in registry['projects']}, now)
            print(render_json(report) if args.format == 'json' else render_markdown(report), end=''); return 0
        if args.command == 'request':
            now = args.now or clock(); req = validate_request(_load_file(args.file), now=now)
            project = projects.get(req['project_id'])
            if not project: raise ContractError('request: unregistered project')
            observation = st.latest_observation(project['id'])
            if not observation or observation['id'] != req['expected_observation_id']:
                raise ContractError('request: stale observation')
            observed = json.loads(observation['payload'])
            observed_at = datetime.fromisoformat(observed['observed_at'].replace('Z', '+00:00'))
            age = (datetime.fromisoformat(now.replace('Z', '+00:00')) - observed_at).total_seconds()
            if age < 0 or age > 3600:
                raise ContractError('request: observation is stale')
            if observed.get('errors') or not observed.get('last_verified'):
                raise ContractError('request: required source collection is not verified')
            if observed.get('checkpoint_snapshot') != st.checkpoint_snapshot(project['id']):
                raise ContractError('request: checkpoint requires a new scan')
            # Existing tracked tasks are read-only dedup evidence.  A marker
            # match wins over packet generation; active/unavailable tasks block
            # a different request rather than being silently replaced.
            target = hermes_board_collect(req['board'], now=now)
            if not target.get('ok'):
                raise ContractError('request: destination board unavailable')
            marker = f"Mission-Control-Request: {req['project_id']}/{req['id']}"
            matches = [task for task in target.get('tasks', []) if marker in (task.get('body') or '').splitlines()]
            if matches:
                if len(matches) != 1 or matches[0].get('assignee') != req['assignee']:
                    raise ContractError('request: existing task marker conflict')
                raise ContractError('request: existing task marker')
            for ref in project.get('kanban_refs') or []:
                live = hermes_collect(ref['board'], ref['task_id'], now=now)
                if not live.get('ok'):
                    raise ContractError('request: tracked task unavailable')
                tasks = live.get('tasks') or []
                if any(marker in (task.get('body') or '').splitlines() for task in tasks):
                    raise ContractError('request: existing task marker')
                if any(task.get('status') in {'triage','todo','ready','running','review','scheduled','blocked'} for task in tasks):
                    raise ContractError('request: tracked task is active')
            packet = make_packet(req, project, observation['id'], now)
            st.save_request(packet); write_packet(st.root, packet)
            print(json.dumps(packet, sort_keys=True)); return 0
        if args.command == 'receipt':
            request_row = st.request(args.request_id)
            if request_row is None: raise StoreError('receipt: request does not exist')
            request = json.loads(request_row['payload'])
            existing = st.receipt(args.request_id)
            if existing is not None:
                if existing['task_id'] != args.task_id or existing['board'] != args.board:
                    raise StoreError('receipt: already bound')
                # A replay is read-only, but still performs the route-specific
                # identity read-back.  Current task status is deliberately not
                # copied into the immutable historical receipt.
                replay = hermes_collect(args.board, args.task_id, now=clock())
                if not replay.get('ok'):
                    raise StoreError(replay.get('error', 'receipt: Hermes read-back failed'))
                replay_task = replay['task']
                marker = f"Mission-Control-Request: {request['project_id']}/{request['id']}"
                if (replay_task['id'] != args.task_id
                        or replay_task['assignee'] != request['assignee']
                        or marker not in (replay_task.get('body') or '').splitlines()):
                    raise StoreError('receipt: task identity or packet marker mismatch')
                print(existing['payload']); return 0
            project = projects.get(request['project_id'])
            if project is None: raise StoreError('receipt: project does not exist')
            result = hermes_collect(args.board, args.task_id, now=clock())
            if not result.get('ok'):
                raise StoreError(result.get('error', 'receipt: Hermes read-back failed'))
            task = result['task']
            marker = f"Mission-Control-Request: {request['project_id']}/{request['id']}"
            if task['id'] != args.task_id or task['assignee'] != request['assignee'] or marker not in (task.get('body') or '').splitlines():
                raise StoreError('receipt: task identity or packet marker mismatch')
            payload = {
                'request_id': args.request_id,
                'project_id': request['project_id'],
                'packet_marker': marker,
                'task_identity_verified': True,
                'idempotency_key_verified': False,
                'task_id': args.task_id,
                'board': args.board,
                'status': task['status'],
                'assignee': task['assignee'],
            }
            st.save_receipt(args.request_id, args.task_id, args.board, payload); print(json.dumps(payload, sort_keys=True)); return 0
    except (ContractError, StoreError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr); return 1
    finally:
        if st is not None: st.close()
    return 1


if __name__ == '__main__': raise SystemExit(main())
