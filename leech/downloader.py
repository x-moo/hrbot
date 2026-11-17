"""Optional aria2c downloads, Python streaming fallback and file splitting."""
import http.client
import os
import re
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from .utils import emit, log_line, fmt_size
UPDATE_EVERY = 3.0
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
    """Fetch with installed aria2c, otherwise stream using Python."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / name
    backend = aria2c_path()
    if not backend:
        return download_python(url, out_dir, name, headers, on_line)
    cmd = [backend, "-x", str(jobs), "-s", str(jobs), "-k", "1M",
           "--file-allocation=none", "--summary-interval=5",
           "--console-log-level=warn", "--allow-overwrite=false",
           "--auto-file-renaming=false", "--user-agent", UA,
           "-d", str(out_dir), "-o", name]
    for h in headers:
        cmd += ["--header", h]
    cmd.append(url)

    log_line("aria2c: downloading")
    last = ""
    sent = 0.0
    with subprocess.Popen(cmd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True,
                          errors="replace", bufsize=1) as proc:
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            summary = progress_of(line)
            if not summary:
                log_line(f"aria2c: {line}")     # only warn-level output remains
                continue
            # one line every UPDATE_EVERY seconds to both the log and the chat
            if time.time() - sent >= UPDATE_EVERY:
                sent = time.time()
                if summary != last:
                    log_line(f"aria2c: {summary}")
                    last = summary
                if on_line:
                    on_line(summary)
    if proc.returncode != 0 or not target.exists():
        raise SystemExit(f"aria2c failed (exit {proc.returncode})")
    return [str(target)]


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
        with urllib.request.urlopen(request, timeout=60) as response:
            length = response.headers.get('Content-Length')
            total = int(length) if length else None
            received = 0
            last = 0.0
            with partial.open('xb') as output:
                owned = True
                emit('python: downloading', on_line)
                while chunk := response.read(1024 * 1024):
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
