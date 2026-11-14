"""링크 입력칸에서 유튜브 검색: 검색어로 영상 목록을 받아 보여 주고, 누르면 바로 재생한다.

yt-dlp 의 "ytsearchN:검색어" 를 목록만(extract_flat) 받아 빠르게 가져온다. 로그인·API 키가 필요 없다.
"""
from __future__ import annotations

import threading
import urllib.request
from dataclasses import dataclass
from typing import Any

from classplayer.bootstrap import https_open
from PySide6.QtCore import QObject, Signal

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/131.0.0.0 Safari/537.36")


@dataclass
class SearchResult:
    video_id: str
    title: str
    channel: str = ""
    duration: float = 0.0
    views: int = 0
    is_live: bool = False

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"

    @property
    def thumb_url(self) -> str:
        return f"https://i.ytimg.com/vi/{self.video_id}/mqdefault.jpg"


def parse_entries(info: dict[str, Any]) -> list[SearchResult]:
    out: list[SearchResult] = []
    for e in info.get("entries") or []:
        if not e:
            continue
        vid = str(e.get("id") or "")
        # 채널·재생목록 결과는 빼고 영상만
        if not vid or len(vid) != 11 or e.get("ie_key") not in (None, "Youtube"):
            continue
        out.append(SearchResult(
            video_id=vid, title=str(e.get("title") or ""),
            channel=str(e.get("channel") or e.get("uploader") or ""),
            duration=float(e.get("duration") or 0.0), views=int(e.get("view_count") or 0),
            is_live=(e.get("live_status") == "is_live"),
        ))
    return out


def fmt_views(n: int) -> str:
    if n >= 100_000_000:
        return f"조회수 {n / 100_000_000:.1f}억회"
    if n >= 10_000:
        return f"조회수 {n / 10_000:.0f}만회" if n >= 100_000 else f"조회수 {n / 10_000:.1f}만회"
    if n >= 1000:
        return f"조회수 {n / 1000:.1f}천회"
    return f"조회수 {n}회" if n else ""


class YouTubeSearcher(QObject):
    results = Signal(str, object)        # 검색어, list[SearchResult]
    failed = Signal(str, str)            # 검색어, 오류
    thumb = Signal(str, bytes)           # video_id, jpg

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._gen = 0
        self._thumb_cache: dict[str, bytes] = {}

    def search(self, query: str, count: int = 20) -> None:
        self._gen += 1
        gen = self._gen

        def work() -> None:
            try:
                import yt_dlp

                opts = {"quiet": True, "no_warnings": True, "skip_download": True,
                        "extract_flat": "in_playlist", "socket_timeout": 15}
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(f"ytsearch{count}:{query}", download=False)
                res = parse_entries(info or {})
            except Exception as e:  # noqa: BLE001
                if gen == self._gen:
                    self.failed.emit(query, str(e).splitlines()[0] if str(e) else type(e).__name__)
                return
            if gen == self._gen:
                self.results.emit(query, res)
                self._load_thumbs(res, gen)

        threading.Thread(target=work, daemon=True).start()

    def _load_thumbs(self, res: list[SearchResult], gen: int) -> None:
        todo = list(res)
        lock = threading.Lock()

        def worker() -> None:
            while True:
                with lock:
                    if not todo or gen != self._gen:
                        return
                    r = todo.pop(0)
                data = self._thumb_cache.get(r.video_id)
                if data is None:
                    try:
                        req = urllib.request.Request(r.thumb_url, headers={"User-Agent": _UA})
                        with https_open(req, timeout=10) as resp:
                            data = resp.read()
                        self._thumb_cache[r.video_id] = data
                    except Exception:  # noqa: BLE001
                        continue
                if gen == self._gen:
                    self.thumb.emit(r.video_id, data)

        for _ in range(4):
            threading.Thread(target=worker, daemon=True).start()
