"""aria2c downloads and local file splitting."""
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from .utils import emit, log_line, fmt_size
UPDATE_EVERY = 3.0
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# --- aria2c -------------------------------------------------------------------
def aria2c_path():
    """Path to aria2c, installing it once with the system package manager."""
    found = shutil.which("aria2c")
    if found:
        return found
    if os.geteuid() == 0:
        for cmd in (["apt-get", "-y", "install", "aria2"],
                    ["apk", "add", "aria2"],
                    ["dnf", "-y", "install", "aria2"]):
            if not shutil.which(cmd[0]):
                continue
            log_line(f"aria2c missing — installing with {cmd[0]}")
            try:
                subprocess.run(cmd, capture_output=True, timeout=300)
            except (OSError, subprocess.TimeoutExpired):
                continue
            if (found := shutil.which("aria2c")):
                return found
    raise SystemExit("aria2c is required and not installed (apt install aria2)")


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
    """Fetch `url` into out_dir with aria2c. -> list of produced file paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / name
    cmd = [aria2c_path(), "-x", str(jobs), "-s", str(jobs), "-k", "1M",
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
    rc = subprocess.call(["split", "--numeric-suffixes=1", "--suffix-length=3",
                          f"--bytes={split_size}", str(path), f"{path}."])
    if rc != 0:
        raise SystemExit("split failed")
    parts = sorted(str(p) for p in path.parent.glob(path.name + ".[0-9][0-9][0-9]"))
    if not parts:
        raise SystemExit("split produced no parts")
    path.unlink()               # originals go away once the parts exist
    return parts
