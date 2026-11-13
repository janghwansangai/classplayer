"""유튜브 링크를 앱 안에서 yt-dlp로 풀어 재생 주소를 얻는다.

mpv 내장 ytdl 훅 대신 이 경로를 기본으로 쓰는 이유: 다운로드 기능과 같은 yt-dlp 설정(JS 런타임,
나중에 PO 토큰 플러그인)을 그대로 쓰고, 제목·자막·영상 ID를 바로 알 수 있어서다.
"""
from __future__ import annotations

import threading
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


class StreamResolver(QObject):
    started = Signal(str)
    resolved = Signal(object)        # ResolvedStream
    failed = Signal(str, str)        # url, message

    def __init__(self, js_runtime: str | None, max_height: int = 2160, parent=None) -> None:
        super().__init__(parent)
        self.js_runtime = js_runtime
        self.max_height = max_height
        self._gen = 0

    def cancel(self) -> None:
        self._gen += 1

    def resolve(self, url: str) -> None:
        self._gen += 1
        gen = self._gen
        h = int(self.max_height)
        rt = self.js_runtime
        self.started.emit(url)

        def work() -> None:
            try:
                import yt_dlp

                opts: dict[str, Any] = {
                    "quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
                    "format": f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best",
                    "subtitleslangs": list(SUB_LANGS), "writesubtitles": True, "writeautomaticsub": True,
                }
                if rt:
                    opts["js_runtimes"] = {rt: {"path": None}}
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                rs = resolve_info(info, url)
            except Exception as e:  # noqa: BLE001
                if gen == self._gen:
                    msg = str(e).splitlines()[0] if str(e) else type(e).__name__
                    self.failed.emit(url, msg.replace("ERROR: ", ""))
                return
            if gen == self._gen:
                self.resolved.emit(rs)

        threading.Thread(target=work, daemon=True).start()
