"""업데이트 확인과 설치.

깃허브 릴리스(janghwansangai/classplayer)의 최신 버전을 확인하고, 이 컴퓨터에 맞는 zip 을 받아 압축을 푼 뒤
앱이 종료되면 작은 도우미 스크립트가 새 버전으로 바꿔 끼우고 다시 실행한다.
- 맥: ClassPlayer.app 통째로 교체 (ditto 로 풀어 권한·링크 유지, 격리 속성 제거)
- 윈도우: ClassPlayer 폴더 위에 새 파일을 덮어쓴 뒤 ClassPlayer.exe 실행
개발 환경(uv run)에서는 설치 대신 릴리스 페이지를 연다.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from classplayer import __version__
from classplayer.bootstrap import bundled_dir

REPO = "janghwansangai/classplayer"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
UA = "ClassPlayer-updater"


def parse_version(v: str) -> tuple[int, ...]:
    v = v.strip().lstrip("vV").split("-")[0]
    out = []
    for part in v.split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def is_newer(latest: str, current: str = __version__) -> bool:
    return parse_version(latest) > parse_version(current)


def asset_name() -> str:
    if sys.platform == "darwin":
        return f"ClassPlayer-mac-{platform.machine() or 'arm64'}.zip"
    if sys.platform.startswith("win"):
        return "ClassPlayer-windows-x64.zip"
    return ""


@dataclass
class ReleaseInfo:
    version: str
    notes: str
    page: str
    asset_url: str = ""
    asset_size: int = 0


def fetch_latest(timeout: float = 10.0) -> ReleaseInfo:
    req = urllib.request.Request(API_LATEST, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    info = ReleaseInfo(version=str(data.get("tag_name") or ""), notes=str(data.get("body") or ""),
                       page=str(data.get("html_url") or RELEASES_PAGE))
    want = asset_name()
    for a in data.get("assets") or []:
        if a.get("name") == want:
            info.asset_url = str(a.get("browser_download_url") or "")
            info.asset_size = int(a.get("size") or 0)
    return info


def installed_location() -> Path | None:
    """바꿔 끼울 설치 위치. 맥은 .app, 윈도우는 exe 가 있는 폴더. 번들이 아니면 None."""
    if not bundled_dir():
        return None
    exe = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        for p in exe.parents:
            if p.suffix == ".app":
                return p
        return None
    if sys.platform.startswith("win"):
        d = exe.parent
        return d if (d / "ClassPlayer.exe").exists() else None
    return None


def can_self_install() -> bool:
    loc = installed_location()
    return bool(loc and os.access(loc.parent, os.W_OK) and asset_name())


class Updater(QObject):
    checked = Signal(object, str)        # ReleaseInfo | None, 오류 문구
    progress = Signal(int, str)          # 0..100, 문구
    prepared = Signal(str)               # 새 버전이 풀린 폴더/앱 경로
    failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cancel = threading.Event()
        self.work_dir = Path(tempfile.gettempdir()) / "ClassPlayerUpdate"

    def check(self) -> None:
        def work() -> None:
            try:
                self.checked.emit(fetch_latest(), "")
            except Exception as e:  # noqa: BLE001
                self.checked.emit(None, str(e))

        threading.Thread(target=work, daemon=True).start()

    def cancel(self) -> None:
        self._cancel.set()

    def download_and_prepare(self, info: ReleaseInfo) -> None:
        self._cancel.clear()

        def work() -> None:
            try:
                new = self._download_and_extract(info)
            except Exception as e:  # noqa: BLE001
                self.failed.emit("취소했습니다" if self._cancel.is_set() else str(e))
                return
            self.prepared.emit(str(new))

        threading.Thread(target=work, daemon=True).start()

    def _download_and_extract(self, info: ReleaseInfo) -> Path:
        if not info.asset_url:
            raise RuntimeError("이 컴퓨터용 설치 파일을 릴리스에서 찾지 못했습니다")
        shutil.rmtree(self.work_dir, ignore_errors=True)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        zpath = self.work_dir / asset_name()
        req = urllib.request.Request(info.asset_url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r, open(zpath, "wb") as f:
            total = int(r.headers.get("Content-Length") or info.asset_size or 0)
            done = 0
            while True:
                if self._cancel.is_set():
                    raise RuntimeError("취소")
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                pct = int(done * 100 / total) if total else 0
                self.progress.emit(min(99, pct), f"새 버전 받는 중… {done / 1_048_576:.0f} / {total / 1_048_576:.0f} MB")
        self.progress.emit(99, "압축 푸는 중…")
        out = self.work_dir / "new"
        out.mkdir()
        if sys.platform == "darwin":
            subprocess.run(["/usr/bin/ditto", "-x", "-k", str(zpath), str(out)], check=True)
            new = out / "ClassPlayer.app"
        else:
            with zipfile.ZipFile(zpath) as z:
                for m in z.infolist():
                    name = m.filename.replace("\\", "/")
                    if name.endswith("/") or ".." in Path(name).parts:
                        continue
                    dest = out / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(m) as src, open(dest, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            exe = next(out.rglob("ClassPlayer.exe"), None)
            if exe is None:
                raise RuntimeError("받은 파일에 ClassPlayer.exe 가 없습니다")
            new = exe.parent
        if not new.exists():
            raise RuntimeError("받은 파일의 구성이 예상과 다릅니다")
        zpath.unlink(missing_ok=True)
        self.progress.emit(100, "준비 완료")
        return new

    def launch_installer(self, new: str) -> None:
        """앱이 끝나기를 기다렸다가 교체하고 다시 실행하는 도우미를 띄운다. 호출한 뒤 앱을 종료해야 한다."""
        loc = installed_location()
        if loc is None:
            raise RuntimeError("설치 위치를 찾지 못했습니다")
        pid = str(os.getpid())
        if sys.platform == "darwin":
            script = self.work_dir / "install.sh"
            script.write_text(_MAC_SCRIPT, encoding="utf-8")
            script.chmod(0o755)
            subprocess.Popen(["/bin/bash", str(script), pid, new, str(loc)], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            script = self.work_dir / "install.bat"
            script.write_text(_WIN_SCRIPT, encoding="ascii")
            flags = 0x00000008 | 0x00000200 | 0x08000000   # DETACHED, NEW_PROCESS_GROUP, NO_WINDOW
            subprocess.Popen(["cmd", "/c", str(script), pid, new, str(loc)], creationflags=flags, close_fds=True)


_MAC_SCRIPT = r"""#!/bin/bash
# $1=기다릴 PID  $2=새 ClassPlayer.app  $3=지금 설치된 ClassPlayer.app
PID="$1"; NEW="$2"; APP="$3"
for i in $(seq 1 300); do kill -0 "$PID" 2>/dev/null || break; sleep 0.2; done
/usr/bin/xattr -dr com.apple.quarantine "$NEW" 2>/dev/null
rm -rf "$APP.old"
if mv "$APP" "$APP.old" && mv "$NEW" "$APP"; then
  rm -rf "$APP.old"
else
  [ -d "$APP.old" ] && [ ! -d "$APP" ] && mv "$APP.old" "$APP"
fi
open "$APP"
"""

_WIN_SCRIPT = r"""@echo off
rem %1=PID  %2=new folder  %3=installed folder
:wait
tasklist /FI "PID eq %~1" 2>NUL | find "%~1" >NUL
if not errorlevel 1 (
  timeout /t 1 /nobreak >NUL
  goto wait
)
robocopy "%~2" "%~3" /E /R:5 /W:1 /NFL /NDL /NJH /NJS /NP >NUL
start "" "%~3\ClassPlayer.exe"
"""
