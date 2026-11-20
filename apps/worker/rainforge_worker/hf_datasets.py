"""Public HF dataset files only; pinned revisions, bounded downloads, no dataset scripts."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import urllib.request
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit

from rainforge_recovery import atomic_json

LIMIT = 1024 ** 3


def _trusted_url(url):
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('Only official HTTPS Hugging Face downloads are supported')
    if host != 'huggingface.co' and not host.endswith(('.huggingface.co', '.hf.co', '.xethub.hf.co')):
        raise ValueError('Download redirect is outside supported Hugging Face infrastructure')


class TrustedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _trusted_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url, headers=None):
    _trusted_url(url)
    request = urllib.request.Request(url, headers={'User-Agent': 'RainForge-NEXUS/0.1', **(headers or {})})
    return urllib.request.build_opener(TrustedRedirect()).open(request, timeout=25)


def describe(repo, revision='main'):
    if not isinstance(repo, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', repo):
        raise ValueError('Enter a Hugging Face dataset ID: owner/name')
    if not isinstance(revision, str) or not (revision == 'main' or re.fullmatch(r'[0-9a-f]{40}', revision)):
        raise ValueError('Use main or a full 40-character commit revision')
    with _open('https://huggingface.co/api/datasets/' + repo + '/revision/' + revision + '?blobs=true') as response:
        raw = response.read(2 * 1024 ** 2 + 1)
        if len(raw) > 2 * 1024 ** 2:
            raise ValueError('Repository metadata exceeds 2 MiB')
        data = json.loads(raw)
    sha = data.get('sha', '')
    if not re.fullmatch(r'[0-9a-f]{40}', sha) or (revision != 'main' and sha != revision):
        raise ValueError('Repository revision could not be pinned')
    if data.get('private') or data.get('gated'):
        raise ValueError('This downloader supports public, non-gated datasets only')
    card = data.get('cardData') or {}
    license = card.get('license', 'unknown')
    if not isinstance(license, str):
        license = json.dumps(license, ensure_ascii=False)
    files = []
    for row in data.get('siblings', []):
        name, size = row.get('rfilename', ''), row.get('size')
        path = PurePosixPath(name)
        if path.is_absolute() or '\\' in name or any(part in ('.', '..') for part in path.parts):
            continue
        if path.suffix.lower() not in ('.jsonl', '.csv') or type(size) is not int or not 1 <= size <= LIMIT:
            continue
        lfs = row.get('lfs') or {}
        expected = lfs.get('sha256')
        kind = 'sha256'
        if not expected:
            kind, expected = 'git-sha1', row.get('blobId', '')
        if not re.fullmatch(r'[0-9a-f]{64}' if kind == 'sha256' else r'[0-9a-f]{40}', expected):
            continue
        files.append({'name': name, 'bytes': size, 'hash': expected, 'hash_kind': kind})
        if len(files) >= 200:
            break
    return {'repo': repo, 'revision': sha, 'license': license[:200], 'files': files,
            'url': 'https://huggingface.co/datasets/' + repo + '/tree/' + sha,
            'note': 'First 200 supported CSV/JSONL files up to 1 GiB; Parquet and dataset scripts are not downloaded'}


def verify(path, item):
    if path.stat().st_size != item['bytes']:
        return False
    hasher = hashlib.sha256() if item['hash_kind'] == 'sha256' else hashlib.sha1()
    if item['hash_kind'] == 'git-sha1':
        hasher.update(('blob ' + str(item['bytes']) + '\0').encode('ascii'))
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 ** 2), b''):
            hasher.update(block)
    return hasher.hexdigest() == item['hash']


class HFDatasets:
    def __init__(self, datasets):
        self.root = datasets.root / 'inputs'
        self.root.mkdir(exist_ok=True)
        self.path = datasets.root / 'hf-download.json'
        self.guard = threading.RLock()
        self.cancelled = threading.Event()
        self.thread = None
        self.state = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'status': 'IDLE'}
        if self.state['status'] in ('RESOLVING', 'DOWNLOADING', 'VERIFYING'):
            self.state.update(status='INTERRUPTED', error='Worker stopped; retry to resume the pinned file')
            atomic_json(self.path, self.state)

    def status(self):
        with self.guard:
            return dict(self.state)

    def _update(self, **changes):
        with self.guard:
            self.state = {**self.state, **changes}
            atomic_json(self.path, self.state)

    def start(self, repo, revision, filename, accept_license=False):
        if accept_license is not True:
            raise ValueError('Review the dataset license and explicitly allow the download')
        if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision):
            raise ValueError('Inspect the dataset first to pin its revision')
        if not isinstance(filename, str) or not 1 <= len(filename) <= 1000:
            raise ValueError('Select a supported dataset file')
        # Validate ID before allocating a background operation.
        if not isinstance(repo, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', repo):
            raise ValueError('Invalid dataset ID')
        with self.guard:
            if self.thread and self.thread.is_alive():
                raise ValueError('A Hugging Face download is already running')
            self.cancelled.clear()
            self.state = {'status': 'RESOLVING', 'repo': repo, 'revision': revision, 'filename': filename,
                          'downloaded': 0, 'total': 0, 'error': None, 'source': None}
            atomic_json(self.path, self.state)
            self.thread = threading.Thread(target=self._run, args=(repo, revision, filename), daemon=True)
            self.thread.start()
            return dict(self.state)

    def cancel(self):
        self.cancelled.set()
        return {'requested': True}

    def _check(self):
        if self.cancelled.is_set():
            raise InterruptedError('Download cancelled; partial bytes retained for resume')

    def _run(self, repo, revision, filename):
        try:
            info = describe(repo, revision)
            self._check()
            item = next((f for f in info['files'] if f['name'] == filename), None)
            if not item:
                raise ValueError('File is not supported or has no trusted integrity metadata')
            key = hashlib.sha256((repo + '@' + revision + '/' + filename).encode()).hexdigest()
            target = self.root / ('hf-' + key + PurePosixPath(filename).suffix.lower())
            part = target.with_suffix(target.suffix + '.part')
            self._update(status='DOWNLOADING', total=item['bytes'], license=info['license'], provenance=info['url'] + '/' + quote(filename, safe='/'))
            if not (target.exists() and verify(target, item)):
                offset = part.stat().st_size if part.exists() else 0
                if offset > item['bytes']:
                    raise ValueError('Partial file exceeds expected size; retain for inspection')
                if shutil.disk_usage(self.root).free < item['bytes'] - offset + 64 * 1024 ** 2:
                    raise ValueError('Insufficient free disk space')
                self._update(downloaded=offset)
                if offset < item['bytes']:
                    url = 'https://huggingface.co/datasets/' + repo + '/resolve/' + revision + '/' + quote(filename, safe='/')
                    with _open(url, {'Range': f'bytes={offset}-', 'Accept-Encoding': 'identity'}) as response:
                        if response.status == 206:
                            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                            if not match or tuple(map(int, match.groups())) != (offset, item['bytes'] - 1, item['bytes']):
                                raise ValueError('Inconsistent server byte range')
                        elif response.status == 200:
                            offset = 0
                        else:
                            raise ValueError('Unexpected download HTTP status')
                        with part.open('ab' if offset else 'wb') as output:
                            last_report = offset
                            while True:
                                self._check()
                                block = response.read(64 * 1024)
                                if not block:
                                    break
                                offset += len(block)
                                if offset > item['bytes']:
                                    raise ValueError('Download exceeds metadata size')
                                output.write(block)
                                if offset - last_report >= 1024 ** 2:
                                    self._update(downloaded=offset)
                                    last_report = offset
                            output.flush()
                            os.fsync(output.fileno())
                self._check()
                self._update(status='VERIFYING', downloaded=offset)
                if not verify(part, item):
                    raise ValueError('Downloaded file hash does not match pinned repository metadata')
                os.replace(part, target)
            self._check()
            sha = hashlib.sha256()
            with target.open('rb') as source:
                for block in iter(lambda: source.read(1024 ** 2), b''):
                    self._check()
                    sha.update(block)
            self._update(status='COMPLETED', source=str(target.resolve()), downloaded=item['bytes'], sha256=sha.hexdigest(),
                         repository_hash=item['hash'], hash_kind=item['hash_kind'])
        except InterruptedError as exc:
            self._update(status='CANCELLED', error=str(exc))
        except Exception as exc:
            self._update(status='FAILED', error=f'{type(exc).__name__}: {exc}')

    def close(self):
        self.cancel()
        if self.thread:
            self.thread.join(timeout=2)
