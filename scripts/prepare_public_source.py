"""Export reviewed application source without local profiles, models or work logs."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import bootstrap

ROOT_FILES = {'README.md', 'LICENSE', '.gitignore', 'requirements-build.txt', 'pyproject.toml', 'package.json'}
SOURCE_FOLDERS = {'apps', 'packages', 'scripts', 'tests', '.github'}
PUBLIC_DOCS = {'USER_GUIDE.md', 'BUILD_GUIDE.md', 'SECURITY.md', 'WINDOWS_INSTALLER.md',
               'BROWSER_CHECKS.md', 'BROWSER_AGENT.md', 'SMARTLOAD_RESEARCH.md',
               'OFFLINE_ASSETS_NOTICE.txt', 'WEBSOCKET_CLIENT_LICENSE.txt', 'ECOSYSTEM.md',
               'WINDOWS_CORE.md'}
EXCLUDED = {'node_modules', '__pycache__', 'target', 'dist', 'binaries', '.data', '.git',
            'evidence', '.venv', 'cache', 'test-results', 'playwright-report'}
EXTENSIONS = {'.py', '.ts', '.tsx', '.js', '.mjs', '.json', '.toml', '.yaml', '.yml',
              '.md', '.txt', '.html', '.css', '.cs', '.rs', '.ps1', '.config', '.sh', '.svg'}


def prepare(output):
    root = bootstrap.ROOT.resolve()
    output = output.resolve()
    if output.exists():
        raise ValueError('Output already exists. Choose a new source snapshot directory.')
    if output == root or not output.is_relative_to(root / 'outputs' / 'publication'):
        raise ValueError('Use a new directory under outputs/publication.')
    paths = subprocess.check_output(
        ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=root
    ).decode('utf-8').split('\0')
    selected = []
    for name in sorted(set(filter(None, paths))):
        relative = Path(name)
        if any(part in EXCLUDED or part.startswith('.env') for part in relative.parts):
            continue
        allowed = name in ROOT_FILES or (relative.parts[0] in SOURCE_FOLDERS and relative.suffix in EXTENSIONS)
        allowed = allowed or (name == 'runtime/catalog.json')
        allowed = allowed or (len(relative.parts) == 2 and relative.parts[0] == 'docs' and relative.name in PUBLIC_DOCS)
        if not allowed:
            continue
        source = root / relative
        if source.is_symlink() or not source.resolve().is_relative_to(root) or not source.is_file():
            raise ValueError('Source export requires regular repository files.')
        if source.stat().st_size > 5 * 1024 * 1024:
            raise ValueError('Source export contains an unexpected large file.')
        data = source.read_bytes()
        selected.append((relative, data, hashlib.sha256(data).hexdigest()))
    attributes = b'* -text\n'
    selected.append((Path('.gitattributes'), attributes, hashlib.sha256(attributes).hexdigest()))
    output.mkdir(parents=True)
    for relative, data, _ in selected:
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    manifest = {'format': 'rainforge.public-source.v1',
                'files': [{'path':p.as_posix(), 'bytes':len(data), 'sha256':digest} for p,data,digest in selected]}
    (output/'SOURCE_MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    archive = shutil.make_archive(str(output), 'zip', root_dir=output)
    print(json.dumps({'directory':str(output), 'archive':archive, 'files':len(selected),
                      'sha256':hashlib.sha256(Path(archive).read_bytes()).hexdigest()}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    prepare(parser.parse_args().output)

