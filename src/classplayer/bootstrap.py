"""실행 환경 준비: libmpv 검색 경로, 외부 도구(yt-dlp, ffmpeg) 위치."""
from __future__ import annotations

import functools
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

# 윈도우에서 ffmpeg/ffprobe 같은 콘솔 프로그램을 띄울 때 검은 창이 번쩍이지 않게 (CREATE_NO_WINDOW).
# subprocess.run/Popen 의 creationflags 로 넘긴다. 다른 운영체제에선 0.
NO_WINDOW = 0x08000000 if sys.platform.startswith("win") else 0


def keys(text: str) -> str:
    """단축키 표기를 운영체제에 맞게: 맥은 ⌘·⇧ 그대로, 윈도우는 Ctrl+·Shift+."""
    if sys.platform == "darwin":
        return text
    return text.replace("⌘⇧", "Ctrl+Shift+").replace("⇧⌘", "Ctrl+Shift+").replace("⌘", "Ctrl+").replace("⇧", "Shift+")


def is_choco_shim(path: str) -> bool:
    """Chocolatey 가 PATH 에 두는 '심(shim)' 실행 파일인지 (진짜 프로그램을 가리키는 작은 대리 파일).

    v0.5.1 윈도우 번들에 진짜 ffmpeg 대신 이 심(392KB)이 들어가 사용자 PC 에서 ffmpeg 가 동작하지 않았다.
    """
    try:
        if os.path.getsize(path) > 2_000_000:
            return False
        with open(path, "rb") as f:
            return b"ShimGen" in f.read()
    except OSError:
        return False



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
    if b and (b / exe).exists() and not (sys.platform == "win32" and is_choco_shim(str(b / exe))):
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


# 윈도우 구글 드라이브(Drive for desktop '스트리밍')는 G: 같은 가상 드라이브에 붙고, 맨 위 폴더 이름이 정해져 있다
_DRIVE_TOP = {"my drive", "내 드라이브", "shared drives", "공유 드라이브", "other computers", "다른 컴퓨터",
              "shared with me", "공유 문서함"}


@functools.lru_cache(maxsize=32)
def _volume_label(drive: str) -> str:
    """윈도우 드라이브(예: 'G:') 의 볼륨 이름. 라이브러리 훑기에서 파일마다 묻지 않게 기억해 둔다."""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(261)
        if ctypes.windll.kernel32.GetVolumeInformationW(drive + "\\", buf, 261, None, None, None, None, 0):
            return buf.value
    except Exception:  # noqa: BLE001
        pass
    return ""


def is_windows_drive_stream(path: str, platform: str = sys.platform, volume_label: str | None = None) -> bool:
    """윈도우 구글 드라이브 가상 드라이브(G:\내 드라이브\…) 위의 파일인지.

    이 드라이브의 파일은 열 때마다 인터넷에서 받아 오는데, 맥과 달리 '아직 안 받음' 표시(속성)가 없어
    예전 앱은 일반 파일로 여겨 mpv·ffprobe·미리보기 ffmpeg(파일 전체)가 한꺼번에 읽었다.
    볼륨 이름이 'Google Drive' 이거나, 드라이브 맨 위 폴더가 '내 드라이브/공유 드라이브' 면 그렇다고 본다.
    """
    if not platform.startswith("win"):
        return False
    import ntpath
    drive, rest = ntpath.splitdrive(path)
    if not drive or drive.startswith(("\\", "//")):
        return False
    if volume_label is None:
        volume_label = _volume_label(drive.upper())
    if "google drive" in volume_label.lower():
        return True
    parts = [x for x in rest.replace("/", "\\").split("\\") if x]
    return len(parts) >= 2 and parts[0].lower() in _DRIVE_TOP


def is_cloud_file(path: str) -> bool:
    """받아 오면서 재생해야 하는 드라이브 파일인지 (맥·윈도우 '자리만 있는' 파일, 윈도우 구글 드라이브 가상 드라이브)."""
    try:
        if os.path.getsize(path) < (8 << 20):
            return False
    except OSError:
        return False
    return is_cloud_placeholder(path) or is_windows_drive_stream(path)


