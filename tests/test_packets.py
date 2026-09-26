import tempfile
import unittest
from pathlib import Path
from unittest import mock

from geoffrey_mission_control import packets
from geoffrey_mission_control.packets import make_packet, write_packet
from geoffrey_mission_control.contracts import ContractError

class PacketTests(unittest.TestCase):
    def _packet(self, request_id='r1'):
        project={'id':'p','canonical_spec':'docs/spec.md','permission':'coordinate','paused':False,'owner':{'surface':'hermes','profile':'worker'}}
        req={'schema_version':1,'id':request_id,'project_id':'p','created_at':'2026-01-01T00:00:00Z','expires_at':'2026-01-01T01:00:00Z','expected_observation_id':'o1','assignee':'worker','board':'board','title':'T','scope':['scope'],'acceptance':['accept'],'forbidden':['forbidden'],'approval_ref':'a'}
        return make_packet(req,project,'o1','2026-01-01T00:30:00Z')

    def test_packet_marker_and_idempotency_are_deterministic(self):
        project={'id':'p','canonical_spec':'docs/spec.md','permission':'coordinate','paused':False,'owner':{'surface':'hermes','profile':'worker'}}
        req={'schema_version':1,'id':'r1','project_id':'p','expected_observation_id':'o1','assignee':'worker','board':'board','title':'T','approval_ref':'a'}
        packet=make_packet(req,project,'o1','2026-01-01T00:00:00Z')
        self.assertEqual(packet['idempotency_key'],'mc-v1-p-r1')
        self.assertEqual(packet['packet_marker'],'Mission-Control-Request: p/r1')
    def test_unapproved_scope_refused(self):
        project={'id':'p','canonical_spec':None,'permission':'observe','paused':False,'owner':{'surface':'hermes','profile':'worker'}}
        req={'schema_version':1,'id':'r1','project_id':'p','expected_observation_id':'o1','assignee':'worker','board':'b','title':'T','approval_ref':'a'}
        with self.assertRaises(ContractError): make_packet(req,project,'o1','now')

    def test_publication_failure_between_json_and_markdown_is_recoverable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            unrelated = root / 'packets' / 'unrelated.json'
            unrelated.parent.mkdir(mode=0o700)
            unrelated.write_bytes(b'unrelated-valid-packet\n')
            packet = self._packet()
            original = packets._atomic
            calls = []

            def fail_after_json(path, data, packet_root):
                calls.append(path.name)
                if len(calls) == 2:
                    raise OSError('injected markdown publication failure')
                return original(path, data, packet_root)

            with mock.patch.object(packets, '_atomic', side_effect=fail_after_json):
                with self.assertRaises(OSError):
                    write_packet(root, packet)
            self.assertEqual(calls, ['r1.json', 'r1.md'])
            self.assertFalse((root / 'packets' / 'r1.json').exists())
            self.assertFalse((root / 'packets' / 'r1.md').exists())
            self.assertEqual(unrelated.read_bytes(), b'unrelated-valid-packet\n')
            write_packet(root, packet)
            self.assertEqual((root / 'packets' / 'r1.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual((root / 'packets' / 'r1.md').stat().st_mode & 0o777, 0o600)
            self.assertEqual(unrelated.read_bytes(), b'unrelated-valid-packet\n')

    def test_publication_failure_before_json_replace_leaves_no_partial_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            packet = self._packet('before')
            original = packets._atomic

            def fail_before_json(path, data, packet_root):
                if path.name == 'before.json':
                    raise OSError('injected json publication failure')
                return original(path, data, packet_root)

            with mock.patch.object(packets, '_atomic', side_effect=fail_before_json):
                with self.assertRaises(OSError):
                    write_packet(root, packet)
            self.assertFalse((root / 'packets' / 'before.json').exists())
            self.assertFalse((root / 'packets' / 'before.md').exists())
            write_packet(root, packet)
            self.assertTrue((root / 'packets' / 'before.json').exists())
            self.assertTrue((root / 'packets' / 'before.md').exists())

    def test_packet_directory_symlink_is_rejected_without_outside_mutation(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp)
            target = Path(outside)
            (target / 'sentinel').write_bytes(b'unchanged')
            (root / 'packets').symlink_to(target, target_is_directory=True)
            with self.assertRaises(ContractError):
                write_packet(root, self._packet('escape'))
            self.assertEqual((target / 'sentinel').read_bytes(), b'unchanged')
            self.assertFalse((target / 'escape.json').exists())

if __name__=='__main__': unittest.main()
