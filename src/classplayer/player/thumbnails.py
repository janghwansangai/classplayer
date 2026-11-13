"""탐색 슬라이더 미리보기 썸네일.

로컬 파일: ffmpeg 로 키프레임만 빠르게 뽑아 작은 JPG 로 저장한다.
유튜브: yt-dlp 가 주는 스토리보드(타일 시트 이미지)를 필요한 장만 내려받아 잘라 쓴다.
"""
from __future__ import annotations

import hashlib
import subprocess
import threading
import urllib.request
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QStandardPaths, Signal
from PySide6.QtGui import QPixmap

THUMB_W = 256


class ThumbnailProvider(QObject):
    ready = Signal()

    def __init__(self, ffmpeg: str | None, parent=None) -> None:
        super().__init__(parent)
        self.ffmpeg = ffmpeg
        self._gen = 0
        self._lock = threading.Lock()
        # 로컬
        self._dir: Path | None = None
        self._interval = 0.0
        self._count = 0
        self._cache: dict[int, QPixmap] = {}
        # 스토리보드
        self._sb: dict[str, Any] | None = None
        self._sheets: dict[int, QPixmap | None] = {}
        self._fetching: set[int] = set()
        base = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation) or "/tmp")
        self._root = base / "thumbs"
        self._root.mkdir(parents=True, exist_ok=True)

    def clear(self) -> None:
        self._gen += 1
        self._dir = None
        self._count = 0
        self._cache.clear()
        self._sb = None
        self._sheets.clear()
        self._fetching.clear()

    # ------------------------------------------------------------------ 로컬 파일
    def load_local(self, path: str, duration: float) -> None:
        self.clear()
        if not self.ffmpeg or duration <= 0:
            return
        gen = self._gen
        interval = max(1.0, min(10.0, duration / 150.0))
        key = hashlib.sha1(f"{path}|{Path(path).stat().st_mtime}|{interval:.2f}".encode()).hexdigest()[:16]
        out = self._root / key
        self._dir, self._interval = out, interval

        def work() -> None:
            if not (out / "done").exists():
                out.mkdir(parents=True, exist_ok=True)
                cmd = [self.ffmpeg, "-y", "-v", "error", "-skip_frame", "nokey", "-i", path,
                       "-vf", f"fps=1/{interval},scale={THUMB_W}:-2", "-fps_mode", "vfr", "-q:v", "6",
                       str(out / "%05d.jpg")]
                try:
                    subprocess.run(cmd, capture_output=True, timeout=600, check=False)
                    (out / "done").touch()
                except Exception:  # noqa: BLE001
                    return
            if gen == self._gen:
                self._count = len(list(out.glob("*.jpg")))
                self.ready.emit()

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ 유튜브 스토리보드
    def load_storyboard(self, formats: list[dict[str, Any]]) -> None:
        self.clear()
        sbs = [f for f in formats if str(f.get("format_id", "")).startswith("sb") and f.get("fragments")]
        if not sbs:
            return
        # 타일 폭이 150~250 쯤인 것을 고른다 (보통 sb1)
        sbs.sort(key=lambda f: abs(float(f.get("width") or 0) - 200))
        sb = sbs[0]
        frags = []
        t = 0.0
        for fr in sb["fragments"]:
            d = float(fr.get("duration") or 0)
            frags.append((t, d, str(fr.get("url", ""))))
            t += d
        self._sb = {"rows": int(sb.get("rows") or 1), "cols": int(sb.get("columns") or 1),
                    "w": int(sb.get("width") or 0), "h": int(sb.get("height") or 0),
                    "fps": float(sb.get("fps") or 0), "frags": frags}

    def _fetch_sheet(self, idx: int, url: str) -> None:
        gen = self._gen

        def work() -> None:
            data = b""
            try:
                with urllib.request.urlopen(url, timeout=10) as r:
                    data = r.read()
            except Exception:  # noqa: BLE001
                pass
            if gen != self._gen:
                return
            with self._lock:
                self._fetching.discard(idx)
            pm = QPixmap()
            if data:
                pm.loadFromData(data)
            self._sheets[idx] = pm if not pm.isNull() else None
            self.ready.emit()

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ 조회
    def thumbnail(self, t: float) -> QPixmap | None:
        if self._sb:
            return self._from_storyboard(t)
        if self._dir and self._count:
            idx = max(1, min(self._count, int(t / self._interval) + 1))
            pm = self._cache.get(idx)
            if pm is None:
                pm = QPixmap(str(self._dir / f"{idx:05d}.jpg"))
                if pm.isNull():
                    return None
                self._cache[idx] = pm
            return pm
        return None

    def _from_storyboard(self, t: float) -> QPixmap | None:
        sb = self._sb
        assert sb is not None
        frags = sb["frags"]
        fi = None
        for i, (start, d, _u) in enumerate(frags):
            if start <= t < start + d or i == len(frags) - 1:
                fi = i
                break
        if fi is None:
            return None
        start, d, url = frags[fi]
        sheet = self._sheets.get(fi, "missing")
        if sheet == "missing":
            with self._lock:
                if fi not in self._fetching:
                    self._fetching.add(fi)
                    self._fetch_sheet(fi, url)
            return None
        if sheet is None:
            return None
        per_sheet = sb["rows"] * sb["cols"]
        fps = sb["fps"] if sb["fps"] > 0 else per_sheet / max(d, 1e-6)
        j = int((t - start) * fps)
        j = max(0, min(per_sheet - 1, j))
        tw = sheet.width() // sb["cols"]
        th = sheet.height() // sb["rows"]
        if tw <= 0 or th <= 0:
            return None
        return sheet.copy((j % sb["cols"]) * tw, (j // sb["cols"]) * th, tw, th)
