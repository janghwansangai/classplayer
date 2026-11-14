"""유튜브 링크를 앱 안에서 yt-dlp로 풀어 재생 주소를 얻는다.

mpv 내장 ytdl 훅 대신 이 경로를 기본으로 쓰는 이유: 다운로드 기능과 같은 yt-dlp 설정(JS 런타임,
나중에 PO 토큰 플러그인)을 그대로 쓰고, 제목·자막·영상 ID를 바로 알 수 있어서다.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
import urllib.parse
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QStandardPaths, Signal

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
    rs.storyboards = [
        {"format_id": f.get("format_id"), "width": f.get("width"), "height": f.get("height"),
         "rows": f.get("rows"), "columns": f.get("columns"), "fps": f.get("fps"),
         "http_headers": {str(k): str(v) for k, v in (f.get("http_headers") or {}).items()},
         "fragments": [{"url": str(fr.get("url", "")), "duration": float(fr.get("duration") or 0)}
                       for fr in (f.get("fragments") or [])]}
        for f in (info.get("formats") or []) if str(f.get("format_id", "")).startswith("sb")]
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


CACHE_SECONDS = 20 * 60        # 메모리 캐시 (같은 실행 안에서)
# 기본 클라이언트(TV) 주소가 403 으로 막히는 영상(어린이용 등)은 이 순서로 다시 시도한다
# 2026-11-14 확인: 어린이용 영상은 어느 클라이언트든 PO 토큰 없이는 403 → 한 번만 다시 시도하고 안내한다
FALLBACK_CLIENTS: list[list[str]] = [["web_embedded"]]
# PO 토큰 실행 파일(bgutil-pot)이 있으면 토큰을 받는 web 계열 클라이언트로 다시 시도한다
FALLBACK_CLIENTS_POT: list[list[str]] = [["mweb"], ["web_safari"], ["web_embedded"]]
DISK_MAX_SECONDS = 5 * 3600    # 디스크 캐시 최대 (유튜브 주소는 보통 6시간 유효)


def _expiry(rs: ResolvedStream) -> float:
    """재생 주소가 만료되기 30분 전 시각. 주소에 expire 가 없으면 30분 뒤."""
    now = time.time()
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(rs.video_url).query)
        exp = float((q.get("expire") or [0])[0])
    except Exception:  # noqa: BLE001
        exp = 0.0
    if exp <= 0:
        return now + 30 * 60
    return min(exp - 30 * 60, now + DISK_MAX_SECONDS)


class _DiskCache:
    """앱을 껐다 켜도 남는 주소 캐시: 수업 전에 한 번 열어 두면 수업 중에는 바로 재생된다."""

    def __init__(self) -> None:
        base = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation) or "/tmp")
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / "streams.json"
        self._lock = threading.Lock()
        try:
            self._data: dict[str, dict] = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            self._data = {}

    def get(self, key: str) -> ResolvedStream | None:
        with self._lock:
            d = self._data.get(key)
        if not d or float(d.get("until", 0)) < time.time():
            return None
        try:
            return ResolvedStream(**d["rs"])
        except Exception:  # noqa: BLE001
            return None

    def put(self, key: str, rs: ResolvedStream) -> None:
        with self._lock:
            now = time.time()
            self._data = {k: v for k, v in self._data.items() if float(v.get("until", 0)) > now}
            self._data[key] = {"until": _expiry(rs), "rs": asdict(rs)}
            if len(self._data) > 200:
                for k in sorted(self._data, key=lambda k: self._data[k]["until"])[: len(self._data) - 200]:
                    self._data.pop(k, None)
            self._save()

    def drop(self, key: str) -> None:
        with self._lock:
            if self._data.pop(key, None) is not None:
                self._save()

    def _save(self) -> None:
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except Exception:  # noqa: BLE001
            pass


class StreamResolver(QObject):
    """유튜브 주소 풀기. 미리 풀기(prefetch)와 20분 캐시로 '붙여넣고 재생'을 빠르게 한다."""

    started = Signal(str)
    resolved = Signal(object)        # ResolvedStream
    failed = Signal(str, str)        # url, message

    def __init__(self, js_runtime: str | None, max_height: int = 2160, parent=None,
                 js_runtime_path: str | None = None, pot_cli: str | None = None) -> None:
        super().__init__(parent)
        self.pot_cli = pot_cli
        self.js_runtime = js_runtime
        self.js_runtime_path = js_runtime_path
        self.max_height = max_height
        self._gen = 0
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, int], tuple[float, ResolvedStream]] = {}
        self._inflight: dict[tuple[str, int], threading.Event] = {}
        self._disk = _DiskCache()
        self.last_seconds = 0.0
        self.last_from_cache = False

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

    @property
    def fallback_clients(self) -> list[list[str]]:
        return FALLBACK_CLIENTS_POT if self.pot_cli else FALLBACK_CLIENTS

    @staticmethod
    def _dkey(key: tuple[str, int]) -> str:
        return f"{key[1]}|{key[0]}"

    def cached(self, url: str) -> ResolvedStream | None:
        key = (url, int(self.max_height))
        with self._lock:
            hit = self._cache.get(key)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
        rs = self._disk.get(self._dkey(key))
        if rs is not None:
            with self._lock:
                self._cache[key] = (time.time(), rs)
        return rs

    def forget(self, url: str) -> None:
        """저장해 둔 주소가 안 열릴 때(네트워크가 바뀌어 거절 등) 버린다."""
        key = (url, int(self.max_height))
        with self._lock:
            self._cache.pop(key, None)
        self._disk.drop(self._dkey(key))

    def prefetch(self, url: str) -> None:
        """복사·붙여넣기 순간 미리 풀어 둔다. 결과는 캐시에만 넣고 화면에는 아무것도 하지 않는다."""
        key = (url, int(self.max_height))
        if self.cached(url) is not None:
            return
        with self._lock:
            if key in self._inflight:
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

    def resolve(self, url: str, clients: list[str] | None = None) -> None:
        """clients 를 주면 캐시를 건너뛰고 그 유튜브 클라이언트로 새로 푼다 (403 대응)."""
        self._gen += 1
        gen = self._gen
        h = int(self.max_height)
        key = (url, h)
        self.started.emit(url)
        t0 = time.time()

        def work() -> None:
            try:
                rs = None
                if not clients:
                    with self._lock:
                        ev = self._inflight.get(key)
                    if ev is not None:
                        ev.wait(60)             # 미리 풀기가 진행 중이면 그 결과를 기다린다
                    rs = self.cached(url)
                self.last_from_cache = rs is not None
                if rs is None:
                    rs = self._extract(url, h, clients)
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
        self._disk.put(self._dkey(key), rs)
        with self._lock:
            self._cache[key] = (time.time(), rs)
            if len(self._cache) > 30:
                oldest = min(self._cache, key=lambda k: self._cache[k][0])
                self._cache.pop(oldest, None)

    def _extract(self, url: str, h: int, clients: list[str] | None = None) -> ResolvedStream:
        import yt_dlp

        opts: dict[str, Any] = {
            "quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
            "format": f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best",
            "subtitleslangs": list(SUB_LANGS), "writesubtitles": True, "writeautomaticsub": True,
            # 느린 네트워크에서 한없이 기다리지 않게: 15초 안에 응답이 없으면 실패로 보고 다른 방법으로 연다
            "socket_timeout": 15, "extractor_retries": 2,
        }
        if self.js_runtime:
            opts["js_runtimes"] = {self.js_runtime: {"path": self.js_runtime_path}}
        ea: dict[str, Any] = {}
        if clients:
            ea["youtube"] = {"player_client": list(clients)}
        from classplayer.bootstrap import pot_extractor_args
        ea.update(pot_extractor_args(self.pot_cli, enabled=bool(clients)))   # 다시 시도할 때만 토큰 사용
        if ea:
            opts["extractor_args"] = ea
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return resolve_info(info, url)
