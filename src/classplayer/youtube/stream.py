"""유튜브 링크를 앱 안에서 yt-dlp로 풀어 재생 주소를 얻는다.

mpv 내장 ytdl 훅 대신 이 경로를 기본으로 쓰는 이유: 다운로드 기능과 같은 yt-dlp 설정(JS 런타임,
나중에 PO 토큰 플러그인)을 그대로 쓰고, 제목·자막·영상 ID를 바로 알 수 있어서다.
"""
from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QObject, Signal

SUB_LANGS = ("ko", "en")


@dataclass
class ResolvedStream:
    url: str
    title: str = ""
    video_url: str = ""
    audio_url: str = ""
    user_agent: str = ""
    duration: float = 0.0
    video_id: str = ""
    width: int = 0
    height: int = 0
    subtitles: list[tuple[str, str, str]] = field(default_factory=list)   # (lang, 이름, url)
    storyboards: list[dict[str, Any]] = field(default_factory=list)


def _pick_sub_url(entry: dict[str, Any] | None) -> str:
    if not entry:
        return ""
    for pref in ("vtt", "srt", "ttml"):
        for item in entry:
            if item.get("ext") == pref and item.get("url"):
                return str(item["url"])
    for item in entry:
        if item.get("url"):
            return str(item["url"])
    return ""


def resolve_info(info: dict[str, Any], url: str) -> ResolvedStream:
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise RuntimeError("재생목록에 재생할 영상이 없습니다")
        info = entries[0]
    rs = ResolvedStream(url=url, title=str(info.get("title") or ""), duration=float(info.get("duration") or 0.0),
                        video_id=str(info.get("id") or ""), width=int(info.get("width") or 0),
                        height=int(info.get("height") or 0))
    rf = info.get("requested_formats")
    if rf:
        for f in rf:
            if f.get("vcodec") not in (None, "none") and not rs.video_url:
                rs.video_url = str(f["url"])
                rs.user_agent = str((f.get("http_headers") or {}).get("User-Agent") or "")
            elif f.get("acodec") not in (None, "none") and not rs.audio_url:
                rs.audio_url = str(f["url"])
    elif info.get("url"):
        rs.video_url = str(info["url"])
        rs.user_agent = str((info.get("http_headers") or {}).get("User-Agent") or "")
    if not rs.video_url:
        raise RuntimeError("재생할 수 있는 스트림을 찾지 못했습니다")
    rs.storyboards = [f for f in (info.get("formats") or []) if str(f.get("format_id", "")).startswith("sb")]
    subs = info.get("subtitles") or {}
    autos = info.get("automatic_captions") or {}
    for lang in SUB_LANGS:
        u = _pick_sub_url(subs.get(lang))
        if u:
            rs.subtitles.append((lang, f"자막 ({lang})", u))
            continue
        for key in (lang, f"{lang}-orig"):
            u = _pick_sub_url(autos.get(key))
            if u:
                rs.subtitles.append((lang, f"자동 자막 ({lang})", u))
                break
    return rs


CACHE_SECONDS = 20 * 60        # 유튜브 재생 주소는 몇 시간 유효하지만 넉넉히 짧게 둔다


class StreamResolver(QObject):
    """유튜브 주소 풀기. 미리 풀기(prefetch)와 20분 캐시로 '붙여넣고 재생'을 빠르게 한다."""

    started = Signal(str)
    resolved = Signal(object)        # ResolvedStream
    failed = Signal(str, str)        # url, message

    def __init__(self, js_runtime: str | None, max_height: int = 2160, parent=None,
                 js_runtime_path: str | None = None) -> None:
        super().__init__(parent)
        self.js_runtime = js_runtime
        self.js_runtime_path = js_runtime_path
        self.max_height = max_height
        self._gen = 0
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, int], tuple[float, ResolvedStream]] = {}
        self._inflight: dict[tuple[str, int], threading.Event] = {}
        self.last_seconds = 0.0

    # ------------------------------------------------------------------
    def warm_up(self) -> None:
        """앱 시작 직후 한 번: yt-dlp 모듈과 JS 런타임을 미리 불러 첫 재생의 대기 시간을 줄인다."""
        rt, rt_path = self.js_runtime, self.js_runtime_path

        def work() -> None:
            try:
                import yt_dlp
                from yt_dlp.extractor import youtube  # noqa: F401
                yt_dlp.YoutubeDL({"quiet": True})
            except Exception:  # noqa: BLE001
                pass
            if rt_path:
                try:
                    flags = 0x08000000 if hasattr(subprocess, "STARTUPINFO") else 0
                    subprocess.run([rt_path, "--version"], capture_output=True, timeout=20, creationflags=flags)
                except Exception:  # noqa: BLE001
                    pass

        threading.Thread(target=work, daemon=True).start()

    def cancel(self) -> None:
        self._gen += 1

    def cached(self, url: str) -> ResolvedStream | None:
        key = (url, int(self.max_height))
        with self._lock:
            hit = self._cache.get(key)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
        return None

    def prefetch(self, url: str) -> None:
        """복사·붙여넣기 순간 미리 풀어 둔다. 결과는 캐시에만 넣고 화면에는 아무것도 하지 않는다."""
        key = (url, int(self.max_height))
        with self._lock:
            if key in self._inflight or (key in self._cache and time.time() - self._cache[key][0] < CACHE_SECONDS):
                return
            ev = self._inflight[key] = threading.Event()

        def work() -> None:
            try:
                self._store(key, self._extract(url, key[1]))
            except Exception:  # noqa: BLE001
                pass
            finally:
                with self._lock:
                    self._inflight.pop(key, None)
                ev.set()

        threading.Thread(target=work, daemon=True).start()

    def resolve(self, url: str) -> None:
        self._gen += 1
        gen = self._gen
        h = int(self.max_height)
        key = (url, h)
        self.started.emit(url)
        t0 = time.time()

        def work() -> None:
            try:
                with self._lock:
                    ev = self._inflight.get(key)
                if ev is not None:
                    ev.wait(60)             # 미리 풀기가 진행 중이면 그 결과를 기다린다
                rs = self.cached(url)
                if rs is None:
                    rs = self._extract(url, h)
                    self._store(key, rs)
            except Exception as e:  # noqa: BLE001
                if gen == self._gen:
                    msg = str(e).splitlines()[0] if str(e) else type(e).__name__
                    self.failed.emit(url, msg.replace("ERROR: ", ""))
                return
            self.last_seconds = time.time() - t0
            if gen == self._gen:
                self.resolved.emit(rs)

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------
    def _store(self, key: tuple[str, int], rs: ResolvedStream) -> None:
        with self._lock:
            self._cache[key] = (time.time(), rs)
            if len(self._cache) > 30:
                oldest = min(self._cache, key=lambda k: self._cache[k][0])
                self._cache.pop(oldest, None)

    def _extract(self, url: str, h: int) -> ResolvedStream:
        import yt_dlp

        opts: dict[str, Any] = {
            "quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
            "format": f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best",
            "subtitleslangs": list(SUB_LANGS), "writesubtitles": True, "writeautomaticsub": True,
        }
        if self.js_runtime:
            opts["js_runtimes"] = {self.js_runtime: {"path": self.js_runtime_path}}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return resolve_info(info, url)
