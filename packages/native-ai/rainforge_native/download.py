from __future__ import annotations
import hashlib
import http.client
import shutil
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


def sha256(path: Path, check_cancel=lambda: None):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            check_cancel()
            digest.update(block)
    return digest.hexdigest()


class DownloadCancelled(RuntimeError):
    pass


class _TransferInterrupted(RuntimeError):
    pass


class _OversizedPayload(ValueError):
    pass


def _open(url, offset):
    request = urllib.request.Request(url, headers={
        "Range": f"bytes={offset}-", "User-Agent": "RainForge/0.1",
    })
    try:
        return urllib.request.urlopen(request, timeout=30)
    except urllib.error.HTTPError as error:
        status = error.code
        host = urlsplit(error.geturl()).hostname or "model server"
        error.close()
        message = f"Model server {host} returned HTTP {status}"
        if status in (408, 500, 502, 503, 504):
            raise _TransferInterrupted(message) from None
        raise ValueError(message) from None
    except urllib.error.URLError as error:
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            raise ValueError("Model server TLS certificate could not be verified") from None
        raise _TransferInterrupted("Could not reach the model server") from None
    except (OSError, http.client.HTTPException):
        raise _TransferInterrupted("Connection to the model server was interrupted") from None


def _transfer(url, part, offset, size, progress, check_cancel):
    with _open(url, offset) as response:
        if response.status == 206:
            if response.headers.get("Content-Range") != f"bytes {offset}-{size - 1}/{size}":
                raise ValueError("Server returned an inconsistent byte range")
        elif response.status == 200:
            offset = 0
        else:
            raise ValueError(f"Unexpected HTTP status: {response.status}")
        declared = response.headers.get("Content-Length")
        if declared is not None and declared != str(size - offset):
            raise ValueError("Server returned an unexpected download size")
        with part.open("ab" if offset else "wb") as stream:
            while offset < size:
                check_cancel()
                try:
                    chunk = response.read(256 * 1024)
                except (OSError, http.client.HTTPException):
                    raise _TransferInterrupted("Connection interrupted during download") from None
                if not chunk:
                    raise _TransferInterrupted("Connection closed before the complete file arrived")
                if len(chunk) > size - offset:
                    raise _OversizedPayload("Download exceeds expected size")
                stream.write(chunk)
                offset += len(chunk)
                progress(offset, size)
            if declared is None:
                check_cancel()
                try:
                    extra = response.read(1)
                except (OSError, http.client.HTTPException):
                    # The advertised bytes are present and must pass the pinned hash.
                    extra = b""
                if extra:
                    raise _OversizedPayload("Download exceeds expected size")


def _check_disk(part, reusable_bytes, size):
    if shutil.disk_usage(part.parent).free + reusable_bytes < size + 64 * 1024 * 1024:
        raise ValueError("Not enough free disk space for the model download")


def download(url: str, target: Path, expected_hash: str, expected_bytes: int, progress=lambda *_: None, cancelled=lambda: False):
    """Resume pinned downloads and publish only files verified by length and SHA-256."""
    def check_cancel():
        if cancelled():
            raise DownloadCancelled("Download cancelled; partial file retained for resume")

    check_cancel()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size == expected_bytes and sha256(target, check_cancel) == expected_hash:
        progress(expected_bytes, expected_bytes)
        return target

    part = target.with_suffix(target.suffix + ".part")
    offset = part.stat().st_size if part.exists() else 0
    if offset > expected_bytes:
        part.unlink()
        offset = 0

    # A previous session's incomplete bytes may be corrupt. Allow one clean
    # retry after a bad hash, then fail instead of repeatedly fetching the file.
    repair_cached_partial = offset > 0
    interruptions = 0
    while True:
        check_cancel()
        if offset < expected_bytes:
            _check_disk(part, offset, expected_bytes)
            progress(offset, expected_bytes)
            try:
                _transfer(url, part, offset, expected_bytes, progress, check_cancel)
            except _OversizedPayload:
                part.unlink(missing_ok=True)
                raise
            except _TransferInterrupted as error:
                interruptions += 1
                if interruptions >= 3:
                    raise ValueError(f"{error}. Partial download kept for retry.") from None
                offset = part.stat().st_size if part.exists() else 0
                continue

        check_cancel()
        if sha256(part, check_cancel) == expected_hash:
            check_cancel()
            part.replace(target)
            progress(expected_bytes, expected_bytes)
            return target
        part.unlink()
        if not repair_cached_partial:
            raise ValueError("Download integrity check failed. Retry will fetch a fresh copy.")
        repair_cached_partial = False
        offset = 0
