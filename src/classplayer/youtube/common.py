"""URL 판별과 유튜브 영상 ID 추출."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"}
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def is_url(text: str) -> bool:
    t = text.strip().lower()
    return t.startswith(("http://", "https://", "rtsp://", "rtmp://"))


def is_youtube_url(text: str) -> bool:
    try:
        return urlparse(text.strip()).netloc.lower() in _YT_HOSTS
    except ValueError:
        return False


def youtube_video_id(text: str) -> str | None:
    """watch?v=, youtu.be/, shorts/, live/, embed/ 형태에서 11자리 영상 ID를 뽑는다."""
    try:
        u = urlparse(text.strip())
    except ValueError:
        return None
    host = u.netloc.lower()
    if host not in _YT_HOSTS:
        return None
    if host.endswith("youtu.be"):
        cand = u.path.strip("/").split("/")[0]
        return cand if _ID_RE.match(cand) else None
    qs = parse_qs(u.query)
    if "v" in qs and _ID_RE.match(qs["v"][0]):
        return qs["v"][0]
    parts = [p for p in u.path.split("/") if p]
    if len(parts) >= 2 and parts[0] in ("shorts", "live", "embed", "v"):
        return parts[1] if _ID_RE.match(parts[1]) else None
    return None


def start_seconds(url: str) -> float:
    """유튜브 링크의 시작 시각(t=90, t=1m30s, start=90)을 초로. 없으면 0."""
    import re
    import urllib.parse

    try:
        u = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(u.query)
        frag = urllib.parse.parse_qs(u.fragment)
    except ValueError:
        return 0.0
    raw = (q.get("t") or q.get("start") or frag.get("t") or [""])[0].strip().lower()
    if not raw:
        return 0.0
    if raw.isdigit():
        return float(raw)
    total = 0.0
    for num, unit in re.findall(r"(\d+)([hms])", raw):
        total += int(num) * {"h": 3600, "m": 60, "s": 1}[unit]
    return total
