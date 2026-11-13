"""yt-dlp로 유튜브 영상을 파일로 저장한다 (수업 전 미리 받기 / 다운로드 기능)."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    fmt: str
    merge: str | None
    audio_only: bool = False


PRESETS: list[Preset] = [
    Preset("mp4_1080", "1080p MP4 (가장 호환, 기본)",
           "bestvideo[ext=mp4][vcodec^=avc1][height<=1080]+bestaudio[ext=m4a]/"
           "best[ext=mp4][height<=1080]/best[height<=1080]", "mp4"),
    Preset("mp4_720", "720p MP4 (용량 절약)",
           "bestvideo[ext=mp4][vcodec^=avc1][height<=720]+bestaudio[ext=m4a]/"
           "best[ext=mp4][height<=720]/best[height<=720]", "mp4"),
    Preset("best", "최고 화질 (4K/8K, 원본 코덱, MKV)", "bestvideo+bestaudio/best", "mkv"),
    Preset("audio", "소리만 (MP3)", "bestaudio/best", None, audio_only=True),
]
PRESET_BY_KEY = {p.key: p for p in PRESETS}


@dataclass
class DownloadRequest:
    url: str
    out_dir: str
    preset: str = "mp4_1080"
    subtitles: bool = True
    sub_langs: tuple[str, ...] = ("ko", "en")
    remove_sponsors: bool = True
    sponsor_categories: tuple[str, ...] = ("sponsor", "selfpromo")
    playlist: bool = False
    ffmpeg_path: str | None = None
    js_runtime: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class DownloadJob(QObject):
    """하나의 다운로드 작업. 작업 스레드에서 yt-dlp를 돌리고 진행 상황을 시그널로 보낸다."""

    progress = Signal(float, str)      # 0..1, 상태 문구
    title_known = Signal(str)
    finished = Signal(str)             # 최종 파일 경로 ("" 이면 실패/취소)
    failed = Signal(str)
    log = Signal(str)

    def __init__(self, req: DownloadRequest, parent=None) -> None:
        super().__init__(parent)
        self.req = req
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self.final_path: str = ""
        self.title: str = ""

    # ------------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ------------------------------------------------------------------
    def _build_opts(self) -> dict[str, Any]:
        import yt_dlp  # 느린 import 는 작업 스레드에서

        r = self.req
        preset = PRESET_BY_KEY.get(r.preset, PRESETS[0])
        out_dir = Path(r.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        job = self

        class _Logger:
            def debug(self, msg: str) -> None:
                if msg.startswith("[download]"):
                    return
                job.log.emit(msg)

            def info(self, msg: str) -> None:
                job.log.emit(msg)

            def warning(self, msg: str) -> None:
                job.log.emit("경고: " + msg)

            def error(self, msg: str) -> None:
                job.log.emit("오류: " + msg)

        def progress_hook(d: dict[str, Any]) -> None:
            if job._cancel.is_set():
                raise yt_dlp.utils.DownloadCancelled("사용자가 취소했습니다")
            st = d.get("status")
            info = d.get("info_dict") or {}
            if info.get("title") and not job.title:
                job.title = str(info["title"])
                job.title_known.emit(job.title)
            if st == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes") or 0
                frac = (done / total) if total else 0.0
                speed = d.get("speed") or 0
                eta = d.get("eta")
                spd = f"{speed / 1_048_576:.1f} MB/s" if speed else ""
                eta_s = f" · 남은 시간 {int(eta)}초" if eta else ""
                job.progress.emit(max(0.0, min(0.99, frac)), f"받는 중 {frac * 100:.0f}% {spd}{eta_s}")
            elif st == "finished":
                job.progress.emit(0.99, "합치는 중...")

        def pp_hook(d: dict[str, Any]) -> None:
            if job._cancel.is_set():
                raise yt_dlp.utils.DownloadCancelled("사용자가 취소했습니다")
            if d.get("status") == "started":
                job.progress.emit(0.99, f"후처리: {d.get('postprocessor', '')}")
            if d.get("status") == "finished":
                info = d.get("info_dict") or {}
                fp = info.get("filepath") or info.get("_filename")
                if fp:
                    job.final_path = str(fp)

        postprocessors: list[dict[str, Any]] = []
        if r.remove_sponsors and not preset.audio_only:
            cats = list(r.sponsor_categories)
            postprocessors.append({"key": "SponsorBlock", "categories": cats, "when": "after_filter"})
            postprocessors.append({"key": "ModifyChapters", "remove_sponsor_segments": cats, "force_keyframes": False})
        if r.subtitles and not preset.audio_only:
            postprocessors.append({"key": "FFmpegSubtitlesConvertor", "format": "srt"})
        if preset.audio_only:
            postprocessors.append({"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"})

        opts: dict[str, Any] = {
            "format": preset.fmt,
            "outtmpl": str(out_dir / "%(title).120B [%(id)s].%(ext)s"),
            "windowsfilenames": True,
            "noplaylist": not r.playlist,
            "progress_hooks": [progress_hook],
            "postprocessor_hooks": [pp_hook],
            "postprocessors": postprocessors,
            "logger": _Logger(),
            "noprogress": True,
            "quiet": True,
            "retries": 5,
            "fragment_retries": 10,
            "concurrent_fragment_downloads": 4,
            "overwrites": False,
        }
        if preset.merge:
            opts["merge_output_format"] = preset.merge
        if r.subtitles and not preset.audio_only:
            opts.update({"writesubtitles": True, "writeautomaticsub": True,
                         "subtitleslangs": list(r.sub_langs), "subtitlesformat": "srt/best"})
        if r.ffmpeg_path:
            opts["ffmpeg_location"] = r.ffmpeg_path
        if r.js_runtime:
            opts["js_runtimes"] = {r.js_runtime: {"path": None}}
        opts.update(r.extra)
        return opts

    def _run(self) -> None:
        try:
            import yt_dlp

            opts = self._build_opts()
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(self.req.url, download=True)
            if not self.final_path and info:
                entry = info
                if info.get("entries"):
                    entries = [e for e in info["entries"] if e]
                    entry = entries[-1] if entries else info
                rd = entry.get("requested_downloads") or []
                if rd and rd[-1].get("filepath"):
                    self.final_path = str(rd[-1]["filepath"])
                elif entry.get("filepath"):
                    self.final_path = str(entry["filepath"])
            self.progress.emit(1.0, "완료")
            self.finished.emit(self.final_path)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "취소" in msg or "Cancelled" in type(e).__name__:
                self.failed.emit("취소됨")
            else:
                self.failed.emit(msg.splitlines()[0] if msg else type(e).__name__)
            self.finished.emit("")
