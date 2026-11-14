"""SponsorBlock 공개 데이터로 영상 안의 협찬/광고 구간을 건너뛴다."""
from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass

from classplayer.bootstrap import https_open
from PySide6.QtCore import QObject, Signal

API = "https://sponsor.ajay.app/api/skipSegments"
CATEGORY_LABELS = {
    "sponsor": "협찬 광고",
    "selfpromo": "자체 홍보",
    "interaction": "구독/좋아요 요청",
    "intro": "인트로",
    "outro": "아웃트로",
    "preview": "미리보기",
    "music_offtopic": "비음악 구간",
    "filler": "잡담",
}


@dataclass
class Segment:
    start: float
    end: float
    category: str


def fetch_segments(video_id: str, categories: list[str], timeout: float = 6.0) -> list[Segment]:
    q = urllib.parse.urlencode({"videoID": video_id, "categories": json.dumps(categories)})
    req = urllib.request.Request(f"{API}?{q}", headers={"User-Agent": "ClassPlayer/0.1"})
    try:
        with https_open(req, timeout=timeout) as r:
            if r.status == 404:
                return []
            data = json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001  (404 포함, 네트워크 오류는 조용히 무시)
        return []
    segs = []
    for item in data:
        try:
            s, e = item["segment"]
            segs.append(Segment(float(s), float(e), str(item.get("category", "sponsor"))))
        except (KeyError, ValueError, TypeError):
            continue
    segs.sort(key=lambda x: x.start)
    return segs


class SponsorSkipper(QObject):
    """재생 위치를 보고 있다가 구간 안에 들어오면 끝으로 건너뛴다."""

    segments_loaded = Signal(str, int)      # video_id, count
    skipped = Signal(str, float)            # category label, seconds skipped
    request_seek = Signal(float)

    def __init__(self, categories: list[str], parent=None) -> None:
        super().__init__(parent)
        self.categories = list(categories)
        self.enabled = True
        self._video_id: str | None = None
        self._segments: list[Segment] = []
        self._lock = threading.Lock()
        self._last_skip_end = -1.0

    def clear(self) -> None:
        with self._lock:
            self._video_id = None
            self._segments = []
        self._last_skip_end = -1.0

    def load(self, video_id: str) -> None:
        self.clear()
        self._video_id = video_id

        def work() -> None:
            segs = fetch_segments(video_id, self.categories)
            with self._lock:
                if self._video_id == video_id:
                    self._segments = segs
            self.segments_loaded.emit(video_id, len(segs))

        threading.Thread(target=work, daemon=True).start()

    def on_position(self, t: float) -> None:
        if not self.enabled:
            return
        with self._lock:
            segs = self._segments
        for s in segs:
            if s.start <= t < s.end - 0.2:
                if abs(s.end - self._last_skip_end) < 0.01 and t < s.end:
                    return  # 방금 건너뛴 구간: 중복 방지
                self._last_skip_end = s.end
                self.request_seek.emit(s.end)
                self.skipped.emit(CATEGORY_LABELS.get(s.category, s.category), s.end - s.start)
                return
