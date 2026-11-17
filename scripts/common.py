#!/usr/bin/env python3
"""Shared helpers for the haramain.info media-archive pipeline.

The media regexes, host exclusions and URL-hygiene rules live here so every
stage uses one definition.
"""
import re

AUDIO_EXTS = {"mp3", "wma", "m4a", "ogg", "oga", "aac", "flac", "wav", "ra", "ram"}
VIDEO_EXTS = {"wmv", "asf", "rm", "mp4", "3gp", "mkv", "avi", "swf"}
MEDIA_EXTS = AUDIO_EXTS | VIDEO_EXTS

# HTML attribute delimiters + whitespace + angle brackets, as a char-class body
DISALLOWED = "\"'<>\\s"

MEDIA_RE = re.compile(
    r"https?://[^" + DISALLOWED + r"]+?\."
    r"(?:mp3|wma|m4a|ogg|oga|aac|flac|wav|ra|ram"      # audio
    r"|wmv|asf|rm|mp4|3gp|mkv|avi|swf)"                 # video
    r"(?:\?[^" + DISALLOWED + r"]*)?",
    re.I,
)

# Excluded hosts (requirement: no YouTube/Google).
BAD_HOST = re.compile(r"youtube|youtu\.be|google|gstatic|ytimg", re.I)
YMD_RE = re.compile(r"(\d{4})/(\d{2})/")
DEFAULT_SITE = "http://www.haramain.info"


def year_of(url):
    """Year of a /YYYY/MM/... post URL, or None."""
    m = YMD_RE.search(url)
    return int(m.group(1)) if m else None


def host_of(url):
    """Hostname without path."""
    return re.sub(r"^https?://([^/]+)/.*$", r"\1", url)


def unglue(url):
    """Split two URLs jammed together with no separator (broken HTML/href).

    A second scheme NOT preceded by a query/fragment separator means the
    source page concatenated two hrefs; keep the piece that looks like media.
    """
    matches = list(re.finditer(r"https?://", url, re.I))
    if len(matches) <= 1:
        return url
    parts, prev = [], 0
    for m in matches:
        if m.start() == 0:
            continue
        if url[m.start() - 1] in "?&=#":     # legit query param: &link=http://
            continue
        parts.append(url[prev:m.start()])
        prev = m.start()
    parts.append(url[prev:])
    media = [p for p in parts if ext_of(p) in MEDIA_EXTS]
    return media[0] if media else max(parts, key=len)


def ext_of(url):
    """Lowercase extension of the path (query string ignored), '' if none."""
    path = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    name = path.rsplit("/", 1)[-1].lower()
    return name.rsplit(".", 1)[1] if "." in name else ""


def add_unique(url, bucket, seen):
    if url not in seen:
        seen.add(url)
        bucket.append(url)


def classify(url, audio, video, seen_audio, seen_video):
    """Route one raw URL into audio/video buckets, applying all exclusions.

    audio/video are lists (order preserved); seen_* the matching sets.
    Mutates in place. Handles excluded hosts, glued hrefs, literal spaces
    from broken src= attrs, and nested player payloads (`?theFile=<url>`).
    """
    url = unglue(url.strip())
    if not url or BAD_HOST.search(url):
        return

    # nested payload: a player wrapper like divshare.com/flash/x.swf?theFile=<url>
    # — the wrapper itself is not media, so unwrap and stop when it yields one.
    if "theFile=" in url:
        m = MEDIA_RE.search(url.split("theFile=", 1)[1])
        if m:
            inner = m.group(0).rstrip('.,;"')
            ext = ext_of(inner)
            if not BAD_HOST.search(inner) and ext in MEDIA_EXTS:
                bucket, seen = ((audio, seen_audio) if ext in AUDIO_EXTS
                                else (video, seen_video))
                add_unique(inner, bucket, seen)
                return

    if re.search(r"\s", url):          # literal spaces from broken src= attrs
        url = re.sub(r"\s+", "%20", url)

    ext = ext_of(url)
    if ext in AUDIO_EXTS:
        add_unique(url, audio, seen_audio)
    elif ext in VIDEO_EXTS:
        add_unique(url, video, seen_video)


def extract_media(body_html):
    """All media URLs in a decoded HTML string (deduped, exclusions applied)."""
    audio, video = [], []
    seen_a, seen_v = set(), set()
    for m in MEDIA_RE.findall(body_html):
        classify(m.rstrip('.,;"'), audio, video, seen_a, seen_v)
    return audio, video


def read_lines(path):
    """Non-empty stripped lines of a text file ([] if missing)."""
    try:
        with open(path) as f:
            return [l.strip() for l in f if l.strip()]
    except FileNotFoundError:
        return []
