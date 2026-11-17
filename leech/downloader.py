"""Optional aria2c downloads, Python streaming fallback and file splitting."""
import http.client
import os
import re
import shutil
import subprocess
import time
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path
from .utils import emit, log_line, fmt_size
UPDATE_EVERY = 3.0


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        def origin(url):
            parsed = urlsplit(url)
            return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)
        if redirected and origin(req.full_url) != origin(newurl):
            for key in list(redirected.headers):
                if key.lower() in ('authorization', 'cookie', 'proxy-authorization'):
                    del redirected.headers[key]
        return redirected


def _limit(name, default):
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# --- aria2c -------------------------------------------------------------------
def aria2c_path():
    """Find an optional installed binary; never install system packages."""
    return shutil.which("aria2c")


def progress_of(line):
    """Safe one-liner from an aria2c line, or None. Never returns the raw line."""
    m = re.search(r"(\d{1,3})%", line)
    if m:
        return f"downloading... {m.group(1)}%"
    m = re.search(r"DL:([\d.]+\w+)(?:\s+ETA:(\S+))?", line)
    if m:
        return f"downloading at {m.group(1)}/s"
    return None


def download(url, out_dir, name, headers, jobs, on_line=None):
    """Use the bounded Python backend for every download."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / name
    return download_python(url, out_dir, name, headers, on_line)


# --- splitting ----------------------------------------------------------------
def split_file(path, split_size, on_line=None):
    """Split `path` into <name>.001, .002 ... (returns the file list)."""
    path = Path(path)
    size = path.stat().st_size
    if split_size <= 0 or size <= split_size:
        return [str(path)]
    emit(f"splitting {fmt_size(size)} into {fmt_size(split_size)} parts", on_line)
    parts = []
    try:
        with path.open('rb') as source:
            remaining = size
            while remaining:
                part = Path(f'{path}.{len(parts) + 1:03d}')
                with part.open('xb') as output:
                    parts.append(str(part))
                    budget = min(split_size, remaining)
                    while budget:
                        chunk = source.read(min(1024 * 1024, budget))
                        if not chunk:
                            raise OSError('source ended during split')
                        output.write(chunk)
                        remaining -= len(chunk)
                        budget -= len(chunk)
    except BaseException:
        for part in parts:
            Path(part).unlink(missing_ok=True)
        raise
    path.unlink()
    return parts


def download_python(url, out_dir, name, headers, on_line=None):
    """Stream into a local partial file with bounded memory usage."""
    if not name or Path(name).name != name or name in ('.', '..'):
        raise RuntimeError('invalid download filename')
    if not url.lower().startswith(('http://', 'https://', 'ftp://')):
        raise RuntimeError('Python supports HTTP, HTTPS and FTP URLs; magnet needs aria2c')
    target = Path(out_dir) / name
    partial = target.with_name(target.name + '.part')
    maximum = _limit('LEECH_MAX_BYTES', 512 * 1024 * 1024)
    reserve = _limit('LEECH_MIN_FREE_BYTES', 128 * 1024 * 1024)
    duration = _limit('LEECH_DOWNLOAD_TIMEOUT', 1800)
    deadline = time.monotonic() + duration
    if shutil.disk_usage(out_dir)[2] < reserve:
        raise RuntimeError('insufficient free space')
    if target.exists():
        raise RuntimeError('download target already exists')
    request_headers = {'User-Agent': UA}
    for header in headers:
        key, separator, value = header.partition(':')
        if not separator or not key.strip():
            raise RuntimeError('invalid download header')
        request_headers[key.strip()] = value.strip()
    request = urllib.request.Request(url, headers=request_headers)
    owned = False
    try:
        opener = urllib.request.build_opener(SafeRedirect())
        with opener.open(request, timeout=min(60, duration)) as response:
            length = response.headers.get('Content-Length')
            total = int(length) if length else None
            if total is not None and total > maximum:
                raise RuntimeError('maximum download size exceeded')
            if total is not None and shutil.disk_usage(out_dir)[2] < total + reserve:
                raise RuntimeError('insufficient free space')
            received = 0
            last = 0.0
            with partial.open('xb') as output:
                owned = True
                emit('python: downloading', on_line)
                while chunk := response.read(1024 * 1024):
                    if time.monotonic() >= deadline:
                        raise RuntimeError('download timed out')
                    if received + len(chunk) > maximum:
                        raise RuntimeError('maximum download size exceeded')
                    if shutil.disk_usage(out_dir)[2] < reserve + len(chunk):
                        raise RuntimeError('insufficient free space')
                    output.write(chunk)
                    received += len(chunk)
                    if time.monotonic() - last >= UPDATE_EVERY:
                        last = time.monotonic()
                        text = (f'downloading... {received * 100 // total}%' if total
                                else f'downloaded {fmt_size(received)}')
                        emit(text, on_line)
            if total is not None and received != total:
                raise RuntimeError('incomplete download')
        partial.rename(target)
        owned = False
    except (OSError, ValueError, http.client.HTTPException):
        raise RuntimeError('Python download failed (network, HTTP or storage error)') from None
    finally:
        if owned:
            partial.unlink(missing_ok=True)
    return [str(target)]
