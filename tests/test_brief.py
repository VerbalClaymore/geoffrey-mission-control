import unittest
from geoffrey_mission_control.brief import build_report, render_markdown


def project(pid="a", **overrides):
    value = {'id': pid, 'name': 'A|x [link](url) <tag>', 'goal': 'g', 'milestone': 'm',
             'repo_path': None, 'owner': {'surface': 'unknown', 'profile': None, 'session_ref': None},
             'canonical_spec': None, 'permission': 'observe', 'paused': False,
             'stale_after_hours': 48, 'kanban_refs': []}
    value.update(overrides)
    return value


class BriefTests(unittest.TestCase):
    def test_one_project_stable_grouping_and_safe_untrusted_markdown(self):
        registry = {'schema_version': 1, 'projects': [project('z'), project('a')]}
        report = build_report(registry, now='2026-01-03T00:00:00Z')
        self.assertEqual([p['project_id'] for p in report['projects']], ['a', 'z'])
        markdown = render_markdown(report)
        self.assertIn('A\\|x \\[link\\]\\(url\\) \\<tag\\>', markdown)
        self.assertNotIn('<tag>', markdown)

    def test_raw_task_status_and_reported_vs_verified_provenance(self):
        registry = {'schema_version': 1, 'projects': [project('a', kanban_refs=[{'board':'b','task_id':'t'}])]}
        observations = {'a': {'observed_at':'2026-01-02T00:00:00Z', 'last_verified':'2026-01-01T00:00:00Z',
                              'activity_at':'2026-01-02T00:00:00Z', 'sources': {'hermes':[{'result': {'ok':True, 'task': {'status':'done'}}}]}, 'errors':[]}}
        checkpoints = {'a': [{'state':'running','observed_at':'2026-01-02T00:00:00Z','summary':'reported','next_action':'ship','decision':None,'provenance':{'surface':'hermes','author':'owner'}}]}
        row = build_report(registry, observations, checkpoints, now='2026-01-02T01:00:00Z')['projects'][0]
        self.assertEqual(row['raw_task_statuses'], ['done'])
        self.assertEqual(row['status'], 'conflict')
        self.assertEqual(row['reported_state'], 'running')
        self.assertEqual(row['verified_state'], 'reported_done')
        self.assertEqual(row['next_action'], 'ship')

    def test_failure_preserves_old_verified_and_stale_equality_is_fresh(self):
        registry = {'schema_version':1, 'projects':[project('a', stale_after_hours=1)]}
        observations = {'a': {'observed_at':'2026-01-02T01:00:00Z','last_verified':'2026-01-02T00:00:00Z',
                              'activity_at':'2026-01-02T00:00:00Z','errors':[{'source':'git','error':'git: unavailable evidence'}]}}
        row = build_report(registry, observations, now='2026-01-02T01:00:00Z')['projects'][0]
        self.assertEqual(row['last_verified'], '2026-01-02T00:00:00Z')
        self.assertFalse(row['stale'])
        self.assertEqual(row['status'], 'unavailable')

    def test_paused_and_decision_conflict_unknowns_and_capability(self):
        registry = {'schema_version':1, 'projects':[project('a', paused=True), project('b')]}
        cps = {'b':[{'state':'blocked','observed_at':'2026-01-01T00:00:00Z','summary':'x','next_action':None,
                     'decision':{'question':'q','options':['x','y'],'recommendation':'x'}, 'provenance':{'surface':'unknown','author':'o'}}]}
        rows = build_report(registry, checkpoints=cps, now='2026-01-03T00:00:00Z')['projects']
        self.assertEqual(rows[0]['status'], 'paused')
        self.assertEqual(rows[1]['status'], 'awaiting_user')
        self.assertEqual(rows[1]['owner_decision']['question'], 'q')
        self.assertEqual(rows[1]['capability'], 'checkpoint_only')

    def test_checkpoint_task_compatibility_matrix(self):
        cases = [
            ('unknown', 'running', False), ('running', 'running', False),
            ('ready', 'ready', False), ('ready', 'awaiting_user', False), ('blocked', 'blocked', False),
            ('done', 'reported_done', False), ('running', 'reported_done', True),
            ('running', 'archived', True), ('done', 'running', True),
            ('done', 'ready', True), ('done', 'blocked', True),
            ('blocked', 'reported_done', True),
        ]
        for checkpoint_state, task_state, expected in cases:
            with self.subTest(checkpoint_state=checkpoint_state, task_state=task_state):
                registry = {'schema_version': 1, 'projects': [project('a')]}
                observations = {'a': {'sources': {'hermes': [{'result': {'ok': True, 'task': {'status': {
                    'running': 'running', 'ready': 'ready', 'awaiting_user': 'review', 'blocked': 'blocked',
                    'reported_done': 'done', 'archived': 'archived'}[task_state]}}}]}, 'errors': []}}
                checkpoints = {'a': [{'state': checkpoint_state, 'observed_at': '2026-01-01T00:00:00Z',
                                      'summary': 'owner', 'next_action': None, 'decision': None,
                                      'provenance': {'surface': 'unknown', 'author': 'owner'}}]}
                row = build_report(registry, observations, checkpoints, now='2026-01-01T01:00:00Z')['projects'][0]
                self.assertEqual(row['conflict'], expected)

    def test_precedence_and_cli_rendering_keep_decision_conflict_and_raw_status(self):
        registry = {'schema_version': 1, 'projects': [project('a', paused=True), project('b')]}
        observations = {'b': {'observed_at': '2026-01-02T00:00:00Z', 'errors': [{'source': 'git', 'error': 'unavailable'}],
                              'sources': {'hermes': [{'board': 'b', 'task_id': 't', 'result': {'ok': True, 'task': {'status': 'running'}}}]}}}
        checkpoints = {'b': [{'state': 'done', 'observed_at': '2026-01-02T00:00:00Z', 'summary': 'owner',
                              'next_action': 'decide', 'decision': {'question': 'q', 'options': ['x', 'y'], 'recommendation': 'x'},
                              'provenance': {'surface': 'unknown', 'author': 'owner'}}]}
        report = build_report(registry, observations, checkpoints, now='2026-01-02T01:00:00Z')
        rows = report['projects']
        self.assertEqual(rows[0]['status'], 'paused')
        self.assertEqual(rows[1]['status'], 'unavailable')
        self.assertTrue(rows[1]['conflict'])
        self.assertEqual(rows[1]['raw_task_statuses'], ['running'])
        self.assertIn('unavailable', render_markdown(report))
        self.assertIn('decide', render_markdown(report))

    def test_status_origin_is_the_winning_precedence_branch(self):
        cases = [
            ({'paused': True}, {}, {}, 'paused'),
            ({}, {'errors': [{'source': 'git', 'error': 'offline'}]}, {}, 'collection_error'),
            ({}, {'sources': {'hermes': [{'result': {'ok': True, 'task': {'status': 'running'}}}]}, 'errors': []},
             {'state': 'done', 'decision': None}, 'conflict'),
            ({}, {'sources': {'hermes': [{'result': {'ok': True, 'task': {'status': 'blocked'}}}]}, 'errors': []},
             {'state': 'running', 'decision': {'question': 'q', 'options': ['x', 'y'], 'recommendation': 'x'}}, 'checkpoint_decision'),
            ({}, {'sources': {'hermes': [{'result': {'ok': True, 'task': {'status': 'running'}}}]}, 'errors': []}, {}, 'task'),
            ({}, {}, {'state': 'paused', 'decision': None}, 'checkpoint'),
        ]
        for project_overrides, observation, checkpoint, expected in cases:
            with self.subTest(expected=expected):
                registry = {'schema_version': 1, 'projects': [project('a', **project_overrides)]}
                checkpoints = {'a': [dict(checkpoint, observed_at='2026-01-01T00:00:00Z', summary='s',
                                          next_action=None, provenance={'surface': 'unknown', 'author': 'o'})]} if checkpoint else {}
                row = build_report(registry, {'a': observation}, checkpoints,
                                   now='2026-01-01T01:00:00Z')['projects'][0]
                self.assertEqual(row['status_origin'], expected)

    def test_markdown_renders_all_evidence_and_distinct_provenance(self):
        registry = {'schema_version': 1, 'projects': [project('a', owner={
            'surface': 'hermes', 'profile': 'p', 'session_ref': 's'})]}
        observation = {'observed_at': '2026-01-01T00:00:00Z', 'last_verified': None,
                       'activity_at': None, 'errors': [{'source': 'git', 'error': 'offline|[x]'}],
                       'sources': {'hermes': [{'board': 'b', 'task_id': 't',
                           'result': {'ok': True, 'task': {'status': 'blocked'}}}]}}
        checkpoint = {'state': 'blocked', 'observed_at': '2026-01-01T00:00:00Z', 'summary': 'owner',
                      'next_action': 'fix|`it`', 'decision': None,
                      'provenance': {'surface': 'hermes', 'author': 'owner'}}
        row = build_report(registry, {'a': observation}, {'a': [checkpoint]},
                           now='2026-01-01T01:00:00Z')['projects'][0]
        markdown = render_markdown({'schema_version': 1, 'projects': [row]})
        for label in ('Stale', 'Source refs', 'Capability', 'Errors', 'Owner provenance', 'Task provenance'):
            self.assertIn(label, markdown)
        self.assertIn('offline\\|\\[x\\]', markdown)
        self.assertIn('fix\\|\\`it\\`', markdown)
        self.assertIn('hermes_kanban', markdown)
        self.assertIn('owner', markdown)

    def test_markdown_explicitly_shows_unknown_evidence(self):
        row = build_report({'schema_version': 1, 'projects': [project('a')]},
                           now='2026-01-01T00:00:00Z')['projects'][0]
        markdown = render_markdown({'schema_version': 1, 'projects': [row]})
        self.assertIn('unknown', markdown)

    def test_multi_project_markdown_keeps_summary_table_contiguous(self):
        registry = {'schema_version': 1, 'projects': [
            project('running', name='Running'),
            project('blocked', name='Blocked'),
            project('paused', name='Paused', paused=True),
        ]}
        observations = {
            'running': {
                'observed_at': '2026-01-01T00:00:00Z', 'errors': [],
                'sources': {'hermes': [{'result': {'ok': True,
                    'task': {'status': 'running'}}}]},
            },
            'blocked': {
                'observed_at': '2026-01-01T00:00:00Z',
                'errors': [{'source': 'git', 'error': 'offline'}],
            },
        }
        report = build_report(registry, observations=observations,
                              now='2026-01-01T01:00:00Z')
        markdown = render_markdown(report)
        lines = markdown.splitlines()
        header = '| Project | Status | Origin | Owner state | Task state/raw | Conflict | Checked | Verified | Meaningful activity | Next action | Decision |'
        separator = '|---|---|---|---|---|---|---|---|---|---|---|'
        self.assertEqual(lines.count(header), 1)
        self.assertEqual(lines.count(separator), 1)
        first_evidence = next(i for i, line in enumerate(lines)
                              if line.startswith('Evidence for '))
        table_rows = [line for line in lines if line.startswith('| ') and line != header]
        self.assertEqual(len(table_rows), 3)
        self.assertEqual([report['projects'][i]['name'] for i in range(3)],
                         ['Paused', 'Blocked', 'Running'])
        self.assertEqual([row.split(' | ')[0][2:] for row in table_rows],
                         ['Paused', 'Blocked', 'Running'])
        self.assertTrue(all(lines.index(row) < first_evidence for row in table_rows))
        self.assertEqual([line for line in lines if line.startswith('Evidence for ')],
                         ['Evidence for paused:', 'Evidence for blocked:', 'Evidence for running:'])
        evidence_start = [i for i, line in enumerate(lines)
                          if line.startswith('Evidence for ')]
        self.assertTrue(all(i > first_evidence for i in evidence_start[1:]))
        self.assertFalse(any(line.startswith('|') and line not in {header, separator, *table_rows}
                             for line in lines))


if __name__ == '__main__': unittest.main()
