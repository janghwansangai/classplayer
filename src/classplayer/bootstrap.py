"""실행 환경 준비: libmpv 검색 경로, 외부 도구(yt-dlp, ffmpeg) 위치."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "ClassPlayer"
VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts", ".m2ts", ".mts",
    ".wmv", ".flv", ".mpg", ".mpeg", ".3gp", ".ogv", ".vob", ".mxf", ".hevc", ".h265", ".insv", ".insp", ".osv", ".lrf", ".lrv",
}
# 녹음 파일과 받은 소리 파일도 라이브러리에서 바로 재생할 수 있게
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus"}
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS



def bundled_dir() -> Path | None:
    """PyInstaller 등으로 묶였을 때 리소스 폴더."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else None


def add_to_path(folder: str) -> None:
    """실행 파일 검색 경로(PATH) 맨 앞에 폴더를 넣는다 (이미 있으면 그대로)."""
    if not folder or not os.path.isdir(folder):
        return
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if folder not in parts:
        os.environ["PATH"] = os.pathsep.join([folder] + parts)


def setup_library_paths() -> None:
    """mpv 모듈을 import 하기 전에 libmpv를 찾을 수 있도록 검색 경로를 넣는다."""
    candidates: list[Path] = []
    b = bundled_dir()
    if b:
        candidates += [b / "lib", b]
    if sys.platform == "darwin":
        if not b:   # 번들 실행 중에는 시스템(Homebrew) libmpv 를 섞어 쓰지 않는다
            candidates += [Path("/opt/homebrew/lib"), Path("/usr/local/lib")]
        key = "DYLD_LIBRARY_PATH"
    elif sys.platform == "win32":
        candidates += [Path(sys.executable).parent, Path.cwd() / "bin"]
        key = "PATH"
    else:
        key = "LD_LIBRARY_PATH"

    if b:
        # yt-dlp 는 구간 받기 등에서 ffmpeg 를 PATH 에서만 찾는다(ffmpeg_location 무시). 번들 폴더를 PATH 앞에 둔다.
        add_to_path(str(b))
    existing = [p for p in os.environ.get(key, "").split(os.pathsep) if p]
    ordered = [str(c) for c in candidates if c.is_dir()] + [p for p in existing if p not in map(str, candidates)]
    os.environ[key] = os.pathsep.join(ordered)
    if sys.platform == "win32":
        for c in candidates:
            if c.is_dir():
                try:
                    os.add_dll_directory(str(c))
                except OSError:
                    pass


def loaded_libmpv_path() -> str:
    """실제로 로드된 libmpv 경로 (자가 점검용)."""
    try:
        import mpv  # noqa: WPS433

        return str(getattr(mpv.backend, "_name", ""))
    except Exception:  # noqa: BLE001
        return ""


def find_tool(name: str) -> str | None:
    """yt-dlp, ffmpeg, ffprobe, node 같은 외부 실행 파일 경로를 찾는다."""
    exe = name + (".exe" if sys.platform == "win32" else "")
    b = bundled_dir()
    if b and (b / exe).exists():
        return str(b / exe)
    venv_bin = Path(sys.executable).parent / exe
    if venv_bin.exists():
        return str(venv_bin)
    found = shutil.which(name)
    if found:
        return found
    for p in ("/opt/homebrew/bin", "/usr/local/bin"):
        if (Path(p) / exe).exists():
            return str(Path(p) / exe)
    return None


def is_cloud_placeholder(path: str) -> bool:
    """구글 드라이브·아이클라우드·원드라이브처럼 '자리만 있고 내용은 아직 안 받은' 파일인지.

    이런 파일을 바로 열면 재생하면서 내려받기 때문에 자주 멈춘다.
    """
    try:
        st = os.stat(path)
    except OSError:
        return False
    if st.st_size < (8 << 20):
        return False
    if sys.platform == "darwin":
        # SF_DATALESS(0x40000000): 내용이 클라우드에만 있음
        if getattr(st, "st_flags", 0) & 0x40000000:
            return True
        return getattr(st, "st_blocks", 0) * 512 < st.st_size * 0.5
    if sys.platform.startswith("win"):
        attrs = getattr(st, "st_file_attributes", 0)
        # FILE_ATTRIBUTE_OFFLINE, RECALL_ON_DATA_ACCESS, RECALL_ON_OPEN
        return bool(attrs & (0x1000 | 0x400000 | 0x40000))
    return False
