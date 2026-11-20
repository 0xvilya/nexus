"""User-operated developer commands with bounded output and owned process cleanup.

This is an explicit execution boundary, not an OS sandbox. Agent planners never
receive this tool. Python and Node run reviewed workspace files, not shell text.
"""
from __future__ import annotations

import codecs
import copy
import os
from pathlib import Path
import runpy
import shutil
import signal
import subprocess
import sys
import threading
import time
from uuid import uuid4

from rainforge_native.windows_job import WindowsJob

MAX_OUTPUT = 256 * 1024
MAX_HISTORY = 20


def run_python_script(path: Path, arguments: list[str]):
    """Entrypoint used by the packaged worker's bundled Python interpreter."""
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    sys.stdin.readline()  # Parent assigns its process job before execution.
    sys.dont_write_bytecode = True
    sys.argv = [str(path), *arguments]
    sys.path.insert(0, str(path.parent))
    runpy.run_path(str(path), run_name='__main__')


class DeveloperTerminal:
    def __init__(self, workspace):
        self.workspace = workspace
        self.lock = threading.RLock()
        self.runs = {}
        self.current = None
        self.thread = None
        self.stopping = threading.Event()
        self.closed = False

    def _directory(self, directory):
        if not isinstance(directory, str) or len(directory) > 200 or '\\' in directory or ':' in directory:
            raise ValueError('Use a relative workspace directory with forward slashes')
        relative = Path(directory)
        if relative.is_absolute() or '..' in relative.parts or any(part.startswith('.') for part in relative.parts):
            raise ValueError('Directory must stay inside the coding workspace')
        root = self.workspace.root.resolve()
        target = root / relative
        cursor = root
        for part in relative.parts:
            cursor /= part
            if cursor.is_symlink() or (os.name == 'nt' and cursor.exists() and cursor.stat().st_file_attributes & 0x400):
                raise ValueError('Linked workspace directories are not supported')
        target = target.resolve()
        if not target.is_relative_to(root) or not target.is_dir():
            raise ValueError('Workspace directory does not exist')
        return target

    def _script(self, directory, argument, suffixes):
        if not isinstance(argument, str) or not argument or argument.startswith('-') or '\\' in argument or ':' in argument:
            raise ValueError('Choose a relative script path')
        relative = Path(argument)
        if relative.is_absolute() or '..' in relative.parts or any(part.startswith('.') for part in relative.parts):
            raise ValueError('Script must stay inside the selected workspace directory')
        target = directory / relative
        cursor = directory
        for part in relative.parts:
            cursor /= part
            if cursor.is_symlink() or (os.name == 'nt' and cursor.exists() and cursor.stat().st_file_attributes & 0x400):
                raise ValueError('Linked scripts are not supported')
        target = target.resolve()
        if not target.is_relative_to(directory) or not target.is_file() or target.suffix.lower() not in suffixes:
            raise ValueError('The selected script is unavailable or has an unsupported extension')
        return target

    def _command(self, tool, arguments, directory):
        if tool == 'python':
            if not arguments:
                raise ValueError('Python needs a workspace .py file as its first argument')
            path = self._script(directory, arguments[0], {'.py'})
            if getattr(sys, 'frozen', False):
                return [sys.executable, '--developer-python', str(path), *arguments[1:]]
            entrypoint = Path(__file__).resolve().parents[3] / 'scripts' / 'worker.py'
            return [sys.executable, str(entrypoint), '--developer-python', str(path), *arguments[1:]]
        executable = shutil.which(tool)
        if not executable or (os.name == 'nt' and Path(executable).suffix.casefold() != '.exe'):
            raise ValueError(tool + ' is not installed as a native executable')
        if tool == 'node':
            offset = 1 if arguments and arguments[0] == '--check' else 0
            if len(arguments) <= offset:
                raise ValueError('Node needs a workspace JavaScript file')
            self._script(directory, arguments[offset], {'.js', '.mjs', '.cjs'})
            return [executable, *arguments]
        if not arguments or arguments[0] not in {'status', 'diff', 'log', 'show', 'rev-parse'}:
            raise ValueError('Git console supports status, diff, log, show and rev-parse')
        if any(argument.startswith(('--output', '--ext-diff', '--textconv', '--exec')) for argument in arguments):
            raise ValueError('Git output redirection and external commands are not supported')
        flags = ['--no-ext-diff', '--no-textconv'] if arguments[0] in {'diff', 'show', 'log'} else []
        # Even a read-only status can invoke an untrusted repository's
        # configured fsmonitor hook. Disable it for every inspection command.
        return [executable, '-c', 'core.fsmonitor=false', '--no-pager',
                arguments[0], *flags, *arguments[1:]]

    def start(self, tool, args, directory='', allow_execute=False, timeout=300):
        if allow_execute is not True:
            raise ValueError('Review the command and explicitly allow this execution')
        if tool not in ('python', 'node', 'git'):
            raise ValueError('Choose Python, Node.js or Git')
        if not isinstance(args, list) or len(args) > 64 or any(not isinstance(value, str) or '\0' in value for value in args):
            raise ValueError('Arguments must be an array of at most 64 strings')
        if sum(len(value) for value in args) > 8192:
            raise ValueError('Command arguments exceed 8192 characters')
        if type(timeout) is not int or not 1 <= timeout <= 3600:
            raise ValueError('Execution timeout must be 1–3600 seconds')
        cwd = self._directory(directory)
        command = self._command(tool, args, cwd)
        with self.lock:
            if self.closed:
                raise RuntimeError('Developer console is closed')
            if self.thread and self.thread.is_alive():
                raise RuntimeError('A developer command is already running')
            self.stopping.clear()
            key = uuid4().hex
            record = {'id': key, 'tool': tool, 'args': args[:], 'directory': directory,
                      'state': 'starting', 'output': '', 'exit_code': None,
                      'elapsed_seconds': 0, 'timeout': timeout, 'output_limited': False,
                      'error': None, 'started_at': time.time()}
            self.runs[key] = record
            self.current = key
            while len(self.runs) > MAX_HISTORY:
                del self.runs[next(iter(self.runs))]
            self.thread = threading.Thread(target=self._run, args=(record, command, cwd), daemon=True)
            self.thread.start()
            return copy.deepcopy(record)

    def _run(self, record, command, cwd):
        started = time.monotonic()
        process = None
        job = None
        readers = []
        output_bytes = 0
        output_full = threading.Event()
        environment = {key: value for key, value in os.environ.items() if key.upper() in {
            'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA',
            'LANG', 'LC_ALL', 'PATHEXT', 'PROGRAMFILES', 'PROGRAMFILES(X86)', 'COMSPEC',
        }}
        environment.update(PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1',
                           GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0', GIT_PAGER='cat',
                           GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)

        def collect(stream):
            nonlocal output_bytes
            decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
            try:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        break
                    with self.lock:
                        remaining = max(0, MAX_OUTPUT - output_bytes)
                        output_bytes += len(chunk)
                        text = decoder.decode(chunk[:remaining])
                        if text:
                            record['output'] += text
                        if output_bytes > MAX_OUTPUT:
                            record['output_limited'] = True
                            output_full.set()
            finally:
                with self.lock:
                    record['output'] += decoder.decode(b'', final=True)
                stream.close()

        try:
            job = WindowsJob()
            process = subprocess.Popen(command, cwd=cwd, env=environment,
                                       stdin=subprocess.PIPE if record['tool'] == 'python' else subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                                       start_new_session=os.name != 'nt')
            job.assign(process)
            for stream in (process.stdout, process.stderr):
                reader = threading.Thread(target=collect, args=(stream,), daemon=True)
                readers.append(reader)
                reader.start()
            if record['tool'] == 'python':
                process.stdin.write(b'run\n')
                process.stdin.flush()
                process.stdin.close()
            with self.lock:
                record['state'] = 'running'
            while process.poll() is None:
                if self.stopping.is_set() or output_full.is_set() or time.monotonic() - started >= record['timeout']:
                    break
                time.sleep(.05)
            if process.poll() is None:
                if os.name != 'nt':
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    job.close()
                process.kill()
            process.wait(timeout=5)
            if os.name != 'nt':
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            job.close()  # Also stop descendants which outlived the command.
            for reader in readers:
                reader.join(timeout=3)
            with self.lock:
                record['exit_code'] = process.returncode
                record['state'] = ('cancelled' if self.stopping.is_set() else
                                   'output_limited' if output_full.is_set() else
                                   'timed_out' if time.monotonic() - started >= record['timeout'] else
                                   'completed' if process.returncode == 0 else 'failed')
        except Exception as error:
            with self.lock:
                record.update(state='failed', error=str(error)[:500])
        finally:
            if process and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if job:
                job.close()
            for reader in readers:
                reader.join(timeout=3)
            with self.lock:
                record['elapsed_seconds'] = round(time.monotonic() - started, 2)

    def status(self, run_id=None):
        with self.lock:
            if run_id is None:
                return {'runs': [{key: copy.deepcopy(value) for key, value in row.items() if key != 'output'}
                                 for row in reversed(list(self.runs.values()))]}
            if not isinstance(run_id, str) or run_id not in self.runs:
                raise ValueError('Developer command is not available in this session')
            record = copy.deepcopy(self.runs[run_id])
            if record['state'] in ('starting', 'running'):
                record['elapsed_seconds'] = round(time.time() - record['started_at'], 2)
            return record

    def cancel(self, run_id):
        with self.lock:
            if run_id != self.current or run_id not in self.runs:
                raise ValueError('Developer command is not active')
            if self.runs[run_id]['state'] in ('starting', 'running'):
                self.stopping.set()
            return self.status(run_id)

    def close(self):
        with self.lock:
            self.closed = True
            self.stopping.set()
            thread = self.thread
        if thread:
            thread.join(timeout=10)
