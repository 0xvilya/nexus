"""Real developer process execution, lifecycle and boundary regressions."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import bootstrap
from rainforge_worker.developer_terminal import DeveloperTerminal, MAX_OUTPUT
from rainforge_worker.workspace import WorkspaceTools


class DeveloperTerminalTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = WorkspaceTools(Path(directory.name))
        self.terminal = DeveloperTerminal(self.workspace)
        self.addCleanup(self.terminal.close)

    def finish(self, run):
        self.terminal.thread.join(timeout=12)
        self.assertFalse(self.terminal.thread.is_alive(), 'Developer command did not stop')
        return self.terminal.status(run['id'])

    def test_real_stdout_stderr_arguments_exit_and_no_shell(self):
        self.workspace.write('hello.py', 'import sys\nprint("hello", sys.argv[1])\nprint("problem", file=sys.stderr)\n')
        result = self.finish(self.terminal.start('python', ['hello.py', 'two words & still one argument'], allow_execute=True))
        self.assertEqual(result['state'], 'completed', result)
        self.assertEqual(result['exit_code'], 0)
        self.assertIn('hello two words & still one argument', result['output'])
        self.assertIn('problem', result['output'])
        self.workspace.write('fail.py', 'raise SystemExit(7)\n')
        failed = self.finish(self.terminal.start('python', ['fail.py'], allow_execute=True))
        self.assertEqual(failed['state'], 'failed')
        self.assertEqual(failed['exit_code'], 7)

    def test_permission_directories_and_argument_validation(self):
        self.workspace.write('app.py', 'print("hello")\n')
        with self.assertRaises(ValueError):
            self.terminal.start('python', ['app.py'])
        for kwargs in (
            {'tool':'powershell','args':['app.py']},
            {'tool':'python','args':['-c','print(1)']},
            {'tool':'python','args':['../app.py']},
            {'tool':'python','args':['app.py'],'directory':'../'},
            {'tool':'python','args':['app.py'],'timeout':True},
            {'tool':'python','args':'app.py'},
            {'tool':'python','args':['app.py', '\0']},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.terminal.start(allow_execute=True, **kwargs)
        self.workspace.write('nested/app.py', 'import os\nprint(os.path.basename(os.getcwd()))\n')
        result = self.finish(self.terminal.start('python', ['app.py'], directory='nested', allow_execute=True))
        self.assertEqual(result['state'], 'completed', result)
        self.assertIn('nested', result['output'])

    def test_output_is_live_single_process_cancel_and_close(self):
        self.workspace.write('slow.py', 'import time\nprint("alive", flush=True)\ntime.sleep(30)\n')
        run = self.terminal.start('python', ['slow.py'], allow_execute=True)
        deadline = time.monotonic() + 8
        while 'alive' not in self.terminal.status(run['id'])['output'] and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertIn('alive', self.terminal.status(run['id'])['output'])
        with self.assertRaises(RuntimeError):
            self.terminal.start('python', ['slow.py'], allow_execute=True)
        self.terminal.cancel(run['id'])
        result = self.finish(run)
        self.assertEqual(result['state'], 'cancelled')
        self.terminal.start('python', ['slow.py'], allow_execute=True)
        self.terminal.close()
        self.assertFalse(self.terminal.thread.is_alive())
        self.assertEqual(self.terminal.status()['runs'][0]['state'], 'cancelled')
        with self.assertRaises(RuntimeError):
            self.terminal.start('python', ['slow.py'], allow_execute=True)

    def test_owned_descendants_stop_with_completed_parent_and_environment_is_reduced(self):
        self.workspace.write('child.py', 'import time\nfrom pathlib import Path\ntime.sleep(2)\nPath("leaked.txt").write_text("child survived")\n')
        self.workspace.write('parent.py', 'import os, subprocess, sys\nsubprocess.Popen([sys.executable, "child.py"])\nprint(os.environ.get("NEXUS_TERMINAL_FIXTURE_SECRET", "removed"), flush=True)\n')
        from unittest.mock import patch
        with patch.dict(os.environ, {'NEXUS_TERMINAL_FIXTURE_SECRET':'fixture-value'}):
            run = self.terminal.start('python', ['parent.py'], allow_execute=True)
            result = self.finish(run)
        self.assertEqual(result['state'], 'completed', result)
        self.assertIn('removed', result['output'])
        self.assertNotIn('fixture-value', result['output'])
        time.sleep(2.2)
        self.assertFalse((self.workspace.root / 'leaked.txt').exists())

    def test_timeout_output_cap_and_session_history(self):
        self.workspace.write('slow.py', 'import time\ntime.sleep(20)\n')
        result = self.finish(self.terminal.start('python', ['slow.py'], timeout=1, allow_execute=True))
        self.assertEqual(result['state'], 'timed_out')
        self.workspace.write('flood.py', 'while True:\n print("x"*8192, flush=True)\n')
        result = self.finish(self.terminal.start('python', ['flood.py'], allow_execute=True))
        self.assertEqual(result['state'], 'output_limited')
        self.assertTrue(result['output_limited'])
        self.assertLessEqual(len(result['output'].encode('utf-8')), MAX_OUTPUT)
        self.assertEqual(len(self.terminal.status()['runs']), 2)
        with self.assertRaises(ValueError):
            self.terminal.status('missing')

    @unittest.skipUnless(shutil.which('git'), 'Git is not installed')
    def test_read_only_git_never_runs_repository_fsmonitor_hook(self):
        import subprocess

        subprocess.run(['git', 'init'], cwd=self.workspace.root,
                       check=True, capture_output=True)
        hook = self.workspace.root / '.git' / 'hooks' / 'audit-fsmonitor'
        hook.write_text('#!/bin/sh\necho invoked > hook-ran.txt\nprintf "token\\\\0"\n',
                        encoding='utf-8')
        hook.chmod(0o700)
        subprocess.run(['git', 'config', 'core.fsmonitor', hook.as_posix()],
                       cwd=self.workspace.root, check=True, capture_output=True)
        # First prove that the repository configuration really can invoke
        # this hook, then verify that the console disables that mechanism.
        subprocess.run(['git', 'status', '--short'], cwd=self.workspace.root,
                       check=True, capture_output=True)
        marker = self.workspace.root / 'hook-ran.txt'
        self.assertTrue(marker.exists(), 'The hook fixture did not execute')
        marker.unlink()
        result = self.finish(self.terminal.start(
            'git', ['status', '--short'], allow_execute=True))
        self.assertEqual(result['state'], 'completed', result)
        self.assertEqual(result['exit_code'], 0)
        self.assertFalse((self.workspace.root / 'hook-ran.txt').exists())

    @unittest.skipUnless(shutil.which('git'), 'Git is not installed')
    def test_read_only_git_and_no_unreviewed_output_redirection(self):
        result = self.finish(self.terminal.start('git', ['status', '--short'], allow_execute=True))
        self.assertEqual(result['state'], 'failed')
        self.assertIn('not a git repository', result['output'])
        for arguments in (['clone', 'https://example.org/repo'], ['diff', '--output=outside.txt']):
            with self.assertRaises(ValueError):
                self.terminal.start('git', arguments, allow_execute=True)


if __name__ == '__main__':
    unittest.main()
