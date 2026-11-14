"""녹화·녹음: 지금 보는 영상의 전체 또는 일부 구간을 파일로 저장한다.

- 로컬 파일: ffmpeg 로 잘라 낸다. 기본은 '빠르게(복사)'라 몇 초면 끝나지만 시작점이 앞쪽 키프레임에 맞춰지고,
  '정확하게'를 고르면 다시 인코딩해서 지정한 시각에 딱 맞춘다.
- 유튜브·드라이브 등 링크: yt-dlp 의 구간 받기(download_ranges)로 그 부분만 받는다.
- 소리만(녹음)은 MP3 로 저장한다.
저장 파일 이름에는 "[녹화 …]" / "[녹음 …]" 표시가 붙어 라이브러리의 '녹화·녹음' 목록에 모인다.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from classplayer.youtube.download import DownloadJob, DownloadRequest

# 복사(-c copy)로 그대로 담을 수 있는 확장자 → ffmpeg 출력 형식
_COPY_FORMATS = {
    ".mp4": "mp4", ".m4v": "mp4", ".mov": "mov", ".mkv": "matroska", ".webm": "webm",
    ".insv": "mp4", ".insp": "mp4", ".osv": "mp4", ".lrf": "mp4", ".lrv": "mp4",
}
_SPHERICAL_EXT = {".insv", ".insp", ".osv", ".lrf", ".lrv"}


def fmt_tag_time(sec: float) -> str:
    s = int(round(max(0.0, sec)))
    h, rem = divmod(s, 3600)
    m, ss = divmod(rem, 60)
    return f"{h}-{m:02d}-{ss:02d}" if h else f"{m:02d}-{ss:02d}"


def make_tag(audio: bool, start: float | None, end: float | None) -> str:
    kind = "녹음" if audio else "녹화"
    if start is None or end is None:
        return f"[{kind} 전체]"
    return f"[{kind} {fmt_tag_time(start)}~{fmt_tag_time(end)}]"


def safe_name(name: str, limit: int = 90) -> str:
    name = re.sub(r'[\\/:*?"<>|\n\r\t]+', " ", name).strip().strip(".")
    return (name[:limit].rstrip() or "영상")


def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    for i in range(2, 1000):
        q = p.with_name(f"{p.stem} ({i}){p.suffix}")
        if not q.exists():
            return q
    return p


@dataclass
class _Req:
    url: str          # 다운로드 목록에 처음 보이는 문구


@dataclass
class LocalRecordRequest:
    src: str
    out_dir: str
    title: str
    start: float | None = None      # None 이면 전체
    end: float | None = None
    audio: bool = False
    accurate: bool = False
    duration: float = 0.0           # 진행률 계산용 (전체 녹화일 때)
    ffmpeg: str | None = None

    def output_path(self) -> tuple[Path, str | None]:
        ext = Path(self.src).suffix.lower()
        tag = make_tag(self.audio, self.start, self.end)
        if self.audio:
            out_ext, fmt = ".mp3", None
        elif self.accurate:
            # 360도 원본은 확장자를 지켜야 다시 열 때 360도로 인식된다
            out_ext = ext if ext in _SPHERICAL_EXT else ".mp4"
            fmt = "mp4"
        elif ext in _COPY_FORMATS:
            out_ext, fmt = ext, _COPY_FORMATS[ext]
        else:
            out_ext, fmt = ".mkv", "matroska"
        name = f"{safe_name(self.title)} {tag}{out_ext}"
        return unique_path(Path(self.out_dir) / name), fmt


def build_ffmpeg_cmd(r: LocalRecordRequest, out: Path, fmt: str | None) -> list[str]:
    cmd = [r.ffmpeg or "ffmpeg", "-y", "-hide_banner", "-nostats", "-loglevel", "error", "-progress", "pipe:1"]
    if r.start is not None and r.start > 0:
        cmd += ["-ss", f"{r.start:.3f}"]
    cmd += ["-i", r.src]
    if r.start is not None and r.end is not None:
        cmd += ["-t", f"{max(0.1, r.end - r.start):.3f}"]
    if r.audio:
        cmd += ["-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-b:a", "192k"]
    elif r.accurate:
        cmd += ["-map", "0:v", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-map", "0:v", "-map", "0:a?"]
        if fmt == "matroska":
            cmd += ["-map", "0:s?"]
        cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    if fmt:
        cmd += ["-f", fmt]
    cmd.append(str(out))
    return cmd


class LocalRecordJob(QObject):
    """로컬 파일을 ffmpeg 로 잘라 저장한다. DownloadJob 과 같은 시그널을 내므로 다운로드 목록에 함께 보인다."""

    progress = Signal(float, str)
    title_known = Signal(str)
    finished = Signal(str)
    failed = Signal(str)
    log = Signal(str)

    def __init__(self, req: LocalRecordRequest, parent=None) -> None:
        super().__init__(parent)
        self.rec = req
        self.out, self.fmt = req.output_path()
        self.req = _Req(url=self.out.name)
        self.display = self.out.name
        self._proc: subprocess.Popen | None = None
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _run(self) -> None:
        r = self.rec
        self.title_known.emit(("녹음: " if r.audio else "녹화: ") + self.out.name)
        total = (r.end - r.start) if (r.start is not None and r.end is not None) else r.duration
        self.out.parent.mkdir(parents=True, exist_ok=True)
        cmd = build_ffmpeg_cmd(r, self.out, self.fmt)
        self.log.emit("ffmpeg " + " ".join(cmd[1:]))
        flags = 0x08000000 if sys.platform.startswith("win") else 0   # CREATE_NO_WINDOW
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                          encoding="utf-8", errors="replace", creationflags=flags)
            assert self._proc.stdout is not None
            for line in self._proc.stdout:
                if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                    try:
                        t = int(line.split("=", 1)[1]) / 1_000_000
                    except ValueError:
                        continue
                    if total > 0:
                        frac = max(0.0, min(0.99, t / total))
                        self.progress.emit(frac, f"저장하는 중 {frac * 100:.0f}%")
            err = self._proc.stderr.read() if self._proc.stderr else ""
            code = self._proc.wait()
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
            self.finished.emit("")
            return
        if self._cancel.is_set() or code != 0 or not self.out.exists() or self.out.stat().st_size == 0:
            try:
                self.out.unlink(missing_ok=True)
            except OSError:
                pass
            if self._cancel.is_set():
                self.failed.emit("취소됨")
            else:
                msg = (err or "").strip().splitlines()
                self.failed.emit(msg[-1] if msg else f"ffmpeg 오류 ({code})")
                if err:
                    self.log.emit(err.strip())
            self.finished.emit("")
            return
        self.progress.emit(1.0, "완료")
        self.finished.emit(str(self.out))


def make_url_record_job(url: str, title: str, out_dir: str, *, start: float | None, end: float | None,
                        audio: bool, ffmpeg: str | None, js_runtime: str | None,
                        remove_sponsors: bool, sponsor_categories: tuple[str, ...]) -> DownloadJob:
    """유튜브·드라이브 링크의 전체/구간을 받는 작업. 이름에 녹화/녹음 표시를 붙인다."""
    tag = make_tag(audio, start, end)
    extra: dict = {"outtmpl": str(Path(out_dir) / f"%(title).90B {tag}.%(ext)s")}
    section = start is not None and end is not None
    if section:
        from yt_dlp.utils import download_range_func

        extra["download_ranges"] = download_range_func(None, [(float(start), float(end))])
        extra["force_keyframes_at_cuts"] = False
    req = DownloadRequest(
        url=url, out_dir=out_dir, preset="audio" if audio else "mp4_1080",
        subtitles=not section and not audio,
        # 구간 녹화에서 광고 구간을 빼면 시간이 어긋나므로 전체 녹화에서만 뺀다
        remove_sponsors=remove_sponsors and not section,
        sponsor_categories=sponsor_categories,
        ffmpeg_path=ffmpeg, js_runtime=js_runtime, extra=extra,
    )
    job = DownloadJob(req)
    job.display = f"{'녹음' if audio else '녹화'}: {title or url} {tag}"   # 목록에 보일 이름 (req.url 은 그대로)
    return job


def default_out_dir(download_dir: str) -> str:
    return os.path.expanduser(download_dir)