def enable_po_token_plugin() -> str | None:
    """PO 토큰 실행 파일(bgutil-pot)이 있으면 yt-dlp 플러그인 폴더를 검색 경로에 넣고 그 경로를 돌려준다.

    유튜브 어린이용 영상 등은 PO 토큰 없이는 영상 주소가 403 으로 막힌다. yt-dlp 가 처음 YoutubeDL 을 만들 때
    sys.path 에서 yt_dlp_plugins 를 찾으므로, yt-dlp 를 쓰기 전에 불러야 한다. 실행 파일이 없으면 아무것도 안 한다.
    """
    exe = find_tool("bgutil-pot")
    if not exe:
        return None
    b = bundled_dir()
    vendor = (b / "classplayer_vendor") if b else Path(__file__).resolve().parent / "vendor"
    if vendor.is_dir() and str(vendor) not in sys.path:
        sys.path.append(str(vendor))
    return exe


_POT_OFF = os.path.join(os.sep, "classplayer-pot-disabled", "bgutil-pot")


def pot_extractor_args(pot_cli: str | None, enabled: bool) -> dict:
    """yt-dlp extractor_args 에 넣을 PO 토큰 플러그인 설정.

    평소에는 없는 경로를 넘겨 플러그인을 '사용 불가'로 만들고(일반 영상 속도·동작 그대로),
    403 으로 막혀 다시 시도할 때만 실제 실행 파일 경로를 넘긴다.
    """
    if not pot_cli:
        return {}
    return {"youtubepot-bgutilcli": {"cli_path": [pot_cli if enabled else _POT_OFF]}}


_SSL_CTX = None


def ssl_context():
    """HTTPS 인증서 확인용 설정: 운영체제 인증서 + certifi 인증서를 함께 쓴다.

    패키지로 묶은 앱의 Python 은 운영체제 인증서 위치를 못 찾을 때가 있어 urllib 연결이
    CERTIFICATE_VERIFY_FAILED 로 실패한다(업데이트 확인, 미리보기 그림, 검색 썸네일, SponsorBlock).
    학교망 HTTPS 검사 장비는 자체 인증서를 키체인/윈도우 저장소에만 넣으므로 truststore 로 운영체제에 맡긴다.
    """
    global _SSL_CTX
    if _SSL_CTX is None:
        import ssl
        ctx = None
        try:
            # 운영체제 방식으로 확인 (맥 키체인, 윈도우 인증서 저장소). 학교망 HTTPS 검사 인증서도 브라우저처럼 믿는다.
            import truststore
            ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except Exception:  # noqa: BLE001
            ctx = None
        if ctx is None:
            ctx = ssl.create_default_context()
            try:
                import certifi
                ctx.load_verify_locations(cafile=certifi.where())
            except Exception:  # noqa: BLE001
                pass
        _SSL_CTX = ctx
    return _SSL_CTX


def trust_os_certificates() -> bool:
    """프로그램 전체(yt-dlp 포함)의 HTTPS 확인을 운영체제 인증서 저장소에 맡긴다.

    yt-dlp 는 자체 certifi 인증서만 써서, 학교망 HTTPS 검사 장비의 자체 인증서를 거부한다
    (2026-11-16 윈도우: SponsorBlock 'self-signed certificate in certificate chain' → 미리 받기 실패).
    truststore.inject_into_ssl() 은 pip 와 같은 방식으로 ssl.SSLContext 를 운영체제 확인으로 바꾼다.
    yt-dlp 를 import 하기 전에 부른다.
    """
    if os.environ.get("CLASSPLAYER_NO_TRUSTSTORE"):
        return False
    try:
        import truststore
        truststore.inject_into_ssl()
        return True
    except Exception:  # noqa: BLE001
        return False


def https_open(req, timeout: float = 15.0):
    """urllib.request.urlopen 대신 쓴다 (인증서 문제 방지)."""
    import urllib.request
    return urllib.request.urlopen(req, timeout=timeout, context=ssl_context())


def run_low_priority(cmd: list[str], timeout: float = 600):
    """배경 ffmpeg(미리보기 그림, 자동 맞추기 등)를 낮은 우선순위로 돌려 재생이 끊기지 않게 한다.

    preexec_fn 은 스레드가 있는 프로그램에서 안전하지 않아, 띄운 직후 우선순위를 낮춘다.
    돌려주는 값: (stdout 바이트, 종료 코드)
    """
    import subprocess
    flags = (0x00004000 | 0x08000000) if sys.platform.startswith("win") else 0   # BELOW_NORMAL | NO_WINDOW
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=flags)
    if not sys.platform.startswith("win"):
        try:
            os.setpriority(os.PRIO_PROCESS, proc.pid, 10)
        except (OSError, AttributeError):
            pass
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
    return out, proc.returncode
