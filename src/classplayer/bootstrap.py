"""실행 환경 준비: libmpv 검색 경로, 외부 도구(yt-dlp, ffmpeg) 위치."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "ClassPlayer"
VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts", ".m2ts", ".mts",
    ".wmv", ".flv", ".mpg", ".mpeg", ".3gp", ".ogv", ".vob", ".mxf", ".hevc", ".h265",
}


def bundled_dir() -> Path | None:
    """PyInstaller 등으로 묶였을 때 리소스 폴더."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else None


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
