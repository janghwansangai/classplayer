"""yt-dlp로 유튜브 영상을 파일로 저장한다 (수업 전 미리 받기 / 다운로드 기능)."""
from __future__ import annotations

import os
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
    pot_cli: str | None = None          # PO 토큰 실행 파일 (어린이용 영상 403 대응)
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
            # SponsorBlock 은 _open_ydl() 이 '실패해도 괜찮은' 판으로 붙인다 (학교망 인증서 등으로 실패해도 영상은 저장)
            postprocessors.append({"key": "ModifyChapters", "remove_sponsor_segments": cats, "force_keyframes": False})
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
        if r.ffmpeg_path:
            opts["ffmpeg_location"] = r.ffmpeg_path
        if r.js_runtime:
            opts["js_runtimes"] = {r.js_runtime: {"path": None}}
        if r.pot_cli:
            from classplayer.bootstrap import pot_extractor_args
            opts["extractor_args"] = pot_extractor_args(r.pot_cli, enabled=False)
        opts.update(r.extra)
        return opts

    def _open_ydl(self, opts: dict[str, Any]):
        """YoutubeDL 을 만들고, 협찬 구간 자르기를 켰으면 실패해도 괜찮은 SponsorBlock 후처리를 붙인다.

        yt-dlp 기본 SponsorBlock 은 API 연결이 실패하면(예: 학교망 HTTPS 검사 인증서) 오류를 남겨 두었다가
        영상을 다 받은 뒤 작업 전체를 실패로 끝낸다. 여기서는 경고만 남기고 자르지 않은 채 저장한다.
        """
        import yt_dlp
        from yt_dlp.postprocessor.sponsorblock import SponsorBlockPP

        job = self

        class _SafeSponsorBlockPP(SponsorBlockPP):
            def run(self, info):
                try:
                    return super().run(info)
                except Exception as e:  # noqa: BLE001
                    job.log.emit("경고: SponsorBlock 에 연결하지 못해 협찬 구간을 자르지 않고 저장합니다 "
                                 f"({str(e).splitlines()[0][:160]})")
                    return [], info

        ydl = yt_dlp.YoutubeDL(opts)
        r = self.req
        if r.remove_sponsors and not PRESET_BY_KEY.get(r.preset, PRESETS[0]).audio_only:
            ydl.add_post_processor(_SafeSponsorBlockPP(ydl, categories=list(r.sponsor_categories)), when="after_filter")
        return ydl

    def _download_subtitles(self, video_path: str) -> None:
        """자막은 영상과 따로, 실패해도 괜찮게 받는다.

        유튜브는 자동 번역 자막(예: 한국어 영상의 영어 자막)을 자주 429(요청 과다)로 막는데,
        한 언어가 막혀도 영상 저장 전체가 실패하지 않도록 한다.
        """
        import yt_dlp

        r = self.req
        base = str(Path(video_path).with_suffix(""))
        common: dict[str, Any] = {
            "skip_download": True, "quiet": True, "noprogress": True, "ignoreerrors": True,
            "noplaylist": True, "outtmpl": base + ".%(ext)s", "subtitlesformat": "srt/vtt/best",
            "postprocessors": [{"key": "FFmpegSubtitlesConvertor", "format": "srt"}],
            "logger": None, "socket_timeout": 20,
        }
        if r.ffmpeg_path:
            common["ffmpeg_location"] = r.ffmpeg_path
        if r.js_runtime:
            common["js_runtimes"] = {r.js_runtime: {"path": None}}
        if r.pot_cli:
            from classplayer.bootstrap import pot_extractor_args
            common["extractor_args"] = pot_extractor_args(r.pot_cli, enabled=False)
        # 1) 사람이 만든 자막(한국어·영어)  2) 없으면 원래 말소리 자동 자막(번역 아님)
        passes = [
            {"writesubtitles": True, "writeautomaticsub": False, "subtitleslangs": list(r.sub_langs)},
            {"writesubtitles": False, "writeautomaticsub": True,
             "subtitleslangs": [f"{lang}-orig" for lang in r.sub_langs]},
        ]
        folder, stem = Path(video_path).parent, Path(base).name

        def subs() -> set[str]:   # 파일 이름의 [ ] 때문에 glob 대신 직접 비교
            return {n for n in os.listdir(folder) if n.startswith(stem + ".") and n.endswith(".srt")}

        got = 0
        for extra in passes:
            if self._cancel.is_set():
                return
            before = subs()
            try:
                with yt_dlp.YoutubeDL({**common, **extra}) as ydl:
                    ydl.extract_info(r.url, download=True)
            except Exception as e:  # noqa: BLE001
                self.log.emit(f"자막 받기 일부 실패(영상은 저장됨): {str(e).splitlines()[0]}")
            after = subs()
            got += len(after - before)
            if got:
                break
        self.log.emit(f"자막 {got}개 저장" if got else "받을 수 있는 자막이 없었습니다 (영상은 저장됨)")

    def _run(self) -> None:
        try:

            opts = self._build_opts()
            try:
                with self._open_ydl(opts) as ydl:
                    info = ydl.extract_info(self.req.url, download=True)
            except Exception as e:  # noqa: BLE001
                # 유튜브가 기본 방식 주소를 거절(403)하면 받는 방식을 바꿔 가며 다시 받는다
                if "403" not in str(e) or self._cancel.is_set() or "youtube" in (self.req.extra.get("extractor_args") or {}):
                    raise
                from classplayer.bootstrap import pot_extractor_args
                plans = ([["mweb", "web_safari"]] if self.req.pot_cli else []) + [["tv_simply"], ["android_vr"]]
                info, last = None, e
                for n, clients in enumerate(plans, 1):
                    if self._cancel.is_set():
                        raise last
                    self.log.emit(f"유튜브가 주소를 거절(403)해 다른 방식({', '.join(clients)})으로 다시 받습니다")
                    self.progress.emit(0.0, f"다른 방법으로 다시 받는 중… ({n}/{len(plans)})")
                    opts = self._build_opts()
                    ea = dict(opts.get("extractor_args") or {})
                    ea["youtube"] = {"player_client": clients}
                    ea.update(pot_extractor_args(self.req.pot_cli, enabled=True))
                    opts["extractor_args"] = ea
                    try:
                        with self._open_ydl(opts) as ydl:
                            info = ydl.extract_info(self.req.url, download=True)
                        break
                    except Exception as e2:  # noqa: BLE001
                        last = e2
                        if "403" not in str(e2) and "not available" not in str(e2):
                            raise
                if info is None:
                    raise last
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
            r = self.req
            preset = PRESET_BY_KEY.get(r.preset, PRESETS[0])
            if r.subtitles and not preset.audio_only and self.final_path and not self._cancel.is_set():
                self.progress.emit(0.995, "자막 받는 중…")
                self._download_subtitles(self.final_path)
            self.progress.emit(1.0, "완료")
            self.finished.emit(self.final_path)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "취소" in msg or "Cancelled" in type(e).__name__:
                self.failed.emit("취소됨")
            else:
                self.failed.emit(msg.splitlines()[0] if msg else type(e).__name__)
            self.finished.emit("")
