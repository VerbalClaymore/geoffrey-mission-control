import contextlib, io, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from geoffrey_mission_control.cli import main

class CLITests(unittest.TestCase):
    def test_init_and_brief_lifecycle(self):
        with tempfile.TemporaryDirectory() as d:
            reg=Path(d)/'registry.json'; state=Path(d)/'state'
            reg.write_text(json.dumps({'schema_version':1,'projects':[]}))
            out=io.StringIO()
            with contextlib.redirect_stdout(out): self.assertEqual(main(['--state-dir',str(state),'init','--registry',str(reg)]),0)
            with contextlib.redirect_stdout(out): self.assertEqual(main(['--state-dir',str(state),'brief','--format','json']),0)
            self.assertIn('"projects": []',out.getvalue())
    def test_state_inside_cwd_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            import os
            old=os.getcwd(); os.chdir(d)
            try:
                with contextlib.redirect_stderr(io.StringIO()): self.assertEqual(main(['--state-dir',d,'brief']),1)
            finally: os.chdir(old)

    def test_fresh_cli_rejects_state_directory_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            outside = root / 'outside'; outside.mkdir()
            link = root / 'state'; link.symlink_to(outside, target_is_directory=True)
            env = os.environ.copy()
            env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1] / 'src')
            result = subprocess.run(
                [sys.executable, '-m', 'geoffrey_mission_control', '--state-dir', str(link), 'brief'],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((outside / 'mission-control.sqlite3').exists())

if __name__=='__main__': unittest.main()
